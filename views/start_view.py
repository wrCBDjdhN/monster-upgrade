"""开始游戏页面"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from game.sound_manager import sound_manager


def _enter_facility(view, facility_id, target_factory):
    """设施入口守卫（阶段10：市场/锻造坊的**唯一口径**，全部调用方共用）

    判定顺序：
    1. 教程激活（gs.tutorial.active）→ 直接放行进业务 View
       （新档教程期必然未建设施，守卫不能把引导卡死）
    2. get_facility_level(pid, fid) >= 1（已建造）→ 进业务 View
       （target_factory 由调用方延迟 import 后传入，如 MarketView / ForgeView）
    3. 未建造 → 进 FacilityView 建造页（消耗仓库材料+金币）

    调用方需先完成 init_db + get_or_create_player（守卫要读 player_id）。
    """
    from db.database import get_facility_level
    gs = getattr(view.window, "game_state", None)
    tut = getattr(gs, "tutorial", None)
    pid = getattr(gs, "player_id", None)
    # 未建造且非教程期 → 拦到设施页建造
    if (tut is None or not tut.active) and pid and get_facility_level(pid, facility_id) < 1:
        from views.facility_view import FacilityView
        view.window.show_view(FacilityView(view.window_ref, facility_id))
        return
    view.window.show_view(target_factory(view.window_ref))


class StartView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.title = "打怪升级"
        # 按钮纵向排列：新增图鉴后整体重排，间距 55px 均匀分布（保持风格统一）
        self.btn_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 55, 220, 50)
        self.wh_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 110, 220, 50)
        self.market_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 165, 220, 50)
        self.forge_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 220, 220, 50)  # 锻造坊入口
        # 图鉴与任务板同行左右并排（两枚按钮整体仍以屏幕中线居中）
        self.codex_rect = arcade.XYWH(WINDOW_WIDTH // 2 - 110, WINDOW_HEIGHT // 2 - 275, 200, 50)  # 图鉴入口
        self.mission_rect = arcade.XYWH(WINDOW_WIDTH // 2 + 110, WINDOW_HEIGHT // 2 - 275, 200, 50)  # 任务板入口
        self.net_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 330, 220, 46)  # 局域网联机入口
        self.settings_gear_rect = arcade.XYWH(35, WINDOW_HEIGHT - 35, 40, 40)  # 左上角齿轮图标
        self.btn_hover = False
        self.wh_hover = False
        self.market_hover = False
        self.forge_hover = False
        self.codex_hover = False  # 图鉴按钮悬停态
        self.mission_hover = False  # 任务板按钮悬停态
        self.net_hover = False
        self.settings_hover = False
        # 新手教程（阶段 1）：向导弹窗状态
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False
        # 设施等级（阶段10：市场/锻造坊按钮的🔒置灰与 Lv.N 显示；避免 on_draw 每帧查库）
        self._fac_levels = {}
        self._refresh_facilities()

    def _refresh_facilities(self):
        """刷新设施等级缓存 {facility_id: level}（0 = 未建造）

        读库失败（如首次启动 init_db 尚未建表）时退化为「全部未建造」，
        保证开始界面（启动链上的第一个界面）不会因缺表崩掉。
        """
        gs = getattr(self.window_ref, "game_state", None)
        pid = getattr(gs, "player_id", None) if gs is not None else None
        if not pid:
            self._fac_levels = {}
            return
        try:
            from db.database import get_facilities
            self._fac_levels = get_facilities(pid)
        except Exception:
            self._fac_levels = {}

    def _fac_built(self, facility_id):
        """该设施是否已建造（等级 >= 1）"""
        return int(self._fac_levels.get(facility_id, 0)) >= 1

    def _build_tutorial_pages(self):
        """新手教程阶段 0：开始界面向导页（介绍各入口 + 引导进入游戏）

        修改原因：阶段0扩充（2026-09-26）——在收尾的「准备出发」页之前追加
        图鉴奖励与配方 / 任务板 / 设施建造与升级 三页（共 11 页）。页数不硬编码，
        推进逻辑只用 len(self.tut_pages) 做上界判定，追加即生效。
        """
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
                "开局需先用仓库材料建造（教程期间可直接进入）。",
            ], highlight=self.market_rect),
            TutorialPage("锻造坊", [
                "【锻造坊】升级装备：",
                "用材料升级武器/装备，还可合成强力神器。",
                "开局需先用仓库材料建造（教程期间可直接进入）。",
            ], highlight=self.forge_rect),
            TutorialPage("图鉴", [
                "【图鉴】记录你遭遇过的怪物与获得过的物品：",
                "击杀怪物、拾取装备药水、商店购买均可解锁条目，",
                "解锁后可查看完整属性与图鉴进度。",
            ], highlight=self.codex_rect),
            TutorialPage("局域网联机", [
                "【局域网联机】最多 4 人联机：",
                "建房或加入好友房间，一起打怪一起撤离。",
            ], highlight=self.net_rect),
            # ── 阶段0 扩充（2026-09-26）：图鉴档位领奖 + 配方解锁锻造 ──
            TutorialPage("图鉴奖励与配方", [
                "【图鉴】集齐条目还能拿奖励：",
                "每类图鉴集齐 40% / 70% / 100% 三个档位，",
                "各领一次金币 + 材料奖励。",
                "条目集齐后会解锁对应的【锻造配方】，",
                "去锻造坊就能合成那件装备。",
            ], highlight=self.codex_rect),
            # ── 阶段0 扩充（2026-09-26）：任务 / 成就入口 ──
            TutorialPage("任务板", [
                "【任务板】有两个 Tab：",
                "每日任务每天刷新，做完点领奖拿金币与经验；",
                "成就是累计型的，永久不清零。",
                "打怪、采集、开宝箱、撤离、锻造都会累计进度。",
            ], highlight=self.mission_rect),
            # ── 阶段0 扩充（2026-09-26）：设施建造与升级 ──
            TutorialPage("设施建造与升级", [
                "【市场】和【锻造坊】要先花仓库材料 + 金币建造，",
                "建造后开放，还能继续升级（最高 3 级）：",
                "升级可拿折扣、回收加成、神器概率等增益。",
                "（教程期间不建也能先进去看看。）",
            ], highlight=self.forge_rect),
            TutorialPage("准备出发", [
                "点击【下一步】开始你的第一场冒险！",
                "（教程的每一步都会有提示指引）",
            ], highlight=self.btn_rect, next_text="进入游戏"),
        ]

    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY
        # 设施等级刷新（阶段10：从设施页建造/升级后返回，按钮🔒与 Lv.N 立即生效）
        self._refresh_facilities()
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

        # 市场按钮（阶段10：未建造置灰🔒，点击先进设施页建造；已建造右侧显示 Lv.N）
        mkt_built = self._fac_built("market")
        if mkt_built:
            market_color = arcade.color.PURPLE if self.market_hover else (80, 40, 100)
        else:
            market_color = (105, 105, 115) if self.market_hover else arcade.color.GRAY
        arcade.draw_rect_filled(self.market_rect, market_color)
        arcade.draw_rect_outline(self.market_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_market",
            "市 场" if mkt_built else "🔒 市 场（未建造）",
            self.market_rect.center_x, self.market_rect.center_y,
            arcade.color.WHITE, size=22 if mkt_built else 16, anchor_x="center", anchor_y="center",
        )
        if mkt_built:
            self._tc.text(
                "btn_market_lv", f"Lv.{int(self._fac_levels.get('market', 0))}",
                self.market_rect.right - 14, self.market_rect.center_y,
                arcade.color.GOLD, size=13, anchor_x="right", anchor_y="center",
            )

        # 锻造坊按钮（阶段10：同市场；内含 Lv.5+ 材料合成高级装备/神器）
        forge_built = self._fac_built("forge")
        if forge_built:
            forge_color = (160, 60, 40) if self.forge_hover else (110, 40, 30)
        else:
            forge_color = (105, 105, 115) if self.forge_hover else arcade.color.GRAY
        arcade.draw_rect_filled(self.forge_rect, forge_color)
        arcade.draw_rect_outline(self.forge_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_forge",
            "锻 造 坊" if forge_built else "🔒 锻 造 坊（未建造）",
            self.forge_rect.center_x, self.forge_rect.center_y,
            arcade.color.WHITE, size=22 if forge_built else 16, anchor_x="center", anchor_y="center",
        )
        if forge_built:
            self._tc.text(
                "btn_forge_lv", f"Lv.{int(self._fac_levels.get('forge', 0))}",
                self.forge_rect.right - 14, self.forge_rect.center_y,
                arcade.color.GOLD, size=13, anchor_x="right", anchor_y="center",
            )

        # 图鉴按钮（查看怪物/装备/资源图鉴）
        codex_color = (60, 90, 160) if self.codex_hover else (45, 65, 115)
        arcade.draw_rect_filled(self.codex_rect, codex_color)
        arcade.draw_rect_outline(self.codex_rect, arcade.color.WHITE, border_width=2)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_codex",
            "图 鉴",
            self.codex_rect.center_x, self.codex_rect.center_y,
            arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center",
        )

        # 任务板按钮（每日任务 + 成就领奖，位置样式仿图鉴按钮）
        mission_color = (150, 110, 40) if self.mission_hover else (110, 80, 30)
        # 渲染铁律：禁线框描边，用「白色实心底 + 内缩 2px 实心色块」等效出 2px 白边
        arcade.draw_rect_filled(self.mission_rect, arcade.color.WHITE)
        arcade.draw_rect_filled(
            arcade.XYWH(
                self.mission_rect.center_x, self.mission_rect.center_y,
                self.mission_rect.width - 4, self.mission_rect.height - 4,
            ),
            mission_color,
        )
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_mission",
            "任 务",
            self.mission_rect.center_x, self.mission_rect.center_y,
            arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center",
        )

        # 局域网联机按钮
        net_color = (40, 160, 120) if self.net_hover else (30, 110, 85)
        arcade.draw_rect_filled(self.net_rect, net_color)
        arcade.draw_rect_outline(self.net_rect, arcade.color.WHITE, border_width=2)
        self._tc.text(
            "btn_net",
            "局 域 网 联 机",
            self.net_rect.center_x, self.net_rect.center_y,
            arcade.color.WHITE, size=18, anchor_x="center", anchor_y="center",
        )

        # 左上角齿轮图标（设置入口）
        gear_color = (100, 100, 120) if not self.settings_hover else (140, 140, 170)
        arcade.draw_rect_filled(self.settings_gear_rect, gear_color)
        # 用 "⚙" 齿轮符号
        self._tc.text(
            "gear_icon",
            "⚙",
            self.settings_gear_rect.center_x, self.settings_gear_rect.center_y,
            arcade.color.WHITE, size=24, anchor_x="center", anchor_y="center",
        )

        # 底部提示（联机按钮占用了底部空间，提示上移）
        # 阶段10：悬停未建造的市场/锻造坊按钮时改为「点击建造：XX」引导
        hint_text = "WASD移动 | 鼠标瞄准攻击 | 击杀怪物获取资源"
        hint_color = arcade.color.GRAY
        if self.market_hover and not mkt_built:
            hint_text, hint_color = "点击建造：市场（需仓库材料 + 金币）", arcade.color.GOLD
        elif self.forge_hover and not forge_built:
            hint_text, hint_color = "点击建造：锻造坊（需仓库材料 + 金币）", arcade.color.GOLD
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "hint",
            hint_text,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30,
            hint_color, size=12, anchor_x="center",
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
        self.codex_hover = self.codex_rect.point_in_rect((x, y))  # 图鉴按钮悬停
        self.mission_hover = self.mission_rect.point_in_rect((x, y))  # 任务板按钮悬停
        self.net_hover = self.net_rect.point_in_rect((x, y))
        self.settings_hover = self.settings_gear_rect.point_in_rect((x, y))

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

        sound_manager.play_ui()
        # 局域网联机按钮
        if self.net_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.lobby_view import LobbyView
            self.window.show_view(LobbyView(self.window_ref))
            return

        # 锻造坊按钮（阶段10：过 _enter_facility 守卫——未建造先进设施页）
        if self.forge_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.forge_view import ForgeView
            _enter_facility(self, "forge", ForgeView)
            return

        # 图鉴按钮
        if self.codex_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.codex_view import CodexView
            self.window.show_view(CodexView(self.window_ref))
            return

        # 任务板按钮（每日任务 + 成就领奖）
        if self.mission_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.mission_view import MissionView
            self.window.show_view(MissionView(self.window_ref))
            return

        # 仓库按钮
        if self.wh_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.warehouse_view import WarehouseView
            self.window.show_view(WarehouseView(self.window_ref))
            return

        # 市场按钮（阶段10：过 _enter_facility 守卫——未建造先进设施页）
        if self.market_rect.point_in_rect((x, y)):
            init_db()
            gs = self.window.game_state
            gs.player_id = get_or_create_player(gs.player_name)
            from views.market_view import MarketView
            _enter_facility(self, "market", MarketView)
            return

        # 开始游戏按钮
        if self.btn_rect.point_in_rect((x, y)):
            self._start_game()
            return

        # 设置按钮：打开设置界面（from_game=False，显示"退出游戏"替代"放弃行动"）
        if self.settings_gear_rect.point_in_rect((x, y)):
            from views.settings_view import SettingsView
            self.window.show_view(SettingsView(self.window_ref, from_game=False))
            return
