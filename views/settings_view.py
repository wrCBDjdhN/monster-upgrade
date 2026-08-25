"""设置界面：主音量 / 静音开关 / 按键绑定（ESC 键打开）

- 打开时机：游戏内按 ESC（input_handler 处理，任何模式含观战均可打开）；
- 音量：横向滑块，点击/拖动调整 → 写 db settings + 即时应用 SoundManager；
- 静音：开关按钮切换 → 写 db + 应用 SoundManager；
- 按键绑定：列出全部动作与当前绑定键，点击动作行进入「重绑」状态，
  按下新键即保存（写 db）并刷新 GameView.key_bindings / PlayerController 移动键，
  立即生效无需重启；ESC 取消重绑；
- 恢复默认：一键还原 config.KEY_BINDINGS；
- 参考 LevelUpView 模式：全屏 View + TextCache 文本缓存 + XYWH 矩形按钮。
"""

import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, KEY_BINDINGS,
)
from views.text_cache import TextCache
from game.sound_manager import sound_manager


# 动作 → 中文名（设置界面按键列表显示）
_ACTION_NAMES = {
    "move_up": "向上移动", "move_down": "向下移动",
    "move_left": "向左移动", "move_right": "向右移动",
    "interact": "交互（E）", "skill": "技能（F）",
    "backpack": "背包/升级", "potion_1": "药水 1",
    "potion_2": "药水 2", "potion_3": "药水 3",
    "rocket_destroy": "发射台-炸毁", "rocket_evac": "发射台-撤离",
    "spectate": "观战切换", "minimap_zoom": "小地图缩放",
}


def _key_display(name: str) -> str:
    """arcade.key 属性名 → 人类可读键名（显示用）

    "KEY_1"→"1"、"KEYPAD_1"→"小键盘1"、"SPACE"→"空格"、"TAB"→"Tab"、
    "ESCAPE"→"Esc"；字母/功能键直接显示原名。
    """
    if name.startswith("KEYPAD_"):
        return "小键盘" + name[7:]
    if name.startswith("KEY_"):
        return name[4:]
    return {
        "SPACE": "空格", "TAB": "Tab", "ESCAPE": "Esc",
        "ENTER": "回车", "LSHIFT": "左Shift", "RSHIFT": "右Shift",
        "LCTRL": "左Ctrl", "RCTRL": "右Ctrl", "LALT": "左Alt", "RALT": "右Alt",
        "UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
        "MOUSE_BUTTON_LEFT": "鼠标左键", "MOUSE_BUTTON_RIGHT": "鼠标右键",
    }.get(name, name)


# 键码 → arcade.key 属性名（反向查找缓存；KEY_1/KEY_A 等别名取短名优先）
_KEY_NAME_CACHE: dict[int, str] = {}


def _key_name(code: int) -> str:
    """键码 → arcade.key 属性名（反向映射；无匹配返回十六进制键码）"""
    if not _KEY_NAME_CACHE:
        for name in dir(arcade.key):
            if name.startswith("_"):
                continue
            try:
                val = getattr(arcade.key, name)
            except Exception:
                continue
            if not isinstance(val, int):
                continue
            # 短名优先（"A" 优先于 "KEY_A"），避免反向映射落到 KEY_ 前缀别名
            if val not in _KEY_NAME_CACHE or len(name) < len(_KEY_NAME_CACHE[val]):
                _KEY_NAME_CACHE[val] = name
    return _KEY_NAME_CACHE.get(code, f"键{code}")


