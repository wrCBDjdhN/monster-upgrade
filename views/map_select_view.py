"""选择地图页面

阶段8 难度星级（Task 8.2）：
- 卡片显示各图已达星数 `★N/M`（M = 该图 config.MAP_STAR_CRITERIA 条件条数）与
  金/暗双色**实心**★星标（禁空心/线框，未达成用暗色实心★表示）；
- 地图解锁从「只看战备」改为「**累计星数**达 config.MAP_UNLOCK_STARS 门槛」：
  forest 0 星（初始解锁，保证新手教程期森林可进不受影响）→ desert 需 ≥1 → space 需 ≥2；
  星数不足点击 → 飘中文提示拦截；
- **行为变更**：战备不足由「硬拦截」降级为「卡片黄色警告条 + 仍可进入」
  （check_battle_readiness 调用保留，结果只用于警告渲染）。
"""

import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT,
    MAP_STAR_CRITERIA, MAP_UNLOCK_STARS, MAP_MAX_STARS,
)
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from game.sound_manager import sound_manager

# 阶段8 星级：未达成星标的暗色（实心★用暗色画，禁 ☆ 空心字形/线框）
STAR_DARK = (78, 66, 38)
# 阶段8 星级：已达星标的金色
STAR_GOLD = (255, 215, 0)
# 阶段8 星级：星数不足时未解锁卡片的置灰色（卡片背景压暗用）
CARD_LOCKED_TINT = (58, 58, 64)
# 战备不足警告条配色（实心填充 + 深色文字，保证可读）
WARN_BAR_COLOR = (198, 150, 20)
WARN_TEXT_COLOR = (40, 30, 4)


# ── 战备检查函数 ──────────────────────────────────────────────────────────────

def check_battle_readiness(pid: int, theme: str, equipped_weapon_id: int | None = None) -> tuple[bool, str, dict]:
    """检查战备是否满足地图进入条件

    Args:
        pid: 玩家 ID
        theme: 地图主题 ("forest" / "desert" / "space")
        equipped_weapon_id: 当前携带的武器 DB row id（从 game_state.equipped_weapon_id 传入）

    Returns:
        (passed, error_msg, info)
        - passed: 是否通过
        - error_msg: 错误提示（passed=True 时为空）
        - info: 战备信息字典（equip_value / max_level / has_high_level / has_artifact）
    """
    from db.equipment import get_equipment
    from entities.weapon_defs import ALL_WEAPONS
    from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS

    if theme == "forest":
        return True, "", {}

    equip = get_equipment(pid)

    # 获取携带的单把武器（而非所有武器）
    carried_weapon = None
    if equipped_weapon_id is not None:
        from db.connection import _conn
        with _conn() as c:
            row = c.execute(
                "SELECT id, item_id, level FROM weapons WHERE id=? AND player_id=?",
                (equipped_weapon_id, pid),
            ).fetchone()
            if row:
                carried_weapon = {"id": row[0], "item_id": row[1], "level": row[2]}

    # 计算身上穿着装备（头盔/护甲/背包）的金币价值
    equip_defs = {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}
    equip_value = 0
    has_artifact = False
    has_high_level = False
    max_level = 0

    # 检查已装备物品（头盔/护甲/背包）
    for slot, item in equip.items():
        defs = equip_defs.get(slot, {})
        item_def = defs.get(item["item_id"], {})
        price = item_def.get("price", 0)
        # 神器物品 price=0（不可购买），使用属性值计算等效价值
        if price == 0 and item_def:
            if "defense" in item_def:
                price = item_def["defense"] * max(item["level"], 1) * 5
            elif "capacity" in item_def:
                price = item_def["capacity"] * max(item["level"], 1) * 2
        equip_value += price
        if item_def.get("artifact", False):
            has_artifact = True
        if item["level"] >= 5:
            has_high_level = True
        if item["level"] > max_level:
            max_level = item["level"]

    # 检查携带的武器（仅一把，非全部仓库武器）
    if carried_weapon:
        wdef = ALL_WEAPONS.get(carried_weapon["item_id"], {})
        price = wdef.get("price", 0)
        # 神器武器 price=0（不可购买），使用伤害值计算等效价值
        if price == 0 and wdef:
            price = wdef.get("damage", 0) * max(carried_weapon["level"], 1) * 5
        equip_value += price
        if wdef.get("artifact", False):
            has_artifact = True
        if carried_weapon["level"] >= 5:
            has_high_level = True
        if carried_weapon["level"] > max_level:
            max_level = carried_weapon["level"]

    info = {
        "equip_value": equip_value,
        "max_level": max_level,
        "has_high_level": has_high_level,
        "has_artifact": has_artifact,
    }

    # 沙漠荒地：装备价值 > 100 + 至少一件 Lv.5+ 物品
    if theme == "desert":
        if equip_value < 100:
            return False, f"需要装备价值达到 100 金币，当前装备价值为 {equip_value} 金币", info
        if not has_high_level:
            return False, f"需要至少一件 Lv.5+ 物品，当前最高等级 Lv.{max_level}", info
    # 航天基地：装备价值 > 500 + 至少一件神器
    elif theme == "space":
        if equip_value < 500:
            return False, f"需要装备价值达到 500 金币，当前装备价值为 {equip_value} 金币", info
        if not has_artifact:
            return False, "需要至少一件神器装备，当前没有神器装备", info

    return True, "", info


