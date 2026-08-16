"""可滚动视图公共基类

提取 market_view / forge_view / warehouse_view / backpack_view 共享的：
- 滚轮滚动 + offset 管理
- content_height 追踪
- 返回按钮（→ StartView）
- 背景色设置
- 滚动限制计算
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT


class ScrollView(arcade.View):
    """可滚动页面的公共基类，子类只需重写 on_draw 和 on_mouse_press 中的业务逻辑。"""

    # 右侧滚动条几何常量（与各子类绘制/命中保持一致）
    SCROLLBAR_X = WINDOW_WIDTH - 12      # 滚动条手柄中心 x
    SCROLLBAR_W = 6                      # 手柄宽度
    SCROLLBAR_HIT_W = 24                 # 命中宽度（便于鼠标点击拖拽）
    SCROLLBAR_BOTTOM = 60                # 轨道底部 y（导航按钮上方）

    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        # 滚动状态
        self.scroll_offset = 0.0
        self.content_height = 0.0
        # 底部「返回」按钮
        self.back_rect = arcade.XYWH(80, 40, 100, 36)
        # 滚动条拖拽状态：True = 鼠标按住手柄拖动中
        self._scroll_dragging = False
        # 拖拽使用的轨道顶部 y（与 draw_scrollbar 传入的 track_top 保持一致）
        self._scrollbar_track_top = None

    # ── 生命周期 ──────────────────────────────────────────────

    def on_show_view(self):
        """子类可重写 get_bg_color() 来自定义背景色。"""
        self.window.background_color = self.get_bg_color()

    def get_bg_color(self) -> tuple:
        """返回背景色 RGB 元组。子类重写此方法即可。"""
        return (25, 30, 40)

    # ── 滚动 ─────────────────────────────────────────────────

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """通用滚轮处理：scroll_y > 0 表示向上滚动（内容下移）。"""
        self.scroll_offset -= scroll_y * 30
        self.clamp_scroll()

    def clamp_scroll(self):
        """将 scroll_offset 限制在 [0, max_scroll] 范围内。"""
        max_scroll = max(0, self.content_height - WINDOW_HEIGHT + 80)
        self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))

    # ── 滚动条（拖拽） ────────────────────────────────────────

    def _scrollbar_max_scroll(self) -> float:
        """滚动条使用的最大滚动量（与滚轮 clamp_scroll 保持一致）。"""
        return max(0, self.content_height - WINDOW_HEIGHT + 80)

    def _scrollbar_handle_h(self, track_top=None) -> float:
        """滚动条手柄高度：随内容高度自适应缩小，最小 30px。"""
        view_h = (track_top or self.content_top) - self.SCROLLBAR_BOTTOM
        return max(30, min(view_h, view_h * view_h / max(1, self.content_height)))

    def _scrollbar_handle_y(self, track_top=None) -> float:
        """滚动条手柄中心 y（屏幕坐标），按 scroll_offset 比例定位。"""
        view_h = (track_top or self.content_top) - self.SCROLLBAR_BOTTOM
        bar_h = self._scrollbar_handle_h(track_top)
        max_scroll = self._scrollbar_max_scroll()
        ratio = 0.0 if max_scroll <= 0 else self.scroll_offset / max_scroll
        return (track_top or self.content_top) - bar_h - (view_h - bar_h) * ratio

    def draw_scrollbar(self, track_top=None):
        """绘制右侧滚动条（轨道 + 手柄）。子类在 on_draw 末尾调用即可。

        track_top：可滚动内容区顶部 y（默认取基类 content_top 属性）。
        """
        if track_top is None:
            track_top = self.content_top
        if self.content_height <= track_top - self.SCROLLBAR_BOTTOM:
            return  # 内容未超出一屏，无需滚动条
        track_bottom = self.SCROLLBAR_BOTTOM
        # 轨道（细长条）
        arcade.draw_rect_filled(
            arcade.XYWH(self.SCROLLBAR_X, (track_top + track_bottom) / 2, 3,
                        track_top - track_bottom),
            (70, 70, 80),
        )
        # 手柄
        bar_h = self._scrollbar_handle_h(track_top)
        bar_y = self._scrollbar_handle_y(track_top)
        color = (180, 180, 190) if self._scroll_dragging else (140, 140, 150)
        arcade.draw_rect_filled(
            arcade.XYWH(self.SCROLLBAR_X, bar_y, self.SCROLLBAR_W, bar_h), color,
        )

    def _scrollbar_hit(self, x, y, track_top=None) -> bool:
        """判断点 (x,y) 是否落在滚动条命中区域（轨道附近加宽，便于点击）。"""
        if track_top is None:
            track_top = self.content_top
        if self.content_height <= track_top - self.SCROLLBAR_BOTTOM:
            return False
        if abs(x - self.SCROLLBAR_X) > self.SCROLLBAR_HIT_W / 2:
            return False
        return self.SCROLLBAR_BOTTOM - 15 <= y <= track_top + 15

    def start_scroll_drag(self, x, y, track_top=None) -> bool:
        """尝试开始拖拽滚动条：命中则进入拖拽状态并立即定位，返回 True。"""
        if track_top is None:
            track_top = self.content_top
        if not self._scrollbar_hit(x, y, track_top):
            return False
        self._scroll_dragging = True
        # 记录本次拖拽的轨道顶部，保证 update_scroll_drag 与绘制一致
        self._scrollbar_track_top = track_top
        self.update_scroll_drag(x, y)
        return True

    def update_scroll_drag(self, x, y):
        """拖拽中：按鼠标 y 与轨道比例换算 scroll_offset。"""
        if not self._scroll_dragging:
            return
        track_top = self._scrollbar_track_top or self.content_top
        view_h = track_top - self.SCROLLBAR_BOTTOM
        bar_h = self._scrollbar_handle_h(track_top)
        max_scroll = self._scrollbar_max_scroll()
        if view_h - bar_h <= 0 or max_scroll <= 0:
            return
        # 手柄中心随鼠标移动：手柄顶部对应 offset=0，底部对应 offset=max_scroll
        ratio = (y - self.SCROLLBAR_BOTTOM - bar_h / 2) / (view_h - bar_h)
        self.scroll_offset = max(0, min(max_scroll, ratio * max_scroll))

    def end_scroll_drag(self):
        """结束滚动条拖拽。"""
        self._scroll_dragging = False
        self._scrollbar_track_top = None

    def on_mouse_motion(self, x, y, dx, dy):
        """基类默认：拖拽滚动条时跟随鼠标。子类若重写需自行调用 update_scroll_drag。"""
        if self._scroll_dragging:
            self.update_scroll_drag(x, y)

    def on_mouse_release(self, x, y, button, modifiers):
        """基类默认：松开鼠标结束滚动条拖拽。子类若重写需自行调用 end_scroll_drag。"""
        self.end_scroll_drag()

    # ── 点击 ─────────────────────────────────────────────────

    def handle_back_click(self, x, y) -> bool:
        """检测返回按钮点击，命中则跳转 StartView 并返回 True。

        联机模式（net_mode != solo）下返回 LobbyView 复用连接（房间保持），
        单机保持原 StartView 行为。
        """
        if self.back_rect.point_in_rect((x, y)):
            gs = self.window.game_state
            if getattr(gs, "net_mode", "solo") != "solo":
                from views.lobby_view import LobbyView
                self.window.show_view(LobbyView(self.window_ref))
            else:
                from views.start_view import StartView
                self.window.show_view(StartView(self.window_ref))
            return True
        return False

    # ── 工具 ─────────────────────────────────────────────────

    @property
    def content_top(self) -> float:
        """可滚动内容区域的顶部 Y 坐标（屏幕坐标）。"""
        return WINDOW_HEIGHT - 120

    def world_y(self, logical_y: float) -> float:
        """将逻辑 Y 坐标（从 content_top 向下递减）转换为屏幕 Y 坐标。"""
        return self.content_top + logical_y + self.scroll_offset
