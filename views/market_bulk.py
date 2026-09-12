"""市场批量购买弹窗 + 开箱逻辑（从 market_view.py 拆分）

MarketBulkOverlay 持有市场视图引用（self.mv），所有市场状态
（_bulk_state/_box_results/_tc 等）均通过 self.mv 访问，不新增全局状态。
"""

import arcade
import random
from config import WINDOW_WIDTH, WINDOW_HEIGHT, WEAPON_BOXES, EQUIPMENT_BOXES, ALL_WEAPON_IDS, ALL_EQUIP_POOL
from db.database import get_gold, spend_gold, add_equipment, add_potion, add_weapon
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
from game.sound_manager import sound_manager


class MarketBulkOverlay:
    """批量购买弹窗 + 开箱逻辑：通过 self.mv 访问市场视图状态"""

    def __init__(self, market_view):
        """保存市场视图引用，供各方法经 self.mv 读写其状态"""
        self.mv = market_view

    def _open_bulk(self, item_type, data):
        """打开批量购买弹窗：计算金币可购买上限，数量初始为 1

        item_type: buy_helmet/buy_armor/buy_backpack/buy_potion/buy_weapon/buy_box
        data     : _build_content 中该商品对应的 data 字典
        """
        pid = self.mv.window.game_state.player_id
        gold = get_gold(pid)
        cost = data["cost"]
        max_qty = max(1, gold // cost) if cost > 0 else 1
        self.mv._bulk_state = {
            "item_type": item_type,
            "data": data,
            "cost": cost,
            "max_qty": max_qty,
            "qty": 1,
            "dragging": False,     # 是否正在拖动滑块
            "input_active": False,  # 输入框是否处于编辑态
        }
        self.mv._input_cursor_timer = 0.0

    def _cancel_bulk(self):
        """取消批量购买，关闭弹窗（不产生任何消费）"""
        self.mv._bulk_state = None

    def _set_qty(self, qty):
        """设置数量并夹取到 [1, max_qty] 范围内"""
        st = self.mv._bulk_state
        if not st:
            return
        st["qty"] = max(1, min(st["max_qty"], qty))

    def _slider_handle_x(self):
        """根据当前数量计算滑块手柄的屏幕 X 坐标（数量 1→最左，max→最右）"""
        st = self.mv._bulk_state
        track_left = 640 - 215   # 与 _draw_bulk_overlay 中轨道定义保持一致
        track_w = 430
        if st["max_qty"] <= 1:
            return 640
        ratio = (st["qty"] - 1) / (st["max_qty"] - 1)
        return track_left + ratio * track_w

    def _handle_bulk_press(self, x, y):
        """处理批量购买弹窗内的点击：滑块/输入框/加减/确认/取消/点击外部关闭"""
        st = self.mv._bulk_state
        if not st:
            return
        # 点击弹窗外部遮罩 → 取消
        panel = arcade.XYWH(640, 360, 540, 360)
        if not panel.point_in_rect((x, y)):
            self._cancel_bulk()
            return
        # 确认 / 取消按钮
        if arcade.XYWH(560, 195, 170, 42).point_in_rect((x, y)):
            self._confirm_bulk()
            return
        if arcade.XYWH(720, 195, 170, 42).point_in_rect((x, y)):
            self._cancel_bulk()
            return
        # 减号 / 加号按钮
        if arcade.XYWH(528, 300, 34, 36).point_in_rect((x, y)):
            self._set_qty(st["qty"] - 1)
            return
        if arcade.XYWH(752, 300, 34, 36).point_in_rect((x, y)):
            self._set_qty(st["qty"] + 1)
            return
        # 输入框：进入编辑态
        if arcade.XYWH(640, 300, 180, 36).point_in_rect((x, y)):
            st["input_active"] = True
            self.mv._input_cursor_timer = 0.0
            return
        # 滑块轨道 / 手柄：按下开始拖动（点击轨道也可直接跳转数量）
        st["input_active"] = False
        track_left = 640 - 215
        track_w = 430
        if track_left - 15 <= x <= track_left + track_w + 15 and 360 <= y <= 420:
            st["dragging"] = True
            self._set_qty(round((x - track_left) / track_w * (st["max_qty"] - 1)) + 1)
            return

    def _confirm_bulk(self):
        """确认批量购买：一次性扣费，按数量循环入库；宝箱则批量开箱并播放序列动画"""
        st = self.mv._bulk_state
        if not st:
            return
        pid = self.mv.window.game_state.player_id
        cost = st["cost"]
        qty = st["qty"]
        item_type = st["item_type"]
        data = st["data"]
        total_cost = cost * qty
        # 再次校验金币足够（防止弹窗停留期间余额变动）
        if get_gold(pid) < total_cost:
            return
        spend_gold(pid, total_cost)
        sound_manager.play_upgrade()
        if item_type in ("buy_helmet", "buy_armor", "buy_backpack"):
            slot = "helmet" if "helmet" in item_type else ("armor" if "armor" in item_type else "backpack")
            for _ in range(qty):
                add_equipment(pid, data["item_id"], slot)
        elif item_type == "buy_potion":
            for _ in range(qty):
                add_potion(pid, data["item_id"], data["name"], data["effect"],
                           data["value"], data["duration"])
        elif item_type == "buy_weapon":
            for _ in range(qty):
                add_weapon(pid, data["item_id"], data["name"], data["kind"],
                           data["damage"], data["attack_speed"])
        elif item_type == "buy_box":
            # 批量开箱：逐个随机产出并入库存，结果进入队列，随后统一播放动画
            for _ in range(qty):
                self._roll_box(data["box_type"], data["box_id"])
            self._start_box_sequence()
        self.mv._bulk_state = None
        self.mv._rebuild_keep_view()

    def _roll_box(self, box_type, box_id):
        """开一个宝箱：随机产出并入库存，结果追加到 _box_results 队列

        原单次开箱逻辑抽离以便批量复用：每次产出相互独立
        """
        pid = self.mv.window.game_state.player_id
        if box_type == "weapon":
            box = WEAPON_BOXES[box_id]
            random_wid = random.choice(ALL_WEAPON_IDS)
            wdef = RANGED_WEAPONS.get(random_wid) or MELEE_WEAPONS.get(random_wid)
            if wdef:
                lo, hi = box["level_range"]
                level = random.randint(lo, hi)
                # 根据等级调整伤害（平方根亚线性倍率，与锻造/升级数值平衡一致）
                from config import upgrade_mult_product
                adjusted_damage = round(wdef["damage"] * upgrade_mult_product(level), 1)
                add_weapon(pid, random_wid, wdef["name"], wdef["kind"],
                           adjusted_damage, wdef.get("attack_speed", 1.0), level)
                self.mv._box_results.append({
                    "name": wdef["name"], "level": level,
                    "color": wdef.get("color", (255, 200, 50)),
                })
        else:  # equipment box
            box = EQUIPMENT_BOXES[box_id]
            slot, eid = random.choice(ALL_EQUIP_POOL)
            lo, hi = box["level_range"]
            level = random.randint(lo, hi)
            add_equipment(pid, eid, slot, level)
            # 获取装备名称
            from entities.equipment_defs import HELMETS, ARMORS
            eq_def = HELMETS.get(eid) or ARMORS.get(eid)
            eq_name = eq_def["name"] if eq_def else eid
            self.mv._box_results.append({
                "name": eq_name, "level": level,
                "color": eq_def.get("color", (150, 180, 200)) if eq_def else (150, 180, 200),
            })

    def _start_box_sequence(self):
        """启动批量开箱序列动画：按批量数动态压缩单箱时长，避免大批量等待过久"""
        total = len(self.mv._box_results)
        if total <= 0:
            return
        self.mv._box_index = 0
        self.mv._box_total = total
        # 批量越多单箱动画越快（买 1 个用默认 1.5s，大批量压缩到 0.45s 下限）
        self.mv._box_open_duration = max(0.45, 1.5 - (total - 1) * 0.04)
        self.mv._box_open_timer = 0.0
        self.mv._box_opening = True
        sound_manager.play_chest_open()

    def _draw_bulk_overlay(self):
        """绘制批量购买弹窗：遮罩 + 面板 + 数量显示 + 滑块 + 输入框 + 加减 + 按钮"""
        st = self.mv._bulk_state
        if not st:
            return
        data = st["data"]
        qty = st["qty"]
        # 全屏半透明遮罩
        arcade.draw_rect_filled(
            arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
            (0, 0, 0, 170),
        )
        # 面板
        arcade.draw_rect_filled(arcade.XYWH(640, 360, 540, 360), (30, 35, 50))
        # 标题与单价
        self.mv._tc.text("bulk_title", f"批量购买 - {data['name']}", 640, 520,
                         arcade.color.GOLD, 20, anchor_x="center", anchor_y="center")
        self.mv._tc.text("bulk_price", f"单价: {st['cost']} 金币", 640, 488,
                         arcade.color.LIGHT_GRAY, 12, anchor_x="center", anchor_y="center")
        # 数量大数字
        self.mv._tc.text("bulk_qty", f"{qty}", 640, 430, arcade.color.WHITE, 42,
                         anchor_x="center", anchor_y="center")
        # 滑块轨道与手柄
        arcade.draw_rect_filled(arcade.XYWH(640, 390, 430, 8), (70, 70, 82))
        handle_x = self._slider_handle_x()
        arcade.draw_rect_filled(arcade.XYWH(handle_x, 390, 26, 36), (220, 220, 230))
        self.mv._tc.text("bulk_hint", f"可购买 1 ~ {st['max_qty']} 个（拖动滑块或输入数量）", 640, 352,
                         arcade.color.GRAY, 10, anchor_x="center", anchor_y="center")
        # 输入框（点击进入编辑态，编辑时用实心矩形叠加模拟高亮边框，避免线框绘制闪烁）
        box = arcade.XYWH(640, 300, 180, 36)
        if st["input_active"]:
            arcade.draw_rect_filled(arcade.XYWH(640, 300, 186, 42), (120, 180, 120))
        arcade.draw_rect_filled(box, (20, 24, 32))
        # 输入框文本：编辑态且光标亮起时末尾追加 "|" 模拟光标
        if st["input_active"] and int(self.mv._input_cursor_timer * 2) % 2 == 0:
            self.mv._tc.text("bulk_input", str(qty) + "|", 640, 300, arcade.color.WHITE, 20,
                             anchor_x="center", anchor_y="center")
        else:
            self.mv._tc.text("bulk_input", str(qty), 640, 300, arcade.color.WHITE, 20,
                             anchor_x="center", anchor_y="center")
        # 减号 / 加号按钮
        arcade.draw_rect_filled(arcade.XYWH(528, 300, 34, 36), (70, 70, 82))
        self.mv._tc.text("bulk_minus", "-", 528, 300, arcade.color.WHITE, 24,
                         anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(arcade.XYWH(752, 300, 34, 36), (70, 70, 82))
        self.mv._tc.text("bulk_plus", "+", 752, 300, arcade.color.WHITE, 22,
                         anchor_x="center", anchor_y="center")
        # 总价
        self.mv._tc.text("bulk_total", f"总价: {st['cost']} × {qty} = {st['cost'] * qty} 金币", 640, 250,
                         arcade.color.YELLOW, 15, anchor_x="center", anchor_y="center")
        # 确认 / 取消按钮
        arcade.draw_rect_filled(arcade.XYWH(560, 195, 170, 42), arcade.color.DARK_GREEN)
        self.mv._tc.text("bulk_confirm", "确认购买", 560, 195, arcade.color.WHITE, 14,
                         anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(arcade.XYWH(720, 195, 170, 42), arcade.color.DARK_RED)
        self.mv._tc.text("bulk_cancel", "取消", 720, 195, arcade.color.WHITE, 14,
                         anchor_x="center", anchor_y="center")