# ── 战备要求定义（用于卡片上显示）──────────────────────────────────────────────

BATTLE_READY_REQS = {
    "forest": None,  # 无要求
    "desert": {"min_value": 100, "min_level": 5, "need_artifact": False},
    "space": {"min_value": 500, "min_level": 0, "need_artifact": True},
}


# ── 地图数据定义 ───────────────────────────────────────────────────────────────

MAPS = [
    {
        "id": 1,
        "name": "幽暗森林",
        "desc": "僵尸骷髅出没的阴森林地",
        "monsters": "僵尸(近战) / 骷髅(远程)",
        "difficulty": "普通",
        "color": (40, 80, 40),
        "theme": "forest",  # 地图主题：决定生成逻辑与配色
        # 阶段8：进入该图所需的**累计星数**（解锁链唯一来源 = config.MAP_UNLOCK_STARS）
        "unlock_stars": MAP_UNLOCK_STARS["forest"],
    },
    {
        "id": 2,
        "name": "沙漠荒地",
        "desc": "木乃伊骆驼盘踞的炙热沙丘",
        "monsters": "木乃伊 / 骆驼 / 僵尸 / 骷髅",
        "difficulty": "困难",
        "color": (150, 120, 50),
        "theme": "desert",  # 地图主题：决定生成逻辑与配色
        "unlock_stars": MAP_UNLOCK_STARS["desert"],
    },
    {
        "id": 3,
        "name": "航天基地",
        "desc": "深空中的废弃基地，隐藏着火箭发射台",
        "monsters": "狙击兵 / 突击兵 / 土匪 / 火箭兵",
        "difficulty": "极难",
        "color": (50, 60, 100),
        "theme": "space",  # 地图主题：决定生成逻辑与配色
        "unlock_stars": MAP_UNLOCK_STARS["space"],
    },
]


