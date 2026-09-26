"""锻造配方数据（阶段7：图鉴集齐解锁的专属装备制作表）

FORGE_RECIPES 由 views/forge_view.py 的「配方」页消费，4 条配方各对应一类图鉴：

- 解锁条件：该类图鉴的**常规**条目全部解锁
  （进度 = db.codex.get_codex_count(player_id, codex_category)，
   目标 = get_codex_category_total(codex_category)）
- 集齐总数刻意**不含**配方专属产物（recipe_only=True 的 4 件专属装备/武器）：
  否则「装备图鉴集齐」会要求先拿到装备配方自己的产物 → 自锁死循环。
  产物制作后仍会写 codex_unlocks 记录（但不进计数/分母/档位，见 get_codex_countable_ids）。
- 口径唯一来源 = get_codex_countable_ids()：db 计数、图鉴页分母、配方集齐目标三处同源
- 费用：金币 + 仓库资源，全部取自 config（CODEX_RECIPE_GOLD / CODEX_RECIPE_RESOURCES）
- 产物等级：config.CODEX_RECIPE_LEVEL（固定等级，不参与普通锻造的等级相加公式）
"""

from config import CODEX_RECIPE_GOLD, CODEX_RECIPE_RESOURCES, CODEX_RECIPE_LEVEL
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS, ALL_WEAPONS
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS, POTIONS
from entities.monster_defs import get_all_monster_class_names

# 图鉴类别 → 中文名（与 views/codex_view.py 的 Tab 名一致，用于"集齐XX图鉴解锁"提示）
CODEX_CATEGORY_LABELS = {
    "monster": "怪物",
    "weapon": "武器",
    "equipment": "装备",
    "potion": "药水",
}

# 图鉴集齐解锁的 4 条配方（每类图鉴 1 条）
# 字段：id / name / codex_category / result_item_id / result_level / gold / resources
FORGE_RECIPES = [
    {
        "id": "recipe_monster_blade",
        "name": "屠魔者之刃",
        "codex_category": "monster",
        "result_item_id": "codex_monster_blade",
        "result_level": CODEX_RECIPE_LEVEL,
        "gold": CODEX_RECIPE_GOLD,
        "resources": dict(CODEX_RECIPE_RESOURCES),
    },
    {
        "id": "recipe_weapon_helm",
        "name": "守望者冠冕",
        "codex_category": "weapon",
        "result_item_id": "codex_weapon_helm",
        "result_level": CODEX_RECIPE_LEVEL,
        "gold": CODEX_RECIPE_GOLD,
        "resources": dict(CODEX_RECIPE_RESOURCES),
    },
    {
        "id": "recipe_equipment_bag",
        "name": "万物流转囊",
        "codex_category": "equipment",
        "result_item_id": "codex_equipment_bag",
        "result_level": CODEX_RECIPE_LEVEL,
        "gold": CODEX_RECIPE_GOLD,
        "resources": dict(CODEX_RECIPE_RESOURCES),
    },
    {
        "id": "recipe_potion_cuirass",
        "name": "不朽胸铠",
        "codex_category": "potion",
        "result_item_id": "codex_potion_cuirass",
        "result_level": CODEX_RECIPE_LEVEL,
        "gold": CODEX_RECIPE_GOLD,
        "resources": dict(CODEX_RECIPE_RESOURCES),
    },
]


def get_recipe_by_category(category: str) -> dict | None:
    """按图鉴类别取配方；无对应配方返回 None"""
    for r in FORGE_RECIPES:
        if r["codex_category"] == category:
            return r
    return None


def get_codex_countable_ids(category: str) -> list[str]:
    """该类图鉴「计入进度」的条目 id 列表（保序，即定义表原始顺序）

    口径唯一来源 —— 以下三处必须同源、数字必须相同：
      1) db.codex.get_codex_count 的计数（已解锁 N / 本函数长度）
      2) views/codex_view.py 的图鉴条目列表与进度分母
      3) views/forge_view.py 配方解锁条件（count >= 本函数长度 = 100% 语义）
    （旧档位固定条数 [10,25,50] 与各类条目数不匹配，已在 config 改为比例制
      CODEX_TIER_RATIOS，档位阈值一律经 config.codex_tiers_for(本函数长度) 取得）

    recipe_only=True 的配方专属产物**排除**在外（武器/装备各若干件）：
    这些产物唯一来源是「配方」页制作，而配方又要求「该类图鉴集齐」，
    若计入则形成自锁死循环（要先有配方才能解锁产物，配方又要集齐含产物）。
    产物制作后仍会写 codex_unlocks 记录，但不参与任何计数/分母/档位判定。
    """
    if category == "monster":
        return list(get_all_monster_class_names())
    if category == "weapon":
        return [wid for wid, w in ALL_WEAPONS.items() if not w.get("recipe_only")]
    if category == "equipment":
        return [
            eid
            for pool in (HELMETS, ARMORS, BACKPACKS)
            for eid, e in pool.items()
            if not e.get("recipe_only")
        ]
    if category == "potion":
        return list(POTIONS.keys())
    return []


def get_codex_category_total(category: str) -> int:
    """该类图鉴的「集齐」目标条目数 = get_codex_countable_ids 的长度（含口径说明）

    统计口径与 views/codex_view.py 的条目列表、db.codex.get_codex_count 的计数
    完全一致（同一份 get_codex_countable_ids），三者必然同一数字。
    """
    return len(get_codex_countable_ids(category))


def get_result_def(item_id: str) -> tuple[str, str | None, dict] | None:
    """取配方产物的定义与投放位置，返回 (kind, slot, def)；未找到返回 None

    kind: "weapon"（slot=None）/ "equipment"（slot=helmet|armor|backpack）
    供 db.add_weapon / db.add_equipment 直接消费，避免视图层重复查表。
    """
    wdef = MELEE_WEAPONS.get(item_id) or RANGED_WEAPONS.get(item_id)
    if wdef:
        return "weapon", None, wdef
    for slot, pool in (("helmet", HELMETS), ("armor", ARMORS), ("backpack", BACKPACKS)):
        edef = pool.get(item_id)
        if edef:
            return "equipment", slot, edef
    return None
