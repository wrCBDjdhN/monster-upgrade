"""任务板页面：每日任务 + 成就两 Tab，展示进度并发放奖励

- 顶部两个 Tab：「每日任务」（db.missions.get_daily，每日 config.DAILY_COUNT 条，跨天自动重抽）
  与「成就」（db.missions.get_achievements，累计进度、一次性奖励）；
- 每行结构：任务描述 + 奖励预览 + 进度条（progress/target）+ 领取按钮；
- 领取按钮：达成未领 → 亮黄（缓慢呼吸提示可领），未达成/已领 → 置灰不可点；
- 领奖链：db.missions.claim_* 返回奖励 → db.players.add_gold 入账金币
  → db.levels.add_exp 入账经验（角色取 game_state.character_id 当前选中角色）
  → 飘奖励文字 + 金币音效；
- 打开即刷新：on_show_view 调 get_daily（自带跨天重抽）与 get_achievements；
- 滚动复用 views/scroll_view.py（ScrollView：滚轮 + 滚动条拖拽 + 返回按钮）；
- 返回：按钮 + ESC（基类规则：联机回大厅 / 单机回开始界面，延迟导入）。
"""

import math

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from db.database import (
    get_daily, claim_daily, get_achievements, claim_achievement,
    add_gold, add_exp, get_gold,
)
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from game.sound_manager import sound_manager


