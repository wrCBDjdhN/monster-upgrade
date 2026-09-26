"""阶段5 祝福面板：触发一次祝福 → 3 选 1（局内肉鸽词条）

- 打开时机：宝箱（BLESSING_CHEST_CHANCE 概率）/ 精英击杀（保底）/ 撤离点激活进入防守
  任一触发点登记待选次数（GameView.blessing_pending）后，由 GameView.on_update 末尾自动弹出；
- 选择即生效：BlessingState.add 写入持有列表 → 立即 recompute 按「无祝福基准 + 聚合加成」
  绝对重算属性（护盾为一次性效果，选中瞬间单独授予）；
- 多次触发逐次消费 blessing_pending：选完一个若仍 >0 则继续下一组 3 选 1，全部选完才回游戏；
- 参考 LevelUpView 模式：全屏 View + TextCache 文本缓存 + XYWH 矩形卡片，
  渲染全部不透明实心填充（禁 draw_*_outline 线框，见根 AGENTS.md 渲染铁律）。
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT, BLESSING_MAX
from entities.blessing_defs import BLESSINGS
from game.sound_manager import sound_manager
from views.text_cache import TextCache


class BlessingView(arcade.View):
    """祝福 3 选 1 面板：展示本局已持有祝福与本轮 3 个候选，点击一个即生效"""

    def __init__(self, window, game_view=None):
        super().__init__()
        self.window_ref = window
        self.game_view = game_view   # 保存 GameView 引用：返回不重建 + 直接操作 blessing_state
        self._tc = TextCache()       # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        self.options = []            # 本轮候选 blessing_id 列表（roll_options 结果，天然不重复）
        self.option_rects = []       # [(rect, blessing_id), ...]：on_draw 重建，on_mouse_press 检测
        self.close_rect = arcade.XYWH(WINDOW_WIDTH // 2, 40, 180, 40)
        self._hover_index = -1       # 当前悬停下标（-1=无），绘制高亮
        self._refresh_options()

    # ── 数据读取 ──
    def _state(self):
        """取 GameView 上的祝福状态机（未 setup 时返回 None）"""
        return getattr(self.game_view, "blessing_state", None)

    def _refresh_options(self):
        """掷本轮 3 个候选；无待选次数或候选池为空时清空（面板随即关闭）"""
        state = self._state()
        if state is not None and getattr(self.game_view, "blessing_pending", 0) > 0:
            self.options = state.roll_options(3)
        else:
            self.options = []

    def _owned_ids(self) -> list[str]:
        """本局已持有的祝福 id（面板顶部展示）"""
        state = self._state()
        return list(state.ids) if state is not None else []

    # ── 绘制 ──
    def on_show_view(self):
        arcade.set_background_color((22, 26, 36))

    def on_draw(self):
        self.clear()

        owned = self._owned_ids()
        pending = getattr(self.game_view, "blessing_pending", 0)

        # 标题 + 待选次数
        self._tc.text("bl_title", "祝 福 ！", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 70,
                      arcade.color.GOLD, 36, anchor_x="center")
        self._tc.text("bl_count", f"选择 1 / 待选 {pending} 次　·　已持有 {len(owned)}/{BLESSING_MAX}",
                      WINDOW_WIDTH // 2, WINDOW_HEIGHT - 112,
                      arcade.color.LIGHT_GRAY, 16, anchor_x="center")
        self._tc.text("bl_hint", "选择一条祝福（本局永久生效）", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 142,
                      arcade.color.YELLOW, 15, anchor_x="center")

        # 已持有列表（换行绘制，超出显示省略）
        if owned:
            names = "、".join(BLESSINGS.get(b, {}).get("name", b) for b in owned[-8:])
            self._tc.text("bl_owned", f"已持有：{names}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 172,
                          arcade.color.WHITE, 14, anchor_x="center")
        else:
            self._tc.text("bl_owned", "尚未持有任何祝福", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 172,
                          arcade.color.LIGHT_GRAY, 14, anchor_x="center")

        # 3 个候选卡片（横排实心填充 + 顶部色带，无线框）
        self.option_rects = []
        card_w, card_h = 250, 190
        gap = 30
        total_w = len(self.options) * card_w + (len(self.options) - 1) * gap
        x0 = WINDOW_WIDTH // 2 - total_w // 2 + card_w // 2
        y_center = WINDOW_HEIGHT // 2 - 20
        for i, bid in enumerate(self.options):
            cfg = BLESSINGS.get(bid, {})
            cx = x0 + i * (card_w + gap)
            rect = arcade.XYWH(cx, y_center, card_w, card_h)
            self.option_rects.append((rect, bid))
            color = cfg.get("color", arcade.color.WHITE)
            # 卡底：悬停时提亮
            base = (58, 62, 78) if i != self._hover_index else (78, 86, 110)
            arcade.draw_rect_filled(rect, base)
            # 顶部主色带（实心，代替线框描边）
            arcade.draw_rect_filled(
                arcade.XYWH(cx, y_center + card_h // 2 - 6, card_w, 12), color)
            # 名称 + 描述 + 快捷键提示
            self._tc.text(f"bl_name_{i}", cfg.get("name", bid), cx, y_center + 40,
                          color, 24, anchor_x="center")
            self._tc.text(f"bl_desc_{i}", cfg.get("desc", ""), cx, y_center,
                          arcade.color.WHITE, 15, anchor_x="center")
            self._tc.text(f"bl_key_{i}", f"按 {i + 1} 选择", cx, y_center - 55,
                          arcade.color.LIGHT_GRAY, 14, anchor_x="center")

        # 跳过按钮（保留待选次数，下次触发或按面板继续；与 LevelUpView「稍后再选」同语义）
        arcade.draw_rect_filled(self.close_rect, arcade.color.DARK_BLUE)
        self._tc.text("bl_back", "稍后再选", self.close_rect.center_x, self.close_rect.center_y,
                      arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")

    # ── 交互 ──
    def on_mouse_motion(self, x, y, dx, dy):
        self._hover_index = -1
        for i, (rect, _bid) in enumerate(self.option_rects):
            if rect.point_in_rect((x, y)):
                self._hover_index = i
                break

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        for rect, bid in self.option_rects:
            if rect.point_in_rect((x, y)):
                sound_manager.play_ui()
                self._choose(bid)
                return
        if self.close_rect.point_in_rect((x, y)):
            sound_manager.play_ui()
            self._close(keep_closed=True)
            return

    def on_key_press(self, key, modifiers):
        # 数字键 1/2/3 直选（与卡片上的提示一致）
        for i, bid in enumerate(self.options):
            if key == getattr(arcade.key, f"KEY_{i + 1}", None):
                sound_manager.play_ui()
                self._choose(bid)
                return
        if key in (arcade.key.ESCAPE, arcade.key.TAB):
            self._close(keep_closed=True)

    # ── 逻辑 ──
    def _choose(self, blessing_id: str):
        """选中一条祝福：写入持有列表 → 一次性护盾 → 绝对重算 → 消费一次待选"""
        state = self._state()
        if state is None:
            self._close()
            return
        gs = self.window_ref.game_state
        if not state.add(blessing_id):
            # 不可叠加且已持有，或已达上限：本次不计次，让玩家重新在三张卡里选
            self._refresh_options()
            if not self.options:
                self._consume_and_advance()
            return

        player = getattr(self.game_view, "player", None)
        # 护盾是选中瞬间的一次性效果（不进 recompute）
        shield = state.grant_one_shot(player, blessing_id)
        # 立即生效：按「无祝福基准 + 全部祝福聚合」重算，可反复调用不漂移
        state.recompute(player, gs)

        cfg = BLESSINGS.get(blessing_id, {})
        if player is not None:
            from game.effects import floating_texts
            tip = cfg.get("name", blessing_id)
            if shield > 0:
                tip += f" +{int(shield)} 护盾"
            floating_texts.add(player.center_x, player.center_y + 60, tip,
                               cfg.get("color", arcade.color.GOLD), life=2.0, font_size=20)
        print(f"[Blessing] 选中 {blessing_id}，当前持有 {len(state.ids)} 条")

        self._consume_and_advance()

    def _consume_and_advance(self):
        """消费一次待选次数：仍有待选则继续下一组，否则复位面板标记并回游戏"""
        if self.game_view is not None:
            self.game_view.blessing_pending = max(0, self.game_view.blessing_pending - 1)
            self.window_ref.game_state.blessing_pending = self.game_view.blessing_pending
        self._refresh_options()
        if self.options:
            return  # 还有待选 → 留在面板里继续 3 选 1
        self._close()

    def _close(self, keep_closed: bool = False):
        """返回游戏（复用保存的 GameView 实例，不重建以免丢失局内状态）

        keep_closed=True 用于「稍后再选」：保持 GameView 的「面板已打开」标记，
        使 on_update 不会在同一帧立刻重弹；下一次触发点会重新置起标记。
        """
        if self.game_view is not None:
            if not keep_closed:
                # 本轮待选已消费完：复位标记，下一次触发才能再次自动弹出
                self.game_view._blessing_panel_open = False
            self.window.show_view(self.game_view)
        else:
            from views.game_view import GameView
            self.window.show_view(GameView(self.window_ref))
