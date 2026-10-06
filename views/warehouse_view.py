"""仓库页面：查看/售卖资源，装备/售卖武器，管理装备（支持滚轮滚动）

阶段10：武器/装备的**售价**计入市场设施的售出回收加成
（entities.facility_defs.market_sell_bonus(市场设施等级)）：
Lv0（未建造）/Lv1 = +0% → 与旧口径完全一致；Lv2 = +5%、Lv3 = +10%。
显示价与实际入账金币同经 db 的 weapon_sell_price/equipment_sell_price 纯函数计算，
保证「看到的价 = 售得的金币」。资源售卖价走 config 的 sell_price，不受该加成影响。
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from entities.resource_defs import RESOURCES
from entities.facility_defs import market_sell_bonus
from db.database import (
    get_warehouse, get_weapons, get_gold, sell_warehouse_item, sell_weapon,
    get_equipment_inventory, equip_from_inventory, sell_equipment,
    get_facility_level, weapon_sell_price, equipment_sell_price,
    # 阶段11：仓库等级 / 容量上限 / 升级链路
    get_warehouse_level, get_warehouse_capacity, warehouse_used_capacity,
    warehouse_upgrade_cost_at, warehouse_can_afford, upgrade_warehouse,
)
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from game.sound_manager import sound_manager

# 市场设施 id（售出回收加成的来源；等级 0 = 未建造 → 无加成）
MARKET_FACILITY_ID = "market"


class WarehouseView(ScrollView):
    def __init__(self, window):
        super().__init__(window)
        self._tc = TextCache()  # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        # 顶部分类 Tab 栏：资源/武器/装备，切换时重建并按分类显示
        self._tabs = ["资源", "武器", "装备"]
        self._tab = "资源"
        self.tab_rects = {}
        tab_w, tab_h, gap = 120, 30, 12
        total_w = len(self._tabs) * tab_w + (len(self._tabs) - 1) * gap
        start_x = (WINDOW_WIDTH - total_w) // 2
        for i, name in enumerate(self._tabs):
            self.tab_rects[name] = arcade.XYWH(
                start_x + i * (tab_w + gap) + tab_w / 2, WINDOW_HEIGHT - 135, tab_w, tab_h,
            )
        self.sell_buttons = []      # [(rect, wh_id, gold_per, item_id)]
        self.equip_buttons = []     # [(rect, weapon_dict)]
        self.weapon_sell_buttons = []  # [(rect, weapon_dict, sell_price)]
        self.equip_item_buttons = []   # [(rect, equip_dict)]
        self.equip_sell_buttons = []   # [(rect, equip_dict, sell_price)]
        # 资源售卖数量对话框状态（2026-10-04：售卖改选数量，仿 backpack 丢弃弹窗）
        self._sell_dialog_active = False   # 对话框是否显示
        self._sell_dialog_item = None      # (wh_id, item_id, name, max_qty, gold_per)
        self._sell_dialog_qty = 1          # 确认时生效的售卖数量
        self._sell_dialog_input = "1"      # 输入缓冲区（字符串）
        self._sell_dialog_btn_1 = None     # "卖1个" 按钮
        self._sell_dialog_btn_all = None   # "全部" 按钮
        self._sell_dialog_btn_confirm = None  # "确认" 按钮
        self._sell_dialog_btn_cancel = None   # "取消" 按钮
        self.market_rect = arcade.XYWH(WINDOW_WIDTH - 100, 40, 120, 36)
        # 阶段11：仓库升级按钮（底部居中；与左侧「返回」/右侧「市场」留足间距）
        self.upgrade_rect = arcade.XYWH(WINDOW_WIDTH // 2, 40, 300, 36)
        self.upgrade_hover = False
        # 阶段11：飘字提示（参考 views/facility_view.py 的 toast 模式）
        self._toasts: list[list] = []   # [[text, color, 剩余寿命], ...]
        self._rebuild()

    # ── 阶段11 飘字提示 ────────────────────────────────────────────

    def _toast(self, text: str, color=(255, 200, 120)) -> None:
        """弹一条飘字提示（实心文字，禁 outline/线框以免闪烁）；最多同时 3 条

        与 facility_view/codex_view/mission_view 的飘字同模式：纯文字、无描边，
        故不受渲染铁律里「禁空心/线框绘制」的限制。
        """
        self._toasts.append([text, color, 2.0])
        del self._toasts[:-3]

    def on_update(self, delta_time: float) -> None:
        """飘字寿命倒计时（到点移除，避免残留）"""
        for t in self._toasts:
            t[2] -= delta_time
        self._toasts = [t for t in self._toasts if t[2] > 0]

    def _draw_toasts(self) -> None:
        """绘制飘字（屏幕中下部，自下而上堆叠；纯实心文字，禁 outline/线框绘制）"""
        for i, (text, color, _life) in enumerate(reversed(self._toasts)):
            ty = 96 - i * 26
            self._tc.text(f"toast_{i}", text, WINDOW_WIDTH // 2, ty, color,
                          14, anchor_x="center", anchor_y="center")

    # ── 资源售卖数量对话框（2026-10-04，仿 views/backpack_view.py 丢弃弹窗）──

    def _show_sell_dialog(self, wh_id: int, gold_per: int) -> None:
        """打开售卖数量对话框（资源售卖按钮点击时调用）

        - 库存在开窗时现查（按钮坐标来自 _rebuild 缓存，可能已被其他操作改动）
        - 数量 1 件直接售出不弹面板（与背包丢弃同口径：无可选数量时面板无意义）
        - 数量 >1 弹面板：1个 / 全部(N) / 自定义输入 / 确认 / 取消
        """
        pid = self.window.game_state.player_id
        max_qty, item_id, name = 0, "", ""
        for item in get_warehouse(pid):
            if item["id"] == wh_id and item["item_type"] == "resource":
                max_qty = item["quantity"]
                item_id = item["item_id"]
                name = RESOURCES.get(item_id, {}).get("name", item_id)
                break
        if max_qty <= 0:
            self._rebuild()  # 库存已空（他处已售/存入变化），刷新列表防幽灵按钮
            return
        if max_qty == 1:
            gold, _ = sell_warehouse_item(pid, wh_id, qty=1)
            self._rebuild()
            if gold > 0:
                self._toast(f"已出售 {name} x1，+{gold} 金币", (140, 230, 140))
            return
        self._sell_dialog_active = True
        self._sell_dialog_item = (wh_id, item_id, name, max_qty, gold_per)
        self._sell_dialog_qty = 1
        self._sell_dialog_input = "1"
        # 按钮布局与背包丢弃弹窗完全一致（标题→数量→快捷按钮→输入框→确认/取消）
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        self._sell_dialog_btn_1 = arcade.XYWH(cx - 60, cy + 5, 100, 32)
        self._sell_dialog_btn_all = arcade.XYWH(cx + 60, cy + 5, 100, 32)
        self._sell_dialog_btn_confirm = arcade.XYWH(cx + 60, cy - 75, 80, 32)
        self._sell_dialog_btn_cancel = arcade.XYWH(cx - 60, cy - 75, 80, 32)

    def _handle_sell_dialog_click(self, x: int, y: int) -> None:
        """处理售卖对话框内的按钮点击（弹窗激活时 on_mouse_press 只进本方法）"""
        if self._sell_dialog_btn_1 and self._sell_dialog_btn_1.point_in_rect((x, y)):
            self._sell_dialog_qty = 1
            self._execute_sell()
            return
        if self._sell_dialog_btn_all and self._sell_dialog_btn_all.point_in_rect((x, y)):
            self._sell_dialog_qty = self._sell_dialog_item[3]
            self._execute_sell()
            return
        if self._sell_dialog_btn_confirm and self._sell_dialog_btn_confirm.point_in_rect((x, y)):
            try:
                qty = max(1, min(int(self._sell_dialog_input), self._sell_dialog_item[3]))
            except ValueError:
                qty = 1
            self._sell_dialog_qty = qty
            self._execute_sell()
            return
        if self._sell_dialog_btn_cancel and self._sell_dialog_btn_cancel.point_in_rect((x, y)):
            self._sell_dialog_active = False
            self._sell_dialog_item = None
            return

    def _execute_sell(self) -> None:
        """执行售卖并关闭对话框（部分售出走 sell_warehouse_item 的 qty 参数）"""
        if not self._sell_dialog_item:
            return
        wh_id, item_id, name, max_qty, _gold_per = self._sell_dialog_item
        qty = self._sell_dialog_qty
        self._sell_dialog_active = False
        self._sell_dialog_item = None
        pid = self.window.game_state.player_id
        gold, _ = sell_warehouse_item(pid, wh_id, qty=qty)
        self._rebuild()
        if gold > 0:
            self._toast(f"已出售 {name} x{qty}，+{gold} 金币", (140, 230, 140))
        else:
            self._toast("出售失败：物品已不存在", (255, 120, 120))

    def _draw_sell_dialog(self) -> None:
        """绘制售卖数量对话框（屏幕坐标系覆盖层，不受滚动影响）

        渲染铁律：全部不透明实心填充，不调用 draw_rect_outline/draw_line 线框 API。
        """
        # 半透明遮罩（实心填充）
        arcade.draw_rect_filled(
            arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
            (0, 0, 0, 150)
        )
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        wh_id, item_id, name, max_qty, gold_per = self._sell_dialog_item
        # 对话框背景 + 白色描边感（用两层实心矩形叠出边框，禁 outline 线框）
        arcade.draw_rect_filled(arcade.XYWH(cx, cy, 336, 246), (235, 235, 235))
        arcade.draw_rect_filled(arcade.XYWH(cx, cy, 324, 234), (30, 35, 50))
        # 标题与数量/价格信息
        self._tc.text("sdlg_title", f"售卖: {name}", cx, cy + 88,
                      arcade.color.WHITE, 16, anchor_x="center")
        self._tc.text("sdlg_max", f"当前数量: {max_qty}　单价: {gold_per} 金币/个",
                      cx, cy + 62, arcade.color.LIGHT_GRAY, 13, anchor_x="center")
        # 合计预览（按当前输入框数值实时换算，仅展示）
        try:
            preview = max(1, min(int(self._sell_dialog_input), max_qty))
        except ValueError:
            preview = 1
        self._tc.text("sdlg_total", f"合计可得: {gold_per * preview} 金币",
                      cx, cy + 38, arcade.color.GOLD, 13, anchor_x="center")
        # "卖1个" / "全部(N)" 快捷按钮
        if self._sell_dialog_btn_1:
            arcade.draw_rect_filled(self._sell_dialog_btn_1, (50, 90, 60))
            self._tc.text("sdlg_btn_1", "卖1个", self._sell_dialog_btn_1.center_x,
                          self._sell_dialog_btn_1.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")
        if self._sell_dialog_btn_all:
            arcade.draw_rect_filled(self._sell_dialog_btn_all, (110, 90, 30))
            self._tc.text("sdlg_btn_all", f"全部({max_qty})", self._sell_dialog_btn_all.center_x,
                          self._sell_dialog_btn_all.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")
        # 自定义数量输入框（实心填充）
        self._tc.text("sdlg_input_label", "自定义数量:", cx, cy - 12,
                      arcade.color.LIGHT_GRAY, 12, anchor_x="center")
        arcade.draw_rect_filled(arcade.XYWH(cx, cy - 34, 80, 24), (50, 50, 60))
        self._tc.text("sdlg_input_val", self._sell_dialog_input, cx, cy - 34,
                      arcade.color.YELLOW, 14, anchor_x="center", anchor_y="center")
        # 确认 / 取消
        if self._sell_dialog_btn_confirm:
            arcade.draw_rect_filled(self._sell_dialog_btn_confirm, (50, 100, 50))
            self._tc.text("sdlg_confirm", "确认", self._sell_dialog_btn_confirm.center_x,
                          self._sell_dialog_btn_confirm.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")
        if self._sell_dialog_btn_cancel:
            arcade.draw_rect_filled(self._sell_dialog_btn_cancel, (80, 80, 80))
            self._tc.text("sdlg_cancel", "取消", self._sell_dialog_btn_cancel.center_x,
                          self._sell_dialog_btn_cancel.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")

    def _rebuild(self):
        self._tc.clear()  # 内容结构变化，清空文本缓存防止旧 key 残留
        self.sell_buttons = []
        self.equip_buttons = []
        self.weapon_sell_buttons = []
        self.equip_item_buttons = []
        self.equip_sell_buttons = []
        pid = self.window.game_state.player_id
        wh = get_warehouse(pid)
        # 阶段11：缓存仓库等级/容量（on_draw 只读缓存，不每帧查库——同 start_view 禁每帧查库约定）
        self._wh_level = get_warehouse_level(pid)
        self._wh_total_cap = get_warehouse_capacity(pid)
        self._wh_used_cap = warehouse_used_capacity(pid)
        y = WINDOW_HEIGHT - 160  # 内容区起点（下方为 Tab 栏，与 on_draw 一致）
        if self._tab == "资源":
            for item in wh:
                if item["item_type"] == "resource":
                    sp = RESOURCES.get(item["item_id"], {}).get("sell_price", 1)
                    btn = arcade.XYWH(WINDOW_WIDTH - 80, y, 80, 28)
                    self.sell_buttons.append((btn, item["id"], sp, item["item_id"]))
                y -= 36
        # 估算内容高度（按当前 Tab 分类，滚轮/滚动条共用一致范围）
        self.content_height = self._calc_content_height()

    def _calc_content_height(self) -> float:
        """动态计算实际内容高度（按当前 Tab 分类）"""
        pid = self.window.game_state.player_id
        wh = get_warehouse(pid)
        weapons = get_weapons(pid)
        equipment = get_equipment_inventory(pid)

        header_height = 160  # 顶部标题区域 + Tab 栏
        if self._tab == "资源":
            resource_count = len([i for i in wh if i["item_type"] == "resource"])
            total = header_height + 30 + max(resource_count, 1) * 30 + 36 * resource_count  # 标题 + 列表 + 售卖按钮
        elif self._tab == "武器":
            # 每条武器基础占 32px；带效果词条额外占 16px（效果行；神器特效行已移除，不再计入）
            extra = sum(16 for w in weapons if w["effects"])
            total = header_height + 50 + len(weapons) * 32 + extra + 100
        else:  # 装备
            # 每条装备基础占 32px；带效果词条额外占 16px（效果行；神器特效行已移除，不再计入）
            extra = 0
            for eq in equipment:
                if eq["effects"]:
                    extra += 16
            total = header_height + 50 + len(equipment) * 32 + extra + 100
        return max(total, WINDOW_HEIGHT)

    def get_bg_color(self):
        return (30, 25, 40)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """覆盖基类：使用动态 content_height 计算滚动限制"""
        self.scroll_offset -= scroll_y * 30
        max_scroll = max(0, self._calc_content_height() - WINDOW_HEIGHT + 80)
        self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))

    def on_draw(self):
        self.clear()
        gs = self.window.game_state
        pid = gs.player_id
        gold = get_gold(pid)
        wh = get_warehouse(pid)
        weapons = get_weapons(pid)
        offset = self.scroll_offset
        # 阶段10：市场设施售出回收加成（Lv0/Lv1 = 0.0 → 与旧口径完全一致）
        sell_bonus = market_sell_bonus(get_facility_level(pid, MARKET_FACILITY_ID))

        # 固定头部
        self._tc.text("header_title", "仓 库", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 50,
                      arcade.color.GOLD, 30, anchor_x="center")
        # 阶段11：头部同排显示「金币 + 仓库容量 已用/上限 + 等级」（占用口径与背包一致）
        self._tc.text("header_gold",
                      f"金币: {gold}    仓库容量: {self._wh_used_cap}/{self._wh_total_cap}    Lv.{self._wh_level}",
                      WINDOW_WIDTH // 2, WINDOW_HEIGHT - 85,
                      arcade.color.YELLOW, 18, anchor_x="center")
        self._tc.text("header_hint", "滚轮滚动或拖动滚动条查看", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 105,
                      arcade.color.GRAY, 11, anchor_x="center")

        # 顶部 Tab 栏（固定，不随内容滚动）
        for name, rect in self.tab_rects.items():
            active = (name == self._tab)
            bg = (80, 130, 80) if active else (50, 55, 65)
            arcade.draw_rect_filled(rect, bg)
            self._tc.text(f"tab_{name}", name, rect.center_x, rect.center_y,
                          arcade.color.WHITE if active else arcade.color.LIGHT_GRAY,
                          15, anchor_x="center", anchor_y="center")

        # 内容起点（带滚动偏移，位于 Tab 栏下方）
        y = WINDOW_HEIGHT - 160 + offset

        if self._tab == "资源":
            # ── 资源列表 ──
            self._tc.text("res_title", "资源:", 50, y, arcade.color.WHITE, 16)
            y -= 30
            for i, item in enumerate(wh):
                if item["item_type"] == "resource":
                    name = RESOURCES.get(item["item_id"], {}).get("name", item["item_id"])
                    self._tc.text(f"res_{i}", f"{name} x{item['quantity']}", 60, y,
                                  arcade.color.LIGHT_GRAY, 14)
                    y -= 30
            if not any(i["item_type"] == "resource" for i in wh):
                self._tc.text("res_empty", "(空)", 60, y, arcade.color.GRAY, 12)
                y -= 30

            # ── 资源售卖按钮 ──
            for i, (btn, wh_id, gold_per, item_id) in enumerate(self.sell_buttons):
                # 重新计算按钮屏幕位置（受滚动影响）
                screen_y = btn.center_y + offset
                draw_btn = arcade.XYWH(btn.center_x, screen_y, btn.width, btn.height)
                arcade.draw_rect_filled(draw_btn, arcade.color.DARK_GREEN)
                self._tc.text(f"sell_btn_{i}", "售卖", draw_btn.center_x, draw_btn.center_y,
                              arcade.color.WHITE, 11, anchor_x="center", anchor_y="center")

        elif self._tab == "武器":
            # ── 武器列表（神器武器保留"★ "前缀，特效描述行已移除）──
            from entities.weapon_defs import ALL_WEAPONS  # 延迟导入
            self.equip_buttons = []
            self.weapon_sell_buttons = []
            equipped_id = gs.equipped_weapon_id
            for i, w in enumerate(weapons):
                kind_label = "近战" if w["kind"] == "melee" else "远程"
                is_equipped = (w["id"] == equipped_id)
                label_color = arcade.color.GOLD if is_equipped else arcade.color.CORNFLOWER_BLUE
                prefix = ">> " if is_equipped else "   "
                is_artifact = ALL_WEAPONS.get(w["item_id"], {}).get("artifact")
                name_prefix = "★ " if is_artifact else ""
                self._tc.text(
                    f"wep_{i}",
                    f"{prefix}{name_prefix}[{kind_label}] {w['name']}  伤害:{w['damage']:.0f}  距离:{self._get_range(w)}  Lv.{w['level']}",
                    60, y, label_color, 13,
                )
                # 装备按钮
                eq_btn = arcade.XYWH(WINDOW_WIDTH - 160, y + 6, 70, 26)
                self.equip_buttons.append((eq_btn, w))
                eq_color = arcade.color.DARK_GOLDENROD if is_equipped else arcade.color.DARK_SLATE_GRAY
                arcade.draw_rect_filled(eq_btn, eq_color)
                self._tc.text(f"wep_equip_{i}", "已装备" if is_equipped else "装备",
                              eq_btn.center_x, eq_btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
                # 售卖按钮（显示价 = 实际入账金币，同经 db 纯函数计算）
                sell_price = weapon_sell_price(w["damage"], sell_bonus)
                sv_btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 6, 70, 26)
                self.weapon_sell_buttons.append((sv_btn, w, sell_price))
                arcade.draw_rect_filled(sv_btn, (100, 30, 30))
                self._tc.text(f"wep_sell_{i}", f"卖{sell_price}金币", sv_btn.center_x, sv_btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
                y -= 32
                # 特殊属性（effects）行：武器带效果词条时在名称下方显示（如"剧毒+2、燃烧+4"）
                if w["effects"]:
                    self._tc.text(f"wep_eff_{i}", f"    效果: {self._effects_value_desc(w['effects'])}", 70, y,
                                  arcade.color.LIGHT_GRAY, 11)
                    y -= 16
                # 神器武器特效描述行已移除（保留"★ "前缀标识神器）

            if not weapons:
                self._tc.text("wep_empty", "(无武器，击杀怪物获取)", 60, y, arcade.color.GRAY, 12)

        elif self._tab == "装备":
            # ── 装备列表（神器装备保留"★ "前缀，特效描述行已移除）──
            from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS  # 延迟导入
            self.equip_item_buttons = []
            self.equip_sell_buttons = []
            equipment = get_equipment_inventory(pid)
            for i, eq in enumerate(equipment):
                slot_label = {"helmet": "头盔", "armor": "护甲", "backpack": "背包"}.get(eq["slot"], eq["slot"])
                is_eq = "★" if eq["is_equipped"] else "  "
                label_color = arcade.color.GOLD if eq["is_equipped"] else arcade.color.LIGHT_GRAY
                # 查找装备定义以判断是否为神器
                edef = HELMETS.get(eq["item_id"]) or ARMORS.get(eq["item_id"]) or BACKPACKS.get(eq["item_id"])
                is_artifact = edef.get("artifact") if edef else False
                name_prefix = "★ " if is_artifact else ""
                self._tc.text(
                    f"eq_{i}",
                    f"{is_eq} {name_prefix}[{slot_label}] {eq['name']}  防+{eq['defense']}  Lv.{eq['level']}",
                    60, y, label_color, 13,
                )
                # 装备/卸下按钮
                eq_btn = arcade.XYWH(WINDOW_WIDTH - 160, y + 6, 70, 26)
                self.equip_item_buttons.append((eq_btn, eq))
                eq_color = arcade.color.DARK_GOLDENROD if eq["is_equipped"] else arcade.color.DARK_SLATE_GRAY
                arcade.draw_rect_filled(eq_btn, eq_color)
                self._tc.text(f"eq_equip_{i}", "已装备" if eq["is_equipped"] else "装备",
                              eq_btn.center_x, eq_btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
                # 售卖按钮（显示价 = 实际入账金币，同经 db 纯函数计算）
                base = eq["capacity"] if eq["slot"] == "backpack" else eq["defense"]
                sell_price = equipment_sell_price(base, sell_bonus)
                sv_btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 6, 70, 26)
                self.equip_sell_buttons.append((sv_btn, eq, sell_price))
                arcade.draw_rect_filled(sv_btn, (100, 30, 30))
                self._tc.text(f"eq_sell_{i}", f"卖{sell_price}金币", sv_btn.center_x, sv_btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
                y -= 32
                # 特殊属性（effects）行：装备带效果词条时在名称下方显示（如"吸血+3%、致命+5%"）
                if eq["effects"]:
                    self._tc.text(f"eq_eff_{i}", f"    效果: {self._effects_value_desc(eq['effects'])}", 70, y,
                                  arcade.color.LIGHT_GRAY, 11)
                    y -= 16
                # 神器装备特效描述行已移除（保留"★ "前缀标识神器）

            if not equipment:
                self._tc.text("eq_empty", "(无装备)", 60, y, arcade.color.GRAY, 12)
                y -= 32

        # 滚动条（基类统一绘制 + 支持鼠标拖拽）
        self.draw_scrollbar(WINDOW_HEIGHT - 160)

        # ── 阶段11 仓库升级区（固定位置，不随滚动）──
        self._draw_upgrade_panel()

        # 阶段11 飘字提示（实心填充）
        self._draw_toasts()

        # ── 导航按钮（固定位置，不随滚动）──
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("nav_back", "返回大厅", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(self.market_rect, arcade.color.DARK_BLUE)
        self._tc.text("nav_market", "市场", self.market_rect.center_x, self.market_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

        # 售卖数量对话框（最后绘制：覆盖层压住全部内容）
        if self._sell_dialog_active and self._sell_dialog_item:
            self._draw_sell_dialog()

    def on_key_press(self, key, modifiers):
        """键盘输入（仅售卖对话框激活时处理：数字/退格/回车确认/ESC取消）

        对话框未激活时不接管任何按键（仓库页此前无键位绑定，保持原状）。
        """
        if not self._sell_dialog_active:
            return
        max_qty = self._sell_dialog_item[3] if self._sell_dialog_item else 1
        if key == arcade.key.BACKSPACE:
            self._sell_dialog_input = self._sell_dialog_input[:-1]
            if not self._sell_dialog_input:
                self._sell_dialog_input = "1"
        elif key == arcade.key.RETURN or key == arcade.key.NUM_ENTER:
            try:
                qty = max(1, min(int(self._sell_dialog_input), max_qty))
            except ValueError:
                qty = 1
            self._sell_dialog_qty = qty
            self._execute_sell()
        elif key == arcade.key.ESCAPE:
            self._sell_dialog_active = False
            self._sell_dialog_item = None
        elif arcade.key.NUM_0 <= key <= arcade.key.NUM_9 or arcade.key.KEY_0 <= key <= arcade.key.KEY_9:
            if key >= arcade.key.NUM_0 and key <= arcade.key.NUM_9:
                digit = str(key - arcade.key.NUM_0)
            else:
                digit = str(key - arcade.key.KEY_0)
            # 首位默认 "1" 被新输入替换，其余位追加（与背包丢弃弹窗同规则）
            if self._sell_dialog_input == "1" and len(self._sell_dialog_input) == 1:
                self._sell_dialog_input = digit
            else:
                if len(self._sell_dialog_input) < 4:
                    self._sell_dialog_input += digit

    def _draw_upgrade_panel(self):
        """阶段11 仓库升级区：下一级容量预告 + 费用明细 + 实心升级按钮

        渲染铁律：一律不透明实心填充（禁 outline/线框）。满级时按钮置灰不可点。
        数值全部实查 config.WAREHOUSE_*（经 db 纯函数），禁在视图里硬编码容量/费用。
        """
        from config import WAREHOUSE_BASE_CAPACITY, WAREHOUSE_CAPACITY_STEP, WAREHOUSE_MAX_LEVEL
        lv = self._wh_level
        if lv >= WAREHOUSE_MAX_LEVEL:
            # 满级：画置灰按钮占位（与未满级同位置，避免面板忽有忽无）+ 达标文案
            arcade.draw_rect_filled(self.upgrade_rect, (55, 50, 45))
            self._tc.text("up_btn", f"仓库已满级 Lv.{lv}",
                          self.upgrade_rect.center_x, self.upgrade_rect.center_y,
                          arcade.color.GRAY, 15, anchor_x="center", anchor_y="center", bold=True)
            self._tc.text("up_next", f"容量上限 {self._wh_total_cap} 格",
                          WINDOW_WIDTH // 2, 78, arcade.color.GRAY, 12, anchor_x="center")
            self._tc.text("up_cost", "", WINDOW_WIDTH // 2, 40, arcade.color.GRAY, 12,
                          anchor_x="center")
            return
        next_lv = lv + 1
        next_cap = self._wh_total_cap + WAREHOUSE_CAPACITY_STEP
        # 容量预告：当前 → 下一级（增量取 config.WAREHOUSE_CAPACITY_STEP，不写死）
        self._tc.text("up_next",
                      f"下一级 Lv.{next_lv}：容量 {self._wh_total_cap} → {next_cap}"
                      f"（+{WAREHOUSE_CAPACITY_STEP}，初始 {WAREHOUSE_BASE_CAPACITY}）",
                      WINDOW_WIDTH // 2, 78, arcade.color.LIGHT_GRAY, 12, anchor_x="center")
        # 费用明细（金币 + 仓库材料，顺序按费用表）
        cost = warehouse_upgrade_cost_at(next_lv)
        parts = []
        for key, need in cost.items():
            if key == "gold":
                parts.append(f"金币{int(need)}")
            else:
                parts.append(f"{RESOURCES.get(key, {}).get('name', key)}×{int(need)}")
        self._tc.text("up_cost", "费用: " + "、".join(parts),
                      WINDOW_WIDTH // 2, 62, arcade.color.GOLD, 12, anchor_x="center")
        # 实心升级按钮（禁空心/线框）
        btn_color = (110, 90, 40) if self.upgrade_hover else (70, 60, 30)
        arcade.draw_rect_filled(self.upgrade_rect, btn_color)
        self._tc.text("up_btn", f"升级仓库 Lv.{lv} → {next_lv}",
                      self.upgrade_rect.center_x, self.upgrade_rect.center_y,
                      arcade.color.WHITE, 15, anchor_x="center", anchor_y="center", bold=True)

    def on_mouse_motion(self, x, y, dx, dy):
        """更新升级按钮悬停态；基类滚动条拖拽逻辑照常保留"""
        self.upgrade_hover = self.upgrade_rect.point_in_rect((x, y))
        super().on_mouse_motion(x, y, dx, dy)

    def _get_range(self, w):
        from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
        table = MELEE_WEAPONS if w["kind"] == "melee" else RANGED_WEAPONS
        for wdef in table.values():
            if wdef["name"] == w["name"]:
                return wdef.get("range", 50)
        return 50

    @staticmethod
    def _effects_value_desc(effects) -> str:
        """效果词条 -> 带数值的中文描述（如"吸血+3%、致命+5%"），空返回"无"

        比例类效果（value<1，如吸血/暴击率）显示为百分比，数值类效果（如中毒/防御）直接显示数值。
        """
        from entities.effects_defs import parse_effect_item, effect_params  # 延迟导入
        parts = []
        for e in effects:
            eid, lvl = parse_effect_item(e)
            params = effect_params(eid, lvl)
            name = params.get("name", eid)
            value = params.get("value", 0)
            if isinstance(value, (int, float)) and value > 0:
                if value < 1:
                    parts.append(f"{name}+{int(round(value * 100))}%")
                else:
                    parts.append(f"{name}+{int(value)}")
            else:
                parts.append(name)
        return "、".join(parts) if parts else "无"

    def on_mouse_press(self, x, y, button, modifiers):
        sound_manager.play_ui()
        gs = self.window.game_state
        pid = gs.player_id
        offset = self.scroll_offset

        # 售卖对话框激活时，优先处理对话框按钮（同时拦截 Tab/滚动条/装备/升级/导航全部点击）
        if self._sell_dialog_active:
            self._handle_sell_dialog_click(x, y)
            return

        # 顶部 Tab 栏切换分类
        for name, rect in self.tab_rects.items():
            if rect.point_in_rect((x, y)):
                if name != self._tab:
                    self._tab = name
                    self.scroll_offset = 0.0  # 切换分类时归零滚动
                    self._rebuild()
                return

        # 右侧滚动条拖拽（轨道顶部与 on_draw 内容区一致）
        if self.start_scroll_drag(x, y, WINDOW_HEIGHT - 160):
            return

        # 装备武器（btn.center_y 已在 on_draw 时包含 offset，直接使用）
        for btn, w in self.equip_buttons:
            if btn.point_in_rect((x, y)):
                if gs.equipped_weapon_id == w["id"]:
                    gs.equipped_weapon_id = None
                else:
                    gs.equipped_weapon_id = w["id"]
                return

        # 售卖武器（价格含市场设施回收加成，与本页显示价同口径）
        sell_bonus = market_sell_bonus(get_facility_level(pid, MARKET_FACILITY_ID))
        for btn, w, sell_price in self.weapon_sell_buttons:
            if btn.point_in_rect((x, y)):
                sell_weapon(pid, w["id"], sell_bonus)
                if gs.equipped_weapon_id == w["id"]:
                    gs.equipped_weapon_id = None
                self._rebuild()
                return

        # 售卖资源 → 弹数量选择对话框（2026-10-04：不再直接整栈售出）
        for btn, wh_id, gold_per, item_id in self.sell_buttons:
            # 资源售卖按钮在 on_draw 中重算为 center_y + offset，这里同步
            screen_btn = arcade.XYWH(btn.center_x, btn.center_y + offset, btn.width, btn.height)
            if screen_btn.point_in_rect((x, y)):
                self._show_sell_dialog(wh_id, gold_per)
                return

        # 装备物品（头盔/护甲/背包）
        for btn, eq in self.equip_item_buttons:
            if btn.point_in_rect((x, y)):
                if eq["is_equipped"]:
                    from db.database import unequip_slot
                    unequip_slot(pid, eq["slot"])
                    # 修复：卸下装备时同步清空 GameState 对应槽位，避免残留
                    # equipped_*_id 导致进入游戏后背包栏显示已装备但属性未生效
                    if eq["slot"] == "helmet":
                        gs.equipped_helmet_id = None
                    elif eq["slot"] == "armor":
                        gs.equipped_armor_id = None
                    elif eq["slot"] == "backpack":
                        gs.equipped_backpack_id = None
                else:
                    equip_from_inventory(pid, eq["id"])
                    # 修复：装备时同步 GameState 对应槽位（与卸下对称），
                    # 使游戏内背包栏/开局加载与仓库操作保持一致
                    if eq["slot"] == "helmet":
                        gs.equipped_helmet_id = eq["item_id"]
                    elif eq["slot"] == "armor":
                        gs.equipped_armor_id = eq["item_id"]
                    elif eq["slot"] == "backpack":
                        gs.equipped_backpack_id = eq["item_id"]
                self._rebuild()
                return

        # 售卖装备（价格含市场设施回收加成，与本页显示价同口径）
        for btn, eq, sell_price in self.equip_sell_buttons:
            if btn.point_in_rect((x, y)):
                sell_equipment(pid, eq["id"], sell_bonus)
                self._rebuild()
                return

        # 阶段11：仓库升级（先校验后扣，失败不花钱——口径同 db.upgrade_warehouse）
        if self.upgrade_rect.point_in_rect((x, y)):
            ok, desc = warehouse_can_afford(pid)
            if not ok:
                self._toast(desc, (255, 120, 120))
                return
            if upgrade_warehouse(pid):
                self._rebuild()
                self._toast(f"仓库已升级到 Lv.{self._wh_level}，容量上限 {self._wh_total_cap} 格",
                           (140, 230, 140))
            else:
                self._toast("升级失败：材料或金币不足", (255, 120, 120))
            return

        # 返回大厅 → StartView（联机房间内返回 LobbyView 复用连接，保持房间）
        if self.back_rect.point_in_rect((x, y)):
            gs = self.window.game_state
            if getattr(gs, "net_mode", "solo") != "solo":
                from views.lobby_view import LobbyView
                self.window.show_view(LobbyView(self.window_ref))
            else:
                from views.start_view import StartView
                self.window.show_view(StartView(self.window_ref))
            return

        # 市场（阶段10：过 _enter_facility 守卫——未建造先进设施页）
        if self.market_rect.point_in_rect((x, y)):
            from views.market_view import MarketView
            from views.start_view import _enter_facility
            _enter_facility(self, "market", MarketView)
            return
