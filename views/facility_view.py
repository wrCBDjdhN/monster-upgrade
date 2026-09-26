"""设施详情页（阶段10）：市场/锻造坊的建造与升级界面

结构仿 views/codex_view.py（标题 + 列表 + 按钮 + 飘字 + TextCache 文本缓存）：
- 顶部：设施名 + 当前状态（未建造 / Lv.N / 上限）+ 设施介绍
- 中部「各级增益」：Lv1..MAX 逐级 perks 渲染，已达等级高亮（金）、下一等级白色、更远灰色
- 底部费用区：目标等级费用逐项对照仓库库存/金币，充足绿、不足红
- 操作按钮：建 造 / 升 级 至 Lv.N / 已满级（置灰）；已建造时追加「进入市场/进入锻造坊」
- 遵守「读条期间禁点击」：建造/升级后进入 0.3s 短飘字锁，锁定期内忽略全部按钮点击
- 渲染铁律：一律不透明实心填充（禁空心/线框），文本统一经 TextCache
"""
import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT, FACILITY_MAX_LEVEL
from entities.facility_defs import FACILITIES, facility_cost_at
from views.text_cache import TextCache
from game.sound_manager import sound_manager


class FacilityView(arcade.View):
    """单设施详情页：展示增益与费用，执行建造/升级，并可跳进对应业务 View"""

    # ── 布局常量（1280×720 逻辑分辨率）──
    TITLE_Y = WINDOW_HEIGHT - 100
    STATUS_Y = WINDOW_HEIGHT - 145
    DESC_Y = WINDOW_HEIGHT - 176
    PERKS_HEADER_Y = WINDOW_HEIGHT - 220
    PERK_TOP = WINDOW_HEIGHT - 254
    PERK_STEP = 20          # 增益行行距
    COST_HEADER_Y = 252
    COST_TOP = 220
    COST_STEP = 24          # 费用行行距
    STOCK_Y = 112           # 当前持有量提示行
    BTN_Y = 66              # 操作按钮行中心 y
    BTN_W, BTN_H = 260, 46
    # 飘字（建造/升级结果提示）
    TOAST_LIFE = 1.6        # 飘字存活秒数
    TOAST_RISE = 26         # 飘字每秒上浮像素
    BUILD_LOCK = 0.3        # 建造/升级后短锁秒数（读条期间禁点击）

    def __init__(self, window, facility_id: str):
        super().__init__()
        self.window_ref = window
        self.facility_id = facility_id
        self._tc = TextCache()  # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        # 返回按钮（左上角）
        self.back_rect = arcade.XYWH(80, WINDOW_HEIGHT - 45, 100, 36)
        self.back_hover = False
        # 操作按钮：主按钮（建造/升级/已满级）+ 已建造时的「进入业务 View」按钮
        self.main_rect = arcade.XYWH(WINDOW_WIDTH // 2 - 145, self.BTN_Y, self.BTN_W, self.BTN_H)
        self.enter_rect = arcade.XYWH(WINDOW_WIDTH // 2 + 145, self.BTN_Y, self.BTN_W, self.BTN_H)
        self.main_hover = False
        self.enter_hover = False
        self._toasts = []   # 飘字 [{text, color, age}]
        self._lock = 0.0    # 读条短锁（>0 时忽略点击）
        # 运行时数据（_refresh 填充）
        self._level = 0
        self._cost = {}
        self._stock = {}    # {费用项 key: 持有量}
        self._maxed = False
        self._refresh()

    # ── 数据 ────────────────────────────────────────────────────

    def _player_id(self):
        """读取当前玩家 id；无玩家时返回 None

        用 window_ref（__init__ 已赋值）而非 self.window：后者在 show_view 前可能未就绪。
        """
        gs = getattr(self.window_ref, "game_state", None)
        return getattr(gs, "player_id", None) if gs is not None else None

    def _def(self) -> dict:
        """设施定义（未知 id 退化为空 dict，页面照常渲染不崩）"""
        return FACILITIES.get(self.facility_id, {})

    def _refresh(self):
        """从 DB 重新读取等级/目标费用/持有量（进入页面与建造升级后各调一次）"""
        self._tc.clear()
        self._level = 0
        self._cost = {}
        self._stock = {}
        self._maxed = False
        pid = self._player_id()
        if pid is None:
            return
        from db.database import get_facility_level, get_gold, get_warehouse
        self._level = get_facility_level(pid, self.facility_id)
        # 目标等级：未建造→1；已建造且未满级→下一级；已满级→无（_maxed）
        self._maxed = self._level >= FACILITY_MAX_LEVEL
        if not self._maxed:
            self._cost = facility_cost_at(self.facility_id, self._level + 1)
        # 持有量：金币 + 仓库资源（按 item_type=='resource' 过滤汇总）
        self._stock = {"gold": get_gold(pid)}
        for w in get_warehouse(pid):
            if w["item_type"] == "resource":
                self._stock[w["item_id"]] = self._stock.get(w["item_id"], 0) + int(w["quantity"])

    def _target_level(self) -> int:
        """当前目标等级（未建造→1，已建造→下一级；满级返回 0 表示无目标）"""
        return 0 if self._maxed else self._level + 1

    # ── 绘制 ────────────────────────────────────────────────────

    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY
        self._refresh()  # 从业务 View 返回时同步最新等级

    def on_draw(self):
        self.clear()
        fdef = self._def()
        name = fdef.get("name", self.facility_id)
        # 顶部：设施名（持久 Text 对象，避免 draw_text 每帧重建纹理）
        self._tc.text("f_title", name, WINDOW_WIDTH // 2, self.TITLE_Y,
                      arcade.color.GOLD, size=40, anchor_x="center", bold=True)
        # 状态行：未建造 / Lv.N / 上限
        if self._level <= 0:
            status = "未建造"
        else:
            status = f"Lv.{self._level} / {FACILITY_MAX_LEVEL}"
        self._tc.text("f_status", status, WINDOW_WIDTH // 2, self.STATUS_Y,
                      arcade.color.LIGHT_GRAY, size=20, anchor_x="center")
        self._tc.text("f_desc", fdef.get("desc", ""), WINDOW_WIDTH // 2, self.DESC_Y,
                      arcade.color.GRAY, size=14, anchor_x="center")
        self._draw_perks(fdef)
        self._draw_cost()
        self._draw_buttons(name)
        self._draw_toasts()

    def _perk_color(self, lv: int):
        """按当前进度取增益行颜色：已达=金、下一级=白、更远=灰"""
        if lv <= self._level:
            return arcade.color.GOLD
        if lv == self._level + 1:
            return arcade.color.WHITE
        return arcade.color.GRAY

    def _draw_perks(self, fdef: dict):
        """「各级增益」列表：Lv1..MAX 每级 perks 逐条渲染 + 分级配色"""
        self._tc.text("perks_header", "各级增益", WINDOW_WIDTH // 2, self.PERKS_HEADER_Y,
                      arcade.color.CYAN, size=20, anchor_x="center", bold=True)
        perks: dict = fdef.get("perks", {})
        y = self.PERK_TOP
        key = 0
        for lv in range(1, FACILITY_MAX_LEVEL + 1):
            color = self._perk_color(lv)
            mark = "✓" if lv <= self._level else ("▶" if lv == self._level + 1 else "·")
            self._tc.text(f"perk_lv_{lv}", f"{mark} Lv.{lv}", 70, y,
                          color, size=16, bold=True)
            key += 1
            y -= self.PERK_STEP
            for line in perks.get(lv, []):
                self._tc.text(f"perk_{key}", line, 150, y, color, size=14)
                key += 1
                y -= self.PERK_STEP

    def _cost_color(self, key: str, need: int):
        """费用项配色：持有量充足=绿、不足=红"""
        have = int(self._stock.get(key, 0))
        return (arcade.color.GREEN if have >= need else arcade.color.RED,
                have >= need)

    def _draw_cost(self):
        """费用区：目标等级费用逐项对照持有量（充足绿 / 不足红）+ 当前持有量提示"""
        if self._maxed:
            self._tc.text("cost_header", "已满级（无法继续升级）", WINDOW_WIDTH // 2,
                          self.COST_HEADER_Y, arcade.color.GRAY, size=18, anchor_x="center")
            return
        target = self._target_level()
        verb = "建造费用" if self._level <= 0 else f"升 级 至 Lv.{target} 费用"
        self._tc.text("cost_header", verb, WINDOW_WIDTH // 2, self.COST_HEADER_Y,
                      arcade.color.CYAN, size=18, anchor_x="center", bold=True)
        y = self.COST_TOP
        if not self._cost:
            self._tc.text("cost_none", "（无费用配置）", WINDOW_WIDTH // 2, y,
                          arcade.color.GRAY, size=15, anchor_x="center")
            return
        for i, (key, need) in enumerate(self._cost.items()):
            need = int(need)
            color, enough = self._cost_color(key, need)
            label = "金币" if key == "gold" else self._resource_name(key)
            have = int(self._stock.get(key, 0))
            self._tc.text(f"cost_{i}", f"{label} ×{need}", WINDOW_WIDTH // 2 - 90, y,
                          color, size=17, bold=True)
            self._tc.text(f"cost_have_{i}", f"持有 {have}", WINDOW_WIDTH // 2 + 110, y,
                          arcade.color.LIGHT_GRAY, size=14)
            if enough:
                self._tc.text(f"cost_ok_{i}", "充足", WINDOW_WIDTH // 2 + 200, y,
                              arcade.color.GREEN, size=14)
            else:
                self._tc.text(f"cost_ok_{i}", f"不足（差 {need - int(self._stock.get(key, 0))}）",
                              WINDOW_WIDTH // 2 + 200, y, arcade.color.RED, size=14)
            y -= self.COST_STEP
        # 提示行原在 STOCK_Y-34(=78)，横穿按钮行（BTN_Y=66, BTN_H=46 → y 43..89），故上移到 y=20
        self._tc.text("cost_stock", "（建造/升级消耗仓库材料与金币，失败不扣费）",
                      WINDOW_WIDTH // 2, 20,
                      arcade.color.GRAY, size=12, anchor_x="center")

    def _resource_name(self, item_id: str) -> str:
        """资源 item_id → 中文名（缺定义时回退 item_id）"""
        from entities.resource_defs import RESOURCES
        return str(RESOURCES.get(item_id, {}).get("name", item_id))

    def _draw_buttons(self, name: str):
        """底部按钮：主操作（建造/升级/已满级置灰）+ 已建造时的「进入{name}」"""
        # 主按钮
        if self._maxed:
            bg, fg, label = (70, 70, 76), arcade.color.GRAY, "已满级"
        elif self._player_id() is None:
            bg, fg, label = (70, 70, 76), arcade.color.GRAY, "请先开始游戏"
        else:
            bg, fg, label = ((80, 140, 80) if self._level <= 0 else (80, 110, 170)), \
                arcade.color.WHITE, ("建 造" if self._level <= 0 else f"升 级 至 Lv.{self._target_level()}")
        if self.main_hover and not self._maxed:
            bg = tuple(min(255, c + 30) for c in bg)
        arcade.draw_rect_filled(self.main_rect, bg)
        self._tc.text("btn_main", label, self.main_rect.center_x, self.main_rect.center_y,
                      fg, size=20, anchor_x="center", anchor_y="center", bold=True)
        # 进入业务 View（未建造时不渲染；满级后仍可进入）
        if self._level >= 1:
            ebg = (80, 40, 100) if not self.enter_hover else (105, 55, 130)
            arcade.draw_rect_filled(self.enter_rect, ebg)
            self._tc.text("btn_enter", f"进入{name}", self.enter_rect.center_x,
                          self.enter_rect.center_y, arcade.color.WHITE, size=20,
                          anchor_x="center", anchor_y="center", bold=True)
        # 返回按钮
        bbg = (100, 100, 120) if self.back_hover else (70, 70, 90)
        arcade.draw_rect_filled(self.back_rect, bbg)
        self._tc.text("btn_back", "返 回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, size=16, anchor_x="center", anchor_y="center")

    def _draw_toasts(self):
        """飘字：向上浮动并渐隐（色值向背景色插值，保持不透明实心渲染）"""
        bg = (47, 79, 79)  # 与 on_show_view 的 DARK_SLATE_GRAY 一致
        for i, t in enumerate(self._toasts):
            fade = max(0.0, 1.0 - max(0.0, t["age"] - (self.TOAST_LIFE - 0.6)) / 0.6)
            color = tuple(int(bg[c] + (t["color"][c] - bg[c]) * fade) for c in range(3))
            self._tc.text(f"toast_{i}", t["text"], WINDOW_WIDTH // 2,
                          self.STOCK_Y + 10 + t["age"] * self.TOAST_RISE,
                          color, size=20, anchor_x="center", anchor_y="center", bold=True)

    # ── 交互 ────────────────────────────────────────────────────

    def on_mouse_motion(self, x, y, dx, dy):
        self.back_hover = self.back_rect.point_in_rect((x, y))
        self.main_hover = self.main_rect.point_in_rect((x, y))
        self.enter_hover = self.enter_rect.point_in_rect((x, y))

    def on_update(self, delta_time: float = 1 / 60):
        # 读条短锁倒计时（锁定期内禁点击，防重复提交建造/升级）
        if self._lock > 0.0:
            self._lock = max(0.0, self._lock - delta_time)
        # 飘字存活计时与回收
        for t in self._toasts:
            t["age"] += delta_time
        self._toasts = [t for t in self._toasts if t["age"] < self.TOAST_LIFE]

    def on_mouse_press(self, x, y, button, modifiers):
        # 读条期间禁点击：短锁未结束直接忽略全部按钮
        if self._lock > 0.0:
            return
        if self.back_rect.point_in_rect((x, y)):
            sound_manager.play_ui()
            self._go_back()
            return
        if self.main_rect.point_in_rect((x, y)):
            sound_manager.play_ui()
            self._do_main()
            return
        if self._level >= 1 and self.enter_rect.point_in_rect((x, y)):
            sound_manager.play_ui()
            self._enter_business()
            return

    def on_key_press(self, key, modifiers):
        """ESC 返回开始界面"""
        if key == arcade.key.ESCAPE:
            self._go_back()

    def _do_main(self):
        """主按钮：未建造→建造；已建造未满级→升级一级；满级/无玩家→飘字提示"""
        pid = self._player_id()
        if pid is None:
            self._toast("请先开始游戏以获得存档", (255, 160, 120))
            return
        if self._maxed:
            self._toast("已达最高等级", (200, 200, 200))
            return
        from db.database import build_facility, upgrade_facility, facility_can_afford
        target = self._target_level()
        ok, missing = facility_can_afford(pid, self.facility_id, target)
        if not ok:
            # 费用不足：飘中文缺失提示（db 层保证此时一分未扣）
            self._toast(missing or "资源不足", (255, 120, 110))
            return
        success = (build_facility(pid, self.facility_id) if self._level <= 0
                   else upgrade_facility(pid, self.facility_id))
        self._lock = self.BUILD_LOCK  # 短锁：读条期间禁点击
        if success:
            self._toast("建造成功！" if self._level <= 0 else f"升级成功！Lv.{target}",
                        arcade.color.GOLD)
        else:
            self._toast("操作失败（状态已变化）", (255, 160, 120))
        self._refresh()

    def _enter_business(self):
        """已建造时进入对应业务 View（延迟 import，项目导航唯一模式）"""
        if self.facility_id == "forge":
            from views.forge_view import ForgeView
            self.window.show_view(ForgeView(self.window_ref))
        else:
            from views.market_view import MarketView
            self.window.show_view(MarketView(self.window_ref))

    def _go_back(self):
        """返回开始界面（延迟导入，项目导航约定）"""
        from views.start_view import StartView
        self.window.show_view(StartView(self.window_ref))

    def _toast(self, text: str, color):
        """压入一条飘字（追加到列表，同屏多条向上错开）"""
        self._toasts.append({"text": text, "color": color, "age": 0.0})
