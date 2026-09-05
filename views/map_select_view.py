"""选择地图页面"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from game.sound_manager import sound_manager


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
    },
    {
        "id": 2,
        "name": "沙漠荒地",
        "desc": "木乃伊骆驼盘踞的炙热沙丘",
        "monsters": "木乃伊 / 骆驼 / 僵尸 / 骷髅",
        "difficulty": "困难",
        "color": (150, 120, 50),
        "theme": "desert",  # 地图主题：决定生成逻辑与配色
    },
    {
        "id": 3,
        "name": "航天基地",
        "desc": "深空中的废弃基地，隐藏着火箭发射台",
        "monsters": "狙击兵 / 突击兵 / 土匪 / 火箭兵",
        "difficulty": "极难",
        "color": (50, 60, 100),
        "theme": "space",  # 地图主题：决定生成逻辑与配色
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

        # 新手教程（阶段 3）：地图选择向导（须在 cards 定义后构建，高亮需卡片矩形）
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False
        # 战备检查错误提示（on_mouse_press 设置，on_draw 绘制）
        self._error = ""

    def _build_tutorial_pages(self):
        """新手教程阶段 3：地图选择向导（介绍地图与战备要求）"""
        from views.tutorial import TutorialPage
        return [
            TutorialPage("选择地图", [
                "3 张地图，难度递增：",
                "【幽暗森林】普通 · 【沙漠荒地】困难 · 【航天基地】极难",
                "新手先挑战【幽暗森林】，点击卡片右下角的【进入】按钮。",
            ], highlight=self.cards[0]),
            TutorialPage("战备要求", [
                "困难和极难地图有装备价值要求：",
                "【沙漠荒地】需要装备价值达到 100 金币 + 至少一件 Lv.5+ 物品",
                "【航天基地】需要装备价值达到 500 金币 + 至少一件神器装备",
                "装备价值 = 身上穿着的头盔/护甲/背包 + 携带武器的金币价值",
                "可去市场购买更强装备，或去仓库取出已有装备后再挑战。",
            ]),
        ]

    def _tut_showing(self):
        """教程向导是否正在本界面显示（阶段 3 且未翻完页）"""
        tut = getattr(self.window.game_state, "tutorial", None)
        return (tut is not None and tut.active and tut.stage == 2
                and tut.page < len(self.tut_pages))

    def on_show_view(self):
        self.window.background_color = arcade.color.BLACK

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
            # 卡片背景
            bg_color = (m["color"][0]+30, m["color"][1]+30, m["color"][2]+30) if is_hover else m["color"]
            arcade.draw_rect_filled(rect, bg_color)
            border_color = arcade.color.GOLD if is_hover else arcade.color.WHITE
            arcade.draw_rect_outline(rect, border_color, border_width=2)
            # 地图名
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_name_{i}", m["name"], rect.center_x, rect.top - 30,
                arcade.color.WHITE, size=22, anchor_x="center", bold=True,
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
            theme = m.get("theme", "forest")
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
                # 检查当前战备状态
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
            # 进入按钮（放在卡片下方，与卡片保持间距）
            btn = arcade.XYWH(rect.center_x, rect.bottom - 30, 100, 30)
            btn_color = arcade.color.DARK_GREEN if is_hover else (60, 120, 60)
            arcade.draw_rect_filled(btn, btn_color)
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                f"card_btn_{i}", "进入", btn.center_x, btn.center_y,
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

        # 战备不足错误提示（红色醒目）
        if self._error:
            self._tc.text(
                "battle_err", self._error,
                WINDOW_WIDTH // 2, 90,
                arcade.color.RED, size=16, anchor_x="center",
            )

        # 新手教程（阶段 3）：向导弹窗覆盖层（画在最上层）
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
                # 翻完向导：隐藏，让玩家自行点击幽暗森林卡片进入
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
                # 战备检查（非森林地图）
                theme = MAPS[i].get("theme", "forest")
                gs = self.window.game_state
                pid = gs.player_id if gs else 0
                ewid = gs.equipped_weapon_id if gs else None
                passed, error_msg, _ = check_battle_readiness(pid, theme, ewid)
                if not passed:
                    # 战备不足：显示错误提示（用 self._error 持久化，on_draw 可绘制）
                    self._error = error_msg
                    return
                self._error = ""
                # 每次进入随机生成种子，确保房间/资源/怪物/宝箱位置不固定
                import random
                self.window.game_state.current_map_seed = random.randint(1, 999999)
                # 记录地图主题，供 GameView 生成对应主题地图
                self.window.game_state.map_theme = MAPS[i].get("theme", "forest")
                # 新手教程：地图已选定 → 进入游戏内引导（阶段 4 接管）
                tut = getattr(self.window.game_state, "tutorial", None)
                if tut is not None and tut.active and tut.stage == 2:
                    tut.stage = 3
                    tut.page = 0
                from views.game_view import GameView
                gv = GameView(self.window_ref)
                gv.setup()
                self.window.show_view(gv)
                return
