"""锻造坊页面：消耗2件Lv.5+的武器/装备 + 锻造费用，锻造出更强物品（支持滚轮滚动）

锻造规则：
- 材料：Lv.5 及以上的武器（不含当前已装备的）和未装备的 Lv.5+ 装备，至少选择2件
- 费用：FORGE_BASE_COST(200) + 材料平均等级 × FORGE_PER_LEVEL_COST(20)，随材料等级递增
- 结果：结果等级 = 两材料等级相加 ± 随机(0~2)（普通与神器统一）；两材料同名效果合并并 +1 级（上限5级）；
  20% 获得一件神器（神器同样使用相加公式，且继承合并效果）
"""

import arcade
import random
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT,
    FORGE_BASE_COST, FORGE_PER_LEVEL_COST, upgrade_mult_product,
    ALL_WEAPON_IDS, ALL_EQUIP_POOL,
)
from db.database import (
    get_weapons, get_equipment_inventory, get_gold,
    spend_gold, delete_weapon, delete_equipment, add_weapon, add_equipment,
)
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
from entities.effects_defs import (
    effects_label, parse_effect_item, EFFECT_LEVEL_MAX,
)
from views.scroll_view import ScrollView
from views.text_cache import TextCache

# 材料最低等级
FORGE_MIN_LEVEL = 5
# 神器概率（20%）
ARTIFACT_CHANCE = 0.2
# 普通结果等级浮动范围（两材料等级相加 ± 0~2）
FORGE_LEVEL_VARIATION = 2


def forge_result_level(mat_levels):
    """计算普通锻造结果等级：两材料等级相加 ± 随机(0~2)，下限 1（用户要求相加公式）

    参数 mat_levels 为材料等级列表（通常2件），返回结果等级（int）。
    """
    sum_level = sum(mat_levels) if mat_levels else 1
    return max(1, sum_level + random.randint(-FORGE_LEVEL_VARIATION, FORGE_LEVEL_VARIATION))


def merge_forge_effects(effects_a, effects_b):
    """合并两份材料的特殊效果（用户需求：同名效果等级+1，上限5级；不同名取并集）

    参数 effects_a/effects_b 为 effects 列表（元素格式 "poison:3" 或纯 id=等级1），
    返回合并后的 effects 列表（level>1 用 "id:level"，level==1 用纯 id，与旧格式兼容）。
    """
    merged = {}
    for e in list(effects_a or []) + list(effects_b or []):
        eid, elvl = parse_effect_item(e)
        if eid in merged:
            # 同名效果：取较高等级并 +1（上限 EFFECT_LEVEL_MAX）
            merged[eid] = min(max(merged[eid], elvl) + 1, EFFECT_LEVEL_MAX)
        else:
            merged[eid] = elvl
    result = []
    for eid, elvl in merged.items():
        result.append(f"{eid}:{elvl}" if elvl > 1 else eid)
    return result

# 神器列表：从武器/装备定义自动筛选 artifact=True 的条目（kind/slot 按定义池推导，避免手写维护）
ARTIFACTS = (
    [{"kind": "weapon", "item_id": wid, "slot": None} for wid, w in {**MELEE_WEAPONS, **RANGED_WEAPONS}.items() if w.get("artifact")]
    + [{"kind": "equipment", "item_id": eid, "slot": slot} for slot, pool in (("helmet", HELMETS), ("armor", ARMORS), ("backpack", BACKPACKS)) for eid, e in pool.items() if e.get("artifact")]
)


