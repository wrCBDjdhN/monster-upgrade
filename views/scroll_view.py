"""可滚动视图公共基类

提取 market_view / forge_view / warehouse_view / backpack_view 共享的：
- 滚轮滚动 + offset 管理
- content_height 追踪
- 返回按钮（→ StartView）
- 背景色设置
- 滚动限制计算
"""

import arcade
from config import WINDOW_HEIGHT


class ScrollView(arcade.View):
    """可滚动页面的公共基类，子类只需重写 on_draw 和 on_mouse_press 中的业务逻辑。"""

    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        # 滚动状态
        self.scroll_offset = 0.0
        self.content_height = 0.0
        # 底部「返回」按钮
        self.back_rect = arcade.XYWH(80, 40, 100, 36)

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
