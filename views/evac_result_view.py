"""撤离结果页面：显示撤离成功/失败和当局收益"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from entities.resource_defs import RESOURCES       # 资源ID → 中文名
from entities.weapon_defs import ALL_WEAPONS       # 武器ID → 中文名
from entities.equipment_defs import get_item_def   # 装备(头盔/护甲)ID → 中文名
from game.sound_manager import sound_manager


class EvacResultView(arcade.View):
    """撤离结果页面"""
    
    def __init__(self, window, success: bool, run_carried: dict = None):
        """
        初始化撤离结果页面
        
        Args:
            window: 窗口引用
            success: 是否撤离成功
            run_carried: 本次携带的物品（成功时显示收益，失败时为空）
        """
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.success = success
        self.run_carried = run_carried or {}
        self.return_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 180, 220, 50)
        self.return_hover = False
        # 新手教程（阶段 5）：撤离成功后的火箭发射台图文教学页（无高亮，纯图文）
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False

    def _build_tutorial_pages(self):
        """新手教程阶段 5：BOSS 介绍 + 航天基地火箭发射台教学（撤离结算页上覆盖展示）"""
        from views.tutorial import TutorialPage, build_boss_intro_pages
        # BOSS 介绍页（4 页）+ 火箭发射台教学页（1 页）
        boss_pages = build_boss_intro_pages()
        rocket_pages = [
            TutorialPage("航天基地 · 火箭发射台", [
                "航天基地（极难地图）有火箭发射台：走近按 E 激活，召唤镇守 BOSS。",
                "击败 BOSS 后二选一：",
                "  · 按 7 炸毁发射台 → 立即获得大量奖励，随后地图毁灭需马上撤离",
                "  · 按 8 启用发射台撤离 → 站上平台读条 3 秒撤离，同样带走战利品",
                "本局你从普通撤离点撤离成功，战利品已入库。",
            ], next_text="查看收益"),
        ]
        return boss_pages + rocket_pages

    def _tut_showing(self):
        """教程教学页是否正在本界面显示（阶段 5 且未翻完页）"""
        tut = getattr(self.window.game_state, "tutorial", None)
        return (tut is not None and tut.active and tut.stage == 4
                and tut.page < len(self.tut_pages))
        
    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY
        
    def on_draw(self):
        self.clear()
        
        # 标题
        if self.success:
            title = "撤离成功!"
            title_color = arcade.color.GREEN
            subtitle = "战利品已存入仓库"
        else:
            title = "撤离失败!"
            title_color = arcade.color.RED
            subtitle = "所有携带物品已丢失"
            
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "title",
            title,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 120,
            title_color, size=48, anchor_x="center", bold=True,
        )
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "subtitle",
            subtitle,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 70,
            arcade.color.LIGHT_GRAY, size=18, anchor_x="center",
        )
        
        # 显示收益详情
        if self.success and self.run_carried:
            y = WINDOW_HEIGHT // 2 + 20
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                "gain",
                "本次收益:",
                WINDOW_WIDTH // 2, y,
                arcade.color.GOLD, size=20, anchor_x="center", bold=True,
            )
            y -= 35
            
            # 金币
            gold = self.run_carried.get("gold", 0)
            if gold > 0:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    "gold",
                    f"金币: {gold}",
                    WINDOW_WIDTH // 2, y,
                    arcade.color.YELLOW, size=16, anchor_x="center",
                )
                y -= 25
                
            BOTTOM_Y = 230  # 资源/武器/装备列表最低绘制线，防止条目过多时超出屏幕
            hidden = 0      # 因空间不足被省略的条目数

            # 资源（显示中文名）
            resources = self.run_carried.get("resource", {})
            for i, (res_id, qty) in enumerate(resources.items()):
                if y < BOTTOM_Y:
                    hidden += 1
                    continue
                res_name = RESOURCES.get(res_id, {}).get("name", res_id)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"res_{i}",
                    f"{res_name}: {qty}",
                    WINDOW_WIDTH // 2, y,
                    arcade.color.WHITE, size=16, anchor_x="center",
                )
                y -= 25
                
            # 武器（显示中文名）
            weapons = self.run_carried.get("weapon", {})
            for i, ((wid, level), qty) in enumerate(weapons.items()):
                if y < BOTTOM_Y:
                    hidden += 1
                    continue
                w_name = ALL_WEAPONS.get(wid, {}).get("name", wid)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"wep_{i}",
                    f"武器: {w_name} Lv{level} x{qty}",
                    WINDOW_WIDTH // 2, y,
                    (100, 200, 255), size=16, anchor_x="center",
                )
                y -= 25
                
            # 装备（显示中文名）
            for slot in ("helmet", "armor"):
                items = self.run_carried.get(slot, {})
                for i, ((iid, level), qty) in enumerate(items.items()):
                    if y < BOTTOM_Y:
                        hidden += 1
                        continue
                    eq_def = get_item_def(slot, iid)
                    e_name = eq_def["name"] if eq_def else iid
                    # 持久 Text 对象，避免 draw_text 每帧重建纹理
                    self._tc.text(
                        f"eq_{slot}_{i}",
                        f"装备: {e_name} Lv{level} x{qty}",
                        WINDOW_WIDTH // 2, y,
                        (180, 180, 220), size=16, anchor_x="center",
                    )
                    y -= 25

            # 空间不足时给出提示，避免信息被省略后用户不知去向
            if hidden > 0:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    "hidden",
                    f"（另有 {hidden} 项已存入仓库）",
                    WINDOW_WIDTH // 2, BOTTOM_Y,
                    arcade.color.GRAY, size=14, anchor_x="center",
                )
        elif not self.success:
            # 失败时显示提示
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                "fail_tip",
                "下次加油!",
                WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2,
                arcade.color.ORANGE, size=24, anchor_x="center",
            )
        
        # 返回按钮
        btn_color = arcade.color.DARK_GREEN if self.return_hover else arcade.color.GREEN
        arcade.draw_rect_filled(self.return_rect, btn_color)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_back",
            "返回大厅",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 180,
            arcade.color.WHITE, size=18, anchor_x="center", anchor_y="center",
            bold=True,
        )

        # 新手教程（阶段 5）：火箭发射台教学页覆盖层（画在最上层）
        if self._tut_showing():
            from views.tutorial import draw_tutorial_page
            tut = getattr(self.window.game_state, "tutorial", None)
            page = self.tut_pages[tut.page]
            self.tut_next_rect, self.tut_skip_rect = draw_tutorial_page(
                self, page, tut.page, len(self.tut_pages),
                self._tc, self.tut_next_hover)

    def on_key_press(self, key, modifiers):
        # 新手教程激活：ESC 立即跳过并标记完成
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active:
            if key == arcade.key.ESCAPE:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return

    def on_mouse_motion(self, x, y, dx, dy):
        # 新手教程教学页显示：只更新下一步按钮悬停态
        if self._tut_showing():
            self.tut_next_hover = bool(
                self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)))
            return
        self.return_hover = self.return_rect.point_in_rect((x, y))

    def on_mouse_press(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            # 新手教程教学页显示：只响应 下一步/跳过
            if self._tut_showing():
                from views.tutorial import finish_tutorial
                if self.tut_skip_rect and self.tut_skip_rect.point_in_rect((x, y)):
                    finish_tutorial(self.window)
                    return
                if self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)):
                    # 翻页：下一页或完成教学
                    tut = getattr(self.window.game_state, "tutorial", None)
                    if tut is not None:
                        tut.page += 1
                        if tut.page >= len(self.tut_pages):
                            # 翻完全部教学页：露出撤离结算页（查看收益后点返回按钮）
                            pass
                    return
                return

            sound_manager.play_ui()
            if self.return_rect.point_in_rect((x, y)):
                # 新手教程：看完撤离结算 → 引导进入市场买卖教学（阶段 6 接管）
                gs = self.window.game_state
                tut = getattr(gs, "tutorial", None)
                if tut is not None and tut.active and tut.stage == 4:
                    tut.stage = 5
                    tut.page = 0
                    from views.market_view import MarketView
                    self.window.show_view(MarketView(self.window_ref))
                    return
                # 联机模式：撤离结果页仅单机路径可达（联机撤离/死亡均走回房等待），
                # 防御性分流：联机回 LobbyView 复用连接，单机回 StartView
                if getattr(gs, "net_mode", "solo") != "solo":
                    from views.lobby_view import LobbyView
                    self.window.show_view(LobbyView(self.window_ref))
                else:
                    from views.start_view import StartView
                    self.window.show_view(StartView(self.window_ref))
