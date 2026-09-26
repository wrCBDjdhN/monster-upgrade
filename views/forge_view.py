"""锻造坊页面：消耗2件Lv.5+的武器/装备 + 锻造费用，锻造出更强物品（支持滚轮滚动）

锻造规则：
- 材料：Lv.5 及以上的武器（不含当前已装备的）和未装备的 Lv.5+ 装备，至少选择2件
- 费用：FORGE_BASE_COST(200) + 材料平均等级 × FORGE_PER_LEVEL_COST(20)，随材料等级递增
- 结果：结果等级 = 两材料等级相加 ± 随机(0~2)（普通与神器统一）；两材料同名效果合并并 +1 级（上限5级）；
  神器概率按材料组合动态判定：两件普通 20%、一件神器+一件普通 60%、两件神器 100%
  （神器同样使用相加公式，且继承合并效果）

阶段 10 设施增益接入：
- 费用折扣：_forge_cost() 与配方制作费统一套 apply_forge_discount（Lv2 = 8 折，Lv3 = 7 折）。
- 神器率：forge_artifact_chance() 叠加 forge_artifact_bonus(设施等级) 后 clamp ≤ 1.0。
- 熔炉余温：Lv3 锻造成功后按概率返还部分锻造费（FORGE_REFUND_*），飘字提示。
- 顶部「设施 Lv.N ▲」按钮 → FacilityView("forge") 升级入口。
"""

import arcade
import math
import random
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT,
    FORGE_BASE_COST, FORGE_PER_LEVEL_COST, upgrade_mult_product,
    ALL_WEAPON_IDS, ALL_EQUIP_POOL,
    FORGE_ARTIFACT_CHANCE_NONE, FORGE_ARTIFACT_CHANCE_MIXED,
    FORGE_ARTIFACT_CHANCE_DOUBLE,
    FORGE_REFUND_LEVEL, FORGE_REFUND_CHANCE, FORGE_REFUND_RATIO,
)
from db.database import (
    get_weapons, get_equipment_inventory, get_gold, get_warehouse,
    spend_gold, delete_weapon, delete_equipment, add_weapon, add_equipment,
    get_codex_count, spend_warehouse_item, add_gold, get_facility_level,
)
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
from entities.resource_defs import RESOURCES
from entities.forge_recipes import (
    CODEX_CATEGORY_LABELS, FORGE_RECIPES,
    get_codex_category_total, get_result_def,
)
from entities.effects_defs import (
    effects_label, parse_effect_item, EFFECT_LEVEL_MAX,
)
from entities.facility_defs import forge_cost_discount, forge_artifact_bonus
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from game.sound_manager import sound_manager

# 材料最低等级
FORGE_MIN_LEVEL = 5
# 普通结果等级浮动范围（两材料等级相加 ± 0~2）
FORGE_LEVEL_VARIATION = 2
# 配方页飘字存活时长（秒）
FORGE_TOAST_LIFE = 2.0
# 锻造坊设施 id（查库/进设施页用；等级 0 = 未建造，入口守卫在 start_view._enter_facility）
FORGE_FACILITY_ID = "forge"

# ── 设施状态行 + 升级入口按钮几何（绘制与点击共用，避免两处坐标漂移）──
# 锻造坊顶部右侧已被 锻造/配方 Tab 占用，故状态行与按钮统一放左侧（避开居中标题与规则文本）
# 状态文本锚点由按钮几何派生：放在按钮右侧 12px、与按钮同高（绘制时按 anchor_y="center" 垂直居中）
# 原先与左侧「已选材料」行同为 (60, WINDOW_HEIGHT-100) 而互相压字，故拆到按钮右侧
FACILITY_BTN_W, FACILITY_BTN_H = 160, 34     # 升级入口按钮尺寸
FACILITY_BTN_CX, FACILITY_BTN_CY = 125, WINDOW_HEIGHT - 52   # 顶部左侧按钮
FACILITY_STATUS_X = FACILITY_BTN_CX + FACILITY_BTN_W / 2 + 12   # 按钮右缘外 12px，状态行文本左锚点
FACILITY_STATUS_Y = FACILITY_BTN_CY                              # 与按钮同高


