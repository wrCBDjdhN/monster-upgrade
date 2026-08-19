"""开始游戏页面"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text


class StartView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.title = "打怪升级"
        self.btn_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 60, 220, 50)
        self.wh_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 130, 220, 50)
        self.market_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 200, 220, 50)
        self.forge_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 270, 220, 50)  # 锻造坊入口
        self.net_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 330, 220, 46)  # 局域网联机入口
        self.btn_hover = False
        self.wh_hover = False
        self.market_hover = False
        self.forge_hover = False
        self.net_hover = False
        # 新手教程（阶段 1）：向导弹窗状态
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False

    def _build_tutorial_pages(self):
        """新手教程阶段 1：开始界面向导页（介绍各入口 + 引导进入游戏）"""
        from views.tutorial import TutorialPage
        return [
            TutorialPage("欢迎来到《打怪升级》！", [
                "这是一款 2D 动作 RPG：",
                "进入地图打怪升级、拾取战利品，",
                "在倒计时结束前撤离，把宝物带回家强化自己，",
                "挑战更强力的地图！接下来带你走一遍完整流程。",
            ]),
            TutorialPage("开始游戏", [
                "点击【开始游戏】进入实战：",
                "选择角色 → 选择地图 → 进入战场打怪升级。",
            ], highlight=self.btn_rect),
            TutorialPage("仓库", [
                "【仓库】存放局间战利品：",
                "撤离得到的武器/装备/资源都在这里，可随时存取。",
            ], highlight=self.wh_rect),
            TutorialPage("市场", [
                "【市场】买卖物品：",
                "购买更强力的武器装备、出售不需要的物品换取金币。",
            ], highlight=self.market_rect),
            TutorialPage("锻造坊", [
                "【锻造坊】升级装备：",
                "用材料升级武器/装备，还可合成强力神器。",
            ], highlight=self.forge_rect),
            TutorialPage("局域网联机", [
                "【局域网联机】最多 4 人联机：",
                "建房或加入好友房间，一起打怪一起撤离。",
            ], highlight=self.net_rect),
            TutorialPage("准备出发", [
                "点击【下一步】开始你的第一场冒险！",
                "（教程的每一步都会有提示指引）",
            ], highlight=self.btn_rect, next_text="进入游戏"),
        ]

    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY
        # 新手教程：回大厅时若未完成（撤离失败/中途返回/关游戏重开），从头开始
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active:
            tut.stage = 0
            tut.page = 0

    def on_draw(self):
        self.clear()
        gs = self.window.game_state

        # 标题
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "title",
            self.title,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 60,
            arcade.color.GOLD, size=48, anchor_x="center", bold=True,
        )
        # 副标题
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "subtitle",
            "2D Action RPG",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 10,
            arcade.color.LIGHT_GRAY, size=18, anchor_x="center",
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
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "equip", f"携带武器: {equipped_text}",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 25,
            arcade.color.CORNFLOWER_BLUE, 14, anchor_x="center",
        )

        # 开始游戏按钮
        color = arcade.color.CORNFLOWER_BLUE if self.btn_hover else arcade.color.STEEL_BLUE
        arcade.draw_rect_filled(self.btn_rect, color)
        arcade.draw_rect_outline(self.btn_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_start",
            "开 始 游 戏",
            self.btn_rect.center_x, self.btn_rect.center_y,
            arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center",
        )

        # 仓库按钮
        wh_color = arcade.color.DARK_ORANGE if self.wh_hover else (100, 70, 30)
        arcade.draw_rect_filled(self.wh_rect, wh_color)
        arcade.draw_rect_outline(self.wh_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_wh",
            "仓 库",
            self.wh_rect.center_x, self.wh_rect.center_y,
            arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center",
        )

        # 市场按钮
        market_color = arcade.color.PURPLE if self.market_hover else (80, 40, 100)
        arcade.draw_rect_filled(self.market_rect, market_color)
        arcade.draw_rect_outline(self.market_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_market",
            "市 场",
            self.market_rect.center_x, self.market_rect.center_y,
            arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center",
        )

        # 锻造坊按钮（消耗Lv.5+材料合成高级装备/神器）
        forge_color = (160, 60, 40) if self.forge_hover else (110, 40, 30)
        arcade.draw_rect_filled(self.forge_rect, forge_color)
        arcade.draw_rect_outline(self.forge_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_forge",
            "锻 造 坊",
            self.forge_rect.center_x, self.forge_rect.center_y,
            arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center",
        )

        # 局域网联机按钮
        net_color = (40, 160, 120) if self.net_hover else (30, 110, 85)
        arcade.draw_rect_filled(self.net_rect, net_color)
        arcade.draw_rect_outline(self.net_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_net",
            "局 域 网 联 机",
            self.net_rect.center_x, self.net_rect.center_y,
            arcade.color.WHITE, size=18, anchor_x="center", anchor_y="center",
        )

        # 底部提示（联机按钮占用了底部空间，提示上移）
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "hint",
            "WASD移动 | 鼠标瞄准攻击 | 击杀怪物获取资源",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30,
            arcade.color.GRAY, size=12, anchor_x="center",
        )

        # 新手教程（阶段 1）：向导弹窗覆盖层（画在最上层）
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active and tut.stage == 0:
            from views.tutorial import draw_tutorial_page
            page = self.tut_pages[min(tut.page, len(self.tut_pages) - 1)]
            self.tut_next_rect, self.tut_skip_rect = draw_tutorial_page(
                self, page, tut.page, len(self.tut_pages),
                self._tc, self.tut_next_hover)

    def on_mouse_motion(self, x, y, dx, dy):
        # 新手教程激活：只更新向导弹窗的下一步按钮悬停态
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active and tut.stage == 0:
            self.tut_next_hover = bool(
                self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)))
            return
        self.btn_hover = self.btn_rect.point_in_rect((x, y))
        self.wh_hover = self.wh_rect.point_in_rect((x, y))
        self.market_hover = self.market_rect.point_in_rect((x, y))
        self.forge_hover = self.forge_rect.point_in_rect((x, y))
        self.net_hover = self.net_rect.point_in_rect((x, y))

    def _start_game(self):
        """开始游戏：初始化玩家并进入角色选择页（单机流程入口）"""
        from db.database import init_db, get_or_create_player
        init_db()
        gs = self.window.game_state
        gs.player_id = get_or_create_player(gs.player_name)
        # 角色系统：单机流程 开始游戏 → 角色选择 → 地图选择（角色选择页内可购买）
        from views.character_select_view import CharacterSelectView
        self.window.show_view(CharacterSelectView(self.window_ref))

    def on_key_press(self, key, modifiers):
        # 新手教程激活：ESC 立即跳过并标记完成；其余按键不响应
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active:
            if key == arcade.key.ESCAPE:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return

    def on_mouse_press(self, x, y, button, modifiers):
        from db.database import init_db, get_or_create_player

        # 新手教程激活：只响应向导弹窗的 下一步/跳过（不透传到界面按钮）
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active and tut.stage == 0:
            from views.tutorial import finish_tutorial
            if self.tut_skip_rect and self.tut_skip_rect.point_in_rect((x, y)):
                finish_tutorial(self.window)
                return
            if self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)):
                tut.page += 1
                if tut.page >= len(self.tut_pages):
                    # 向导结束：引导进入游戏（阶段切到 1，由角色选择页接管）
                    tut.stage = 1
                    tut.page = 0
                    self._start_game()
                return
            return

        # 局域网联机按钮
        if self.net_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.lobby_view import LobbyView
            self.window.show_view(LobbyView(self.window_ref))
            return

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
            self._start_game()
