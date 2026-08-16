"""选择地图页面"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text


# 地图数据定义
MAPS = [
    {
        "id": 1,
        "name": "幽暗森林",
        "desc": "僵尸骷髅出没的阴森林地",
        "monsters": "僵尸(近战) / 骷髅(远程)",
        "difficulty": "普通",
        "color": (40, 80, 40),
        "theme": "forest",  # 地图主题：决定生成逻辑与配色
    },
    {
        "id": 2,
        "name": "沙漠荒地",
        "desc": "木乃伊骆驼盘踞的炙热沙丘",
        "monsters": "木乃伊 / 骆驼 / 僵尸 / 骷髅",
        "difficulty": "困难",
        "color": (150, 120, 50),
        "theme": "desert",  # 地图主题：决定生成逻辑与配色
    },
    {
        "id": 3,
        "name": "航天基地",
        "desc": "深空中的废弃基地，隐藏着火箭发射台",
        "monsters": "狙击兵 / 突击兵 / 土匪 / 火箭兵",
        "difficulty": "极难",
        "color": (50, 60, 100),
        "theme": "space",  # 地图主题：决定生成逻辑与配色
    },
]


class MapSelectView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.hovered_map = -1
        # 计算卡片位置（多张卡片整体居中）
        self.cards = []
        card_w, card_h = 300, 180
        spacing = 40  # 卡片间距
        total_w = len(MAPS) * card_w + (len(MAPS) - 1) * spacing
        start_x = (WINDOW_WIDTH - total_w) // 2
        start_y = WINDOW_HEIGHT // 2 - card_h // 2 + 40
        for i, m in enumerate(MAPS):
            rect = arcade.XYWH(start_x + i * (card_w + spacing), start_y, card_w, card_h)
            self.cards.append(rect)

    def on_show_view(self):
        self.window.background_color = arcade.color.BLACK

    def on_draw(self):
        self.clear()
        # 标题
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "title",
            "选 择 地 图",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 80,
            arcade.color.WHITE, size=36, anchor_x="center",
        )
        # 地图卡片
        for i, m in enumerate(MAPS):
            rect = self.cards[i]
            is_hover = (self.hovered_map == i)
            # 卡片背景
            bg_color = (m["color"][0]+30, m["color"][1]+30, m["color"][2]+30) if is_hover else m["color"]
            arcade.draw_rect_filled(rect, bg_color)
            border_color = arcade.color.GOLD if is_hover else arcade.color.WHITE
            arcade.draw_rect_outline(rect, border_color, border_width=2)
            # 地图名
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_name_{i}", m["name"], rect.center_x, rect.top - 30,
                arcade.color.WHITE, size=22, anchor_x="center", bold=True,
            )
            # 怪物信息
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_monsters_{i}", m["monsters"], rect.center_x, rect.center_y + 10,
                arcade.color.LIGHT_GRAY, size=12, anchor_x="center",
            )
            # 地图描述
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_desc_{i}", m["desc"], rect.center_x, rect.center_y - 15,
                arcade.color.GRAY, size=10, anchor_x="center",
            )
            # 难度
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_diff_{i}", "难度: " + m["difficulty"], rect.center_x, rect.bottom + 25,
                arcade.color.YELLOW, size=14, anchor_x="center",
            )
            # 进入按钮
            btn = arcade.XYWH(rect.center_x, rect.bottom + 50, 100, 30)
            btn_color = arcade.color.DARK_GREEN if is_hover else (60, 120, 60)
            arcade.draw_rect_filled(btn, btn_color)
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_btn_{i}", "进入", btn.center_x, btn.center_y,
                arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center",
            )

        # 返回按钮
        back_rect = arcade.XYWH(80, 40, 100, 36)
        arcade.draw_rect_filled(back_rect, arcade.color.DARK_RED)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "back_btn",
            "返回", back_rect.center_x, back_rect.center_y,
            arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center",
        )

    def on_mouse_motion(self, x, y, dx, dy):
        self.hovered_map = -1
        for i, rect in enumerate(self.cards):
            if rect.point_in_rect((x, y)):
                self.hovered_map = i
                break

    def on_mouse_press(self, x, y, button, modifiers):
        # 返回按钮（单机流程：角色选择 → 地图选择，返回时回角色选择页）
        back_rect = arcade.XYWH(80, 40, 100, 36)
        if back_rect.point_in_rect((x, y)):
            from views.character_select_view import CharacterSelectView
            self.window.show_view(CharacterSelectView(self.window_ref))
            return

        # 地图卡片点击
        for i, rect in enumerate(self.cards):
            if rect.point_in_rect((x, y)):
                # 每次进入随机生成种子，确保房间/资源/怪物/宝箱位置不固定
                import random
                self.window.game_state.current_map_seed = random.randint(1, 999999)
                # 记录地图主题，供 GameView 生成对应主题地图
                self.window.game_state.map_theme = MAPS[i].get("theme", "forest")
                from views.game_view import GameView
                gv = GameView(self.window_ref)
                gv.setup()
                self.window.show_view(gv)
                return
