"""持久 arcade.Text 对象缓存工具

将 arcade.draw_text 全面替换为持久 arcade.Text 对象：
- 同一 key 复用同一个 Text 对象，仅在文本/颜色/字号/对齐/加粗变化时重建纹理，
  避免 draw_text 每帧重建纹理导致的卡顿与 PerformanceWarning 警告。
- 调用方式与 arcade.draw_text 参数一致（text/x/y/color/font_size/anchor 等）。
- 动态列表（滚动视图）建议用「前缀+索引」作为 key；内容结构变化时调用 clear()。
"""

import arcade


class TextCache:
    """按 key 复用 arcade.Text 对象，用法兼容 arcade.draw_text 常用参数。"""

    def __init__(self):
        self._texts: dict = {}  # key -> arcade.Text

    def clear(self):
        """清空全部缓存。内容结构变化（列表增删）时调用，避免旧 key 残留。"""
        self._texts.clear()

    def text(self, key, text, x, y, color, size=12, anchor_x="left",
             anchor_y="baseline", bold=False):
        """获取/创建并绘制文本，参数含义与 arcade.draw_text 保持一致。

        样式（size/color/bold/anchor）变化时重建 Text；仅文本或位置变化时
        只更新 value/position，复用已有纹理，性能远高于 draw_text。
        """
        t = self._texts.get(key)
        if t is None or getattr(t, "_tc_size", None) != size \
                or getattr(t, "_tc_color", None) != color \
                or getattr(t, "_tc_bold", None) != bold \
                or getattr(t, "_tc_anchor_x", None) != anchor_x \
                or getattr(t, "_tc_anchor_y", None) != anchor_y:
            t = arcade.Text(text, x, y, color, size,
                            anchor_x=anchor_x, anchor_y=anchor_y, bold=bold)
            t._tc_size = size
            t._tc_color = color
            t._tc_bold = bold
            t._tc_anchor_x = anchor_x
            t._tc_anchor_y = anchor_y
            self._texts[key] = t
        if t.value != text:
            t.value = text
        t.position = (x, y)
        t.draw()