class MissionView(ScrollView):
    """任务板：每日任务 / 成就两 Tab，进度条 + 领奖按钮 + 奖励飘字"""

    # ── 布局常量（1280×720 逻辑分辨率）──
    LIST_X = 90                             # 列表左边界
    LIST_W = WINDOW_WIDTH - 180             # 列表宽度（左右各留 90px）
    ROW_H = 62                              # 行高（实心圆角矩形面板）
    ROW_STEP = 74                           # 行间距
    BAR_W = 460                             # 进度条宽度
    BAR_H = 16                              # 进度条高度
    CLAIM_W = 130                           # 领取按钮宽
    CLAIM_H = 40                            # 领取按钮高
    CONTENT_BOTTOM = 70                     # 列表可视区底部 y（返回按钮上方）
    GOLD_X = WINDOW_WIDTH - 100             # 顶部金币显示 x
    # 颜色
    C_PANEL = (38, 42, 54)                  # 行面板底色
    C_PANEL_HOVER = (48, 54, 68)            # 行面板悬停底色
    C_BAR_BG = (62, 66, 78)                 # 进度条底槽
    C_BAR_FILL = (90, 190, 120)             # 进度条填充（绿）
    C_BAR_DONE = (255, 200, 60)             # 进度条满格（金）
    C_CLAIM_OK = (215, 165, 40)             # 可领取按钮底色（亮黄）
    C_CLAIM_OK_HOVER = (250, 210, 80)       # 可领取按钮悬停
    C_CLAIM_OFF = (62, 64, 72)              # 不可领取按钮底色（置灰）
    C_TAB_ON = (80, 130, 80)                # 当前 Tab
    C_TAB_OFF = (50, 55, 65)                # 未选 Tab
    C_TAB_HOVER = (62, 70, 86)              # 未选 Tab 悬停
    C_BACK = (110, 30, 30)                  # 返回按钮底色
    C_BACK_HOVER = arcade.color.DARK_RED    # 返回按钮悬停
    # 飘字
    TOAST_LIFE = 1.6                        # 飘字存活时长（秒）
    TOAST_RISE = 38.0                       # 飘字每秒上浮像素
    C_BG = (25, 30, 40)                     # 背景色（飘字淡出终点色）

    def __init__(self, window):
        super().__init__(window)
        self._tc = TextCache()              # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        self._rows: list[dict] = []         # 当前 Tab 的行数据（已归一化，见 _normalize）
        self._gold = 0                      # 顶部金币显示缓存（避免每帧查库）
        # 顶部分类 Tab 栏：每日任务 / 成就
        self._tabs = ["每日任务", "成就"]
        self._tab = "每日任务"
        self.tab_rects = {}
        self.tab_hover = {}
        tab_w, tab_h, gap = 150, 34, 16
        total_w = len(self._tabs) * tab_w + (len(self._tabs) - 1) * gap
        start_x = (WINDOW_WIDTH - total_w) // 2
        for i, name in enumerate(self._tabs):
            self.tab_rects[name] = arcade.XYWH(
                start_x + i * (tab_w + gap) + tab_w / 2,
                WINDOW_HEIGHT - 108, tab_w, tab_h,
            )
            self.tab_hover[name] = False
        # 领取按钮命中区（on_draw 按可见行重建）与悬停下标
        self._claim_rects: list[tuple[arcade.Rect, int]] = []
        self._claim_hover = -1
        self.back_hover = False             # 返回按钮悬停
        self._toasts: list[dict] = []       # 奖励飘字 [{text,color,age}]
        self._pulse = 0.0                   # 可领按钮呼吸动画相位
        self._refresh()

    # ── 布局辅助 ──────────────────────────────────────────────

    @property
    def content_top(self) -> float:
        """可滚动内容区顶部 y（Tab 栏下方），覆盖基类默认值"""
        return WINDOW_HEIGHT - 145

    def _max_scroll(self) -> float:
        """滚动上限：最后一行底边刚好对齐到返回按钮上方"""
        depth = max(0, (len(self._rows) - 1) * self.ROW_STEP + self.ROW_H)
        return max(0.0, depth - (self.content_top - self.CONTENT_BOTTOM))

    def clamp_scroll(self):
        """覆盖基类：按行数计算滚动上限（每日 3 条不产生滚动）"""
        self.scroll_offset = max(0.0, min(self._max_scroll(), self.scroll_offset))

    def _scrollbar_max_scroll(self) -> float:
        """覆盖基类：滚动条比例与 clamp_scroll 保持同一上限"""
        return self._max_scroll()

    def _row_rect(self, y: float) -> arcade.Rect:
        """按屏幕 y 生成行面板矩形（XYWH 首参为中心 x）"""
        return arcade.XYWH(self.LIST_X + self.LIST_W / 2, y, self.LIST_W, self.ROW_H)

    def _claim_rect(self, y: float) -> arcade.Rect:
        """按屏幕 y 生成领取按钮矩形（靠行右边缘内缩 12px）"""
        return arcade.XYWH(
            self.LIST_X + self.LIST_W - 12 - self.CLAIM_W / 2, y, self.CLAIM_W, self.CLAIM_H,
        )

    # ── 数据 ──────────────────────────────────────────────────

    def _player_id(self):
        """当前玩家 id；无档案时返回 None（页面显示空列表提示）"""
        gs = getattr(self.window_ref, "game_state", None)
        return getattr(gs, "player_id", None) if gs is not None else None

    def _normalize(self, raw: dict, is_daily: bool) -> dict:
        """db 层行字典 → 页面统一行结构

        每日任务用 slot 定位、成就用 achievement_id 定位；统一出 key/desc/target/
        progress/claimed/complete/gold/exp 供绘制与领奖使用。
        """
        return {
            "is_daily": is_daily,
            "slot": int(raw.get("slot", -1)),
            "achievement_id": str(raw.get("achievement_id", "")),
            "desc": str(raw.get("desc", "")),
            "target": int(raw.get("target", 0)),
            "progress": int(raw.get("progress", 0)),
            "claimed": bool(raw.get("claimed", False)),
            "complete": bool(raw.get("complete", False)),
            "gold": int(raw.get("reward_gold", 0)),
            "exp": int(raw.get("reward_exp", 0)),
        }

    def _refresh(self):
        """按当前 Tab 重读数据（打开即刷新；每日任务自带跨天重抽）"""
        pid = self._player_id()
        if pid is None:
            self._rows = []
        elif self._tab == "每日任务":
            self._rows = [self._normalize(r, True) for r in get_daily(pid)]
        else:
            self._rows = [self._normalize(r, False) for r in get_achievements(pid)]
        self._gold = get_gold(pid) if pid is not None else 0
        # 列表结构变化 → 清空文本缓存（key 含下标，避免旧行文本残留）
        self._tc.clear()
        self._claim_rects = []
        self._claim_hover = -1
        self.content_height = max(0, (len(self._rows) - 1) * self.ROW_STEP + self.ROW_H)
        self.clamp_scroll()

    def on_show_view(self):
        """基类设背景色 + 打开即刷新（跨天重抽在此生效）"""
        super().on_show_view()
        self._refresh()

    # ── 绘制 ──────────────────────────────────────────────────

    def on_draw(self):
        self.clear()
        self._draw_header()
        self._draw_tabs()
        self._draw_rows()
        self.draw_scrollbar()
        self._draw_back()
        self._draw_toasts()

    def _draw_header(self):
        """顶部标题 + 金币显示"""
        self._tc.text("title", "任 务 板", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 42,
                      arcade.color.GOLD, 30, anchor_x="center")
        self._tc.text("gold", f"金币 {self._gold}", self.GOLD_X, WINDOW_HEIGHT - 42,
                      arcade.color.YELLOW, 18, anchor_x="center")

    def _draw_tabs(self):
        """顶部分类 Tab 栏（固定，不随列表滚动）"""
        for name, rect in self.tab_rects.items():
            if name == self._tab:
                bg = self.C_TAB_ON
            elif self.tab_hover.get(name):
                bg = self.C_TAB_HOVER
            else:
                bg = self.C_TAB_OFF
            # 渲染铁律：只用不透明实心填充，禁线框/描边
            arcade.draw_rect_filled(rect, bg)
            self._tc.text(f"tab_{name}", name, rect.center_x, rect.center_y,
                          arcade.color.WHITE if name == self._tab else arcade.color.LIGHT_GRAY,
                          16, anchor_x="center", anchor_y="center")

    def _draw_rows(self):
        """任务/成就列表：面板 + 描述 + 奖励 + 进度条 + 领取按钮（仅绘制可见行）"""
        self._claim_rects = []
        if not self._rows:
            hint = "暂无玩家档案" if self._player_id() is None else "今日暂无任务"
            self._tc.text("empty", hint, WINDOW_WIDTH // 2, self.content_top - 40,
                          arcade.color.GRAY, 16, anchor_x="center")
            return
        y = self.content_top - self.ROW_H / 2 + self.scroll_offset
        for i, row in enumerate(self._rows):
            if self.CONTENT_BOTTOM <= y <= self.content_top:
                self._draw_row(i, row, y)
            y -= self.ROW_STEP

    def _draw_row(self, i: int, row: dict, y: float):
        """绘制单行：实心面板 + 描述 + 奖励 + 进度条 + 进度数字 + 领取按钮"""
        rect = self._row_rect(y)
        hover = (i == self._claim_hover)
        arcade.draw_rect_filled(rect, self.C_PANEL_HOVER if hover else self.C_PANEL)
        left = rect.left
        # 描述 + 奖励预览
        self._tc.text(f"row_desc_{i}", row["desc"], left + 18, y + 9,
                      arcade.color.WHITE, 17, anchor_y="center")
        reward = f"奖励：金币 {row['gold']}"
        if row["exp"] > 0:
            reward += f" · 经验 {row['exp']}"
        # 奖励文案不在此绘制：原位置（left+18, y-13）会被随后绘制的进度条实心底槽整段遮挡，
        # 改画到进度数字右侧、与进度条同高（见本函数进度数字绘制之后）
        # 进度条：底槽 + 填充（满格转金色）
        bar_cx = left + 18 + self.BAR_W / 2
        bar_y = y - self.ROW_H / 2 + 14
        arcade.draw_rect_filled(
            arcade.XYWH(bar_cx, bar_y, self.BAR_W, self.BAR_H), self.C_BAR_BG,
        )
        ratio = 0.0 if row["target"] <= 0 else min(1.0, row["progress"] / row["target"])
        if ratio > 0:
            arcade.draw_rect_filled(
                arcade.XYWH(left + 18 + self.BAR_W * ratio / 2, bar_y,
                            self.BAR_W * ratio, self.BAR_H),
                self.C_BAR_DONE if row["complete"] else self.C_BAR_FILL,
            )
        # 进度数字 x/target
        self._tc.text(f"row_prog_{i}", f"{row['progress']} / {row['target']}",
                      left + 18 + self.BAR_W + 12, bar_y,
                      arcade.color.YELLOW if row["complete"] else arcade.color.LIGHT_GRAY,
                      14, anchor_x="left", anchor_y="center")
        # 奖励文案：原画在 (left+18, y-13) 被进度条实心底槽遮挡，移到进度数字右侧并与进度条同高
        # （bar_y、左锚点，无需 len(text) 手算居中；不改动 ROW_H/ROW_STEP/BAR_W/H，滚动口径不变）
        self._tc.text(f"row_reward_{i}", reward,
                      left + 18 + self.BAR_W + 12 + 80, bar_y,
                      arcade.color.LIGHT_GRAY, 12, anchor_x="left", anchor_y="center")
        # 领取按钮：达成未领 → 亮黄（呼吸），其余置灰
        claim_rect = self._claim_rect(y)
        self._claim_rects.append((claim_rect, i))
        if row["claimed"]:
            self._draw_claim_button(claim_rect, i, "已领取", self.C_CLAIM_OFF,
                                    arcade.color.DARK_GRAY, False)
        elif row["complete"]:
            # 呼吸提示：亮度随相位轻微起伏（不透明度固定，避免闪烁）
            glow = 0.5 + 0.5 * math.sin(self._pulse * 4.0)
            bg = tuple(
                int(self.C_CLAIM_OK[c] + (self.C_CLAIM_OK_HOVER[c] - self.C_CLAIM_OK[c]) * glow)
                for c in range(3)
            )
            self._draw_claim_button(claim_rect, i, "领 取", bg, arcade.color.WHITE, True)
        else:
            self._draw_claim_button(claim_rect, i, "未完成", self.C_CLAIM_OFF,
                                    arcade.color.LIGHT_GRAY, False)

    def _draw_claim_button(self, rect: arcade.Rect, i: int, label: str, bg, fg, active: bool):
        """绘制领取按钮（实心填充 + 居中文字）"""
        if active and i == self._claim_hover:
            bg = self.C_CLAIM_OK_HOVER
        arcade.draw_rect_filled(rect, bg)
        self._tc.text(f"claim_{i}", label, rect.center_x, rect.center_y,
                      fg, 16, anchor_x="center", anchor_y="center")

    def _draw_back(self):
        """左下角返回按钮（位置与 ScrollView 一致）"""
        bg = self.C_BACK_HOVER if self.back_hover else self.C_BACK
        arcade.draw_rect_filled(self.back_rect, bg)
        self._tc.text("nav_back", "返回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

    def _draw_toasts(self):
        """奖励/提示飘字：向上浮动并渐隐（色值向背景色插值，保持不透明实心渲染）"""
        for i, t in enumerate(self._toasts):
            fade = max(0.0, 1.0 - max(0.0, t["age"] - (self.TOAST_LIFE - 0.6)) / 0.6)
            color = tuple(
                int(self.C_BG[c] + (t["color"][c] - self.C_BG[c]) * fade) for c in range(3)
            )
            self._tc.text(f"toast_{i}", t["text"], WINDOW_WIDTH // 2,
                          self.content_top - 20 - t["age"] * self.TOAST_RISE,
                          color, 20, anchor_x="center", anchor_y="center", bold=True)

    # ── 交互 ──────────────────────────────────────────────────

    def on_mouse_motion(self, x, y, dx, dy):
        """更新悬停状态：返回按钮 / Tab / 领取按钮；拖拽滚动条时跟随鼠标"""
        if self._scroll_dragging:
            self.update_scroll_drag(x, y)
        self.back_hover = self.back_rect.point_in_rect((x, y))
        for name, rect in self.tab_rects.items():
            self.tab_hover[name] = rect.point_in_rect((x, y))
        self._claim_hover = -1
        for rect, idx in self._claim_rects:
            if rect.point_in_rect((x, y)):
                self._claim_hover = idx
                break

    def on_mouse_release(self, x, y, button, modifiers):
        """松开鼠标结束滚动条拖拽"""
        self.end_scroll_drag()

    def on_mouse_press(self, x, y, button, modifiers):
        # 返回按钮（联机回大厅 / 单机回开始界面，基类统一处理）
        if self.handle_back_click(x, y):
            sound_manager.play_ui()
            return
        # 右侧滚动条拖拽
        if self.start_scroll_drag(x, y):
            return
        # Tab 切换
        for name, rect in self.tab_rects.items():
            if rect.point_in_rect((x, y)):
                if name != self._tab:
                    sound_manager.play_ui()
                    self._tab = name
                    self.scroll_offset = 0.0
                    self._refresh()
                return
        # 领取按钮（仅可见行，倒序命中后画的在上层）
        for rect, idx in reversed(self._claim_rects):
            if rect.point_in_rect((x, y)):
                self._claim(idx)
                return

    def on_key_press(self, key, modifiers):
        """ESC 返回：复用基类跳转规则（传入返回按钮中心坐标）"""
        if key == arcade.key.ESCAPE:
            self.handle_back_click(self.back_rect.center_x, self.back_rect.center_y)

    # ── 领奖 ──────────────────────────────────────────────────

    def _claim(self, i: int):
        """领取第 i 行奖励：claim_* → add_gold + add_exp → 飘奖励文字"""
        row = self._rows[i]
        if row["claimed"] or not row["complete"]:
            # 按钮已置灰，这里再挡一次（防止状态与绘制不同步）
            self._toast("该奖励不可领取", (255, 160, 120))
            sound_manager.play_ui()
            return
        pid = self._player_id()
        if pid is None:
            self._toast("请先创建玩家档案", (255, 160, 120))
            sound_manager.play_ui()
            return
        if row["is_daily"]:
            res = claim_daily(pid, row["slot"])
        else:
            res = claim_achievement(pid, row["achievement_id"])
        if not res.get("ok"):
            # db 层拒绝原因（未完成/已领取/不存在）直接飘给玩家
            self._toast(str(res.get("reason", "领取失败")), (255, 160, 120))
            sound_manager.play_ui()
            return
        gold = int(res.get("reward_gold", 0))
        exp = int(res.get("reward_exp", 0))
        if gold > 0:
            add_gold(pid, gold)
        if exp > 0:
            # 经验入账到当前选中角色（单机/联机共用的 game_state.character_id）
            gs = getattr(self.window_ref, "game_state", None)
            character_id = getattr(gs, "character_id", "initial")
            add_exp(pid, character_id, exp)
        text = f"领取成功  金币 +{gold}"
        if exp > 0:
            text += f"  经验 +{exp}"
        self._toast(text, arcade.color.GOLD)
        sound_manager.play_gold_pickup()
        # 重新读库刷新行状态（claimed=True）与顶部金币
        self._refresh()

    def _toast(self, text: str, color):
        """新增一条飘字（最多同时 4 条，超出丢弃最旧的）"""
        self._toasts.append({"text": text, "color": color, "age": 0.0})
        if len(self._toasts) > 4:
            self._toasts.pop(0)

    def on_update(self, delta_time: float = 1 / 60):
        """推进飘字计时与呼吸动画相位"""
        self._pulse += delta_time
        for t in self._toasts:
            t["age"] += delta_time
        self._toasts = [t for t in self._toasts if t["age"] < self.TOAST_LIFE]
