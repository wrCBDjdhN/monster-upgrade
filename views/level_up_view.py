"""升级面板：角色升级后 3 选 1 永久属性加成

- 打开时机：经验达标升级后（game_view._level_data["pending_choices"] > 0），
  按 TAB 打开（input_handler 优先判断，见 game/input_handler.py）；TAB/ESC 或
  「返回游戏」按钮关闭，未选完的待选升级保留（连升可累积，下次 TAB 继续选）；
- 选择即生效：调用 db.database.choose_bonus 写库（pending_choices -1，加成列 +1），
  并将本次加成即时应用到玩家实体/GameState（与 game_view.setup() 应用永久加成同口径）；
- 参考 BackpackView 模式：全屏 View + TextCache 文本缓存 + XYWH 矩形按钮。
"""

import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, PLAYER_SPEED,
    exp_needed_for_level, roll_level_up_options,
)
from views.text_cache import TextCache
from game.sound_manager import sound_manager


class LevelUpView(arcade.View):
    """升级面板：展示当前等级/经验，提供 3 个永久加成选项（点击一个即应用）"""

    def __init__(self, window, game_view=None):
        super().__init__()
        self.window_ref = window
        self.game_view = game_view  # 保存当前 GameView 引用：返回时不重建 + 即时应用加成
        self._tc = TextCache()      # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        self.options = []           # 当前 3 个候选加成（roll_level_up_options 结果，元素为 LEVEL_BONUS_POOL 条目）
        self.option_rects = []      # [(rect, option_dict), ...]：on_draw 重建，on_mouse_press 检测
        self.close_rect = arcade.XYWH(WINDOW_WIDTH // 2, 40, 140, 40)
        self._hover_index = -1      # 当前悬停选项下标（-1=无），绘制高亮
        self._refresh_options()

    # ── 数据读取 ──
    def _level_info(self):
        """返回 (等级, 当前经验, 升级所需经验)；无缓存时按 Lv.1 默认显示"""
        ld = getattr(self.game_view, "_level_data", None) or {}
        level = ld.get("level", 1)
        exp = ld.get("exp", 0)
        return level, exp, exp_needed_for_level(level)

    def _refresh_options(self):
        """按当前待选升级次数掷 3 个候选；无待选则清空（面板随即关闭）"""
        ld = getattr(self.game_view, "_level_data", None) or {}
        if ld.get("pending_choices", 0) > 0:
            self.options = roll_level_up_options(3)
        else:
            self.options = []

    # ── 绘制 ──
    def on_show_view(self):
        arcade.set_background_color((25, 30, 40))

    def on_draw(self):
        self.clear()
        gs = self.window_ref.game_state
        level, exp, need = self._level_info()

        # 标题
        self._tc.text("lvl_title", "升 级 ！", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 70,
                      arcade.color.GOLD, 36, anchor_x="center")
        # 角色信息 + 等级/经验
        self._tc.text("lvl_char", f"角色等级 Lv.{level}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 115,
                      arcade.color.WHITE, 20, anchor_x="center")
        exp_txt = "已满级" if need == 0 else f"经验 {exp}/{need}"
        self._tc.text("lvl_exp", exp_txt, WINDOW_WIDTH // 2, WINDOW_HEIGHT - 145,
                      arcade.color.LIGHT_GRAY, 14, anchor_x="center")

        # 经验条（未满级时绘制进度）
        if need > 0:
            bar_w, bar_h = 420, 12
            bx = WINDOW_WIDTH // 2 - bar_w // 2
            by = WINDOW_HEIGHT - 165
            arcade.draw_rect_filled(arcade.XYWH(WINDOW_WIDTH // 2, by + bar_h // 2, bar_w, bar_h),
                                    (60, 60, 70))
            fill = min(1.0, exp / need)
            if fill > 0:
                arcade.draw_rect_filled(
                    arcade.XYWH(bx + bar_w * fill / 2, by + bar_h // 2, bar_w * fill, bar_h),
                    (90, 200, 120))

        # 提示语
        self._tc.text("lvl_hint", "选择一个永久属性加成", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 200,
                      arcade.color.YELLOW, 16, anchor_x="center")

        # 3 个加成卡片（横排）
        self.option_rects = []
        card_w, card_h = 240, 180
        gap = 30
        total_w = len(self.options) * card_w + (len(self.options) - 1) * gap
        x0 = WINDOW_WIDTH // 2 - total_w // 2 + card_w // 2
        y_center = WINDOW_HEIGHT // 2
        for i, opt in enumerate(self.options):
            cx = x0 + i * (card_w + gap)
            rect = arcade.XYWH(cx, y_center, card_w, card_h)
            self.option_rects.append((rect, opt))
            # 卡片底 + 悬停高亮
            base = (70, 75, 90) if i != self._hover_index else (95, 105, 130)
            arcade.draw_rect_filled(rect, base)
            arcade.draw_rect_outline(rect, opt.get("color", arcade.color.WHITE), 3)
            # 加成名称（带颜色）+ 描述 + 数值
            self._tc.text(f"opt_name_{i}", opt.get("name", ""), cx, y_center + 45,
                          opt.get("color", arcade.color.WHITE), 22, anchor_x="center")
            self._tc.text(f"opt_desc_{i}", opt.get("desc", ""), cx, y_center + 5,
                          arcade.color.LIGHT_GRAY, 15, anchor_x="center")
            self._tc.text(f"opt_val_{i}", opt.get("desc", ""), cx, y_center - 45,
                          opt.get("color", arcade.color.WHITE), 14, anchor_x="center")

        # 返回按钮
        arcade.draw_rect_filled(self.close_rect, arcade.color.DARK_BLUE)
        self._tc.text("nav_back", "稍后再选", self.close_rect.center_x, self.close_rect.center_y,
                      arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")

    # ── 交互 ──
    def on_mouse_motion(self, x, y, dx, dy):
        self._hover_index = -1
        for i, (rect, _opt) in enumerate(self.option_rects):
            if rect.point_in_rect((x, y)):
                self._hover_index = i
                break

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return
        sound_manager.play_ui()
        # 点击加成卡片：写库 + 应用 + 刷新（仍有待选则继续下一组）
        for rect, opt in self.option_rects:
            if rect.point_in_rect((x, y)):
                self._choose_bonus(opt)
                return
        # 返回按钮：关闭面板（未选完的待选升级保留，下次 TAB 继续）
        if self.close_rect.point_in_rect((x, y)):
            self._close()
            return

    def on_key_press(self, key, modifiers):
        # TAB/ESC 关闭面板（与 input_handler TAB 打开对称）
        if key in (arcade.key.TAB, arcade.key.ESCAPE):
            self._close()

    # ── 逻辑 ──
    def _choose_bonus(self, opt):
        """选择并应用一个永久加成：写库 + 即时生效 + 刷新缓存"""
        gs = self.window_ref.game_state
        pid = gs.player_id
        if not pid:
            self._close()
            return
        from db.database import choose_bonus, get_character_levels
        cid = getattr(gs, "character_id", "initial") or "initial"
        choose_bonus(pid, cid, opt["key"])
        # 刷新等级数据缓存（choose_bonus 已扣 pending、加成列 +1）
        self.game_view._level_data = get_character_levels(pid, cid)
        # 即时应用到玩家实体/GameState（与 game_view.setup() 应用永久加成同口径）
        self._apply_bonus(opt)
        # 若仍有待选升级（连升）则掷下一组；否则关闭面板回游戏
        self._refresh_options()
        if not self.options:
            self._close()

    def _apply_bonus(self, opt):
        """把本次永久加成即时应用到玩家实体与 GameState

        与 game_view.setup() 中的口径完全一致：
        - bonus_hp → max_hp/hp（直接叠加）
        - bonus_defense → defense（在装备防御计算之后叠加）
        - bonus_speed → char_speed_mult（像素/帧 ÷ PLAYER_SPEED 折算倍率）
        - bonus_damage/bonus_atk_speed → gs.weapon_* + gs.level_bonus_* 缓存
          （_apply_free_equip 拾取新武器时按缓存补回，保证加成不丢失）
        """
        gs = self.window_ref.game_state
        player = self.game_view.player
        key = opt["key"]
        value = opt["value"]
        if key == "bonus_hp":
            player.max_hp += int(value)
            player.hp += int(value)
        elif key == "bonus_damage":
            gs.weapon_damage += value
            gs.level_bonus_damage = getattr(gs, "level_bonus_damage", 0) + value
        elif key == "bonus_defense":
            player.defense += value
        elif key == "bonus_speed":
            player.char_speed_mult += value / PLAYER_SPEED
        elif key == "bonus_atk_speed":
            gs.weapon_speed += value
            gs.level_bonus_atk_speed = getattr(gs, "level_bonus_atk_speed", 0) + value

    def _close(self):
        """返回游戏（复用保存的 GameView 实例，避免重建丢失局内状态）"""
        if self.game_view:
            self.window.show_view(self.game_view)
        else:
            from views.game_view import GameView
            self.window.show_view(GameView(self.window_ref))