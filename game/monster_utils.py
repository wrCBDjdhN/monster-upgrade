"""怪物工具函数：武器距离查找、武器/护甲/头盔穿戴（按主题分策略）+ 被动效果应用"""

import random
from entities.equipment_defs import ARMORS, HELMETS
from entities.monster_defs import MONSTER_METADATA
from config import MONSTER_THEME_EQUIP


def lookup_weapon_range(kind: str, name: str) -> float:
    """根据中文名查找武器攻击距离"""
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    table = MELEE_WEAPONS if kind == "melee" else RANGED_WEAPONS
    for wdef in table.values():
        if wdef["name"] == name:
            return wdef.get("range", 50 if kind == "melee" else 250)
    return 50 if kind == "melee" else 250


def _weighted_choice(pool: list[tuple[str, int]]) -> str:
    """按权重列表 [(item_id, weight), ...] 随机选取一个 item_id"""
    total = sum(w for _, w in pool)
    r = random.uniform(0, total)
    cumulative = 0
    for item_id, weight in pool:
        cumulative += weight
        if r <= cumulative:
            return item_id
    return pool[-1][0]


def _equip_dict(table: dict, item_id: str, level: int) -> dict:
    """从装备表构建怪物穿戴字典（统一格式，含 effects 字段）"""
    info = table[item_id]
    return {
        "item_id": item_id,
        "name": info["name"],
        "defense": info["defense"],
        "color": info["color"],
        "level": level,
        "effects": list(info.get("effects", [])),
    }


def _apply_passive_effects(monster, eq: dict):
    """将装备的被动效果应用到怪物属性

    支持的 passive 效果：
    - max_hp：增加怪物最大生命值并回满
    - regen：增加每秒回血量
    - speed：乘算移速加成
    - defense：增加防御值（累加到 armor/helmet 的 defense 上）
    - damage：增加攻击伤害百分比（累加到 monster.damage 基础值）
    - lifesteal：攻击吸血比例（存储供 combat.py 读取）
    - thorns：荆棘反伤比例（存储供 combat.py 读取）
    - crit_chance：暴击率（存储供 combat.py 读取）
    """
    from entities.effects_defs import parse_effect_item, effect_params
    for e in eq.get("effects", []):
        eid, elvl = parse_effect_item(e)
        pdata = effect_params(eid, elvl)
        if eid == "max_hp":
            bonus = int(pdata.get("value", 25))
            monster.max_hp += bonus
            monster.hp += bonus
        elif eid == "regen":
            monster.regen_per_sec += pdata.get("value", 1)
        elif eid == "speed":
            monster.speed *= (1.0 + pdata.get("value", 0.30))
        elif eid == "defense":
            # 防御加成：累加到装备字典的 defense 字段（take_damage 会读取）
            eq["defense"] = eq.get("defense", 0) + int(pdata.get("value", 3))
        elif eid == "damage":
            # 伤害加成：按百分比增加怪物基础伤害
            monster.damage = round(monster.damage * (1.0 + pdata.get("value", 0.08)))
        elif eid == "lifesteal":
            # 吸血：累加到怪物属性，供 combat.py 近战命中时读取
            current = getattr(monster, "equip_lifesteal", 0.0)
            monster.equip_lifesteal = current + pdata.get("value", 0.03)
        elif eid == "thorns":
            # 荆棘：累加到怪物属性，供 combat.py 近战受击时读取
            current = getattr(monster, "equip_thorns", 0.0)
            monster.equip_thorns = current + pdata.get("value", 0.10)
        elif eid == "crit_chance":
            # 暴击率：累加到怪物属性，供 combat.py 攻击时读取
            current = getattr(monster, "crit_chance", 0.0)
            monster.crit_chance = current + pdata.get("value", 0.05)


def assign_monster_armor(monster, level: int = 1, theme: str = "forest"):
    """按主题给怪物穿戴护甲

    主题装备池与概率由 config.MONSTER_THEME_EQUIP 驱动：
    - forest：皮甲(80%)/锁子甲(15%)/板甲(5%)，30% 概率穿戴，Lv1-5
    - desert：木乃伊护甲(80%)/板甲(20%)，80% 概率穿戴，Lv5-10
    - space：航天护甲(90%)/强相互作用力护甲(10%)，100% 概率穿戴，Lv10-50 / Lv5-10
    """
    cfg = MONSTER_THEME_EQUIP.get(theme, MONSTER_THEME_EQUIP["forest"])

    # 按主题概率判定是否穿戴
    if random.random() > cfg["armor_chance"]:
        return

    item_id = _weighted_choice(cfg["armor_pool"])
    monster.armor = _equip_dict(ARMORS, item_id, level)
    monster.armor_drop_id = item_id
    _apply_passive_effects(monster, monster.armor)


def assign_monster_helmet(monster, level: int = 1, theme: str = "forest"):
    """按主题给怪物穿戴头盔

    主题装备池与概率由 config.MONSTER_THEME_EQUIP 驱动。
    """
    cfg = MONSTER_THEME_EQUIP.get(theme, MONSTER_THEME_EQUIP["forest"])

    if random.random() > cfg["helmet_chance"]:
        return

    item_id = _weighted_choice(cfg["helmet_pool"])
    monster.helmet = _equip_dict(HELMETS, item_id, level)
    monster.helmet_drop_id = item_id
    _apply_passive_effects(monster, monster.helmet)


def assign_monster_weapon(monster, level: int = 1, theme: str = "forest"):
    """按怪物类型+主题分配武器（携带武器，击败后掉落自身武器）

    步骤：
    1. 从 MONSTER_METADATA.weapon_pool 获取该怪物的基础武器池
    2. 按主题概率决定是否追加神器武器（artifact=True）
    3. 复制武器的 effects/debuff 到 monster.weapon，使攻击可触发特殊效果
    """
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    cfg = MONSTER_THEME_EQUIP.get(theme, MONSTER_THEME_EQUIP["forest"])

    cls_name = monster.__class__.__name__
    meta = MONSTER_METADATA.get(cls_name)
    if not meta:
        return
    weapon_ids = meta.get("weapon_pool") or []
    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
    base_pool = {k: v for k, v in all_weapons.items() if k in weapon_ids}
    if not base_pool:
        return

    # 神器武器判定：按主题概率从全量神器武器中选取
    artifact_weapons = {k: v for k, v in all_weapons.items()
                        if v.get("artifact") and not v.get("special")}
    use_artifact = (random.random() < cfg["artifact_weapon_chance"]
                    and artifact_weapons)
    if use_artifact:
        wid = random.choice(list(artifact_weapons.keys()))
        wdef = artifact_weapons[wid]
        level = random.randint(*cfg["artifact_weapon_level"])
    else:
        wid = random.choice(list(base_pool.keys()))
        wdef = base_pool[wid]
        level = random.randint(*cfg["weapon_level"])

    # 构建 weapon 字典，包含 effects 和 debuff（使攻击可触发特殊效果）
    monster.weapon = {
        "item_id": wid,
        "name": wdef["name"],
        "color": wdef.get("color", (100, 200, 255)),
        "level": level,
        "effects": list(wdef.get("effects", [])),
        "debuff": wdef.get("debuff"),
        "kind": wdef.get("kind", "melee"),
    }
    # 武器被动效果应用到怪物属性（如速度加成、回血等）
    _apply_passive_effects(monster, monster.weapon)
