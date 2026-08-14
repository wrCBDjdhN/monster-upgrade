"""仓库页面：查看/售卖资源，装备/售卖武器，管理装备（支持滚轮滚动）"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from entities.resource_defs import RESOURCES
from db.database import (
    get_warehouse, get_weapons, get_gold, sell_warehouse_item, sell_weapon,
    get_equipment_inventory, equip_from_inventory, sell_equipment,
)
from views.scroll_view import ScrollView
from views.text_cache import TextCache


class WarehouseView(ScrollView):
    def __init__(self, window):
        super().__init__(window)
        self._tc = TextCache()  # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        self.sell_buttons = []      # [(rect, wh_id, gold_per, item_id)]
        self.equip_buttons = []     # [(rect, weapon_dict)]
        self.weapon_sell_buttons = []  # [(rect, weapon_dict, sell_price)]
        self.equip_item_buttons = []   # [(rect, equip_dict)]
        self.equip_sell_buttons = []   # [(rect, equip_dict, sell_price)]
        self.market_rect = arcade.XYWH(WINDOW_WIDTH - 100, 40, 120, 36)
        self._rebuild()

    def _rebuild(self):
        self._tc.clear()  # 内容结构变化，清空文本缓存防止旧 key 残留
        self.sell_buttons = []
        self.equip_buttons = []
        self.weapon_sell_buttons = []
        self.equip_item_buttons = []
        self.equip_sell_buttons = []
        pid = self.window.game_state.player_id
        wh = get_warehouse(pid)
        y = WINDOW_HEIGHT - 120
        for item in wh:
            if item["item_type"] == "resource":
                sp = RESOURCES.get(item["item_id"], {}).get("sell_price", 1)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y, 80, 28)
                self.sell_buttons.append((btn, item["id"], sp, item["item_id"]))
            y -= 36
        # 估算内容高度（用于初始滚动限制）
        weapons = get_weapons(pid)
        equipment = get_equipment_inventory(pid)
        est = WINDOW_HEIGHT - 120  # start
        est += 36 * len([i for i in wh if i["item_type"] == "resource"])
        est += 50 + 32 * len(weapons) + 50 + 32 * len(equipment) + 100
        self.content_height = max(est, WINDOW_HEIGHT)

    def _calc_content_height(self) -> float:
        """动态计算实际内容高度"""
        pid = self.window.game_state.player_id
        wh = get_warehouse(pid)
        weapons = get_weapons(pid)
        equipment = get_equipment_inventory(pid)
        
        # 计算各部分高度
        header_height = 120  # 顶部标题区域
        resource_count = len([i for i in wh if i["item_type"] == "resource"])
        resource_height = 30 + max(resource_count, 1) * 30 + 36 * resource_count  # 标题 + 列表 + 售卖按钮
        weapon_height = 50 + max(len(weapons), 1) * 32 + 20  # 标题 + 列表
        equipment_height = 50 + max(len(equipment), 1) * 32 + 100  # 标题 + 列表 + 底部空间
        
        total = header_height + resource_height + weapon_height + equipment_height
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

        # 固定头部
        self._tc.text("header_title", "仓 库", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 50,
                      arcade.color.GOLD, 30, anchor_x="center")
        self._tc.text("header_gold", f"金币: {gold}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 85,
                      arcade.color.YELLOW, 18, anchor_x="center")
        self._tc.text("header_hint", "滚轮滚动查看全部物品", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 105,
                      arcade.color.GRAY, 11, anchor_x="center")

        # 内容起点（带滚动偏移）
        y = WINDOW_HEIGHT - 120 + offset

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

        # ── 武器列表 ──
        self._tc.text("wep_title", "武器:", 50, y - 20, arcade.color.WHITE, 16)
        y -= 50
        self.equip_buttons = []
        self.weapon_sell_buttons = []
        equipped_id = gs.equipped_weapon_id
        for i, w in enumerate(weapons):
            kind_label = "近战" if w["kind"] == "melee" else "远程"
            is_equipped = (w["id"] == equipped_id)
            label_color = arcade.color.GOLD if is_equipped else arcade.color.CORNFLOWER_BLUE
            prefix = ">> " if is_equipped else "   "
            self._tc.text(
                f"wep_{i}",
                f"{prefix}[{kind_label}] {w['name']}  伤害:{w['damage']:.0f}  距离:{self._get_range(w)}  Lv.{w['level']}",
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
            # 售卖按钮
            sell_price = int(w["damage"]) * 2
            sv_btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 6, 70, 26)
            self.weapon_sell_buttons.append((sv_btn, w, sell_price))
            arcade.draw_rect_filled(sv_btn, (100, 30, 30))
            self._tc.text(f"wep_sell_{i}", f"卖{sell_price}G", sv_btn.center_x, sv_btn.center_y,
                          arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
            y -= 32

        if not weapons:
            self._tc.text("wep_empty", "(无武器，击杀怪物获取)", 60, y, arcade.color.GRAY, 12)

        # ── 装备列表 ──
        self._tc.text("eq_title", "装备:", 50, y - 20, arcade.color.WHITE, 16)
        y -= 50
        self.equip_item_buttons = []
        self.equip_sell_buttons = []
        equipment = get_equipment_inventory(pid)
        for i, eq in enumerate(equipment):
            slot_label = {"helmet": "头盔", "armor": "护甲", "backpack": "背包"}.get(eq["slot"], eq["slot"])
            is_eq = "★" if eq["is_equipped"] else "  "
            label_color = arcade.color.GOLD if eq["is_equipped"] else arcade.color.LIGHT_GRAY
            self._tc.text(
                f"eq_{i}",
                f"{is_eq} [{slot_label}] {eq['name']}  防+{eq['defense']}  Lv.{eq['level']}",
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
            # 售卖按钮
            # 修复：装备售卖价改为与武器相同的方式（防御/容量 × 2），避免显示价远高于实际入账金币
            base = eq["capacity"] if eq["slot"] == "backpack" else eq["defense"]
            sell_price = int(base) * 2
            sv_btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 6, 70, 26)
            self.equip_sell_buttons.append((sv_btn, eq, sell_price))
            arcade.draw_rect_filled(sv_btn, (100, 30, 30))
            self._tc.text(f"eq_sell_{i}", f"卖{sell_price}G", sv_btn.center_x, sv_btn.center_y,
                          arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
            y -= 32

        if not equipment:
            self._tc.text("eq_empty", "(无装备)", 60, y, arcade.color.GRAY, 12)
            y -= 32

        # ── 导航按钮（固定位置，不随滚动）──
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("nav_back", "返回大厅", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(self.market_rect, arcade.color.DARK_BLUE)
        self._tc.text("nav_market", "市场", self.market_rect.center_x, self.market_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

    def _get_range(self, w):
        from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
        table = MELEE_WEAPONS if w["kind"] == "melee" else RANGED_WEAPONS
        for wdef in table.values():
            if wdef["name"] == w["name"]:
                return wdef.get("range", 50)
        return 50

    def on_mouse_press(self, x, y, button, modifiers):
        gs = self.window.game_state
        pid = gs.player_id
        offset = self.scroll_offset

        # 装备武器（btn.center_y 已在 on_draw 时包含 offset，直接使用）
        for btn, w in self.equip_buttons:
            if btn.point_in_rect((x, y)):
                if gs.equipped_weapon_id == w["id"]:
                    gs.equipped_weapon_id = None
                else:
                    gs.equipped_weapon_id = w["id"]
                return

        # 售卖武器
        for btn, w, sell_price in self.weapon_sell_buttons:
            if btn.point_in_rect((x, y)):
                sell_weapon(pid, w["id"])
                if gs.equipped_weapon_id == w["id"]:
                    gs.equipped_weapon_id = None
                self._rebuild()
                return

        # 售卖资源
        for btn, wh_id, gold_per, item_id in self.sell_buttons:
            # 资源售卖按钮在 on_draw 中重算为 center_y + offset，这里同步
            screen_btn = arcade.XYWH(btn.center_x, btn.center_y + offset, btn.width, btn.height)
            if screen_btn.point_in_rect((x, y)):
                sell_warehouse_item(pid, wh_id)
                self._rebuild()
                return

        # 装备物品（头盔/护甲/背包）
        for btn, eq in self.equip_item_buttons:
            if btn.point_in_rect((x, y)):
                if eq["is_equipped"]:
                    from db.database import unequip_slot
                    unequip_slot(pid, eq["slot"])
                else:
                    equip_from_inventory(pid, eq["id"])
                self._rebuild()
                return

        # 售卖装备（头盔/护甲/背包）
        for btn, eq, sell_price in self.equip_sell_buttons:
            if btn.point_in_rect((x, y)):
                sell_equipment(pid, eq["id"])
                self._rebuild()
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

        # 市场
        if self.market_rect.point_in_rect((x, y)):
            from views.market_view import MarketView
            self.window.show_view(MarketView(self.window_ref))
            return