class ForgeView(ScrollView):
    def __init__(self, window):
        super().__init__(window)
        # 持久文本缓存（消除 draw_text 每帧重建纹理的 PerformanceWarning）
        self._tc = TextCache()
        self.forge_btn = arcade.XYWH(WINDOW_WIDTH - 120, 40, 200, 40)
        self.selected = []  # 选中的材料 [(type, id)]，type: "weapon" / "equipment"
        # 锻造动画状态（仿市场开箱动画）
        self._forge_opening = False
        self._forge_timer = 0.0
        self._forge_duration = 1.5  # 动画持续时间（秒）
        self._forge_result = None  # {"name","level","color","artifact"}
        self._build_content()

    def _build_content(self):
        """构建材料列表（Lv.5+ 的未装备武器/装备）"""
        # 内容重建：先清空文本缓存，避免旧 key 残留
        self._tc.clear()
        self.content_items = []  # [(type, y, data)]
        pid = self.window.game_state.player_id
        gs = self.window.game_state
        y = 0

        # === 武器材料区 ===
        self.content_items.append(("header", y, "─── 武器材料 (Lv.5+) ───"))
        y -= 22
        weapons = get_weapons(pid)
        # 排除当前已装备的武器，避免锻造后玩家手上武器丢失
        equipped_wid = getattr(gs, "equipped_weapon_id", None)
        weapon_materials = [
            w for w in weapons
            if w["level"] >= FORGE_MIN_LEVEL and w["id"] != equipped_wid
        ]
        if not weapon_materials:
            self.content_items.append(("text", y, "暂无符合条件的武器"))
            y -= 24
        for w in weapon_materials:
            sel = ("weapon", w["id"]) in self.selected
            kind_label = "近战" if w["kind"] == "melee" else "远程"
            eff_txt = ""
            if w.get("effects"):
                eff_txt = f"  效果:{effects_label(w['effects'])}"
            self.content_items.append(("material", y, {
                "mtype": "weapon", "id": w["id"], "selected": sel,
                "level": w["level"],  # 材料等级，用于动态计算锻造费用
                "label": f"[{kind_label}] {w['name']}  Lv.{w['level']}  伤害:{w['damage']:.0f}{eff_txt}",
            }))
            y -= 30
        y -= 10

        # === 装备材料区 ===
        self.content_items.append(("header", y, "─── 装备材料 (Lv.5+) ───"))
        y -= 22
        equip_items = get_equipment_inventory(pid)
        # 只取未装备的（is_equipped=0），已装备的不能作为材料
        equip_materials = [
            e for e in equip_items
            if e["level"] >= FORGE_MIN_LEVEL and not e["is_equipped"]
        ]
        if not equip_materials:
            self.content_items.append(("text", y, "暂无符合条件的装备"))
            y -= 24
        for e in equip_materials:
            sel = ("equipment", e["id"]) in self.selected
            sub = f"防+{e['defense']}" if e["defense"] else f"容量:{e['capacity']}"
            eff_txt = ""
            if e.get("effects"):
                eff_txt = f"  效果:{effects_label(e['effects'])}"
            self.content_items.append(("material", y, {
                "mtype": "equipment", "id": e["id"], "selected": sel,
                "level": e["level"],  # 材料等级，用于动态计算锻造费用
                "label": f"Lv.{e['level']} {e['name']}  {sub}{eff_txt}",
            }))
            y -= 30

        self.content_height = abs(y) + 300  # 总内容高度

    def get_bg_color(self):
        return (35, 20, 25)  # 暗红氛围，贴合锻造主题

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """覆盖基类：锻造动画中禁止滚动"""
        if self._forge_opening:
            return
        super().on_mouse_scroll(x, y, scroll_x, scroll_y)

    def on_update(self, delta_time: float):
        """更新锻造动画计时器"""
        if self._forge_opening:
            self._forge_timer += delta_time
            if self._forge_timer >= self._forge_duration:
                self._forge_opening = False
                self._forge_result = None

    def _forge_cost(self):
        """按已选材料平均等级计算动态锻造费用：基础费用 + 平均等级 × 每级递增费用"""
        if len(self.selected) < 2:
            return FORGE_BASE_COST
        levels = [
            data["level"]
            for item_type, _y, data in self.content_items
            if item_type == "material"
            and (data["mtype"], data["id"]) in self.selected
            and data.get("level")
        ]
        if len(levels) < 2:
            return FORGE_BASE_COST
        avg_level = round(sum(levels) / len(levels))
        return FORGE_BASE_COST + avg_level * FORGE_PER_LEVEL_COST

    def on_draw(self):
        self.clear()
        pid = self.window.game_state.player_id
        gold = get_gold(pid)
        offset = self.scroll_offset

        # === 固定头部 ===
        # 持久文本：标题
        self._tc.text("header_title", "锻 造 坊", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30,
                      arcade.color.GOLD, 30, anchor_x="center")
        # 持久文本：金币数
        self._tc.text("header_gold", f"金币: {gold}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
                      arcade.color.YELLOW, 18, anchor_x="center")
        forge_cost = self._forge_cost()
        # 持久文本：规则说明
        self._tc.text(
            "header_rule",
            "消耗至少2件Lv.5+材料，费用随材料等级递增；结果等级=材料等级相加±2，"
            "同名效果合并升级，20%获得神器",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 80,
                         arcade.color.GRAY, 11, anchor_x="center")
        # 已选材料数（动态显示）
        selected_color = arcade.color.YELLOW if len(self.selected) >= 2 else arcade.color.GRAY
        # 持久文本：已选材料数（颜色随选中数变化，同 key 颜色变化自动重建纹理）
        self._tc.text("header_selected", f"已选材料: {len(self.selected)}", 60, WINDOW_HEIGHT - 100,
                      selected_color, 12)

        content_top = WINDOW_HEIGHT - 125

        # === 可滚动内容（材料列表）===
        for i, (item_type, item_y, data) in enumerate(self.content_items):
            screen_y = content_top + item_y + offset
            if screen_y < 40 or screen_y > content_top + 20:
                continue

            if item_type == "header":
                # 持久文本：滚动区 header 类型（key 用索引区分）
                self._tc.text(f"header_{i}", data, 60, screen_y, arcade.color.LIGHT_GRAY, 13)
            elif item_type == "text":
                # 持久文本：滚动区 text 类型
                self._tc.text(f"text_{i}", data, 60, screen_y, arcade.color.GRAY, 11)
            elif item_type == "material":
                # 选中的材料行加高亮背景
                if data["selected"]:
                    arcade.draw_rect_filled(
                        arcade.XYWH(WINDOW_WIDTH // 2, screen_y + 5,
                                    WINDOW_WIDTH - 240, 24),
                        (60, 60, 40),
                    )
                color = arcade.color.YELLOW if data["selected"] else arcade.color.CORNFLOWER_BLUE
                mark = "✓ " if data["selected"] else "○ "
                # 持久文本：滚动区 material 类型（选中态颜色变化，同 key 自动重建）
                self._tc.text(f"material_{i}", f"{mark}{data['label']}", 70, screen_y, color, 12)

        # 滚动条
        if self.content_height > content_top - 60:
            view_h = content_top - 60
            bar_h = max(30, view_h * view_h / self.content_height)
            max_scroll = max(1, self.content_height - 400)
            bar_y = content_top - bar_h - (view_h - bar_h) * self.scroll_offset / max_scroll
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH - 12, bar_y, 6, bar_h),
                (120, 120, 120),
            )

        # === 固定底部导航 ===
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        # 持久文本：返回按钮
        self._tc.text("nav_back", "返回大厅", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

        # 锻造按钮（需至少2件材料 + 动态锻造费用）
        can_forge = len(self.selected) >= 2 and gold >= forge_cost
        forge_color = arcade.color.DARK_ORANGE if can_forge else (60, 60, 60)
        arcade.draw_rect_filled(self.forge_btn, forge_color)
        arcade.draw_rect_outline(self.forge_btn, arcade.color.WHITE, border_width=2)
        # 持久文本：锻造按钮（动态费用文本，同 key 复用）
        self._tc.text("forge_btn", f"锻 造 ({forge_cost}G)",
                      self.forge_btn.center_x, self.forge_btn.center_y,
                      arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")

        # === 锻造动画覆盖层（仿市场开箱动画）===
        if self._forge_opening and self._forge_result:
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
                (0, 0, 0, 180),
            )
            progress = min(1.0, self._forge_timer / self._forge_duration)
            result = self._forge_result
            # 缩放效果：从0.5倍放大到1.0倍
            scale = 0.5 + 0.5 * min(1.0, progress * 2)
            # 闪光效果：前0.3秒有闪光
            flash_alpha = max(0, 1.0 - progress * 3) * 255
            # 锻造炉图标（用结果颜色）
            result_color = result.get("color", (255, 200, 50))
            box_size = int(80 * scale)
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 40, box_size, box_size),
                result_color,
            )
            if flash_alpha > 0:
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 40,
                                box_size + 20, box_size + 20),
                    (255, 255, 255, int(flash_alpha)),
                )
            # 获得物品信息（动画后半段显示）
            if progress > 0.5:
                text_alpha = min(1.0, (progress - 0.5) * 4)
                name = result.get("name", "")
                level = result.get("level", 1)
                if result.get("artifact"):
                    text = f"✨ 锻造出神器: {name} ✨"
                else:
                    text = f"锻造成功! 获得 {name} Lv.{level}"
                # 持久文本：锻造动画结果
                self._tc.text(
                    "forge_result", text, WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 40,
                    arcade.color.GOLD, 20, anchor_x="center", anchor_y="center",
                )

    def on_mouse_press(self, x, y, button, modifiers):
        # 动画中禁止所有点击
        if self._forge_opening:
            return

        pid = self.window.game_state.player_id
        offset = self.scroll_offset

        # 固定按钮
        if self.handle_back_click(x, y):
            return
        if self.forge_btn.point_in_rect((x, y)):
            self._do_forge()
            return

        # 检测材料行点击（选中/取消）
        content_top = WINDOW_HEIGHT - 125
        for item_type, item_y, data in self.content_items:
            screen_y = content_top + item_y + offset
            if screen_y < 40 or screen_y > content_top + 20:
                continue
            if item_type == "material":
                row = arcade.XYWH(WINDOW_WIDTH // 2, screen_y + 5, WINDOW_WIDTH - 240, 24)
                if row.point_in_rect((x, y)):
                    key = (data["mtype"], data["id"])
                    if key in self.selected:
                        self.selected.remove(key)
                    elif len(self.selected) < 2:
                        # 锻造只能选择2件材料
                        self.selected.append(key)
                    self._build_content()  # 重建以刷新选中高亮
                    return

    def _do_forge(self):
        """执行锻造：扣动态费用 + 消耗材料 + 合并效果 + 随机产出

        结果等级 = 两材料等级相加 ± 随机(0~2)（下限1）；同名效果合并并+1级；20% 神器
        """
        pid = self.window.game_state.player_id
        if len(self.selected) < 2:
            return
        forge_cost = self._forge_cost()
        gold = get_gold(pid)
        if gold < forge_cost:
            return

        # 反查两件材料的等级与效果（删除前）
        weapons = {w["id"]: w for w in get_weapons(pid)}
        equips = {e["id"]: e for e in get_equipment_inventory(pid)}
        mat_levels = []
        mat_effects = []
        for mtype, mid in self.selected:
            if mtype == "weapon" and mid in weapons:
                mat_levels.append(weapons[mid]["level"])
                mat_effects.append(weapons[mid].get("effects", []))
            elif mtype == "equipment" and mid in equips:
                mat_levels.append(equips[mid]["level"])
                mat_effects.append(equips[mid].get("effects", []))

        # 扣除金币
        if not spend_gold(pid, forge_cost):
            return

        # 消耗选中的材料（删除）
        for mtype, mid in self.selected:
            if mtype == "weapon":
                delete_weapon(pid, mid)
            else:
                delete_equipment(pid, mid)
        self.selected = []

        # 合并两材料的效果（同名效果等级+1，上限5级；不同名取并集）
        merged_effects = merge_forge_effects(
            mat_effects[0] if mat_effects else [],
            mat_effects[1] if len(mat_effects) > 1 else [],
        )

        # 结果等级 = 两材料等级相加 ± 2（普通与神器统一，用户需求：神器不再随机等级）
        result_level = forge_result_level(mat_levels)

        # 判定结果：20% 神器，80% 普通（两者等级均用相加公式）
        if random.random() < ARTIFACT_CHANCE:
            artifact = random.choice(ARTIFACTS)
            result = self._grant_artifact(pid, artifact, result_level, merged_effects)
        else:
            result = self._grant_random(pid, result_level, merged_effects)

        # 触发动画
        self._forge_opening = True
        self._forge_timer = 0.0
        self._forge_result = result
        self._build_content()

    def _grant_artifact(self, pid, artifact, result_level, merged_effects):
        """发放神器并返回结果信息（等级 = 两材料等级相加±2，与普通结果统一）

        用户需求：神器不再随机等级；吞天包也拥有等级（不提升容量，容量在
        add_equipment 中固定为定义值），并继承合并效果、可作为锻造材料。
        """
        item_id = artifact["item_id"]
        level = result_level
        if artifact["kind"] == "weapon":
            wdef = MELEE_WEAPONS.get(item_id) or RANGED_WEAPONS.get(item_id)
            # 根据等级调整伤害（平方根亚线性倍率，与升级系统一致）
            adjusted_damage = round(wdef["damage"] * upgrade_mult_product(level), 1)
            add_weapon(pid, item_id, wdef["name"], wdef["kind"],
                       adjusted_damage, wdef.get("attack_speed", 1.0), level,
                       effects=merged_effects)
            return {
                "name": wdef["name"], "level": level,
                "color": wdef.get("color", (255, 255, 255)), "artifact": True,
            }
        else:
            eq_def = HELMETS.get(item_id) or ARMORS.get(item_id) or BACKPACKS.get(item_id)
            add_equipment(pid, item_id, artifact["slot"], level, effects=merged_effects)
            return {
                "name": eq_def["name"], "level": level,
                "color": eq_def.get("color", (200, 200, 255)), "artifact": True,
            }

    def _grant_random(self, pid, result_level, merged_effects):
        """普通结果：随机一件武器或装备（等级与效果由材料决定）"""
        if random.random() < 0.5:
            # 50% 概率出武器
            wid = random.choice(ALL_WEAPON_IDS)
            wdef = RANGED_WEAPONS.get(wid) or MELEE_WEAPONS.get(wid)
            # 根据结果等级调整伤害（平方根亚线性倍率，与升级系统一致）
            adjusted_damage = round(wdef["damage"] * upgrade_mult_product(result_level), 1)
            add_weapon(pid, wid, wdef["name"], wdef["kind"],
                       adjusted_damage, wdef.get("attack_speed", 1.0), result_level,
                       effects=merged_effects)
            return {
                "name": wdef["name"], "level": result_level,
                "color": wdef.get("color", (255, 200, 50)), "artifact": False,
            }
        else:
            # 50% 概率出装备
            slot, eid = random.choice(ALL_EQUIP_POOL)
            eq_def = HELMETS.get(eid) or ARMORS.get(eid)
            add_equipment(pid, eid, slot, result_level, effects=merged_effects)
            return {
                "name": eq_def["name"], "level": result_level,
                "color": eq_def.get("color", (150, 180, 200)), "artifact": False,
            }