class SettingsView(arcade.View):
    """设置界面：音量 / 静音 / 按键绑定

    构造接收 game_view 引用：改键后刷新其 key_bindings 与 controller 移动键，
    返回游戏时直接 show_view 复用（不重建，保留局内状态）。
    """

    # 布局常量
    SLIDER_W = 320
    SLIDER_H = 14
    ROW_W = 230
    ROW_H = 32
    ROW_GAP = 6
    COLS = 2                      # 按键列表分两列
    COL_GAP = 40

    def __init__(self, window, game_view=None, from_game=True):
        super().__init__()
        self.window_ref = window
        self.game_view = game_view
        self._from_game = from_game  # True=游戏内ESC设置 / False=主页面设置
        self._tc = TextCache()
        # 音量/静音（db 读取，应用到 SoundManager）
        from db.database import get_volume, get_sound_enabled
        self._volume = get_volume()
        self._sound_enabled = get_sound_enabled()
        sound_manager.set_volume(self._volume)
        sound_manager.set_enabled(self._sound_enabled)
        # 按键绑定（db 读取，无记录回退默认）
        from db.database import get_key_bindings
        self._bindings: dict = get_key_bindings()
        # 当前正在重绑的动作名（None=未重绑；字符串=等待按新键）
        self._rebind_action: str | None = None
        # 交互矩形缓存（on_draw 重建，鼠标事件检测）
        self._slider_rect = arcade.XYWH(0, 0, self.SLIDER_W, self.SLIDER_H)
        self._mute_rect = arcade.XYWH(0, 0, 90, 34)
        self._reset_rect = arcade.XYWH(0, 0, 150, 40)
        self._close_rect = arcade.XYWH(0, 0, 140, 40)
        self._row_rects: dict[str, arcade.XYWH] = {}
        self._hover_row: str | None = None
        self._hover_btn: str | None = None   # "mute"/"reset"/"close"/"abandon"

    # ── 布局计算 ──
    def _layout(self):
        """计算各交互矩形位置（on_draw 时调用一次）"""
        cx = WINDOW_WIDTH // 2
        # 音量滑块：标题下方居中
        self._slider_rect = arcade.XYWH(cx - self.SLIDER_W // 2,
                                        WINDOW_HEIGHT - 190,
                                        self.SLIDER_W, self.SLIDER_H)
        # 静音开关：滑块右侧
        self._mute_rect = arcade.XYWH(cx + self.SLIDER_W // 2 + 20,
                                      WINDOW_HEIGHT - 190 + self.SLIDER_H // 2 - 17,
                                      90, 34)
        # 按键行：两列，从音量区下方排起
        actions = list(KEY_BINDINGS.keys())
        per_col = (len(actions) + self.COLS - 1) // self.COLS
        x0 = cx - (self.COLS * self.ROW_W + (self.COLS - 1) * self.COL_GAP) // 2 + self.ROW_W // 2
        y_top = WINDOW_HEIGHT - 270
        self._row_rects = {}
        for i, action in enumerate(actions):
            col = i // per_col
            row = i % per_col
            rx = x0 + col * (self.ROW_W + self.COL_GAP)
            ry = y_top - row * (self.ROW_H + self.ROW_GAP)
            self._row_rects[action] = arcade.XYWH(rx, ry, self.ROW_W, self.ROW_H)
        # 底部按钮：恢复默认 / 返回 / 放弃行动（仅游戏中）
        self._reset_rect = arcade.XYWH(cx - 240, 40, 150, 40)
        self._close_rect = arcade.XYWH(cx - 70, 40, 140, 40)
        self._abandon_rect = arcade.XYWH(cx + 110, 40, 150, 40)

    # ── 绘制 ──
    def on_show_view(self):
        arcade.set_background_color((25, 30, 40))

    def on_draw(self):
        self.clear()
        self._layout()
        gs = self.window_ref.game_state

        # 标题
        self._tc.text("set_title", "设 置", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
                      arcade.color.GOLD, 34, anchor_x="center")
        self._tc.text("set_sub", "调整音量与按键绑定（立即生效）", WINDOW_WIDTH // 2,
                      WINDOW_HEIGHT - 95, arcade.color.LIGHT_GRAY, 14, anchor_x="center")

        # 音量区
        self._tc.text("set_vol_label", "主音量", WINDOW_WIDTH // 2 - self.SLIDER_W // 2,
                      WINDOW_HEIGHT - 165, arcade.color.WHITE, 15, anchor_x="left")
        # 滑块轨道（不透明实心）+ 填充
        arcade.draw_rect_filled(self._slider_rect, (60, 65, 75))
        fill_w = self.SLIDER_W * self._volume
        if fill_w > 0:
            arcade.draw_rect_filled(
                arcade.XYWH(self._slider_rect.center_x - self.SLIDER_W // 2 + fill_w / 2,
                            self._slider_rect.center_y, fill_w, self.SLIDER_H),
                (90, 200, 120))
        # 滑块手柄（白色方块）
        handle_x = self._slider_rect.left + self.SLIDER_W * self._volume
        arcade.draw_rect_filled(arcade.XYWH(handle_x, self._slider_rect.center_y, 8, self.SLIDER_H + 8),
                                arcade.color.WHITE)
        # 音量百分比
        self._tc.text("set_vol_pct", f"{int(self._volume * 100)}%",
                      WINDOW_WIDTH // 2 + self.SLIDER_W // 2 + 20,
                      WINDOW_HEIGHT - 165, arcade.color.CYAN, 15, anchor_x="left")

        # 静音开关
        muted = not self._sound_enabled
        base = (120, 60, 60) if muted else (60, 120, 80)
        if self._hover_btn == "mute":
            base = tuple(min(255, c + 25) for c in base)
        arcade.draw_rect_filled(self._mute_rect, base)
        self._tc.text("set_mute", "静音" if not muted else "已静音",
                      self._mute_rect.center_x, self._mute_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

        # 按键绑定列表
        self._tc.text("set_keys_title", "按键绑定（点击动作后按新键）",
                      WINDOW_WIDTH // 2, WINDOW_HEIGHT - 235,
                      arcade.color.YELLOW, 15, anchor_x="center")
        for action, rect in self._row_rects.items():
            name = _ACTION_NAMES.get(action, action)
            rebinding = self._rebind_action == action
            hover = self._hover_row == action
            # 行底色：重绑=亮橙、悬停=灰蓝、普通=深灰
            if rebinding:
                base = (180, 120, 40)
            elif hover:
                base = (80, 90, 110)
            else:
                base = (55, 60, 72)
            arcade.draw_rect_filled(rect, base)
            # 动作名（左）+ 当前键名（右，重绑时闪烁提示）
            self._tc.text(f"key_{action}", name,
                          rect.left + 10, rect.center_y,
                          arcade.color.WHITE, 13, anchor_x="left", anchor_y="center")
            if rebinding:
                blink = (getattr(self.game_view, "_frame", 0) // 20) % 2 == 0 if self.game_view else True
                keys_txt = "请按新键..." if blink else "请按新键..."
                key_color = arcade.color.YELLOW
            else:
                keys = self._bindings.get(action, [])
                keys_txt = " / ".join(_key_display(k) for k in keys) or "未绑定"
                key_color = arcade.color.CYAN
            self._tc.text(f"keyval_{action}", keys_txt,
                          rect.right - 10, rect.center_y,
                          key_color, 12, anchor_x="right", anchor_y="center")

        # 底部按钮：恢复默认 / 返回 / 放弃行动
        arcade.draw_rect_filled(self._reset_rect, (70, 60, 90) if self._hover_btn != "reset" else (100, 85, 130))
        self._tc.text("set_reset", "恢复默认键位", self._reset_rect.center_x, self._reset_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(self._close_rect, arcade.color.DARK_BLUE if self._hover_btn != "close" else (70, 100, 160))
        self._tc.text("set_close", "返回游戏", self._close_rect.center_x, self._close_rect.center_y,
                      arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")
        # 放弃行动按钮：仅在游戏内显示，视为撤离失败
        # 主页面设置显示"退出游戏"，直接关闭窗口
        if self.game_view is not None:
            abandon_color = (140, 40, 40) if self._hover_btn != "abandon" else (180, 55, 55)
            arcade.draw_rect_filled(self._abandon_rect, abandon_color)
            self._tc.text("set_abandon", "放弃行动", self._abandon_rect.center_x, self._abandon_rect.center_y,
                          arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")
        elif not self._from_game:
            quit_color = (120, 40, 40) if self._hover_btn != "abandon" else (160, 55, 55)
            arcade.draw_rect_filled(self._abandon_rect, quit_color)
            self._tc.text("set_quit", "退出游戏", self._abandon_rect.center_x, self._abandon_rect.center_y,
                          arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")

        # 底部提示（ESC 关闭）
        self._tc.text("set_esc_hint", "ESC 关闭设置（重绑中按 ESC 取消）",
                      WINDOW_WIDTH // 2, 95, arcade.color.GRAY, 12, anchor_x="center")

    # ── 鼠标交互 ──
    def on_mouse_motion(self, x, y, dx, dy):
        self._hover_row = None
        self._hover_btn = None
        if self._mute_rect.point_in_rect((x, y)):
            self._hover_btn = "mute"
        elif self._reset_rect.point_in_rect((x, y)):
            self._hover_btn = "reset"
        elif self._close_rect.point_in_rect((x, y)):
            self._hover_btn = "close"
        elif (self.game_view is not None or not self._from_game) and self._abandon_rect.point_in_rect((x, y)):
            self._hover_btn = "abandon"
        else:
            for action, rect in self._row_rects.items():
                if rect.point_in_rect((x, y)):
                    self._hover_row = action
                    break

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        # 音量滑块：点击即设置音量
        if self._slider_rect.point_in_rect((x, y)):
            self._set_volume(max(0.0, min(1.0, (x - self._slider_rect.left) / self.SLIDER_W)))
            return
        # 静音开关
        if self._mute_rect.point_in_rect((x, y)):
            self._set_sound_enabled(not self._sound_enabled)
            return
        # 恢复默认键位
        if self._reset_rect.point_in_rect((x, y)):
            from db.database import reset_key_bindings
            self._bindings = reset_key_bindings()
            self._rebind_action = None
            self._apply_bindings_to_game()
            return
        # 返回游戏
        if self._close_rect.point_in_rect((x, y)):
            self._close()
            return
        # 放弃行动（游戏内）：视为撤离失败，清空装备后回主页面
        if self.game_view is not None and self._abandon_rect.point_in_rect((x, y)):
            self.game_view._fail_run("放弃行动")
            return
        # 退出游戏（主页面设置）：直接关闭窗口
        if not self._from_game and self._abandon_rect.point_in_rect((x, y)):
            self.window.close()
            return
        # 点击动作行：进入重绑状态（再点同一行取消）
        for action, rect in self._row_rects.items():
            if rect.point_in_rect((x, y)):
                self._rebind_action = None if self._rebind_action == action else action
                return

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        # 按住滑块拖动实时调音量（x 限制在滑块范围内）
        if buttons & arcade.MOUSE_BUTTON_LEFT and self._slider_rect.point_in_rect((x, y)):
            self._set_volume(max(0.0, min(1.0, (x - self._slider_rect.left) / self.SLIDER_W)))

    # ── 键盘交互 ──
    def on_key_press(self, key, modifiers):
        # 重绑中：任意键（除 ESC=取消）绑定到当前动作
        if self._rebind_action:
            if key == arcade.key.ESCAPE:
                self._rebind_action = None
                return
            new_name = _key_name(key)
            # 防止绑定到修饰键（Shift/Ctrl/Alt 单独按下无意义，键码是修饰键码）
            if new_name in ("LSHIFT", "RSHIFT", "LCTRL", "RCTRL", "LALT", "RALT"):
                return
            self._bindings[self._rebind_action] = [new_name]
            from db.database import set_key_bindings
            set_key_bindings(self._bindings)
            self._apply_bindings_to_game()
            self._rebind_action = None
            return
        # 未重绑：ESC 关闭设置
        if key == arcade.key.ESCAPE:
            self._close()

    # ── 逻辑 ──
    def _set_volume(self, vol: float):
        """更新音量：写 db + 即时应用 SoundManager"""
        self._volume = vol
        from db.database import set_volume
        set_volume(vol)
        sound_manager.set_volume(vol)

    def _set_sound_enabled(self, enabled: bool):
        """更新静音开关：写 db + 即时应用 SoundManager"""
        self._sound_enabled = enabled
        from db.database import set_sound_enabled
        set_sound_enabled(enabled)
        sound_manager.set_enabled(enabled)

    def _apply_bindings_to_game(self):
        """把最新绑定刷新到 GameView 与 PlayerController（立即生效）"""
        gv = self.game_view
        if gv is None:
            return
        gv.key_bindings = dict(self._bindings)
        if gv.controller:
            gv.controller.refresh_bindings(self._bindings)

    def _close(self):
        """返回游戏（复用保存的 GameView 实例，避免重建丢失局内状态）"""
        self._rebind_action = None
        if self.game_view:
            self.window.show_view(self.game_view)
        else:
            from views.game_view import GameView
            self.window.show_view(GameView(self.window_ref))