def apply_forge_discount(cost, level: int) -> int:
    """锻造坊费用折扣（纯函数，不查库）

    int(cost × (1 - 折扣率)) 向下取整，最低 1 金（杜绝 0 价/负价）。
    折扣率取 entities.facility_defs.forge_cost_discount(level)：
    Lv0（未建造）/Lv1 = 0.0 → 原价，越界等级同样回 0.0（回退验证安全）。
    """
    disc = forge_cost_discount(level)
    if disc <= 0.0:
        return int(cost)
    return max(1, int(int(cost) * (1 - disc)))


def forge_discount_label(level: int) -> str:
    """顶部费用折扣文案：`10 - int(折扣率 × 10)` 折（Lv2 = 8 折，Lv3 = 7 折）

    无折扣（Lv0/Lv1）时返回「费用无折扣」，与市场状态行口径一致。
    """
    disc = forge_cost_discount(level)
    if disc <= 0.0:
        return "费用无折扣"
    return f"费用 {10 - int(disc * 10)} 折"


def forge_artifact_chance(artifact_count: int, facility_level: int) -> float:
    """锻造出神器的概率（纯函数，不查库）：基础概率 + 设施等级加成，clamp ≤ 1.0

    基础概率按材料中的神器件数：0 件 = FORGE_ARTIFACT_CHANCE_NONE、
    1 件 = ..._MIXED、≥2 件 = ..._DOUBLE；叠加 entities.facility_defs
    .forge_artifact_bonus(设施等级)（Lv2 = +10%、Lv3 = +15%），上限 1.0。
    """
    if artifact_count >= 2:
        chance = FORGE_ARTIFACT_CHANCE_DOUBLE
    elif artifact_count == 1:
        chance = FORGE_ARTIFACT_CHANCE_MIXED
    else:
        chance = FORGE_ARTIFACT_CHANCE_NONE
    return min(1.0, chance + forge_artifact_bonus(facility_level))


