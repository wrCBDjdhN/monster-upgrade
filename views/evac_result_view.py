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
    
    def __init__(self, window, success: bool, run_carried: dict = None,
                 star_info: dict = None):
        """
        初始化撤离结果页面
        
        Args:
            window: 窗口引用
            success: 是否撤离成功
            run_carried: 本次携带的物品（成功时显示收益，失败时为空）
            star_info: 阶段8 星级结算载荷（GameView._settle_run_stars 返回；
                撤离成功才有，失败/联机观战路径为 None）。字段：
                theme/stars/prev_stars/new_stars/upgraded/max_stars
        """
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.success = success
        self.run_carried = run_carried or {}
        # 阶段8 星级：本次评星结果载荷（None = 本局无星级结算，不画横幅）
        self.star_info = star_info
        # 阶段8：本次升星顺带解锁的地图名（构造时算一次并缓存，避免 on_draw 每帧查 DB）
        self._newly_unlocked = self._newly_unlocked_names(star_info)
        self.return_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 180, 220, 50)
        self.return_hover = False
        # 新手教程（阶段 4）：撤离结算页的火箭发射台/BOSS/星级图文教学页（无高亮，纯图文）
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False

    def _build_tutorial_pages(self):
        """新手教程阶段 4：BOSS 介绍 + 航天基地火箭发射台教学 + 星级结算

        缺口补齐（2026-09-26）：
        1) **成功/失败两套文案**。原实现只有一套「本局你从普通撤离点撤离成功，
           战利品已入库」，玩家教程局阵亡/超时走到本页时会被告知「撤离成功」，
           与结算页的失败结论直接矛盾。失败局改教「丢了什么 + 下局怎么撤」。
        2) **星级结算页**（仅成功局且本次有评星载荷时追加）。星级是解锁下一张
           图的唯一口径（config.MAP_UNLOCK_STARS），原先只在地图选择页讲过
           「怎么解锁」，没讲「这一局打了几星、怎么凑满」。
        3) **键位/等级/读条秒数实查**（action_key_label / boss_level_label /
           evac_hold_seconds），禁写死「按 7」「按 8」「读条 3 秒」「Lv5+」——
           7/8 可在设置界面重绑，等级门槛与读条秒数是 config/entities 的值。
        """
        from views.tutorial import (
            TutorialPage,
            action_key_label,
            build_boss_intro_pages,
            evac_hold_seconds,
        )
        k_atk = action_key_label("interact")
        k_destroy = action_key_label("rocket_destroy")
        k_rocket_evac = action_key_label("rocket_evac")
        # BOSS 介绍页只在首次抵达本页时完整播放一次（tut.boss_taught 记忆），
        # 避免教程局失败重来后又被 4 页 BOSS 介绍拦一次。
        gs = self.window_ref.game_state
        tut = getattr(gs, "tutorial", None)
        already_taught = bool(tut is not None and getattr(tut, "boss_taught", False))
        boss_pages = [] if already_taught else build_boss_intro_pages()
        pages: list = list(boss_pages)
        # 火箭发射台教学：两种结局都要讲（成功局衔接"下一张图"，失败局衔接"下局怎么撤"）
        pages.append(TutorialPage("航天基地 · 火箭发射台", [
            f"航天基地（极难地图）有火箭发射台：走近按 {k_atk} 激活，召唤镇守 BOSS。",
            "击败 BOSS 后二选一：",
            f"  · 按 {k_destroy} 炸毁发射台 → 立即获得大量奖励，随后地图毁灭需马上撤离",
            f"  · 按 {k_rocket_evac} 启用发射台撤离 → 站上平台读条 {evac_hold_seconds()} 秒撤离，"
            f"同样带走战利品",
        ], next_text="查看结算"))
        if self.success:
            pages.append(TutorialPage("本局结算", [
                "本局你从普通撤离点撤离成功，",
                "携带的战利品与金币已全部入库。",
                "",
                "被拆成废墟的撤离点不影响结算，",
                "只有玩家阵亡或倒计时结束才会丢失携带物。",
            ]))
        else:
            # 教程事实修正（2026-09-26）：失败局不能复用成功局文案
            pages.append(TutorialPage("本局结算", [
                "本局没能撤出，携带的武器/装备/资源与金币全部丢失。",
                "（仓库里原本存放的东西不受影响。）",
                "",
                "想撤出：撤离点要先靠近按 " + k_atk + " 消耗材料激活，",
                f"守住防守波次后再按 {k_atk} 读条 {evac_hold_seconds()} 秒。",
                "下一局多带药水，打不过就先撤。",
            ]))
        # 星级结算页：仅成功局且本次确有评星载荷时追加（无载荷 = 未评星，不硬凑）
        star_page = self._build_star_tutorial_page()
        if star_page is not None:
            pages.append(star_page)
        # 记录 BOSS 页条数（on_mouse_press 翻过即置 boss_taught，重来不再重复播放）
        self.tut_boss_count = len(boss_pages)
        return pages

    def _build_star_tutorial_page(self):
        """星级结算教学页（仅成功局且 star_info 有载荷时返回 TutorialPage，否则 None）

        数值全部实查：本次星数/上限取 star_info 载荷 → config.MAP_STAR_CRITERIA
        → config.MAP_MAX_STARS（与 _draw_star_banner 同一口径）；解锁门槛取
        config.MAP_UNLOCK_STARS。禁硬编码星数。
        """
        info = self.star_info
        if not info or not self.success:
            return None
        from config import MAP_MAX_STARS, MAP_STAR_CRITERIA, MAP_UNLOCK_STARS
        from views.tutorial import TutorialPage, map_max_stars, star_criteria_labels, unlock_star_label
        theme = str(info.get("theme", "forest"))
        criteria = MAP_STAR_CRITERIA.get(theme, [])
        max_stars = int(info.get("max_stars", 0)) or len(criteria) or int(MAP_MAX_STARS)
        stars = max(0, min(max_stars, int(info.get("stars", 0))))
        # 地图中文名复用 views/map_select_view.MAPS（唯一数据源，不另存一份）
        from views.map_select_view import MAPS
        theme_name = next((str(m.get("name")) for m in MAPS
                           if str(m.get("theme")) == theme), theme)
        lines = [
            f"本次撤离给【{theme_name}】评了 {stars} / {max_stars} 星"
            f"（单图上限 {map_max_stars()} 星，条件逐条达成即加星）：",
            f"  ·{star_criteria_labels(theme) or '本图无额外条件'}",
            "",
        ]
        need = int(MAP_UNLOCK_STARS.get(theme, 0))
        if need > 0:
            lines.append(f"【{theme_name}】需累计 ★{need} 解锁——")
        lines.append("回大厅点【任务板】领每日任务奖励，")
        lines.append("去【地图选择】看星标就能知道哪张图已开放。")
        return TutorialPage("星级结算", lines, next_text="去市场逛逛")

    def _tut_showing(self):
        """教程教学页是否正在本界面显示（阶段 4 且未翻完页）"""
        tut = getattr(self.window.game_state, "tutorial", None)
        return (tut is not None and tut.active and tut.stage == 4
                and tut.page < len(self.tut_pages))

    def _newly_unlocked_names(self, info) -> list[str]:
        """本次升星顺带跨过解锁门槛的地图名（阶段8：评星 → 地图解锁的反馈闭环）

        口径：累计星数 = get_stars 全图之和；门槛见 config.MAP_UNLOCK_STARS。
        升星前后各取一次累计值，落在 (前, 后] 区间的门槛即为本次新解锁的图。
        地图中文名复用 views/map_select_view.MAPS（唯一数据源，不在此处另存一份）。
        仅在 __init__ 调用一次（结果缓存到 self._newly_unlocked），on_draw 不再查 DB。
        """
        from config import MAP_UNLOCK_STARS
        from db.database import get_stars
        from views.map_select_view import MAPS
        # 注意：本视图持有的是 window_ref（arcade.View 不注入 self.window）
        pid = getattr(self.window_ref.game_state, "player_id", None)
        if not pid or not info or not info.get("upgraded"):
            return []
        after = sum(get_stars(pid).values())
        delta = int(info.get("new_stars", 0)) - int(info.get("prev_stars", 0))
        before = max(0, after - max(0, delta))
        name_by_theme = {str(m.get("theme")): str(m.get("name")) for m in MAPS}
        return [name_by_theme.get(t, t) for t, need in MAP_UNLOCK_STARS.items()
                if before < int(need) <= after]

    def _draw_star_banner(self):
        """阶段8 星级横幅（撤离成功才有 star_info）

        渲染铁律：一律不透明实心填充（禁 outline/线框）。
        布局：深金实心底 + 大字标题（升星=「★×N 解锁！」）+ 金/暗双色实心★星标
        + 下一星条件 + 本次新解锁地图名。
        """
        info = self.star_info
        if not info:
            return
        from config import MAP_MAX_STARS, MAP_STAR_CRITERIA
        theme = str(info.get("theme", "forest"))
        criteria = MAP_STAR_CRITERIA.get(theme, [])
        # 星数上限：载荷 → 该图条件条数 → config.MAP_MAX_STARS（禁硬编码 3）
        max_stars = int(info.get("max_stars", 0)) or len(criteria) or int(MAP_MAX_STARS)
        stars = max(0, min(max_stars, int(info.get("stars", 0))))
        new_stars = max(0, min(max_stars, int(info.get("new_stars", stars))))
        upgraded = bool(info.get("upgraded"))
        cx = WINDOW_WIDTH // 2
        # 实心深金底横幅（不画描边，避免线框闪烁）
        banner = arcade.XYWH(cx, WINDOW_HEIGHT - 118, 470, 108)
        arcade.draw_rect_filled(banner, (72, 54, 14))
        # 标题：升星金色大字，否则常规浅金
        if upgraded:
            title = f"★×{new_stars} 解锁！"
            title_color = (255, 215, 0)
        else:
            title = f"本次 ★{stars} / {max_stars}"
            title_color = (214, 190, 130)
        self._tc.text("star_title", title, cx, banner.top - 16, title_color,
                      size=22, anchor_x="center", bold=True)
        # 星标：已达成为金色实心★，未达成为暗色实心★（禁空心/线框）。
        # 两个 Text 以同一点为锚左右拼接（已达右对齐 + 未达左对齐），无需测量字宽。
        split_x = cx + 4
        if stars > 0:
            self._tc.text("star_on", "★" * stars, split_x, banner.top - 42,
                          (255, 215, 0), size=18, anchor_x="right")
        if stars < max_stars:
            self._tc.text("star_off", "★" * (max_stars - stars), split_x, banner.top - 42,
                          (88, 74, 40), size=18, anchor_x="left")
        # 下一星条件（顺序达成语义：第 stars+1 条即下一星门槛；已满星则显示已达顶星）
        nxt = str(criteria[stars].get("desc", "")) if stars < len(criteria) else "已达最高星"
        self._tc.text("star_next", f"下一星：{nxt}", cx, banner.top - 64,
                      (190, 190, 200), size=13, anchor_x="center")
        # 本次升星顺带解锁的地图（构造时算一次并缓存；无则画空串占位，保持 Text 缓存 key 稳定）
        unlocked = self._newly_unlocked
        unlock_text = f"新解锁：{'、'.join(unlocked)}" if unlocked else ""
        self._tc.text("star_unlock", unlock_text, cx, banner.top - 86,
                      (120, 220, 255), size=13, anchor_x="center")

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

        # 阶段8 星级横幅（撤离成功才画；升星显示「★×N 解锁！」）
        self._draw_star_banner()
        
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

        # 新手教程（阶段 4）：BOSS/火箭发射台/星级教学页覆盖层（画在最上层）
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
                        # 翻过 BOSS 介绍页即记 boss_taught：教程局失败重来时
                        # _build_tutorial_pages 不再重复播这 4 页（补齐死字段语义）
                        boss_count = int(getattr(self, "tut_boss_count", 0))
                        if boss_count > 0 and tut.page >= boss_count:
                            tut.boss_taught = True
                        if tut.page >= len(self.tut_pages):
                            # 翻完全部教学页：露出撤离结算页（查看收益后点返回按钮）
                            pass
                    return
                return

            sound_manager.play_ui()
            if self.return_rect.point_in_rect((x, y)):
                # 新手教程：看完撤离结算 → 引导进入市场买卖教学（阶段 5 接管）
                gs = self.window.game_state
                tut = getattr(gs, "tutorial", None)
                if tut is not None and tut.active and tut.stage == 4:
                    tut.stage = 5
                    tut.page = 0
                    # 阶段10：过 _enter_facility 守卫（教程期放行 → 正常进市场）
                    from views.market_view import MarketView
                    from views.start_view import _enter_facility
                    _enter_facility(self, "market", MarketView)
                    return
                # 联机模式：撤离结果页仅单机路径可达（联机撤离/死亡均走回房等待），
                # 防御性分流：联机回 LobbyView 复用连接，单机回 StartView
                if getattr(gs, "net_mode", "solo") != "solo":
                    from views.lobby_view import LobbyView
                    self.window.show_view(LobbyView(self.window_ref))
                else:
                    from views.start_view import StartView
                    self.window.show_view(StartView(self.window_ref))
