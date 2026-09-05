"""市场页面：升级武器/装备 + 购买装备/药水 + 开箱（支持滚轮滚动）"""

import arcade
import random
from config import WINDOW_WIDTH, WINDOW_HEIGHT, UPGRADE_BASE_COST, WEAPON_BOXES, EQUIPMENT_BOXES, ALL_WEAPON_IDS, ALL_EQUIP_POOL
from db.database import (
    get_weapons, get_gold, upgrade_weapon, spend_gold,
    add_equipment, add_potion, get_equipment, upgrade_equipment, add_weapon,
)
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS, POTIONS
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
from entities.effects_defs import effects_label
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from game.sound_manager import sound_manager


class MarketView(ScrollView):
    def __init__(self, window):
        super().__init__(window)
        # 文本缓存：持久 arcade.Text 对象，复用纹理避免每帧重建文本（消除 PerformanceWarning）
        self._tc = TextCache()
        self.wh_rect = arcade.XYWH(WINDOW_WIDTH - 100, 40, 120, 36)
        # 顶部分类 Tab 栏：按分类构建内容，切换时重建并归零滚动
        self._tabs = ["武器", "装备", "药水", "宝箱"]
        self._tab = "武器"
        self.tab_rects = {}
        tab_w, tab_h, gap = 120, 30, 12
        total_w = len(self._tabs) * tab_w + (len(self._tabs) - 1) * gap
        start_x = (WINDOW_WIDTH - total_w) // 2
        for i, name in enumerate(self._tabs):
            self.tab_rects[name] = arcade.XYWH(
                start_x + i * (tab_w + gap) + tab_w / 2, WINDOW_HEIGHT - 135, tab_w, tab_h,
            )
        # 开箱动画状态（支持批量：_box_results 为本次批量开出的结果队列）
        self._box_opening = False
        self._box_open_timer = 0.0
        self._box_open_duration = 1.5  # 单个宝箱动画持续时间（秒）
        self._box_results = []  # 批量开箱结果队列：[{"name":..., "level":..., "color":...}, ...]
        self._box_index = 0     # 当前播放到第几个（0 起）
        self._box_total = 0     # 本次批量开箱总数
        # 批量购买弹窗状态（None = 关闭）
        self._bulk_state = None
        self._input_cursor_timer = 0.0  # 输入框光标闪烁计时
        # 新手教程（阶段 6）：市场买卖教学向导（教程最后一站，完成后标记）
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False
        self._build_content()

    def _build_tutorial_pages(self):
        """新手教程阶段 6：市场买卖教学（2 页，完成后标记 tutorial_done）"""
        from views.tutorial import TutorialPage
        return [
            TutorialPage("市场 · 买卖装备", [
                "市场可购买武器/装备/药水，也可消耗材料升级装备，还能开神秘宝箱。",
                "顶部『武器/装备/药水/宝箱』分类栏切换货架，右下角按钮购买或升级。",
                "点『仓库』可存入/取出物品，市场与仓库是打怪战利品的集中管理地。",
            ]),
            TutorialPage("自由逛逛吧", [
                "点击上方分类栏切换看看各个货架（可购买一件装备体验）。",
                "结束后点左下角『返回大厅』即可完成新手教程！",
            ], highlight=self.tab_rects["武器"], next_text="完成教程"),
        ]

    def _tut_showing(self):
        """教程向导是否正在本界面显示（阶段 6 且未翻完页）"""
        tut = getattr(self.window.game_state, "tutorial", None)
        return (tut is not None and tut.active and tut.stage == 5
                and tut.page < len(self.tut_pages))

    def _build_content(self):
        """构建当前分类页的内容列表，每项记录类型和逻辑Y坐标

        顶部分类 Tab（武器/装备/药水/宝箱）决定只构建对应区块，
        切换 Tab 时（on_mouse_press 内联处理）重建此列表并归零滚动。
        """
        self._tc.clear()  # 内容结构重建，清空文本缓存避免旧 key 残留
        self.content_items = []  # [(type, y, data)]
        pid = self.window.game_state.player_id
        weapons = get_weapons(pid)
        gold = get_gold(pid)

        # 统计武器数量
        weapon_counts = {}
        for w in weapons:
            key = (w["name"], w["level"])
            weapon_counts[key] = weapon_counts.get(key, 0) + 1

        y = 0  # 从0开始计算逻辑Y

        if self._tab == "武器":
            # === 武器升级区域 ===
            self.content_items.append(("header", y, "─── 武器升级 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 同名同级武器 x1 + 金币 (伤害+15% 攻速+15%)"))
            y -= 50

            if weapons:
                for w in weapons:
                    kind_label = "近战" if w["kind"] == "melee" else "远程"
                    cost = UPGRADE_BASE_COST * w["level"]
                    key = (w["name"], w["level"])
                    has_material = weapon_counts.get(key, 0) >= 2
                    self.content_items.append(("weapon_upgrade", y, {
                        "id": w["id"], "name": w["name"], "level": w["level"],
                        "kind": kind_label, "damage": w["damage"], "cost": cost,
                        "has_material": has_material, "can_upgrade": has_material and gold >= cost,
                        "effects": w.get("effects") or [],
                    }))
                    y -= 50
            else:
                self.content_items.append(("text", y, "(无武器，击杀怪物获取)"))
                y -= 40

            # === 购买武器区域===
            y -= 20
            self.content_items.append(("header", y, "─── 购买武器 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (购买后可在仓库装备使用)"))
            y -= 40
            for wdef in list(MELEE_WEAPONS.values()) + list(RANGED_WEAPONS.values()):
                # 拳头是初始武器，不可购买
                if wdef["item_id"] == "fist":
                    continue
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if wdef.get("price", 100) <= 0:
                    continue
                # 受限制武器（枪械/权杖/诅咒弯刀等）不可购买，只能开箱/锻造/掉落获得
                if wdef.get("market_restricted"):
                    continue
                cost = wdef.get("price", 100)
                kind_label = "近战" if wdef["kind"] == "melee" else "远程"
                self.content_items.append(("buy_weapon", y, {
                    "item_id": wdef["item_id"], "name": wdef["name"],
                    "kind": wdef["kind"], "kind_label": kind_label,
                    "damage": wdef["damage"], "attack_speed": wdef.get("attack_speed", 1.0),
                    "range": wdef.get("range", 40), "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

        elif self._tab == "装备":
            # === 升级头盔（显示全部拥有的头盔）===
            from db.database import get_equipment_inventory, get_equipment_materials
            inventory = get_equipment_inventory(pid)
            owned_helmets = [e for e in inventory if e["slot"] == "helmet"]
            if owned_helmets:
                y -= 20
                self.content_items.append(("header", y, "─── 升级头盔 ───"))
                self.content_items.append(("subheader", y - 18, "条件: 同名同级头盔 x1 + 金币 (防+15%)"))
                y -= 40
                for helm in owned_helmets:
                    upgrade_cost = helm["defense"] * 30
                    materials = get_equipment_materials(pid, "helmet", helm["name"], helm["level"], exclude_id=helm["id"])
                    has_material = len(materials) > 0
                    status = "★已装备" if helm["is_equipped"] else ""
                    self.content_items.append(("upgrade_helmet", y, {
                        "name": helm["name"], "defense": helm["defense"], "level": helm["level"],
                        "cost": upgrade_cost, "can_upgrade": has_material and gold >= upgrade_cost,
                        "material_id": materials[0] if has_material else None,
                        "has_material": has_material, "equip_id": helm["id"], "status": status,
                        "effects": helm.get("effects") or [],
                    }))
                    y -= 40

            # === 购买头盔 ===
            y -= 20
            self.content_items.append(("header", y, "─── 购买头盔 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (穿戴后减少受到的伤害)"))
            y -= 40
            for item_id, info in HELMETS.items():
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if info.get("price", 100) <= 0:
                    continue
                # 受限制物品（木乃伊头盔等）不可购买，只能开箱/锻造/掉落获得
                if info.get("market_restricted"):
                    continue
                cost = info.get("price", 100)
                self.content_items.append(("buy_helmet", y, {
                    "item_id": item_id, "name": info["name"],
                    "defense": info["defense"], "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

            # === 升级护甲（显示全部拥有的护甲）===
            owned_armors = [e for e in inventory if e["slot"] == "armor"]
            if owned_armors:
                y -= 20
                self.content_items.append(("header", y, "─── 升级护甲 ───"))
                self.content_items.append(("subheader", y - 18, "条件: 同名同级护甲 x1 + 金币 (防+15%)"))
                y -= 40
                for arm in owned_armors:
                    upgrade_cost = arm["defense"] * 30
                    materials = get_equipment_materials(pid, "armor", arm["name"], arm["level"], exclude_id=arm["id"])
                    has_material = len(materials) > 0
                    status = "★已装备" if arm["is_equipped"] else ""
                    self.content_items.append(("upgrade_armor", y, {
                        "name": arm["name"], "defense": arm["defense"], "level": arm["level"],
                        "cost": upgrade_cost, "can_upgrade": has_material and gold >= upgrade_cost,
                        "material_id": materials[0] if has_material else None,
                        "has_material": has_material, "equip_id": arm["id"], "status": status,
                        "effects": arm.get("effects") or [],
                    }))
                    y -= 40

            # === 购买护甲 ===
            y -= 20
            self.content_items.append(("header", y, "─── 购买护甲 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (穿戴后减少受到的伤害)"))
            y -= 40
            for item_id, info in ARMORS.items():
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if info.get("price", 150) <= 0:
                    continue
                # 受限制物品（木乃伊护甲等）不可购买，只能开箱/锻造/掉落获得
                if info.get("market_restricted"):
                    continue
                cost = info.get("price", 150)
                self.content_items.append(("buy_armor", y, {
                    "item_id": item_id, "name": info["name"],
                    "defense": info["defense"], "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

            # === 购买背包 ===
            y -= 20
            self.content_items.append(("header", y, "─── 购买背包 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (没有背包不能拾取资源)"))
            y -= 40
            for item_id, info in BACKPACKS.items():
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if info.get("price", 200) <= 0:
                    continue
                cost = info.get("price", 200)
                self.content_items.append(("buy_backpack", y, {
                    "item_id": item_id, "name": info["name"],
                    "capacity": info["capacity"], "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

        elif self._tab == "药水":
            # === 购买药水 ===
            self.content_items.append(("header", y, "─── 购买药水 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (按1-3使用)"))
            y -= 40
            for pot_id, info in POTIONS.items():
                # 价格为0的物品（仙人掌果实等）不可在市场购买（仅沙漠掉落获得）
                if info.get("price", 50) <= 0:
                    continue
                # 受限制物品不可购买（与武器/装备判断逻辑保持一致）
                if info.get("market_restricted"):
                    continue
                cost = info.get("price", 50)
                self.content_items.append(("buy_potion", y, {
                    "item_id": pot_id, "name": info["name"],
                    "desc": info.get("description", ""), "cost": cost,
                    "effect": info["effect"], "value": info["value"],
                    "duration": info.get("duration", 0),
                    "can_buy": gold >= cost,
                }))
                y -= 40

        else:  # 宝箱
            # === 武器箱区域 ===
            self.content_items.append(("header", y, "─── 武器箱 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (随机获得一把武器)"))
            y -= 40
            for box_id, box in WEAPON_BOXES.items():
                cost = box["price"]
                self.content_items.append(("buy_box", y, {
                    "box_id": box_id, "name": box["name"],
                    "desc": box["description"], "color": box["color"],
                    "cost": cost, "box_type": "weapon",
                    "can_buy": gold >= cost,
                }))
                y -= 40

            # === 装备箱区域 ===
            y -= 20
            self.content_items.append(("header", y, "─── 装备箱 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (随机获得一件装备)"))
            y -= 40
            for box_id, box in EQUIPMENT_BOXES.items():
                cost = box["price"]
                self.content_items.append(("buy_box", y, {
                    "box_id": box_id, "name": box["name"],
                    "desc": box["description"], "color": box["color"],
                    "cost": cost, "box_type": "equipment",
                    "can_buy": gold >= cost,
                }))
                y -= 40

        self.content_height = abs(y) + 300  # 总内容高度（多加一些确保能滚到底）

    def _rebuild_keep_view(self):
        """重建内容列表，并调整 scroll_offset 使当前视口锚点保持稳定。

        购买物品后内容列表会变化（新增升级区/行会推动后续项的逻辑Y坐标），
        若只重建不补偿偏移，页面会莫名跳动/上滑。这里锚定重建前视口内
        第一个带唯一标识的可见项，重建后把它拉回原屏幕位置。
        """
        def _item_key(item_type, data):
            """返回能唯一标识同一种内容项的键（header/subheader/text 无标识返回 None）。"""
            if not isinstance(data, dict):
                return None
            return (data.get("item_id") or data.get("id")
                    or data.get("equip_id") or data.get("box_id"))

        # 1) 记录锚点：视口内第一个有唯一标识的可见项及其屏幕Y
        anchor = None
        content_top = WINDOW_HEIGHT - 160  # 与 on_draw / on_mouse_press 一致（下方为 Tab 栏）
        for item_type, item_y, data in self.content_items:
            key = _item_key(item_type, data)
            if key is None:
                continue
            screen_y = content_top + item_y + self.scroll_offset
            if 40 <= screen_y <= content_top + 20:
                anchor = (item_type, key, screen_y)
                break

        # 2) 重建内容
        self._build_content()

        # 3) 若锚点项仍在，补偿偏移使其回到原来的屏幕位置
        if anchor is not None:
            a_type, a_key, old_screen_y = anchor
            for item_type, item_y, data in self.content_items:
                key = _item_key(item_type, data)
                if item_type == a_type and key == a_key:
                    new_screen_y = content_top + item_y + self.scroll_offset
                    self.scroll_offset += old_screen_y - new_screen_y
                    self.clamp_scroll()
                    break

    def get_bg_color(self):
        return (25, 20, 35)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """覆盖基类：开箱动画中禁止滚动"""
        if self._box_opening:
            return
        super().on_mouse_scroll(x, y, scroll_x, scroll_y)

    def on_update(self, delta_time: float):
        """更新开箱动画计时器（批量：逐个播放结果队列）"""
        self._input_cursor_timer += delta_time
        if self._box_opening:
            self._box_open_timer += delta_time
            if self._box_open_timer >= self._box_open_duration:
                # 当前宝箱动画结束，播放下一个或收尾
                self._box_index += 1
                if self._box_index >= len(self._box_results):
                    self._box_opening = False
                    self._box_results = []
                    self._box_index = 0
                    self._box_total = 0
                else:
                    self._box_open_timer = 0.0

    def on_draw(self):
        self.clear()
        pid = self.window.game_state.player_id
        gold = get_gold(pid)
        equip = get_equipment(pid)
        offset = self.scroll_offset

        # === 固定头部 ===
        self._tc.text("header_title", "市 场", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30,
                      arcade.color.GOLD, 30, anchor_x="center")
        self._tc.text("header_gold", f"金币: {gold}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
                      arcade.color.YELLOW, 18, anchor_x="center")
        self._tc.text("header_hint", "滚轮滚动查看全部商品", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 80,
                      arcade.color.GRAY, 11, anchor_x="center")

        # 当前装备
        equip_text = ""
        if "helmet" in equip:
            equip_text += f"头盔:{equip['helmet']['name']}(防+{equip['helmet']['defense']}) "
        if "armor" in equip:
            equip_text += f"护甲:{equip['armor']['name']}(防+{equip['armor']['defense']}) "
        if "backpack" in equip:
            equip_text += f"背包:{equip['backpack']['name']}(容量:{equip['backpack']['capacity']})"
        if equip_text:
            self._tc.text("equip_now", f"当前装备: {equip_text}", 60, WINDOW_HEIGHT - 100,
                          arcade.color.CORNFLOWER_BLUE, 11)
        else:
            self._tc.text("equip_now", "当前装备: 无", 60, WINDOW_HEIGHT - 100,
                          arcade.color.GRAY, 11)
        content_top = WINDOW_HEIGHT - 160  # 内容区顶部（下方固定 Tab 栏）

        # === 顶部 Tab 栏（固定，不随内容滚动）===
        for name, rect in self.tab_rects.items():
            active = (name == self._tab)
            bg = (80, 130, 80) if active else (50, 55, 65)
            arcade.draw_rect_filled(rect, bg)
            self._tc.text(f"tab_{name}", name, rect.center_x, rect.center_y,
                          arcade.color.WHITE if active else arcade.color.LIGHT_GRAY,
                          15, anchor_x="center", anchor_y="center")

        # === 可滚动内容 ===
        for i, (item_type, item_y, data) in enumerate(self.content_items):
            screen_y = content_top + item_y + offset
            # 跳过不在视口内的项
            if screen_y < 40 or screen_y > content_top + 20:
                continue

            if item_type == "header":
                self._tc.text(f"header_{i}", data, 60, screen_y, arcade.color.LIGHT_GRAY, 13)
            elif item_type == "subheader":
                self._tc.text(f"subheader_{i}", data, 60, screen_y, arcade.color.GRAY, 10)
            elif item_type == "text":
                self._tc.text(f"text_{i}", data, 60, screen_y, arcade.color.GRAY, 12)

            elif item_type == "weapon_upgrade":
                # 武器信息（含附加效果显示）
                eff_txt = ""
                if data.get("effects"):
                    eff_txt = f"  效果:{effects_label(data['effects'])}"
                self._tc.text(
                    f"weapon_upgrade_{i}",
                    f"[{data['kind']}] {data['name']}  Lv.{data['level']}  伤害:{data['damage']:.0f}{eff_txt}",
                    60, screen_y, arcade.color.CORNFLOWER_BLUE, 12,
                )
                # 升级按钮
                btn_color = arcade.color.DARK_GREEN if data["can_upgrade"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                label = f"升级({data['cost']}G)" if data["has_material"] else "缺材料"
                self._tc.text(f"weapon_upgrade_btn_{i}", label, btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type in ("upgrade_helmet", "upgrade_armor"):
                status = data.get("status", "")
                eff_txt = ""
                if data.get("effects"):
                    eff_txt = f"  效果:{effects_label(data['effects'])}"
                self._tc.text(
                    f"{item_type}_{i}",
                    f"Lv{data['level']} {data['name']}  防+{data['defense']}  {status}{eff_txt}",
                    60, screen_y, arcade.color.CORNFLOWER_BLUE, 12,
                )
                btn_color = arcade.color.DARK_GREEN if data["can_upgrade"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                label = f"升级({data['cost']}G)" if data["has_material"] else "缺材料"
                self._tc.text(f"{item_type}_btn_{i}", label, btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type in ("buy_helmet", "buy_armor", "buy_backpack"):
                label = f"{data['name']}  "
                if "defense" in data:
                    label += f"防+{data['defense']}"
                elif "capacity" in data:
                    label += f"容量:{data['capacity']}"
                self._tc.text(f"{item_type}_{i}", label, 60, screen_y, arcade.color.GREEN, 12)
                # 购买按钮
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"{item_type}_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_potion":
                # 第一行：药水名
                self._tc.text(f"buy_potion_{i}", f"{data['name']}", 60, screen_y,
                              arcade.color.GREEN, 13)
                # 第二行：效果介绍（从 POTIONS 定义取 description）
                effect_desc = data.get("desc", "")
                if effect_desc:
                    self._tc.text(f"buy_potion_desc_{i}", f"    {effect_desc}", 70, screen_y - 16,
                                  arcade.color.LIGHT_GRAY, 11)
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"buy_potion_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_weapon":
                self._tc.text(
                    f"buy_weapon_{i}",
                    f"[{data['kind_label']}] {data['name']}  伤害:{data['damage']}  距离:{data['range']}",
                    60, screen_y, arcade.color.CORNFLOWER_BLUE, 12,
                )
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"buy_weapon_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_box":
                color = data.get("color", (200, 150, 50))
                self._tc.text(
                    f"buy_box_{i}",
                    f"📦 {data['name']}  {data['desc']}",
                    60, screen_y, color, 12,
                )
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"buy_box_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

        # 滚动条（基类统一绘制 + 支持鼠标拖拽）
        self.draw_scrollbar(content_top)

        # === 固定底部导航 ===
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("nav_back", "返回大厅", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(self.wh_rect, arcade.color.DARK_BLUE)
        self._tc.text("nav_wh", "仓库", self.wh_rect.center_x, self.wh_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

        # === 开箱动画覆盖层（支持批量：逐个播放 _box_results 队列）===
        if self._box_opening and self._box_results:
            # 半透明黑色遮罩
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
                (0, 0, 0, 180),
            )
            # 动画进度 0→1
            progress = min(1.0, self._box_open_timer / self._box_open_duration)
            result = self._box_results[self._box_index]
            # 缩放效果：从0.5倍放大到1.0倍
            scale = 0.5 + 0.5 * min(1.0, progress * 2)
            # 闪光效果：前0.3秒有闪光
            flash_alpha = max(0, 1.0 - progress * 3) * 255
            # 宝箱图标
            box_color = result.get("color", (255, 200, 50))
            box_size = int(80 * scale)
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 40, box_size, box_size),
                box_color,
            )
            if flash_alpha > 0:
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 40, box_size + 20, box_size + 20),
                    (255, 255, 255, int(flash_alpha)),
                )
            # 获得物品信息（动画后半段显示）
            if progress > 0.5:
                text_alpha = min(1.0, (progress - 0.5) * 4)
                name = result.get("name", "")
                level = result.get("level", 1)
                text = f"获得 {name} Lv.{level}!"
                self._tc.text(
                    "box_result", text, WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 40,
                    arcade.color.GOLD, 22, anchor_x="center", anchor_y="center",
                )
            # 批量进度（仅一次购买多个宝箱时显示）
            if self._box_total > 1:
                self._tc.text(
                    "box_progress", f"宝箱 {self._box_index + 1} / {self._box_total}",
                    WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 150,
                    arcade.color.LIGHT_GRAY, 14, anchor_x="center", anchor_y="center",
                )
                # 整体进度条（尾部两相进度 = 已播完数量 + 当前箱内进度）
                total_progress = (self._box_index + progress) / self._box_total
                bar_w = 360
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 120, bar_w, 10),
                    (60, 60, 60),
                )
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2 - bar_w / 2 + bar_w * total_progress / 2,
                                WINDOW_HEIGHT // 2 + 120, bar_w * total_progress, 10),
                    arcade.color.GOLD,
                )

        # === 批量购买弹窗覆盖层 ===
        self._draw_bulk_overlay()

        # === 新手教程（阶段 6）：买卖教学向导弹窗（画在最上层）===
        if self._tut_showing():
            from views.tutorial import draw_tutorial_page
            tut = getattr(self.window.game_state, "tutorial", None)
            page = self.tut_pages[tut.page]
            self.tut_next_rect, self.tut_skip_rect = draw_tutorial_page(
                self, page, tut.page, len(self.tut_pages),
                self._tc, self.tut_next_hover)

    def on_mouse_press(self, x, y, button, modifiers):
        # 动画中禁止所有点击
        if self._box_opening:
            return

        sound_manager.play_ui()
        # 新手教程向导显示：只响应 下一步/跳过（最后一页点完成后标记教程结束）
        if self._tut_showing():
            from views.tutorial import finish_tutorial
            if self.tut_skip_rect and self.tut_skip_rect.point_in_rect((x, y)):
                finish_tutorial(self.window)
                return
            if self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)):
                tut = getattr(self.window.game_state, "tutorial", None)
                if tut is not None and tut.page + 1 >= len(self.tut_pages):
                    # 最后一页：完成教程并标记（留在市场可自由浏览/购买）
                    finish_tutorial(self.window)
                elif tut is not None:
                    tut.page += 1
                return
            return

        # 批量购买弹窗打开时：只处理弹窗交互，不穿透到下方列表
        if self._bulk_state:
            self._handle_bulk_press(x, y)
            return

        pid = self.window.game_state.player_id
        offset = self.scroll_offset

        # 固定按钮
        if self.handle_back_click(x, y):
            # 新手教程：市场是最后一站，点返回时确保标记完成（防中途未点完成按钮）
            tut = getattr(self.window.game_state, "tutorial", None)
            if tut is not None and tut.active and tut.stage == 5:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return
        if self.wh_rect.point_in_rect((x, y)):
            from views.warehouse_view import WarehouseView
            self.window.show_view(WarehouseView(self.window_ref))
            return

        # 顶部 Tab 栏切换分类
        for name, rect in self.tab_rects.items():
            if rect.point_in_rect((x, y)):
                if name != self._tab:
                    self._tab = name
                    self.scroll_offset = 0.0  # 切换分类时归零滚动
                    self._build_content()
                return

        # 计算内容区域顶部（须与 on_draw 完全一致）
        get_equipment(pid)  # 保持与绘制时一致的状态读取
        content_top = WINDOW_HEIGHT - 160

        # 右侧滚动条拖拽
        if self.start_scroll_drag(x, y, content_top):
            return

        # 检测可滚动内容中的按钮点击
        for item_type, item_y, data in self.content_items:
            screen_y = content_top + item_y + offset
            if screen_y < 40 or screen_y > content_top + 20:
                continue

            if item_type == "weapon_upgrade":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_upgrade"]:
                    upgrade_weapon(pid, data["id"])
                    self._rebuild_keep_view()
                    return

            elif item_type in ("upgrade_helmet", "upgrade_armor"):
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_upgrade"]:
                    upgrade_equipment(pid, data["equip_id"], data["material_id"])
                    self._rebuild_keep_view()
                    return

            elif item_type in ("buy_helmet", "buy_armor", "buy_backpack"):
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"]:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_potion":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"]:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_weapon":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"]:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_box":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"] and not self._box_opening:
                    self._open_bulk(item_type, data)
                    return

    # ── 批量购买弹窗 ─────────────────────────────────────────

    def _open_bulk(self, item_type, data):
        """打开批量购买弹窗：计算金币可购买上限，数量初始为 1

        item_type: buy_helmet/buy_armor/buy_backpack/buy_potion/buy_weapon/buy_box
        data     : _build_content 中该商品对应的 data 字典
        """
        pid = self.window.game_state.player_id
        gold = get_gold(pid)
        cost = data["cost"]
        max_qty = max(1, gold // cost) if cost > 0 else 1
        self._bulk_state = {
            "item_type": item_type,
            "data": data,
            "cost": cost,
            "max_qty": max_qty,
            "qty": 1,
            "dragging": False,     # 是否正在拖动滑块
            "input_active": False,  # 输入框是否处于编辑态
        }
        self._input_cursor_timer = 0.0

    def _cancel_bulk(self):
        """取消批量购买，关闭弹窗（不产生任何消费）"""
        self._bulk_state = None

    def _set_qty(self, qty):
        """设置数量并夹取到 [1, max_qty] 范围内"""
        st = self._bulk_state
        if not st:
            return
        st["qty"] = max(1, min(st["max_qty"], qty))

    def _slider_handle_x(self):
        """根据当前数量计算滑块手柄的屏幕 X 坐标（数量 1→最左，max→最右）"""
        st = self._bulk_state
        track_left = 640 - 215   # 与 _draw_bulk_overlay 中轨道定义保持一致
        track_w = 430
        if st["max_qty"] <= 1:
            return 640
        ratio = (st["qty"] - 1) / (st["max_qty"] - 1)
        return track_left + ratio * track_w

    def _handle_bulk_press(self, x, y):
        """处理批量购买弹窗内的点击：滑块/输入框/加减/确认/取消/点击外部关闭"""
        st = self._bulk_state
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
            self._input_cursor_timer = 0.0
            return
        # 滑块轨道 / 手柄：按下开始拖动（点击轨道也可直接跳转数量）
        st["input_active"] = False
        track_left = 640 - 215
        track_w = 430
        if track_left - 15 <= x <= track_left + track_w + 15 and 360 <= y <= 420:
            st["dragging"] = True
            self._set_qty(round((x - track_left) / track_w * (st["max_qty"] - 1)) + 1)
            return

    def on_mouse_motion(self, x, y, dx, dy):
        """滚动条拖拽实时滚动；滑块拖拽时实时更新数量"""
        # 新手教程向导显示：只更新下一步按钮悬停态
        if self._tut_showing():
            self.tut_next_hover = bool(
                self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)))
            return
        if self._scroll_dragging:
            self.update_scroll_drag(x, y)
            return
        st = self._bulk_state
        if st and st["dragging"]:
            track_left = 640 - 215
            track_w = 430
            self._set_qty(round((x - track_left) / track_w * (st["max_qty"] - 1)) + 1)

    def on_mouse_release(self, x, y, button, modifiers):
        """松开鼠标：结束滚动条拖拽与滑块拖拽"""
        self.end_scroll_drag()
        if self._bulk_state:
            self._bulk_state["dragging"] = False

    def on_key_press(self, symbol, modifiers):
        """弹窗键盘交互：Esc 取消、Enter 确认、输入态下支持数字与退格"""
        # 新手教程激活：ESC 立即跳过并标记完成（优先于批量弹窗的 Esc 取消）
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active:
            if symbol == arcade.key.ESCAPE:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return
        st = self._bulk_state
        if not st:
            return
        if symbol == arcade.key.ESCAPE:
            self._cancel_bulk()
        elif symbol == arcade.key.ENTER:
            self._confirm_bulk()
        elif st["input_active"]:
            if symbol == arcade.key.BACKSPACE:
                s = str(st["qty"])[:-1] or "1"
                self._set_qty(int(s))
            elif arcade.key.KEY_0 <= symbol <= arcade.key.KEY_9:
                s = str(st["qty"]) + chr(symbol)
                self._set_qty(int(s))
            elif arcade.key.KEY_NUM_0 <= symbol <= arcade.key.KEY_NUM_9:
                s = str(st["qty"]) + chr(symbol - arcade.key.KEY_NUM_0 + ord("0"))
                self._set_qty(int(s))

    def _confirm_bulk(self):
        """确认批量购买：一次性扣费，按数量循环入库；宝箱则批量开箱并播放序列动画"""
        st = self._bulk_state
        if not st:
            return
        pid = self.window.game_state.player_id
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
        self._bulk_state = None
        self._rebuild_keep_view()

    def _roll_box(self, box_type, box_id):
        """开一个宝箱：随机产出并入库存，结果追加到 _box_results 队列

        原单次开箱逻辑抽离以便批量复用：每次产出相互独立
        """
        pid = self.window.game_state.player_id
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
                self._box_results.append({
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
            self._box_results.append({
                "name": eq_name, "level": level,
                "color": eq_def.get("color", (150, 180, 200)) if eq_def else (150, 180, 200),
            })

    def _start_box_sequence(self):
        """启动批量开箱序列动画：按批量数动态压缩单箱时长，避免大批量等待过久"""
        total = len(self._box_results)
        if total <= 0:
            return
        self._box_index = 0
        self._box_total = total
        # 批量越多单箱动画越快（买 1 个用默认 1.5s，大批量压缩到 0.45s 下限）
        self._box_open_duration = max(0.45, 1.5 - (total - 1) * 0.04)
        self._box_open_timer = 0.0
        self._box_opening = True
        sound_manager.play_chest_open()

    def _draw_bulk_overlay(self):
        """绘制批量购买弹窗：遮罩 + 面板 + 数量显示 + 滑块 + 输入框 + 加减 + 按钮"""
        st = self._bulk_state
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
        self._tc.text("bulk_title", f"批量购买 - {data['name']}", 640, 520,
                      arcade.color.GOLD, 20, anchor_x="center", anchor_y="center")
        self._tc.text("bulk_price", f"单价: {st['cost']} 金币", 640, 488,
                      arcade.color.LIGHT_GRAY, 12, anchor_x="center", anchor_y="center")
        # 数量大数字
        self._tc.text("bulk_qty", f"{qty}", 640, 430, arcade.color.WHITE, 42,
                      anchor_x="center", anchor_y="center")
        # 滑块轨道与手柄
        arcade.draw_rect_filled(arcade.XYWH(640, 390, 430, 8), (70, 70, 82))
        handle_x = self._slider_handle_x()
        arcade.draw_rect_filled(arcade.XYWH(handle_x, 390, 26, 36), (220, 220, 230))
        self._tc.text("bulk_hint", f"可购买 1 ~ {st['max_qty']} 个（拖动滑块或输入数量）", 640, 352,
                      arcade.color.GRAY, 10, anchor_x="center", anchor_y="center")
        # 输入框（点击进入编辑态，编辑时用实心矩形叠加模拟高亮边框，避免线框绘制闪烁）
        box = arcade.XYWH(640, 300, 180, 36)
        if st["input_active"]:
            arcade.draw_rect_filled(arcade.XYWH(640, 300, 186, 42), (120, 180, 120))
        arcade.draw_rect_filled(box, (20, 24, 32))
        # 输入框文本：编辑态且光标亮起时末尾追加 "|" 模拟光标
        if st["input_active"] and int(self._input_cursor_timer * 2) % 2 == 0:
            self._tc.text("bulk_input", str(qty) + "|", 640, 300, arcade.color.WHITE, 20,
                          anchor_x="center", anchor_y="center")
        else:
            self._tc.text("bulk_input", str(qty), 640, 300, arcade.color.WHITE, 20,
                          anchor_x="center", anchor_y="center")
        # 减号 / 加号按钮
        arcade.draw_rect_filled(arcade.XYWH(528, 300, 34, 36), (70, 70, 82))
        self._tc.text("bulk_minus", "-", 528, 300, arcade.color.WHITE, 24,
                      anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(arcade.XYWH(752, 300, 34, 36), (70, 70, 82))
        self._tc.text("bulk_plus", "+", 752, 300, arcade.color.WHITE, 22,
                      anchor_x="center", anchor_y="center")
        # 总价
        self._tc.text("bulk_total", f"总价: {st['cost']} × {qty} = {st['cost'] * qty} 金币", 640, 250,
                      arcade.color.YELLOW, 15, anchor_x="center", anchor_y="center")
        # 确认 / 取消按钮
        arcade.draw_rect_filled(arcade.XYWH(560, 195, 170, 42), arcade.color.DARK_GREEN)
        self._tc.text("bulk_confirm", "确认购买", 560, 195, arcade.color.WHITE, 14,
                      anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(arcade.XYWH(720, 195, 170, 42), arcade.color.DARK_RED)
        self._tc.text("bulk_cancel", "取消", 720, 195, arcade.color.WHITE, 14,
                      anchor_x="center", anchor_y="center")