def forge_refund_amount(forge_cost: int, facility_level: int, roll: float) -> int:
    """熔炉余温返还金币（纯函数，不查库、不加金币）

    设施等级需达 FORGE_REFUND_LEVEL 且 roll < FORGE_REFUND_CHANCE 才返还，
    返还额 = int(锻造费 × FORGE_REFUND_RATIO)；不满足条件或取整为 0 时返回 0。
    roll 由调用方传 random.random()，便于 REPL 行为链注入验证。
    """
    if facility_level < FORGE_REFUND_LEVEL or roll >= FORGE_REFUND_CHANCE:
        return 0
    return int(int(forge_cost) * FORGE_REFUND_RATIO)



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
# recipe_only（图鉴配方专属）排除：唯一来源是"配方"页制作，不进普通锻造的神器概率池
ARTIFACTS = (
    [{"kind": "weapon", "item_id": wid, "slot": None} for wid, w in {**MELEE_WEAPONS, **RANGED_WEAPONS}.items() if w.get("artifact") and not w.get("recipe_only")]
    + [{"kind": "equipment", "item_id": eid, "slot": slot} for slot, pool in (("helmet", HELMETS), ("armor", ARMORS), ("backpack", BACKPACKS)) for eid, e in pool.items() if e.get("artifact") and not e.get("recipe_only")]
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
        # ── 顶部 Tab：锻造 / 配方（阶段7，图鉴集齐解锁专属装备制作）──
        # 放在标题行右侧（x0=1028, y=690）：标题居中、金币/规则文本更靠下，
        # 底部 content_top(WINDOW_HEIGHT-125) 是材料列表区，三者皆不重叠
        self._tabs = ["锻造", "配方"]
        self._tab = "锻造"
        self.tab_rects = {}
        self.tab_hover = {}
        _tw, _th, _gap = 90, 30, 12
        _x0 = WINDOW_WIDTH - 60 - (_tw * 2 + _gap)
        for i, _name in enumerate(self._tabs):
            self.tab_rects[_name] = arcade.XYWH(
                _x0 + i * (_tw + _gap) + _tw / 2, WINDOW_HEIGHT - 30, _tw, _th)
            self.tab_hover[_name] = False
        # 配方页状态（4 条配方固定，无需滚动）
        self._recipe_rows = []       # 每条配方一行：{recipe,count,total,unlocked,can_make}
        self._recipe_btns = []       # [(rect, idx)] 制作按钮命中区
        self._recipe_hover = -1
        self._toasts = []            # 制作成功/失败飘字
        self._pulse = 0.0
        # 阶段10 设施（Lv.N）缓存：**每帧查库不可取**，构造时读一次 + on_show_view 刷新
        self._facility_level = 0
        self._refresh_facility_level()
        self._build_content()
        self._refresh_recipes()

    def _refresh_facility_level(self):
        """重读锻造坊设施等级（进入界面 / 从设施页返回时调用，避免每帧查库）"""
        pid = self.window.game_state.player_id
        self._facility_level = get_facility_level(pid, FORGE_FACILITY_ID)

    def on_show_view(self):
        """每次进入刷新设施等级、配方解锁进度与材料持有（可能在设施页/图鉴/市场页有变动）"""
        super().on_show_view()
        self._refresh_facility_level()
        self._build_content()
        self._refresh_recipes()

    def facility_status_text(self) -> str:
        """顶部状态行文案：Lv.N + 费用折扣（Lv0/Lv1 无折扣，Lv2/3 显示 X 折）"""
        return f"设施 Lv.{self._facility_level} · {forge_discount_label(self._facility_level)}"

    def _facility_btn_rect(self) -> arcade.Rect:
        """设施升级入口按钮矩形（绘制与点击共用，保证判定与画面一致）"""
        return arcade.XYWH(FACILITY_BTN_CX, FACILITY_BTN_CY, FACILITY_BTN_W, FACILITY_BTN_H)

    def _draw_facility_bar(self):
        """绘制顶部设施状态行 + 升级入口按钮（实心填充，禁线框）"""
        # 状态文本锚在按钮右侧、与按钮同高（Y 用 anchor_y="center" 垂直居中），不再与「已选材料」行重叠
        self._tc.text("facility_status", self.facility_status_text(),
                      FACILITY_STATUS_X, FACILITY_STATUS_Y, arcade.color.GOLD, 12,
                      anchor_x="left", anchor_y="center")
        btn = self._facility_btn_rect()
        arcade.draw_rect_filled(btn, (78, 48, 30))
        self._tc.text("facility_btn", f"设施 Lv.{self._facility_level} ▲",
                      btn.center_x, btn.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

    def _open_facility(self):
        """进入锻造坊设施详情页（建造/升级）——导航唯一模式：处理函数内延迟 import"""
        from views.facility_view import FacilityView
        self.window.show_view(FacilityView(self.window_ref, FORGE_FACILITY_ID))


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

    # ── 阶段7：配方页（图鉴集齐 → 扣金币+资源 → 制作专属装备）──────────

    def _refresh_recipes(self):
        """重算 4 条配方的解锁进度与可制作状态（材料持有量一并算出）

        金币费用经 apply_forge_discount 计入设施折扣（Lv0/Lv1 = 原价），
        展示与扣款共用 row["gold"]，保证「看到的价 = 扣的价」。
        """
        pid = self.window.game_state.player_id
        gold = get_gold(pid)
        wh = {it["item_id"]: int(it["quantity"]) for it in get_warehouse(pid)}
        rows = []
        for r in FORGE_RECIPES:
            cat = r["codex_category"]
            total = get_codex_category_total(cat)
            count = get_codex_count(pid, cat)
            unlocked = count >= total and total > 0
            res_ok = all(wh.get(rid, 0) >= need for rid, need in r["resources"].items())
            cost_gold = apply_forge_discount(int(r["gold"]), self._facility_level)
            rows.append({
                "recipe": r, "count": count, "total": total,
                "unlocked": unlocked,
                "gold": cost_gold,
                "can_make": unlocked and gold >= cost_gold and res_ok,
                "label": CODEX_CATEGORY_LABELS.get(cat, cat),
            })
        self._recipe_rows = rows

    def _draw_tabs(self):
        """绘制 锻造/配方 Tab（当前页高亮，实心填充不描边）"""
        for name, rect in self.tab_rects.items():
            active = name == self._tab
            bg = arcade.color.DARK_ORANGE if active else (58, 40, 40)
            if not active and self.tab_hover.get(name):
                bg = (84, 56, 52)
            arcade.draw_rect_filled(rect, bg)
            self._tc.text(f"tab_{name}", name, rect.center_x, rect.center_y,
                          arcade.color.WHITE if active else arcade.color.LIGHT_GRAY,
                          14, anchor_x="center", anchor_y="center")

    def _draw_recipes(self):
        """绘制配方列表：4 行卡片（进度 X/Y + 费用 + 制作按钮）

        未解锁行整体压暗（灰）；已解锁且材料齐备时按钮做轻微呼吸提示。
        渲染铁律：一律不透明实心填充。
        """
        self._recipe_btns = []
        if not self._recipe_rows:
            return
        w = WINDOW_WIDTH - 120
        h = 64
        step = 76
        top = WINDOW_HEIGHT - 170
        for i, row in enumerate(self._recipe_rows):
            cy = top - i * step
            rect = arcade.XYWH(WINDOW_WIDTH // 2, cy, w, h)
            unlocked = row["unlocked"]
            if unlocked:
                bg = (56, 46, 30) if row["can_make"] else (44, 42, 36)
            else:
                bg = (40, 38, 42)
            arcade.draw_rect_filled(rect, bg)
            r = row["recipe"]
            name_color = arcade.color.GOLD if unlocked else arcade.color.GRAY
            self._tc.text(f"recipe_name_{i}", f"★ {r['name']}  Lv.{r['result_level']}",
                          rect.left + 16, rect.top - 12, name_color, 15)
            self._tc.text(
                f"recipe_prog_{i}",
                f"图鉴进度 {row['count']}/{row['total']}（{row['label']}图鉴）",
                rect.left + 16, rect.top - 32,
                arcade.color.YELLOW if unlocked else arcade.color.GRAY, 12)
            # 费用：金币 + 仓库资源（取 config 定义的消耗表）
            res_txt = " ".join(
                f"{RESOURCES.get(rid, {}).get('name', rid)}×{need}"
                for rid, need in r["resources"].items()
            )
            self._tc.text(f"recipe_cost_{i}", f"费用 {row['gold']}G + {res_txt}",
                          rect.left + 16, rect.top - 50, arcade.color.LIGHT_GRAY, 12)
            if not unlocked:
                lack = max(0, row["total"] - row["count"])
                self._tc.text(f"recipe_lock_{i}", f"未解锁（还差 {lack} 条）",
                              rect.right - 16, rect.center_y, arcade.color.GRAY, 13,
                              anchor_x="right", anchor_y="center")
            else:
                btn = arcade.XYWH(rect.right - 86, rect.center_y, 140, 36)
                label = "制 作"
                if row["can_make"]:
                    glow = int(120 + 40 * (0.5 + 0.5 * math.sin(self._pulse * 4)))
                    if self._recipe_hover == i:
                        glow = min(200, glow + 30)
                    btn_bg = (glow, int(glow * 0.55), 20)
                else:
                    btn_bg, label = (70, 70, 70), "材料不足"
                arcade.draw_rect_filled(btn, btn_bg)
                self._tc.text(f"recipe_btn_{i}", label, btn.center_x, btn.center_y,
                              arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")
                self._recipe_btns.append((btn, i))

    def _draw_toasts(self):
        """制作结果飘字：向上浮动渐隐（色值向背景色插值，保持不透明实心）"""
        bg = (35, 20, 25)  # 与 get_bg_color 一致
        for i, t in enumerate(self._toasts):
            fade = max(0.0, 1.0 - max(0.0, t["age"] - 1.2) / 0.8)
            color = tuple(int(bg[c] + (t["color"][c] - bg[c]) * fade) for c in range(3))
            self._tc.text(f"rtoast_{i}", t["text"], WINDOW_WIDTH // 2,
                          WINDOW_HEIGHT - 210 - t["age"] * 40,
                          color, 20, anchor_x="center", anchor_y="center", bold=True)

    def _toast(self, text: str, color):
        """新增一条飘字（最多 3 条，超出丢弃最旧）"""
        self._toasts.append({"text": text, "color": color, "age": 0.0})
        if len(self._toasts) > 3:
            self._toasts.pop(0)

    def _craft_recipe(self, idx: int):
        """制作配方产物：校验解锁+金币+资源 → 扣除 → add_weapon/add_equipment

        扣款顺序：先校验全部资源（避免半扣），再扣资源、最后扣金币。
        单机单线程下扣除不会失败；spend_* 返回 False 时直接中止并提示。
        """
        if not (0 <= idx < len(self._recipe_rows)):
            return
        row = self._recipe_rows[idx]
        if not row["unlocked"]:
            self._toast("配方未解锁", arcade.color.GRAY)
            return
        pid = self.window.game_state.player_id
        r = row["recipe"]
        # 金币费经设施折扣（与 _draw_recipes 展示同源，见 _refresh_recipes）
        gold_need = int(row["gold"])
        res_need = dict(r["resources"])
        if get_gold(pid) < gold_need:
            self._toast("金币不足", (255, 160, 120))
            return
        wh = {it["item_id"]: int(it["quantity"]) for it in get_warehouse(pid)}
        short = [
            f"{RESOURCES.get(rid, {}).get('name', rid)}×{need - wh.get(rid, 0)}"
            for rid, need in res_need.items() if wh.get(rid, 0) < need
        ]
        if short:
            self._toast("资源不足：" + " ".join(short), (255, 160, 120))
            return
        for rid, need in res_need.items():
            spend_warehouse_item(pid, rid, need)
        if not spend_gold(pid, gold_need):
            self._toast("金币扣除失败", (255, 160, 120))
            return
        info = get_result_def(r["result_item_id"])
        if not info:
            self._toast("配方数据异常", (255, 120, 120))
            return
        kind, slot, rdef = info
        level = int(r["result_level"])
        if kind == "weapon":
            damage = round(rdef["damage"] * upgrade_mult_product(level), 1)
            add_weapon(pid, r["result_item_id"], rdef["name"], rdef["kind"],
                       damage, rdef.get("attack_speed", 1.0), level)
        else:
            add_equipment(pid, r["result_item_id"], slot, level)
        self._toast(f"制作成功：{rdef['name']} Lv.{level}", arcade.color.GOLD)
        sound_manager.play_forge()
        self._refresh_recipes()

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """覆盖基类：锻造动画中禁止滚动（配方页无需滚动）"""
        if self._forge_opening or self._tab == "配方":
            return
        super().on_mouse_scroll(x, y, scroll_x, scroll_y)

    def on_mouse_motion(self, x, y, dx, dy):
        """悬停：Tab 切换高亮 + 配方制作按钮高亮（滚动条拖拽交给基类）"""
        super().on_mouse_motion(x, y, dx, dy)
        for name, rect in self.tab_rects.items():
            self.tab_hover[name] = rect.point_in_rect((x, y))
        self._recipe_hover = -1
        for rect, idx in self._recipe_btns:
            if rect.point_in_rect((x, y)):
                self._recipe_hover = idx
                break

    def on_update(self, delta_time: float):
        """更新锻造动画计时器 + 制作按钮呼吸相位 + 飘字计时"""
        self._pulse += delta_time
        for t in self._toasts:
            t["age"] += delta_time
        self._toasts = [t for t in self._toasts if t["age"] < FORGE_TOAST_LIFE]
        if self._forge_opening:
            self._forge_timer += delta_time
            if self._forge_timer >= self._forge_duration:
                self._forge_opening = False
                self._forge_result = None

    def _forge_cost(self):
        """按已选材料平均等级计算动态锻造费用：基础费用 + 平均等级 × 每级递增费用

        阶段10：整价再套设施折扣 apply_forge_discount（Lv2 = 8 折，Lv3 = 7 折；
        Lv0/Lv1 折扣为 0 → 与旧口径完全一致）。
        """
        if len(self.selected) < 2:
            return apply_forge_discount(FORGE_BASE_COST, self._facility_level)
        levels = [
            data["level"]
            for item_type, _y, data in self.content_items
            if item_type == "material"
            and (data["mtype"], data["id"]) in self.selected
            and data.get("level")
        ]
        if len(levels) < 2:
            return apply_forge_discount(FORGE_BASE_COST, self._facility_level)
        avg_level = round(sum(levels) / len(levels))
        raw = FORGE_BASE_COST + avg_level * FORGE_PER_LEVEL_COST
        return apply_forge_discount(raw, self._facility_level)

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
            "同名效果合并升级，神器概率：2普通20% / 1神器+1普通60% / 2神器100%",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - 80,
                         arcade.color.GRAY, 11, anchor_x="center")
        # 已选材料数（动态显示）
        selected_color = arcade.color.YELLOW if len(self.selected) >= 2 else arcade.color.GRAY
        # 持久文本：已选材料数（颜色随选中数变化，同 key 颜色变化自动重建纹理）
        self._tc.text("header_selected", f"已选材料: {len(self.selected)}", 60, WINDOW_HEIGHT - 100,
                      selected_color, 12)

        # 阶段10 设施状态行 + 升级入口按钮（顶部左侧，避开居中标题/规则文本与右侧 Tab）
        self._draw_facility_bar()

        # 顶部 Tab（锻造 / 配方），两页共用
        self._draw_tabs()

        content_top = WINDOW_HEIGHT - 125

        if self._tab == "配方":
            # 配方页：4 条配方固定展示，无需滚动（不画材料列表/滚动条/锻造按钮）
            self._draw_recipes()
        else:
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

        # 锻造按钮（仅"锻造"页显示；配方页每行自带"制 作"按钮）
        if self._tab == "锻造":
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

        # 制作结果飘字（配方页）+ 熔炉余温返还飘字（锻造页）——最上层，
        # 置于动画覆盖层之后，保证锻造动画期间也能看清余温返还金额
        if self._toasts:
            self._draw_toasts()

    def on_mouse_press(self, x, y, button, modifiers):
        # 动画中禁止所有点击
        if self._forge_opening:
            return

        sound_manager.play_ui()
        pid = self.window.game_state.player_id
        offset = self.scroll_offset

        # 顶部 Tab 切换（锻造 / 配方），先于其它固定按钮判定
        for name, rect in self.tab_rects.items():
            if rect.point_in_rect((x, y)):
                if name != self._tab:
                    self._tab = name
                    self._tc.clear()  # 换页清空文本缓存，避免两页同 key 文本串味
                return

        # 固定按钮
        if self.handle_back_click(x, y):
            return

        # 阶段10 设施升级入口（顶部左侧「设施 Lv.N ▲」）：进设施详情页建造/升级
        if self._facility_btn_rect().point_in_rect((x, y)):
            self._open_facility()
            return

        if self._tab == "配方":
            # 配方页：仅"制 作"按钮可点（未解锁行不生成命中区）
            for rect, idx in self._recipe_btns:
                if rect.point_in_rect((x, y)):
                    self._craft_recipe(idx)
                    return
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

        结果等级 = 两材料等级相加 ± 随机(0~2)（下限1）；同名效果合并并+1级；
        神器概率按材料组合动态判定：两件普通 20%、一件神器+一件普通 60%、两件神器 100%
        """
        pid = self.window.game_state.player_id
        if len(self.selected) < 2:
            return
        forge_cost = self._forge_cost()
        gold = get_gold(pid)
        if gold < forge_cost:
            return

        # 反查两件材料的等级、效果与是否神器（删除前；神器判定 = defs 中 artifact=True）
        weapons = {w["id"]: w for w in get_weapons(pid)}
        equips = {e["id"]: e for e in get_equipment_inventory(pid)}
        mat_levels = []
        mat_effects = []
        mat_artifact_counts = 0
        for mtype, mid in self.selected:
            if mtype == "weapon" and mid in weapons:
                mat_levels.append(weapons[mid]["level"])
                mat_effects.append(weapons[mid].get("effects", []))
                wid = weapons[mid]["item_id"]
                wdef = MELEE_WEAPONS.get(wid) or RANGED_WEAPONS.get(wid)
                if wdef and wdef.get("artifact"):
                    mat_artifact_counts += 1
            elif mtype == "equipment" and mid in equips:
                mat_levels.append(equips[mid]["level"])
                mat_effects.append(equips[mid].get("effects", []))
                eid = equips[mid]["item_id"]
                edef = HELMETS.get(eid) or ARMORS.get(eid) or BACKPACKS.get(eid)
                if edef and edef.get("artifact"):
                    mat_artifact_counts += 1

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

        # 判定结果：神器概率按材料中神器数量动态调整 + 设施等级加成（clamp ≤ 1.0）
        # （两件普通 20%、一件神器+一件普通 60%、两件神器 100%；Lv2 +10% / Lv3 +15%）
        artifact_chance = forge_artifact_chance(mat_artifact_counts, self._facility_level)
        if random.random() < artifact_chance:
            artifact = random.choice(ARTIFACTS)
            result = self._grant_artifact(pid, artifact, result_level, merged_effects)
        else:
            result = self._grant_random(pid, result_level, merged_effects)

        # 阶段10 熔炉余温（设施 Lv3）：锻造成功后按概率返还部分锻造费（先加金币再飘字）
        refund = forge_refund_amount(forge_cost, self._facility_level, random.random())
        if refund > 0:
            add_gold(pid, refund)
            self._toast(f"熔炉余温：返还 {refund} 金币", arcade.color.GOLD)

        # 触发动画
        sound_manager.play_forge()
        # 阶段6.2 任务/成就进度：锻造成功（局外事件，锻造坊本就是各端本地 UI/本地库，各端自计）
        from game.mission_tracker import on_event as mission_on_event
        mission_on_event(self, "forge", 1)
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