class MapSelectView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.hovered_map = -1
        # 计算卡片位置（多张卡片整体居中）
        # 关键：arcade.XYWH(x, y, w, h) 的 x,y 是矩形【中心】坐标（anchor 默认 CENTER），
        # 不是左上角。故第一张卡片的中心 = 组中心 - 组内偏移，组中心对齐窗口中心。
        self.cards = []
        card_w, card_h = 300, 180
        spacing = 40  # 卡片间距
        # 修复：原先按「左上角语义」计算，导致卡片组中心 x=490（偏左150px）、
        # y 偏移 40（偏下），三张卡片整体不居中；现改为组中心 = 窗口中心
        start_x = WINDOW_WIDTH // 2 - (len(MAPS) - 1) * (card_w + spacing) // 2
        start_y = WINDOW_HEIGHT // 2
        for i, m in enumerate(MAPS):
            rect = arcade.XYWH(start_x + i * (card_w + spacing), start_y, card_w, card_h)
            self.cards.append(rect)

        # 新手教程（阶段 2）：地图选择向导（须在 cards 定义后构建，高亮需卡片矩形）
        # 阶段编号口径见 main.TutorialState：0 开始界面 / 1 角色选择 / 2 地图选择 /
        # 3 游戏内 / 4 撤离结算 / 5 市场 / 6 完成
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False
        # 战备检查错误提示（on_mouse_press 设置，on_draw 绘制）
        self._error = ""
        # 阶段8 星级：各图已达星数 {theme: stars}（未打过的图不出现在 dict，取值用 .get(theme, 0)）
        # 与累计星数（解锁判定口径：sum(所有图星数) >= MAP_UNLOCK_STARS[theme]）
        self._stars: dict = {}
        self._total_stars: int = 0
        self._reload_stars()

    def _reload_stars(self) -> None:
        """从 db 读取玩家各图星数并算累计星数（on_show_view 每帧进入时刷新，返回大厅后可见新星）"""
        from db.database import get_stars
        gs = self.window.game_state
        pid = getattr(gs, "player_id", None)
        self._stars = get_stars(pid) if pid else {}
        self._total_stars = sum(self._stars.values())

    def _theme_stars(self, theme: str) -> int:
        """某图已达星数（未打过该图 = 0 星）"""
        return int(self._stars.get(theme, 0))

    def _theme_max_stars(self, theme: str) -> int:
        """某图星数上限（= 该图 MAP_STAR_CRITERIA 条件条数，缺配置回落 MAP_MAX_STARS）"""
        return len(MAP_STAR_CRITERIA.get(theme, [])) or MAP_MAX_STARS

    def _build_tutorial_pages(self):
        """新手教程阶段 2：地图选择向导（介绍地图与星级解锁）

        修改原因：教程事实错误修正（2026-09-26）——第 2 页原教 v1 的「战备要求」
        （装备价值 100/500 金币门槛），但现行唯一硬拦截口径已改为**累计星数**
        （config.MAP_UNLOCK_STARS，forest=0 / desert=1 / space=2，消费点见
        本文件 on_mouse_press 阶段8 校验①），战备不足**不再拦截**只渲染黄色
        警告条，故本页改教「星级解锁」，避免与实际规则冲突误导新手。

        数值去硬编码（2026-09-26）：原页把「每图最多 3 星」「★1 / ★2」写死在
        文案里，改平衡（config.MAP_MAX_STARS / MAP_UNLOCK_STARS / MAP_STAR_CRITERIA）
        后教程即过期。现全部实查 config + 本文件 MAPS 数据表。
        """
        from views.tutorial import (
            TutorialPage,
            map_max_stars,
            star_criteria_labels,
            unlock_star_label,
        )
        # 第 1 页：地图清单——名称/难度实查 MAPS（禁手抄，免得加图漏改）
        map_line = " · ".join(f"【{m['name']}】{m['difficulty']}" for m in MAPS)
        # 第 2 页：星级解锁——上限/门槛/达成条件逐项实查 config
        star_lines = [
            "地图按【累计星数】解锁，不再卡装备价值：",
            f"每次撤离成功都会结算星级，每图最多 {map_max_stars()} 星：",
        ]
        for m in MAPS:
            theme = str(m.get("theme", "forest"))
            crit = star_criteria_labels(theme)
            tail = f"（{crit}）" if crit else ""
            star_lines.append(f"  ·【{m['name']}】{unlock_star_label(theme)}{tail}")
        star_lines.append("战备不足只是黄色警告，照样能进图。")
        star_lines.append("想多拿星：多撤离、多击杀、再打精英怪。")
        return [
            TutorialPage("选择地图", [
                f"共 {len(MAPS)} 张地图，难度递增：",
                map_line,
                "新手先挑战最简单的那张，点击卡片下方的【进入】按钮。",
            ], highlight=self.cards[0]),
            # 教程事实错误修正（2026-09-26）：原「战备要求」页改为「星级解锁」页
            TutorialPage("星级解锁", star_lines),
        ]

    def _tut_showing(self):
        """教程向导是否正在本界面显示（阶段 3 且未翻完页）"""
        tut = getattr(self.window.game_state, "tutorial", None)
        return (tut is not None and tut.active and tut.stage == 2
                and tut.page < len(self.tut_pages))

    def on_show_view(self):
        self.window.background_color = arcade.color.BLACK
        # 阶段8 星级：每次进入本界面重新读库（打星回来后星数/解锁状态即时刷新）
        self._reload_stars()

    def on_draw(self):
        self.clear()
        # 标题
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "title",
            "选 择 地 图",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 80,
            arcade.color.WHITE, size=36, anchor_x="center",
        )
        # 地图卡片
        for i, m in enumerate(MAPS):
            rect = self.cards[i]
            is_hover = (self.hovered_map == i)
            theme = m.get("theme", "forest")
            # 阶段8 星级：解锁判定（累计星数 vs 该图门槛）——星数不足则卡片置灰
            need_stars = int(m.get("unlock_stars", 0))
            locked = self._total_stars < need_stars
            # 卡片背景（未解锁置灰：压暗成冷灰，保留原色相不做渐变）
            bg_color = (m["color"][0]+30, m["color"][1]+30, m["color"][2]+30) if is_hover else m["color"]
            if locked:
                bg_color = CARD_LOCKED_TINT
            arcade.draw_rect_filled(rect, bg_color)
            border_color = arcade.color.GOLD if is_hover else arcade.color.WHITE
            arcade.draw_rect_outline(rect, border_color, border_width=2)
            # 地图名（未解锁压暗为浅灰，置灰卡片上仍可读）
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_name_{i}", m["name"], rect.center_x, rect.top - 30,
                arcade.color.LIGHT_GRAY if locked else arcade.color.WHITE,
                size=22, anchor_x="center", bold=True,
            )
            # 阶段8 星级星标：已达=金色实心★，未达=暗色实心★（禁空心/线框）
            # 两个 Text 以同一点为锚左右拼接（已达右对齐 + 未达左对齐），无需测量字宽
            earned = self._theme_stars(theme)
            max_stars = self._theme_max_stars(theme)
            star_split_x = rect.center_x + 4
            if earned > 0:
                self._tc.text(f"card_star_on_{i}", "★" * earned, star_split_x,
                              rect.top - 56, STAR_GOLD, size=18, anchor_x="right")
            if earned < max_stars:
                self._tc.text(f"card_star_off_{i}", "★" * (max_stars - earned), star_split_x,
                              rect.top - 56, STAR_DARK, size=18, anchor_x="left")
            # 星级数字 + 解锁门槛（★N/M；M = 该图条件条数）
            # 纵向布局（卡片内，自上而下）：地图名 top-30 → 星标 top-56 → 怪物 center+10
            #   → 描述 center-15 → 门槛文案 bottom+50 → 战备警告条 bottom+18（均在卡片实心背景内）
            if need_stars > 0:
                gate_text = f"★{earned}/{max_stars} · 累计 ★{self._total_stars}/{need_stars} 解锁"
            else:
                gate_text = f"★{earned}/{max_stars} · 初始解锁"
            self._tc.text(
                f"card_stars_{i}", gate_text, rect.center_x, rect.bottom + 50,
                arcade.color.DARK_GRAY if locked else arcade.color.LIGHT_GRAY,
                size=11, anchor_x="center",
            )
            # 怪物信息
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_monsters_{i}", m["monsters"], rect.center_x, rect.center_y + 10,
                arcade.color.LIGHT_GRAY, size=12, anchor_x="center",
            )
            # 地图描述
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_desc_{i}", m["desc"], rect.center_x, rect.center_y - 15,
                arcade.color.GRAY, size=10, anchor_x="center",
            )
            # 难度（放在卡片内部靠下位置，避免与下方按钮重合）
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_diff_{i}", "难度: " + m["difficulty"], rect.center_x, rect.bottom - 90,
                arcade.color.YELLOW, size=14, anchor_x="center",
            )
            # 战备要求显示（非森林地图）
            req = BATTLE_READY_REQS.get(theme)
            if req:
                req_lines = []
                if req["min_value"] > 0:
                    req_lines.append(f"装备价值 ≥ {req['min_value']}")
                if req["min_level"] > 0:
                    req_lines.append(f"至少 Lv.{req['min_level']}+ 物品")
                if req["need_artifact"]:
                    req_lines.append("至少一件神器")
                req_text = " | ".join(req_lines)
                # 检查当前战备状态（阶段8：结果只用于警告渲染，不再拦截进入）
                gs = self.window.game_state
                pid = gs.player_id if gs else 0
                ewid = gs.equipped_weapon_id if gs else None
                passed, _, info = check_battle_readiness(pid, theme, ewid)
                req_color = (100, 255, 100) if passed else (255, 100, 100)
                self._tc.text(
                    f"card_req_{i}", req_text, rect.center_x, rect.bottom - 75,
                    req_color, size=11, anchor_x="center",
                )
                # 显示当前价值信息（在要求文字下方、按钮上方）
                if info.get("equip_value") is not None:
                    val_text = f"当前价值: {info['equip_value']}"
                    self._tc.text(
                        f"card_val_{i}", val_text, rect.center_x, rect.bottom - 60,
                        arcade.color.LIGHT_GRAY, size=10, anchor_x="center",
                    )
                # 阶段8 行为变更：战备不足不再硬拦截，改为卡片内**实心**黄色警告条（仍可进入）
                if not passed:
                    warn_rect = arcade.XYWH(rect.center_x, rect.bottom + 18, rect.width - 30, 20)
                    arcade.draw_rect_filled(warn_rect, WARN_BAR_COLOR)
                    self._tc.text(
                        f"card_warn_{i}", "⚠ 战备不足（仍可进入）", warn_rect.center_x,
                        warn_rect.center_y, WARN_TEXT_COLOR, size=10,
                        anchor_x="center", anchor_y="center",
                    )
            # 进入按钮（放在卡片下方，与卡片保持间距）
            btn = arcade.XYWH(rect.center_x, rect.bottom - 30, 100, 30)
            # 阶段8：未解锁地图按钮置灰且文案改为「未解锁」（点击仍响应 → 飘中文拦截提示）
            if locked:
                btn_color = (70, 70, 76)
            else:
                btn_color = arcade.color.DARK_GREEN if is_hover else (60, 120, 60)
            arcade.draw_rect_filled(btn, btn_color)
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_btn_{i}", "未解锁" if locked else "进入", btn.center_x, btn.center_y,
                arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center",
            )

        # 返回按钮
        back_rect = arcade.XYWH(80, 40, 100, 36)
        arcade.draw_rect_filled(back_rect, arcade.color.DARK_RED)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "back_btn",
            "返回", back_rect.center_x, back_rect.center_y,
            arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center",
        )

        # 阶段8：星数不足的解锁拦截提示（红色醒目；战备不足已降级为卡片黄条，不再走此处）
        if self._error:
            self._tc.text(
                "battle_err", self._error,
                WINDOW_WIDTH // 2, 90,
                arcade.color.RED, size=16, anchor_x="center",
            )

        # 新手教程（阶段 2）：向导弹窗覆盖层（画在最上层）
        if self._tut_showing():
            from views.tutorial import draw_tutorial_page
            tut = getattr(self.window.game_state, "tutorial", None)
            page = self.tut_pages[tut.page]
            self.tut_next_rect, self.tut_skip_rect = draw_tutorial_page(
                self, page, tut.page, len(self.tut_pages),
                self._tc, self.tut_next_hover)

    def on_key_press(self, key, modifiers):
        # 新手教程激活：ESC 立即跳过并标记完成
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active:
            if key == arcade.key.ESCAPE:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return

    def on_mouse_motion(self, x, y, dx, dy):
        # 新手教程向导显示：只更新下一步按钮悬停态
        if self._tut_showing():
            self.tut_next_hover = bool(
                self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)))
            return
        self.hovered_map = -1
        for i, rect in enumerate(self.cards):
            # 检测卡片区域或进入按钮区域（按钮在卡片下方 bottom-30 处）
            btn = arcade.XYWH(rect.center_x, rect.bottom - 30, 100, 30)
            if rect.point_in_rect((x, y)) or btn.point_in_rect((x, y)):
                self.hovered_map = i
                break

    def on_mouse_press(self, x, y, button, modifiers):
        # 新手教程向导显示：只响应 下一步/跳过
        if self._tut_showing():
            from views.tutorial import finish_tutorial
            if self.tut_skip_rect and self.tut_skip_rect.point_in_rect((x, y)):
                finish_tutorial(self.window)
                return
            if self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)):
                # 翻完向导：隐藏，让玩家自行点击目标地图卡片进入
                tut = getattr(self.window.game_state, "tutorial", None)
                if tut is not None:
                    tut.page = len(self.tut_pages)
                return
            return

        sound_manager.play_ui()
        # 返回按钮（单机流程：角色选择 → 地图选择，返回时回角色选择页）
        back_rect = arcade.XYWH(80, 40, 100, 36)
        if back_rect.point_in_rect((x, y)):
            from views.character_select_view import CharacterSelectView
            self.window.show_view(CharacterSelectView(self.window_ref))
            return

        # 地图卡片点击（进入按钮在卡片下方 bottom-30 处）
        for i, rect in enumerate(self.cards):
            # 检测点击区域：进入按钮（卡片底部 -30，尺寸 100×30）
            btn = arcade.XYWH(rect.center_x, rect.bottom - 30, 100, 30)
            if btn.point_in_rect((x, y)):
                # 阶段8 校验①：累计星数解锁（唯一硬拦截口径）
                #   门槛 = config.MAP_UNLOCK_STARS（forest=0，故新手教程期森林恒可进）
                need_stars = int(MAPS[i].get("unlock_stars", 0))
                if self._total_stars < need_stars:
                    self._error = (f"需累计 ★{need_stars} 解锁【{MAPS[i]['name']}】"
                                   f"（当前累计 ★{self._total_stars}）")
                    return
                # 阶段8 校验②：战备不足**不再拦截**（行为变更：旧版此处硬拦截），
                #   check_battle_readiness 的结果已在 on_draw 渲染为卡片黄色警告条，仍可进入。
                self._error = ""
                # 每次进入随机生成种子，确保房间/资源/怪物/宝箱位置不固定
                import random
                self.window.game_state.current_map_seed = random.randint(1, 999999)
                # 记录地图主题，供 GameView 生成对应主题地图
                self.window.game_state.map_theme = MAPS[i].get("theme", "forest")
                # 新手教程：地图已选定 → 进入游戏内引导（阶段 3 接管）
                tut = getattr(self.window.game_state, "tutorial", None)
                if tut is not None and tut.active and tut.stage == 2:
                    tut.stage = 3
                    tut.page = 0
                from views.game_view import GameView
                gv = GameView(self.window_ref)
                gv.setup()
                self.window.show_view(gv)
                return
