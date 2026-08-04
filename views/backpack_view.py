"""背包页面：查看/丢弃本次冒险携带的物品（支持滚轮滚动）

显示 run_carried 中的所有物品：
- 金币（不可丢弃）
- 资源（可丢弃）
- 武器（可丢弃）
- 头盔（可丢弃）
- 护甲（可丢弃）
- 背包（可丢弃）
"""

import math
import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from entities.resource_defs import RESOURCES
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
from views.scroll_view import ScrollView


class BackpackView(ScrollView):
    """背包界面：查看和丢弃本次冒险携带的物品"""

    def __init__(self, window, game_view=None):
        super().__init__(window)
        self.game_view = game_view  # 保存当前 GameView 引用，返回时不用重建
        self.discard_buttons = []  # [(rect, item_type, item_id, level, label)]
        self.back_rect = arcade.XYWH(WINDOW_WIDTH // 2, 40, 120, 36)
        self._build_content()

    def _build_content(self):
        """构建内容并计算高度"""
        self.discard_buttons = []
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})

        # 计算内容高度
        y_start = WINDOW_HEIGHT - 120
        y = y_start

        # 金币区域
        y -= 30

        # 资源区域
        resources = carried.get("resource", {})
        if resources:
            y -= 30 * len(resources)
        else:
            y -= 30
        y -= 20  # 间距

        # 武器区域
        weapons = carried.get("weapon", {})
        if weapons:
            y -= 32 * len(weapons)
        else:
            y -= 30
        y -= 20

        # 头盔区域
        helmets = carried.get("helmet", {})
        if helmets:
            y -= 32 * len(helmets)
        else:
            y -= 30
        y -= 20

        # 护甲区域
        armors = carried.get("armor", {})
        if armors:
            y -= 32 * len(armors)
        else:
            y -= 30
        y -= 20

        # 背包区域
        backpacks = carried.get("backpack", {})
        if backpacks:
            y -= 32 * len(backpacks)
        else:
            y -= 30

        self.content_height = max(y_start - y + 120, WINDOW_HEIGHT)

    def get_bg_color(self):
        return (25, 30, 40)

    def on_mouse_press(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            # 检查丢弃按钮（on_draw 中已将 scroll_offset 算入坐标，直接检测即可）
            for rect, item_type, item_id, level, label in self.discard_buttons:
                if rect.point_in_rect((x, y)):
                    self._discard_item(item_type, item_id, level)
                    return

            # 检查返回按钮（固定在底部，不受滚动影响）
            if self.back_rect.point_in_rect((x, y)):
                if self.game_view:
                    self.window.show_view(self.game_view)
                else:
                    from views.game_view import GameView
                    self.window.show_view(GameView(self.window_ref))
                return

    def _discard_item(self, item_type: str, item_id: str, level: int = 1):
        """丢弃指定物品：从 run_carried 移除，并在玩家周围生成地面掉落物"""
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})
        if item_type == "gold":
            return  # 金币不可丢弃

        slot = carried.get(item_type, {})
        # 武器装备/背包以 (item_id, level) 为键；资源仍以 id 为键
        key = (item_id, level) if item_type in ("weapon", "helmet", "armor", "backpack") else item_id
        if key not in slot:
            return

        # 先记录要丢弃的数量（丢弃1个）
        qty_to_drop = 1

        # 从 run_carried 中移除
        if slot[key] > 1:
            slot[key] -= 1
        else:
            del slot[key]

        # 在玩家周围生成地面掉落物（避免立即被拾取）
        if self.game_view and hasattr(self.game_view, 'player') and self.game_view.player:
            import random as _rand
            from game.loot import DropItem
            player = self.game_view.player
            # 在玩家周围 50~90 像素随机位置生成掉落物
            angle = _rand.uniform(0, 6.2832)  # 2π
            dist = _rand.uniform(50, 90)
            drop_x = player.center_x + dist * math.cos(angle)
            drop_y = player.center_y + dist * math.sin(angle)
            drop = DropItem(drop_x, drop_y, item_type, item_id, qty_to_drop, level=level)
            self.game_view.drops.append(drop)

        # 重新构建内容
        self._build_content()

    def on_draw(self):
        self.clear()
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})
        offset = self.scroll_offset

        # 固定头部
        arcade.draw_text("背 包", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 50,
                         arcade.color.WHITE, 30, anchor_x="center")

        # 显示容量信息
        from game.loot import _calc_carried_capacity
        used_cap = _calc_carried_capacity(carried)
        total_cap = getattr(gs, 'backpack_capacity', 0)
        arcade.draw_text(f"容量: {used_cap}/{total_cap}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 85,
                         arcade.color.LIGHT_GRAY, 16, anchor_x="center")
        arcade.draw_text("滚轮滚动查看全部物品", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 105,
                         arcade.color.GRAY, 11, anchor_x="center")

        # 内容起点（带滚动偏移）
        y = WINDOW_HEIGHT - 120 + offset
        self.discard_buttons = []

        # ── 金币 ──
        gold = carried.get("gold", 0)
        arcade.draw_text(f"金币: {gold}", 50, y, arcade.color.GOLD, 16)
        y -= 40

        # ── 资源列表 ──
        arcade.draw_text("资源:", 50, y, arcade.color.WHITE, 16)
        y -= 30
        resources = carried.get("resource", {})
        if resources:
            for item_id, qty in resources.items():
                name = RESOURCES.get(item_id, {}).get("name", item_id)
                arcade.draw_text(f"{name} x{qty}", 60, y, arcade.color.LIGHT_GRAY, 14)
                # 丢弃按钮
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                # 资源无等级概念，level 占位传 1（_discard_item 中对资源类型会忽略 level）
                self.discard_buttons.append((btn, "resource", item_id, 1, "丢弃"))
                y -= 30
        else:
            arcade.draw_text("(空)", 60, y, arcade.color.GRAY, 12)
            y -= 30
        y -= 20

        # ── 武器列表 ──
        arcade.draw_text("武器:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        weapons = carried.get("weapon", {})
        all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
        if weapons:
            for (item_id, level), qty in weapons.items():
                wdef = all_weapons.get(item_id, {})
                name = wdef.get("name", item_id)
                damage = wdef.get("damage", 0)
                kind = "近战" if wdef.get("kind") == "melee" else "远程"
                arcade.draw_text(f"{name} Lv.{level} ({kind} {damage}伤害) x{qty}", 60, y,
                                 arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "weapon", item_id, level, "丢弃"))
                y -= 32
        else:
            arcade.draw_text("(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32
        y -= 20

        # ── 头盔列表 ──
        arcade.draw_text("头盔:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        helmets = carried.get("helmet", {})
        if helmets:
            for (item_id, level), qty in helmets.items():
                hdef = HELMETS.get(item_id, {})
                name = hdef.get("name", item_id)
                defense = hdef.get("defense", 0)
                arcade.draw_text(f"{name} Lv.{level} (防御+{defense}) x{qty}", 60, y,
                                 arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "helmet", item_id, level, "丢弃"))
                y -= 32
        else:
            arcade.draw_text("(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32
        y -= 20

        # ── 护甲列表 ──
        arcade.draw_text("护甲:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        armors = carried.get("armor", {})
        if armors:
            for (item_id, level), qty in armors.items():
                adef = ARMORS.get(item_id, {})
                name = adef.get("name", item_id)
                defense = adef.get("defense", 0)
                arcade.draw_text(f"{name} Lv.{level} (防御+{defense}) x{qty}", 60, y,
                                 arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "armor", item_id, level, "丢弃"))
                y -= 32
        else:
            arcade.draw_text("(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32
        y -= 20

        # ── 背包列表 ──
        arcade.draw_text("背包:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        backpacks = carried.get("backpack", {})
        if backpacks:
            for (item_id, level), qty in backpacks.items():
                bdef = BACKPACKS.get(item_id, {})
                name = bdef.get("name", item_id)
                capacity = bdef.get("capacity", 0)
                arcade.draw_text(f"{name} Lv.{level} (容量+{capacity}) x{qty}", 60, y,
                                 arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "backpack", item_id, level, "丢弃"))
                y -= 32
        else:
            arcade.draw_text("(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32

        # 绘制丢弃按钮（rect 坐标已含 scroll_offset，直接使用即可）
        for rect, item_type, item_id, level, label in self.discard_buttons:
            draw_rect = arcade.XYWH(rect.center_x, rect.center_y, rect.width, rect.height)
            arcade.draw_rect_filled(draw_rect, arcade.color.DARK_RED)
            arcade.draw_text(label, draw_rect.center_x, draw_rect.center_y,
                             arcade.color.WHITE, 11, anchor_x="center", anchor_y="center")

        # 返回按钮
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_BLUE)
        arcade.draw_text("返回游戏", self.back_rect.center_x, self.back_rect.center_y,
                         arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")
