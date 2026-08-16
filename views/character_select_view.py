"""角色选择页面（单机流程：开始游戏 → 选角色 → 进地图）

展示 4 个角色卡片（初始/法师/骑士/刺客，见 entities/character_defs.py）：
- 已解锁角色：点击卡片选中（高亮），底部按钮显示「已选择」
- 未解锁角色：点击卡片选中不可用（提示需购买），底部按钮显示「购买 X 金币」，
  点击扣金币解锁并选中（金币不足提示）
- 底部「进入地图」按钮 → MapSelectView；「返回」按钮 → StartView
- 右上角显示当前金币余额（购买后实时刷新）

联机（局域网联机入口）：不经过本页，角色在 LobbyView 房间内选择（见 lobby_view.py）。
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from entities.character_defs import CHARACTERS, CHARACTER_ORDER


class CharacterSelectView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        # 当前选中角色（默认沿用 GameState 上次选择）
        self.selected = self.window.game_state.character_id
        self.hovered = -1            # 鼠标悬停卡片索引
        self.purchase_hover = -1     # 鼠标悬停购买按钮卡片索引
        self._hint = ""              # 提示信息（购买成功/金币不足等）
        self._unlocked: set[str] = set()  # 已解锁角色 id 集合（从 DB 加载）

        # 计算卡片位置（4 张卡片横排整体居中，与 map_select 卡片模式一致）
        # 注意：arcade.XYWH 的 x/y 是矩形中心坐标，需按中心语义计算
        self.cards = []
        card_w, card_h = 270, 320
        spacing = 20  # 卡片间距
        total_w = len(CHARACTER_ORDER) * card_w + (len(CHARACTER_ORDER) - 1) * spacing
        left = (WINDOW_WIDTH - total_w) // 2          # 整排左边缘
        center_y = WINDOW_HEIGHT // 2                  # 整排垂直居中
        for i in range(len(CHARACTER_ORDER)):
            rect = arcade.XYWH(left + i * (card_w + spacing) + card_w / 2,
                               center_y, card_w, card_h)
            self.cards.append(rect)

        # 底部按钮（进入地图 / 返回）
        # 进入地图按钮仅在已选定角色后显示（见 self._picked，on_draw 中判定）
        self.enter_rect = arcade.XYWH(WINDOW_WIDTH // 2 + 60, 40, 140, 36)
        self.back_rect = arcade.XYWH(WINDOW_WIDTH // 2 - 60, 40, 100, 36)
        self.enter_hover = False
        self.back_hover = False
        # 是否已主动选定角色：False 时不显示「进入地图」按钮（选定后才出现）
        self._picked = False

    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY
        # 每次进入重新加载解锁状态（购买后返回再进入也正确）
        gs = self.window.game_state
        if gs.player_id:
            from db.database import get_unlocked_characters
            self._unlocked = set(get_unlocked_characters(gs.player_id))
        else:
            self._unlocked = {"initial"}
        # 若当前选中角色未解锁（异常状态），回退到初始角色
        if self.selected not in self._unlocked:
            self.selected = "initial"
            gs.character_id = "initial"

    def on_draw(self):
        self.clear()
        gs = self.window.game_state

        # 标题
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "title", "选 择 角 色",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
            arcade.color.WHITE, size=32, anchor_x="center", bold=True,
        )
        # 当前金币（右上角；购买后实时刷新）
        gold = 0
        if gs.player_id:
            from db.database import get_gold
            gold = get_gold(gs.player_id)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "gold", f"金币: {gold}",
            WINDOW_WIDTH - 30, WINDOW_HEIGHT - 40,
            arcade.color.GOLD, size=16, anchor_x="right",
        )

        # 角色卡片
        for i, cid in enumerate(CHARACTER_ORDER):
            rect = self.cards[i]
            char = CHARACTERS[cid]
            is_hover = (self.hovered == i)
            is_selected = (self.selected == cid)
            unlocked = cid in self._unlocked
            # 卡片背景：选中=金色描边 + 角色色，未选中=暗色，悬停=提亮
            bg_color = char["color"] if is_selected else (30, 30, 40)
            if is_hover:
                bg_color = tuple(min(255, c + 30) for c in bg_color)
            arcade.draw_rect_filled(rect, bg_color)
            border_color = arcade.color.GOLD if is_selected else (
                arcade.color.WHITE if is_hover else (90, 90, 90))
            arcade.draw_rect_outline(rect, border_color, border_width=2)

            # 角色名
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"name_{i}", char["name"], rect.center_x, rect.top - 28,
                arcade.color.WHITE, size=20, anchor_x="center", bold=True,
            )
            # 基础数值
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"stats_{i}",
                f"HP {char['hp']}  防 {char['defense']}  速 {char['speed']:.1f}",
                rect.center_x, rect.top - 58,
                arcade.color.LIGHT_GRAY, size=12, anchor_x="center",
            )
            # 技能
            skill = char.get("skill")
            if skill:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"skill_{i}", f"[F] {skill['name']}",
                    rect.center_x, rect.top - 86,
                    arcade.color.CYAN, size=12, anchor_x="center", bold=True,
                )
                # 技能描述（按卡片宽度自动换行，最多两行）
                desc_lines = _wrap_desc(skill.get("desc", ""))
                self._tc.text(
                    f"skill_desc_{i}", desc_lines[0],
                    rect.center_x, rect.top - 106,
                    arcade.color.LIGHT_GRAY, size=10, anchor_x="center",
                )
                if len(desc_lines) > 1:
                    self._tc.text(
                        f"skill_desc2_{i}", desc_lines[1],
                        rect.center_x, rect.top - 120,
                        arcade.color.LIGHT_GRAY, size=10, anchor_x="center",
                    )
                else:
                    # 只有一行时清空第二行，避免切换角色后残留旧文本
                    self._tc.text(
                        f"skill_desc2_{i}", "", rect.center_x, rect.top - 120,
                        arcade.color.LIGHT_GRAY, size=10, anchor_x="center",
                    )
            else:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"skill_{i}", "无技能",
                    rect.center_x, rect.top - 86,
                    arcade.color.GRAY, size=12, anchor_x="center",
                )
                self._tc.text(
                    f"skill_desc_{i}", "", rect.center_x, rect.top - 106,
                    arcade.color.LIGHT_GRAY, size=10, anchor_x="center",
                )
                self._tc.text(
                    f"skill_desc2_{i}", "", rect.center_x, rect.top - 120,
                    arcade.color.LIGHT_GRAY, size=10, anchor_x="center",
                )
            # 被动
            passive = char.get("passive")
            if passive:
                passive_desc = "被动: " + _passive_desc(cid)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"passive_{i}", passive_desc,
                    rect.center_x, rect.top - 130,
                    arcade.color.YELLOW, size=10, anchor_x="center",
                )
            else:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"passive_{i}", "被动: 无",
                    rect.center_x, rect.top - 130,
                    arcade.color.YELLOW, size=10, anchor_x="center",
                )

            # 卡片底部按钮：未解锁=购买，已解锁=已选择/点击选中
            btn = arcade.XYWH(rect.center_x, rect.bottom + 18, 140, 30)
            if not unlocked:
                # 购买按钮（悬停提亮）
                btn_color = (180, 120, 30) if self.purchase_hover == i else (120, 80, 20)
                arcade.draw_rect_filled(btn, btn_color)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"btn_{i}", f"购 买 {char['price']} 金币",
                    btn.center_x, btn.center_y,
                    arcade.color.WHITE, size=13, anchor_x="center", anchor_y="center",
                )
            else:
                # 已解锁：选中状态显示「已选择」，否则「点击选择」
                btn_color = (40, 120, 60) if is_selected else (60, 90, 60)
                arcade.draw_rect_filled(btn, btn_color)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"btn_{i}", "已 选 择" if is_selected else "点 击 选 择",
                    btn.center_x, btn.center_y,
                    arcade.color.WHITE, size=13, anchor_x="center", anchor_y="center",
                )

        # 提示信息（购买成功/金币不足等）
        if self._hint:
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                "hint", self._hint,
                WINDOW_WIDTH // 2, 100,
                arcade.color.GOLD, size=14, anchor_x="center",
            )

        # 进入地图按钮（仅已选定角色后出现；未选时不显示，避免遮挡卡片购买按钮）
        if self._picked:
            enter_color = (40, 160, 120) if self.enter_hover else (30, 110, 85)
            arcade.draw_rect_filled(self.enter_rect, enter_color)
            arcade.draw_rect_outline(self.enter_rect, arcade.color.WHITE, border_width=2)
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                "btn_enter", "进入地图", self.enter_rect.center_x, self.enter_rect.center_y,
                arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center",
            )

        # 返回按钮
        back_color = arcade.color.DARK_RED if self.back_hover else (110, 40, 30)
        arcade.draw_rect_filled(self.back_rect, back_color)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_back", "返回", self.back_rect.center_x, self.back_rect.center_y,
            arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center",
        )

    def on_mouse_motion(self, x, y, dx, dy):
        self.hovered = -1
        self.purchase_hover = -1
        for i, rect in enumerate(self.cards):
            if rect.point_in_rect((x, y)):
                self.hovered = i
                # 购买按钮单独 hover（仅未解锁卡片）
                btn = arcade.XYWH(rect.center_x, rect.bottom + 18, 140, 30)
                if btn.point_in_rect((x, y)) and CHARACTER_ORDER[i] not in self._unlocked:
                    self.purchase_hover = i
                break
        self.enter_hover = self._picked and self.enter_rect.point_in_rect((x, y))
        self.back_hover = self.back_rect.point_in_rect((x, y))

    def on_mouse_press(self, x, y, button, modifiers):
        gs = self.window.game_state

        # 返回按钮
        if self.back_rect.point_in_rect((x, y)):
            from views.start_view import StartView
            self.window.show_view(StartView(self.window_ref))
            return

        # 进入地图按钮（需已选定角色后才可用，正常必有）
        if self._picked and self.enter_rect.point_in_rect((x, y)):
            if self.selected in self._unlocked:
                from views.map_select_view import MapSelectView
                self.window.show_view(MapSelectView(self.window_ref))
            else:
                self._hint = "请先选择已解锁的角色"
            return

        # 角色卡片点击
        for i, rect in enumerate(self.cards):
            if rect.point_in_rect((x, y)):
                cid = CHARACTER_ORDER[i]
                btn = arcade.XYWH(rect.center_x, rect.bottom + 18, 140, 30)
                if cid in self._unlocked:
                    # 已解锁：点击卡片（含按钮）即选中（标记已选定，显示进入地图按钮）
                    self.selected = cid
                    gs.character_id = cid
                    self._picked = True
                    self._hint = f"已选择: {CHARACTERS[cid]['name']}"
                elif btn.point_in_rect((x, y)):
                    # 未解锁 + 点击购买按钮：扣金币解锁并选中
                    self._try_purchase(cid)
                else:
                    self._hint = f"{CHARACTERS[cid]['name']} 未解锁，请先购买"
                return

    def _try_purchase(self, cid: str):
        """尝试购买角色：余额充足则扣金币 + 解锁 + 选中；不足提示"""
        from db.database import get_gold, spend_gold, unlock_character
        gs = self.window.game_state
        char = CHARACTERS[cid]
        if not gs.player_id:
            self._hint = "请先登录（返回主界面重进）"
            return
        gold = get_gold(gs.player_id)
        if gold < char["price"]:
            self._hint = f"金币不足：需要 {char['price']}，当前 {gold}"
            return
        if spend_gold(gs.player_id, char["price"]):
            unlock_character(gs.player_id, cid)
            self._unlocked.add(cid)
            self.selected = cid
            gs.character_id = cid
            self._picked = True  # 购买成功即视为已选定角色，显示进入地图按钮
            self._hint = f"购买成功！已选择: {char['name']}"
        else:
            self._hint = "购买失败，请重试"


def _wrap_desc(text: str, max_chars: int = 18) -> list[str]:
    """按卡片宽度估算把技能描述拆成最多两行（size=10 时每行约 18 个全角字符 ≈ 180px < 卡片内宽）

    中文/全角按 1 个字符宽度、ASCII/数字按 0.55 估算；返回 1~2 行文本列表。
    """
    if not text:
        return [""]
    lines, cur, cur_w = [], "", 0.0
    for ch in text:
        w = 1.0 if ord(ch) > 0x2E80 else 0.55  # 全角≈1，半角≈0.55
        if cur and cur_w + w > max_chars:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur += ch
            cur_w += w
    if cur:
        lines.append(cur)
    return lines


def _passive_desc(cid: str) -> str:
    """被动效果中文描述（供角色卡片显示）"""
    from entities.character_defs import CHARACTERS
    p = CHARACTERS[cid].get("passive") or {}
    parts = []
    if p.get("damage_mult"):
        parts.append(f"伤害+{int((p['damage_mult'] - 1) * 100)}%")
    if p.get("flat_reduce"):
        parts.append(f"受伤-{p['flat_reduce']}")
    if p.get("low_hp_damage_mult"):
        parts.append(f"低血伤害+{int((p['low_hp_damage_mult'] - 1) * 100)}%")
    if p.get("crit_chance"):
        parts.append(f"{int(p['crit_chance'] * 100)}%暴击{p.get('crit_mult', 2):.0f}倍")
    return "、".join(parts) if parts else "无"