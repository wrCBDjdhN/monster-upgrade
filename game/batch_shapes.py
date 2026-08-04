import math
import arcade
from arcade import shape_list as _sl

# Triangle render mode: arcade.gl exposes this as TRIANGLES (some older builds
# called it GL_TRIANGLES).
_TRIANGLE_MODE = getattr(arcade.gl, "TRIANGLES", None) or getattr(
    arcade.gl, "GL_TRIANGLES", 4)


def _rgba(color):
    """RGB / RGBA -> (r,g,b,a) four-channel tuple for Shape."""
    if len(color) == 4:
        return (int(color[0]), int(color[1]), int(color[2]), int(color[3]))
    return (int(color[0]), int(color[1]), int(color[2]), 255)


class ShapeBatch:
    """Accumulate several filled primitives and draw them all at once.

    Usage:
        batch = ShapeBatch()
        batch.rect(cx, cy, w, h, color)
        batch.circle(cx, cy, r, color)
        batch.tri(p1, p2, p3, color)
        batch.poly(points, color)
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
        # two triangles: (x0,y0)-(x1,y0)-(x1,y1) and (x0,y0)-(x1,y1)-(x0,y1)
        pts = (
            (x0, y0), (x1, y0), (x1, y1),
            (x0, y0), (x1, y1), (x0, y1),
        )
        col = _rgba(color)
        cols = (col, col, col, col, col, col)
        self._list.append(_sl.Shape(pts, cols, mode=_TRIANGLE_MODE))

    def circle(self, cx, cy, r, color, seg=24):
        """Solid circle centered at (cx, cy) with radius r (fanned triangles)."""
        col = _rgba(color)
        prev = (cx + r, cy)
        for i in range(1, seg + 1):
            a = 2 * math.pi * i / seg
            cur = (cx + math.cos(a) * r, cy + math.sin(a) * r)
            cols = (col, col, col)
            self._list.append(_sl.Shape(((cx, cy), prev, cur), cols, mode=_TRIANGLE_MODE))
            prev = cur

    def tri(self, p1, p2, p3, color):
        """Solid triangle."""
        col = _rgba(color)
        cols = (col, col, col)
        self._list.append(_sl.Shape((p1, p2, p3), cols, mode=_TRIANGLE_MODE))

    def line(self, x1, y1, x2, y2, color, thickness):
        """粗线段：用细长四边形（2个三角形）拼成，可带透明度。"""
        if thickness <= 0:
            return
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        hw = thickness / 2.0
        nx, ny = -dy / length * hw, dx / length * hw  # 垂直方向半宽
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
        for i in range(1, len(points) - 1):
            p1, p2 = points[i], points[i + 1]
            cols = (col, col, col)
            self._list.append(_sl.Shape(((cx, cy), p1, p2), cols, mode=_TRIANGLE_MODE))

    def arc_outline(self, cx, cy, radius, start_deg, end_deg, color, thickness, seg=48):
        """弧形描边（刀光/挥砍轨迹）：以 (cx,cy) 为圆心、radius 半径的粗弧线，
        从 start_deg 扫到 end_deg（角度制，逆时针）。用若干细长四边形拼成，
        每段拆两个三角形，替代 arcade 自带 draw_arc_outline 以便合批绘制。"""
        if end_deg <= start_deg:
            return
        col = _rgba(color)
        half = thickness / 2.0
        total = math.radians(end_deg - start_deg)
        # 弧长越大分段越多，保证平滑
        steps = max(2, int(seg * abs(end_deg - start_deg) / 120.0))
        prev_in = prev_out = None
        for i in range(steps + 1):
            a = math.radians(start_deg) + total * i / steps
            cos_a, sin_a = math.cos(a), math.sin(a)
            cur_in = (cx + cos_a * (radius - half), cy + sin_a * (radius - half))
            cur_out = (cx + cos_a * (radius + half), cy + sin_a * (radius + half))
            if prev_in is not None:
                # 两个三角形构成一个细长四边形条带
                self._list.append(_sl.Shape(
                    (prev_in, prev_out, cur_out), (col, col, col), mode=_TRIANGLE_MODE))
                self._list.append(_sl.Shape(
                    (prev_in, cur_out, cur_in), (col, col, col), mode=_TRIANGLE_MODE))
            prev_in, prev_out = cur_in, cur_out

    def draw(self):
        self._list.draw()
