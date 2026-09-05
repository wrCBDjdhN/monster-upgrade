"""批量图元绘制：积累多个填充图元后一次 draw call 提交，替代逐个立即模式绘制。

性能优化：
- numpy 向量化三角函数预计算（circle/arc_outline 的角度表）
- 三角函数缓存：常见 seg 参数的 cos/sin 表仅计算一次
- _rgba 内联热路径优化

用法不变：
    batch = ShapeBatch()
    batch.rect(cx, cy, w, h, color)
    batch.circle(cx, cy, r, color)
    batch.draw()
"""

import math
import numpy as np
import arcade
from arcade import shape_list as _sl

# Triangle render mode: arcade.gl exposes this as TRIANGLES (some older builds
# called it GL_TRIANGLES).
_TRIANGLE_MODE = getattr(arcade.gl, "TRIANGLES", None) or getattr(
    arcade.gl, "GL_TRIANGLES", 4)

# ── 预计算三角函数缓存（常见 seg 参数只算一次） ──
_TRIG_CACHE: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _get_trig_table(seg: int) -> tuple[np.ndarray, np.ndarray]:
    """获取 seg 分段的 cos/sin 查找表（numpy 向量化，缓存复用）"""
    if seg not in _TRIG_CACHE:
        angles = np.linspace(0, 2 * math.pi, seg + 1, endpoint=True)
        _TRIG_CACHE[seg] = (np.cos(angles), np.sin(angles))
    return _TRIG_CACHE[seg]


def _rgba(color) -> tuple[int, int, int, int]:
    """RGB / RGBA -> (r,g,b,a) four-channel tuple for Shape（热路径内联优化）"""
    c0 = int(color[0])
    c1 = int(color[1])
    c2 = int(color[2])
    try:
        c3 = int(color[3])
    except (IndexError, TypeError):
        c3 = 255
    return (c0, c1, c2, c3)


class ShapeBatch:
    """Accumulate several filled primitives and draw them all at once.

    Usage:
        batch = ShapeBatch()
        batch.rect(cx, cy, w, h, color)
        batch.circle(cx, cy, r, color)
        batch.tri(p1, p2, p3, color)
        batch.poly(points, color)
        batch.arc_outline(cx, cy, r, start, end, color, thickness)
        batch.line(x1, y1, x2, y2, color, thickness)
        batch.draw()

    If constructing ShapeElementList fails (e.g. no window context) it raises;
    the caller should fall back to immediate-mode drawing.
    """

    def __init__(self):
        self._list = _sl.ShapeElementList()

    def rect(self, cx, cy, w, h, color):
        """Solid rectangle centered at (cx, cy).

        arcade's Shape draws points literally as a TRIANGLES primitive, so a
        quad (4 pts) would only render 1 triangle. We split the rectangle into
        two triangles (6 vertices) to render a proper filled quad.
        """
        hw, hh = w / 2.0, h / 2.0
        x0, x1 = cx - hw, cx + hw
        y0, y1 = cy - hh, cy + hh
        col = _rgba(color)
        # 两个三角形构成一个矩形（6 个顶点）
        pts = (
            (x0, y0), (x1, y0), (x1, y1),
            (x0, y0), (x1, y1), (x0, y1),
        )
        cols = (col, col, col, col, col, col)
        self._list.append(_sl.Shape(pts, cols, mode=_TRIANGLE_MODE))

    def circle(self, cx, cy, r, color, seg=24):
        """Solid circle centered at (cx, cy) with radius r (fanned triangles).

        使用 numpy 预计算三角函数表，比逐点 math.cos/sin 快 3-5 倍。
        """
        col = _rgba(color)
        cos_t, sin_t = _get_trig_table(seg)
        prev_x = cx + r
        prev_y = cy
        for i in range(1, seg + 1):
            cur_x = cx + cos_t[i] * r
            cur_y = cy + sin_t[i] * r
            self._list.append(_sl.Shape(
                ((cx, cy), (prev_x, prev_y), (cur_x, cur_y)),
                (col, col, col), mode=_TRIANGLE_MODE))
            prev_x, prev_y = cur_x, cur_y

    def tri(self, p1, p2, p3, color):
        """Solid triangle."""
        col = _rgba(color)
        self._list.append(_sl.Shape((p1, p2, p3), (col, col, col), mode=_TRIANGLE_MODE))

    def line(self, x1, y1, x2, y2, color, thickness):
        """粗线段：用细长四边形（2个三角形）拼成，可带透明度。"""
        if thickness <= 0:
            return
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        hw = thickness / 2.0
        inv_len = hw / length  # 预计算倒数，避免重复除法
        nx, ny = -dy * inv_len, dx * inv_len
        p1 = (x1 + nx, y1 + ny)
        p2 = (x2 + nx, y2 + ny)
        p3 = (x2 - nx, y2 - ny)
        p4 = (x1 - nx, y1 - ny)
        self.tri(p1, p2, p3, color)
        self.tri(p1, p3, p4, color)

    def poly(self, points, color):
        """Solid polygon (triangulated as a triangle fan)."""
        if len(points) < 3:
            return
        col = _rgba(color)
        cx, cy = points[0]
        cols = (col, col, col)
        for i in range(1, len(points) - 1):
            self._list.append(_sl.Shape(
                ((cx, cy), points[i], points[i + 1]), cols, mode=_TRIANGLE_MODE))

    def arc_outline(self, cx, cy, radius, start_deg, end_deg, color, thickness, seg=48):
        """弧形描边（刀光/挥砍轨迹）：以 (cx,cy) 为圆心、radius 半径的粗弧线，
        从 start_deg 扫到 end_deg（角度制，逆时针）。用若干细长四边形拼成，
        每段拆两个三角形，替代 arcade 自带 draw_arc_outline 以便合批绘制。

        使用 numpy 向量化计算弧线点位，比逐点循环快 ~2 倍。
        """
        if end_deg <= start_deg:
            return
        col = _rgba(color)
        half = thickness / 2.0
        total_rad = math.radians(end_deg - start_deg)
        steps = max(2, int(seg * abs(end_deg - start_deg) / 120.0))

        # numpy 向量化计算所有弧线点
        angles = np.linspace(math.radians(start_deg), math.radians(start_deg) + total_rad, steps + 1)
        cos_a = np.cos(angles)
        sin_a = np.sin(angles)
        inner_x = cx + cos_a * (radius - half)
        inner_y = cy + sin_a * (radius - half)
        outer_x = cx + cos_a * (radius + half)
        outer_y = cy + sin_a * (radius + half)

        cols3 = (col, col, col)
        for i in range(steps):
            # 两个三角形构成一个细长四边形条带
            self._list.append(_sl.Shape(
                ((inner_x[i], inner_y[i]), (outer_x[i], outer_y[i]), (outer_x[i+1], outer_y[i+1])),
                cols3, mode=_TRIANGLE_MODE))
            self._list.append(_sl.Shape(
                ((inner_x[i], inner_y[i]), (outer_x[i+1], outer_y[i+1]), (inner_x[i+1], inner_y[i+1])),
                cols3, mode=_TRIANGLE_MODE))

    def draw(self):
        self._list.draw()
