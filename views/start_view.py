"""开始游戏页面"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT


class StartView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.title = "打怪升级"
        self.btn_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 60, 220, 50)
        self.wh_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 130, 220, 50)
        self.market_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 200, 220, 50)
        self.forge_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 270, 220, 50)  # 锻造坊入口
        self.btn_hover = False
        self.wh_hover = False
        self.market_hover = False
        self.forge_hover = False

    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY

    def on_draw(self):
        self.clear()
        gs = self.window.game_state

        # 标题
        arcade.draw_text(
            self.title,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 60,
            arcade.color.GOLD, font_size=48, anchor_x="center", bold=True,
        )
        # 副标题
        arcade.draw_text(
            "2D Action RPG",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 10,
            arcade.color.LIGHT_GRAY, font_size=18, anchor_x="center",
        )

        # 当前装备显示
        equipped_text = "无"
        if gs.player_id and gs.equipped_weapon_id:
            from db.database import get_weapons
            weapons = get_weapons(gs.player_id)
            for w in weapons:
                if w["id"] == gs.equipped_weapon_id:
                    kind_label = "近战" if w["kind"] == "melee" else "远程"
                    equipped_text = f"[{kind_label}] {w['name']} (伤害:{w['damage']:.0f})"
                    break
        arcade.draw_text(f"携带武器: {equipped_text}",
                         WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 25,
                         arcade.color.CORNFLOWER_BLUE, 14, anchor_x="center")

        # 开始游戏按钮
        color = arcade.color.CORNFLOWER_BLUE if self.btn_hover else arcade.color.STEEL_BLUE
        arcade.draw_rect_filled(self.btn_rect, color)
        arcade.draw_rect_outline(self.btn_rect, arcade.color.WHITE, border_width=2)
        arcade.draw_text(
            "开 始 游 戏",
            self.btn_rect.center_x, self.btn_rect.center_y,
            arcade.color.WHITE, font_size=22, anchor_x="center", anchor_y="center",
        )

        # 仓库按钮
        wh_color = arcade.color.DARK_ORANGE if self.wh_hover else (100, 70, 30)
        arcade.draw_rect_filled(self.wh_rect, wh_color)
        arcade.draw_rect_outline(self.wh_rect, arcade.color.WHITE, border_width=2)
        arcade.draw_text(
            "仓 库",
            self.wh_rect.center_x, self.wh_rect.center_y,
            arcade.color.WHITE, font_size=22, anchor_x="center", anchor_y="center",
        )

        # 市场按钮
        market_color = arcade.color.PURPLE if self.market_hover else (80, 40, 100)
        arcade.draw_rect_filled(self.market_rect, market_color)
        arcade.draw_rect_outline(self.market_rect, arcade.color.WHITE, border_width=2)
        arcade.draw_text(
            "市 场",
            self.market_rect.center_x, self.market_rect.center_y,
            arcade.color.WHITE, font_size=22, anchor_x="center", anchor_y="center",
        )

        # 锻造坊按钮（消耗Lv.5+材料合成高级装备/神器）
        forge_color = (160, 60, 40) if self.forge_hover else (110, 40, 30)
        arcade.draw_rect_filled(self.forge_rect, forge_color)
        arcade.draw_rect_outline(self.forge_rect, arcade.color.WHITE, border_width=2)
        arcade.draw_text(
            "锻 造 坊",
            self.forge_rect.center_x, self.forge_rect.center_y,
            arcade.color.WHITE, font_size=22, anchor_x="center", anchor_y="center",
        )

        # 底部提示
        arcade.draw_text(
            "WASD移动 | 鼠标瞄准攻击 | 击杀怪物获取资源",
            WINDOW_WIDTH // 2, 40,
            arcade.color.GRAY, font_size=12, anchor_x="center",
        )

    def on_mouse_motion(self, x, y, dx, dy):
        self.btn_hover = self.btn_rect.point_in_rect((x, y))
        self.wh_hover = self.wh_rect.point_in_rect((x, y))
        self.market_hover = self.market_rect.point_in_rect((x, y))
        self.forge_hover = self.forge_rect.point_in_rect((x, y))

    def on_mouse_press(self, x, y, button, modifiers):
        from db.database import init_db, get_or_create_player

        # 锻造坊按钮
        if self.forge_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.forge_view import ForgeView
            self.window.show_view(ForgeView(self.window_ref))
            return

        # 仓库按钮
        if self.wh_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.warehouse_view import WarehouseView
            self.window.show_view(WarehouseView(self.window_ref))
            return

        # 市场按钮
        if self.market_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.market_view import MarketView
            self.window.show_view(MarketView(self.window_ref))
            return

        # 开始游戏按钮
        if self.btn_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.map_select_view import MapSelectView
            self.window.show_view(MapSelectView(self.window_ref))
