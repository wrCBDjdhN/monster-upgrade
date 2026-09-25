"""图鉴页面：怪物/武器/装备/药水 4 类图鉴，锁定/解锁条目与详情展示

- 4 个分类 Tab（怪物/武器/装备/药水），顶部切换
- 未解锁条目显示「？？？」并隐藏全部数值；解锁条目展示完整详情
- 右侧详情面板展示选中条目的完整信息
- 条目较多时支持滚轮滚动
- 返回：按钮 + ESC → back_view（无则 StartView，延迟导入）
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from entities.monster_defs import (
    MONSTER_CONFIGS, BOSS_SKILLS, SKILL_PROMPT_CONFIG,
    get_all_monster_class_names, is_melee_monster, is_ranged_monster,
)
from entities.weapon_defs import ALL_WEAPONS
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS, POTIONS
from views.text_cache import TextCache
from game.sound_manager import sound_manager


# 怪物类名 → 中文名映射（游戏内无统一名称表，图鉴本地维护一份）
_MONSTER_NAMES = {
    "Zombie": "僵尸",
    "Skeleton": "骷髅",
    "MummyMelee": "近战木乃伊",
    "MummyRanged": "远程木乃伊",
    "Camel": "骆驼",
    "Sniper": "狙击兵",
    "Assault": "突击兵",
    "Bandit": "土匪",
    "RocketTroop": "火箭兵",
    "BossZombie": "僵尸BOSS",
    "BossSkeleton": "骷髅BOSS",
    "BossMummy": "木乃伊BOSS",
    "BossSpace": "航天BOSS",
}


class CodexView(arcade.View):
    """图鉴页面：4 个分类 Tab，锁定条目显示？？？，解锁条目展示完整详情"""

    # ── 布局常量（1280×720 逻辑分辨率）──
    TAB_Y = WINDOW_HEIGHT - 135          # Tab 栏中心 y
    CONTENT_TOP = WINDOW_HEIGHT - 175    # 列表区顶部 y
    CONTENT_BOTTOM = 90                  # 列表区底部 y（返回按钮上方）
    ROW_H = 24                           # 行高
    ROW_STEP = 24                        # 行间距
    ROW_X = 40                           # 列表左边界
    ROW_W = 620                          # 列表宽度
    PANEL_X = 690                        # 详情面板左边界
    PANEL_W = 550                        # 详情面板宽度

    def __init__(self, window, back_view=None):
        super().__init__()
        self.window_ref = window
        self.back_view = back_view  # 返回目标视图（None 时返回 StartView）
        self._tc = TextCache()  # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        # 顶部分类 Tab 栏：怪物/武器/装备/药水
        self._tabs = ["怪物", "武器", "装备", "药水"]
        self._tab = "怪物"
        self.tab_rects = {}
        self.tab_hover = {}
        tab_w, tab_h, gap = 120, 30, 12
        total_w = len(self._tabs) * tab_w + (len(self._tabs) - 1) * gap
        start_x = (WINDOW_WIDTH - total_w) // 2
        for i, name in enumerate(self._tabs):
            self.tab_rects[name] = arcade.XYWH(
                start_x + i * (tab_w + gap) + tab_w / 2, self.TAB_Y, tab_w, tab_h,
            )
            self.tab_hover[name] = False
        # 返回按钮（左下角，与 ScrollView 一致）
        self.back_rect = arcade.XYWH(80, 40, 100, 36)
        self.back_hover = False
        # 滚动状态（条目较多时滚轮滚动）
        self.scroll_offset = 0.0
        # 当前分类条目与选中项
        self._entries = []      # [(cat_key, item_id, 显示名, 定义字典)]
        self._unlocked = set()  # 当前玩家图鉴解锁集合
        self._selected = 0      # 选中条目下标
        self.row_hover = -1     # 悬停条目下标（-1=无）
        self.row_rects = []     # [(rect, 条目下标)]（on_draw 时按滚动位置重建）
        self._rebuild()

    # ── 数据构建 ──────────────────────────────────────────────

    def _rebuild(self):
        """按当前 Tab 重建条目列表（内容结构变化时调用）"""
        self._tc.clear()
        self._entries = []
        if self._tab == "怪物":
            for cls in get_all_monster_class_names():
                self._entries.append(("monster", cls, _MONSTER_NAMES.get(cls, cls), MONSTER_CONFIGS[cls]))
        elif self._tab == "武器":
            for item_id, wdef in ALL_WEAPONS.items():
                self._entries.append(("weapon", item_id, wdef.get("name", item_id), wdef))
        elif self._tab == "装备":
            for table in (HELMETS, ARMORS, BACKPACKS):
                for item_id, edef in table.items():
                    self._entries.append(("equipment", item_id, edef.get("name", item_id), edef))
        else:  # 药水
            for item_id, pdef in POTIONS.items():
                self._entries.append(("potion", item_id, pdef.get("name", item_id), pdef))
        # 读取当前玩家解锁集合（无玩家时为空集）
        self._unlocked = self._get_unlocked()
        # 切换分类：滚动归零、选中第一条
        self.scroll_offset = 0.0
        self._selected = 0
        self.row_rects = []

    def _get_unlocked(self) -> set:
        """读取当前玩家的图鉴解锁集合；无玩家时返回空集

        用 window_ref（__init__ 已赋值）而非 self.window：后者在 show_view 前
        可能未就绪，会导致解锁集合读取失败、已解锁条目显示为空白。
        """
        gs = getattr(self.window_ref, "game_state", None)
        pid = getattr(gs, "player_id", None) if gs is not None else None
        if not pid:
            return set()
        from db.database import get_codex_unlocks
        return get_codex_unlocks(pid)

    def _is_unlocked(self, cat_key: str, item_id: str) -> bool:
        """判断条目是否已解锁（解锁集合成员形如 ("monster", "Zombie")）"""
        return (cat_key, item_id) in self._unlocked

    # ── 生命周期 ──────────────────────────────────────────────

    def on_show_view(self):
        self.window.background_color = (30, 25, 40)

    def on_draw(self):
        self.clear()
        # ── 固定头部：标题 + 进度 + 提示 ──
        self._tc.text("header_title", "图 鉴", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 50,
                      arcade.color.GOLD, 30, anchor_x="center")
        unlocked_count = sum(1 for e in self._entries if self._is_unlocked(e[0], e[1]))
        self._tc.text("header_progress", f"已解锁 {unlocked_count}/{len(self._entries)}",
                      WINDOW_WIDTH // 2, WINDOW_HEIGHT - 85,
                      arcade.color.YELLOW, 16, anchor_x="center")
        self._tc.text("header_hint", "点击条目查看详情 | 滚轮滚动 | ESC 返回",
                      WINDOW_WIDTH // 2, WINDOW_HEIGHT - 105,
                      arcade.color.GRAY, 11, anchor_x="center")

        # ── 顶部 Tab 栏（固定）──
        for name, rect in self.tab_rects.items():
            active = (name == self._tab)
            if active:
                bg = (80, 130, 80)
            elif self.tab_hover.get(name):
                bg = (60, 70, 85)
            else:
                bg = (50, 55, 65)
            arcade.draw_rect_filled(rect, bg)
            self._tc.text(f"tab_{name}", name, rect.center_x, rect.center_y,
                          arcade.color.WHITE if active else arcade.color.LIGHT_GRAY,
                          15, anchor_x="center", anchor_y="center")

        # ── 条目列表（带滚动偏移，仅绘制可见行）──
        self.row_rects = []
        y = self.CONTENT_TOP + self.scroll_offset
        for i, (cat_key, item_id, name, _def) in enumerate(self._entries):
            if self.CONTENT_BOTTOM <= y <= self.CONTENT_TOP:
                # ROW_X 是列表左边界；XYWH 首参是中心 x，需加半宽换算，
                # 否则矩形中心落在 40、名称画到屏幕外 → 解锁后整行空白
                rect = arcade.XYWH(self.ROW_X + self.ROW_W / 2, y, self.ROW_W, self.ROW_H)
                self.row_rects.append((rect, i))
                self._draw_row(rect, i, cat_key, item_id, name, _def)
            y -= self.ROW_STEP

        # ── 右侧详情面板 ──
        self._draw_detail()

        # ── 返回按钮（固定）──
        back_color = arcade.color.DARK_RED if self.back_hover else (110, 30, 30)
        arcade.draw_rect_filled(self.back_rect, back_color)
        arcade.draw_rect_outline(self.back_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("nav_back", "返回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

    # ── 条目行绘制 ────────────────────────────────────────────

    def _draw_row(self, rect, i, cat_key, item_id, name, _def):
        """绘制单行条目：选中高亮 + 名称（锁定显示？？？，神器加★前缀）"""
        unlocked = self._is_unlocked(cat_key, item_id)
        selected = (i == self._selected)
        if selected:
            arcade.draw_rect_filled(rect, (60, 70, 90))
        elif self.row_hover == i:
            arcade.draw_rect_filled(rect, (45, 50, 65))
        if unlocked:
            prefix = "★ " if _def.get("artifact") else ""
            # name 空值兜底：定义缺 name 字段时回退 item_id，避免解锁后整行空白
            label = f"{prefix}{name or item_id}"
            color = arcade.color.GOLD if _def.get("artifact") else arcade.color.LIGHT_GRAY
        else:
            label = "？？？"
            color = arcade.color.GRAY
        self._tc.text(f"row_{i}", label, rect.left + 12, rect.center_y,
                      color, 14, anchor_y="center")
        if not unlocked:
            self._tc.text(f"row_lock_{i}", "未解锁", rect.right - 10, rect.center_y,
                          arcade.color.DARK_GRAY, 11, anchor_x="right", anchor_y="center")

    # ── 详情面板 ──────────────────────────────────────────────

    def _draw_detail(self):
        """绘制右侧详情面板：选中条目的完整信息（锁定条目只显示？？？）"""
        if not self._entries:
            return
        idx = min(self._selected, len(self._entries) - 1)
        cat_key, item_id, name, _def = self._entries[idx]
        unlocked = self._is_unlocked(cat_key, item_id)

        # 面板背景（固定区域，不随列表滚动）
        panel = arcade.XYWH(self.PANEL_X + self.PANEL_W / 2,
                            (self.CONTENT_TOP + self.CONTENT_BOTTOM) / 2,
                            self.PANEL_W, self.CONTENT_TOP - self.CONTENT_BOTTOM)
        arcade.draw_rect_filled(panel, (40, 35, 55))
        arcade.draw_rect_outline(panel, (90, 85, 110), border_width=1)

        x = self.PANEL_X + 20
        y = self.CONTENT_TOP - 25

        if not unlocked:
            # 锁定条目：只显示？？？与解锁提示，隐藏全部数值
            self._tc.text("detail_name", "？？？", x, y, arcade.color.GRAY, 20, bold=True)
            y -= 32
            self._tc.text("detail_locked", "尚未解锁", x, y, arcade.color.DARK_GRAY, 14)
            y -= 24
            hint = {
                "monster": "击杀该怪物后解锁",
                "weapon": "获得该武器后解锁",
                "equipment": "获得该装备后解锁",
                "potion": "获得该药水后解锁",
            }.get(cat_key, "")
            self._tc.text("detail_locked_hint", hint, x, y, arcade.color.DARK_GRAY, 12)
            return

        # 解锁条目：名称（神器加★前缀）+ 分类详情
        prefix = "★ " if _def.get("artifact") else ""
        name_color = arcade.color.GOLD if _def.get("artifact") else arcade.color.WHITE
        self._tc.text("detail_name", f"{prefix}{name}", x, y, name_color, 20, bold=True)
        y -= 32

        if cat_key == "monster":
            self._draw_monster_detail(item_id, _def, x, y)
        elif cat_key == "weapon":
            self._draw_weapon_detail(_def, x, y)
        elif cat_key == "equipment":
            self._draw_equipment_detail(item_id, _def, x, y)
        else:
            self._draw_potion_detail(_def, x, y)

    def _draw_monster_detail(self, cls, mdef, x, y):
        """怪物详情：血量/攻击力/移速/攻击方式/技能列表"""
        self._tc.text("detail_hp", f"血量: {mdef.get('hp', 0)}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        self._tc.text("detail_dmg", f"攻击力: {mdef.get('damage', 0)}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        self._tc.text("detail_speed", f"移速: {mdef.get('speed', 0)}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        # 攻击方式：远程优先（骷髅同时登记在近战/远程表，实际类为远程基类）
        if is_ranged_monster(cls):
            attack_type = "远程"
        elif is_melee_monster(cls):
            attack_type = "近战"
        else:
            attack_type = "未知"
        self._tc.text("detail_atk", f"攻击方式: {attack_type}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 28
        # 技能列表
        self._tc.text("detail_skill_title", "技能:", x, y, arcade.color.YELLOW, 14, bold=True)
        y -= 24
        skills = self._monster_skills(cls)
        if not skills:
            self._tc.text("detail_skill_none", "无", x + 10, y, arcade.color.DARK_GRAY, 12)
            return
        for i, (sname, sdesc) in enumerate(skills):
            self._tc.text(f"detail_skill_{i}", f"· {sname}", x + 10, y, arcade.color.LIGHT_GRAY, 13)
            y -= 20
            if sdesc:
                self._tc.text(f"detail_skill_desc_{i}", f"    {sdesc}", x + 10, y, arcade.color.GRAY, 12)
                y -= 20

    def _monster_skills(self, cls) -> list:
        """返回怪物技能列表 [(技能名, 描述)]：BOSS 用 BOSS_SKILLS，普通怪用 SKILL_PROMPT_CONFIG"""
        if cls in BOSS_SKILLS:
            return [(s.get("name", ""), s.get("description", "")) for s in BOSS_SKILLS[cls]]
        prompt = SKILL_PROMPT_CONFIG.get(cls, {})
        return [(s.get("name", ""), "") for s in prompt.get("skills", [])]

    def _draw_weapon_detail(self, wdef, x, y):
        """武器详情：类型/伤害/攻速/射程/特效"""
        kind = "近战" if wdef.get("kind") == "melee" else "远程"
        self._tc.text("detail_kind", f"类型: {kind}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        self._tc.text("detail_dmg", f"伤害: {wdef.get('damage', 0)}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        self._tc.text("detail_as", f"攻速: {wdef.get('attack_speed', 0)}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        self._tc.text("detail_range", f"射程: {wdef.get('range', 0)}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 28
        self._tc.text("detail_eff_title", "特效:", x, y, arcade.color.YELLOW, 14, bold=True)
        y -= 24
        self._tc.text("detail_eff", self._weapon_effect_desc(wdef), x + 10, y, arcade.color.LIGHT_GRAY, 13)

    @staticmethod
    def _weapon_effect_desc(wdef: dict) -> str:
        """根据武器定义生成中文特效描述（存在任意特效字段即显示，无则返回"无特效"）"""
        parts = []
        debuff_names = {
            "stun": "命中眩晕目标",
            "freeze": "命中冰冻减速",
            "burn": "命中点燃持续灼烧",
            "poison": "命中附加中毒",
        }
        if wdef.get("lifesteal"):
            parts.append(f"吸血{int(wdef['lifesteal'] * 100)}%")
        if wdef.get("debuff"):
            parts.append(debuff_names.get(wdef["debuff"], wdef["debuff"]))
        special_names = {
            "penetrating": "弹丸穿透敌人",
            "explosive": "爆炸范围伤害",
            "laser": "持续激光（可穿墙）",
        }
        if wdef.get("special"):
            parts.append(special_names.get(wdef["special"], wdef["special"]))
        if wdef.get("spread_count"):
            parts.append(f"一次发射{wdef['spread_count']}发散射弹")
        if wdef.get("aura_slow"):
            parts.append(f"冰霜光环：半径{wdef.get('aura_radius', 0)}px内怪物持续减速")
        if wdef.get("random_debuff"):
            parts.append("每颗子弹随机附带一种异常状态")
        return "、".join(parts) if parts else "无特效"

    def _draw_equipment_detail(self, item_id, edef, x, y):
        """装备详情：类型/防御或容量/描述"""
        if item_id in HELMETS:
            slot_label = "头盔"
            stat = f"防御: {edef.get('defense', 0)}"
        elif item_id in ARMORS:
            slot_label = "护甲"
            stat = f"防御: {edef.get('defense', 0)}"
        else:
            slot_label = "背包"
            stat = f"容量: {edef.get('capacity', 0)}"
        self._tc.text("detail_slot", f"类型: {slot_label}", x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 24
        self._tc.text("detail_stat", stat, x, y, arcade.color.LIGHT_GRAY, 14)
        y -= 28
        self._tc.text("detail_desc_title", "描述:", x, y, arcade.color.YELLOW, 14, bold=True)
        y -= 24
        desc = edef.get("description")
        if not desc and edef.get("artifact"):
            desc = "无特殊效果（超高属性）"
        self._tc.text("detail_desc", desc or "无", x + 10, y, arcade.color.LIGHT_GRAY, 13)

    def _draw_potion_detail(self, pdef, x, y):
        """药水详情：效果摘要/描述"""
        self._tc.text("detail_eff_title", "效果:", x, y, arcade.color.YELLOW, 14, bold=True)
        y -= 24
        self._tc.text("detail_eff", self._potion_effect_summary(pdef), x + 10, y, arcade.color.LIGHT_GRAY, 13)
        y -= 28
        self._tc.text("detail_desc_title", "描述:", x, y, arcade.color.YELLOW, 14, bold=True)
        y -= 24
        self._tc.text("detail_desc", pdef.get("description", "无"), x + 10, y, arcade.color.LIGHT_GRAY, 13)

    @staticmethod
    def _potion_effect_summary(pdef: dict) -> str:
        """根据药水定义生成效果摘要（effect/value/duration）"""
        effect = pdef.get("effect", "")
        value = pdef.get("value", 0)
        duration = pdef.get("duration")
        if effect == "heal":
            return f"回复{int(value)}点生命"
        if effect == "speed":
            pct = int(round((value - 1) * 100))
            return f"移速提升{pct}%，持续{int(duration)}秒" if duration else f"移速提升{pct}%"
        if effect == "shield":
            return f"获得{int(value)}点护盾，持续{int(duration)}秒" if duration else f"获得{int(value)}点护盾"
        if effect == "power":
            pct = int(round((value - 1) * 100))
            return f"攻击伤害提升{pct}%，持续{int(duration)}秒" if duration else f"攻击伤害提升{pct}%"
        if effect == "fruit":
            return f"回复{int(value)}点生命并提升移速30%持续{int(duration)}秒" if duration else f"回复{int(value)}点生命"
        return pdef.get("description", "无")

    # ── 交互 ──────────────────────────────────────────────────

    def on_mouse_motion(self, x, y, dx, dy):
        """更新悬停状态：返回按钮 / Tab / 条目行"""
        self.back_hover = self.back_rect.point_in_rect((x, y))
        for name, rect in self.tab_rects.items():
            self.tab_hover[name] = rect.point_in_rect((x, y))
        self.row_hover = -1
        for rect, idx in self.row_rects:
            if rect.point_in_rect((x, y)):
                self.row_hover = idx
                break

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """滚轮滚动条目列表（条目较多超出屏幕时）"""
        self.scroll_offset -= scroll_y * 30
        max_scroll = max(0, len(self._entries) * self.ROW_STEP - (self.CONTENT_TOP - self.CONTENT_BOTTOM))
        self.scroll_offset = max(0, min(max_scroll, self.scroll_offset))

    def on_mouse_press(self, x, y, button, modifiers):
        sound_manager.play_ui()
        # 顶部 Tab 切换分类
        for name, rect in self.tab_rects.items():
            if rect.point_in_rect((x, y)):
                if name != self._tab:
                    self._tab = name
                    self._rebuild()
                return
        # 条目选中（仅可见行）
        for rect, idx in self.row_rects:
            if rect.point_in_rect((x, y)):
                self._selected = idx
                return
        # 返回按钮
        if self.back_rect.point_in_rect((x, y)):
            self._go_back()
            return

    def on_key_press(self, key, modifiers):
        """ESC 返回上一视图"""
        if key == arcade.key.ESCAPE:
            self._go_back()

    def _go_back(self):
        """返回上一视图；无 back_view 时返回 StartView（延迟导入，项目导航约定）"""
        if self.back_view is not None:
            self.window.show_view(self.back_view)
        else:
            from views.start_view import StartView
            self.window.show_view(StartView(self.window_ref))