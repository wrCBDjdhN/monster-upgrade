"""怪物工具函数：武器距离查找、武器/护甲/头盔穿戴"""

import random
from entities.equipment_defs import ARMORS, HELMETS, MONSTER_ARMOR_DROP, MONSTER_HELMET_DROP
from entities.monster_defs import MONSTER_METADATA
from config import (
    MONSTER_WEAPON_LEVEL_RANGE,
    MONSTER_MUMMY_ARMOR_CHANCE, MONSTER_MUMMY_HELMET_CHANCE,
)


def lookup_weapon_range(kind: str, name: str) -> float:
    """根据中文名查找武器攻击距离"""
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    table = MELEE_WEAPONS if kind == "melee" else RANGED_WEAPONS
    for wdef in table.values():
        if wdef["name"] == name:
            return wdef.get("range", 50 if kind == "melee" else 250)
    return 50 if kind == "melee" else 250


def assign_monster_weapon(monster, level: int = None, is_desert: bool = False, is_space: bool = False):
    """按怪物类型分配主题武器（携带武器，击败后掉落自身武器）

    每类怪物的可携带武器池由 MONSTER_METADATA.weapon_pool 统一定义，
    此处直接查表获取，新增怪物无需改动本函数。
    """
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    cls_name = monster.__class__.__name__
    # 从 MONSTER_METADATA 读取该怪物的武器池（未注册的怪物直接返回）
    meta = MONSTER_METADATA.get(cls_name)
    if not meta:
        return
    weapon_ids = meta.get("weapon_pool") or []
    if not weapon_ids:
        return
    # 按 weapon_pool 中登记的武器 ID 构建武器池（合并近战+远程全量武器表）
    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
    pool = {k: v for k, v in all_weapons.items() if k in weapon_ids}
    if not pool:
        return
    if level is None:
        level = random.randint(*MONSTER_WEAPON_LEVEL_RANGE)
    wid = random.choice(list(pool.keys()))
    wdef = pool[wid]
    monster.weapon = {
        "item_id": wid,
        "name": wdef["name"],
        "color": wdef.get("color", (100, 200, 255)),
        "level": level,
    }


def assign_monster_armor(monster, level: int = 1, is_desert: bool = False, is_space: bool = False):
    """随机给怪物穿戴护甲

    is_desert=True 时额外按 MONSTER_MUMMY_ARMOR_CHANCE 概率穿戴木乃伊护甲
    （木乃伊系装备不再随机掉落，改由沙漠怪穿戴后掉落，保持木乃伊装备产出）。
    is_space=True 时强制穿戴航天护甲（BOSS必穿，普通怪30%概率）。
    """
    # 航天BOSS强制穿戴航天护甲
    cls_name = monster.__class__.__name__
    if is_space and cls_name == "BossSpace":
        info = ARMORS["space_armor"]
        monster.armor = {
            "item_id": "space_armor",
            "name": info["name"],
            "defense": info["defense"],
            "color": info["color"],
            "level": level,
        }
        monster.armor_drop_id = "space_armor"
        return
    
    for item_id, prob in MONSTER_ARMOR_DROP:
        if random.random() < prob:
            info = ARMORS[item_id]
            monster.armor = {
                "item_id": item_id,
                "name": info["name"],
                "defense": info["defense"],
                "color": info["color"],
                "level": level,
            }
            monster.armor_drop_id = item_id
            break
    # 沙漠怪额外穿戴木乃伊护甲（未命中普通护甲时）
    if is_desert and monster.armor is None and random.random() < MONSTER_MUMMY_ARMOR_CHANCE:
        info = ARMORS["mummy_armor"]
        monster.armor = {
            "item_id": "mummy_armor",
            "name": info["name"],
            "defense": info["defense"],
            "color": info["color"],
            "level": level,
        }
        monster.armor_drop_id = "mummy_armor"
    # 航天怪额外穿戴航天护甲（未命中普通护甲时）
    if is_space and monster.armor is None and random.random() < 0.30:
        info = ARMORS["space_armor"]
        monster.armor = {
            "item_id": "space_armor",
            "name": info["name"],
            "defense": info["defense"],
            "color": info["color"],
            "level": level,
        }
        monster.armor_drop_id = "space_armor"


def assign_monster_helmet(monster, level: int = 1, is_desert: bool = False, is_space: bool = False):
    """随机给怪物穿戴头盔

    is_desert=True 时额外按 MONSTER_MUMMY_HELMET_CHANCE 概率穿戴木乃伊头盔
    （木乃伊系装备不再随机掉落，改由沙漠怪穿戴后掉落，保持木乃伊装备产出）。
    is_space=True 时强制穿戴航天头盔（BOSS必穿，普通怪30%概率）。
    """
    # 航天BOSS强制穿戴航天头盔
    cls_name = monster.__class__.__name__
    if is_space and cls_name == "BossSpace":
        info = HELMETS["space_helmet"]
        monster.helmet = {
            "item_id": "space_helmet",
            "name": info["name"],
            "defense": info["defense"],
            "color": info["color"],
            "level": level,
        }
        monster.helmet_drop_id = "space_helmet"
        return
    
    for item_id, prob in MONSTER_HELMET_DROP:
        if random.random() < prob:
            info = HELMETS[item_id]
            monster.helmet = {
                "item_id": item_id,
                "name": info["name"],
                "defense": info["defense"],
                "color": info["color"],
                "level": level,
            }
            monster.helmet_drop_id = item_id
            break
    # 沙漠怪额外穿戴木乃伊头盔（未命中普通头盔时）
    if is_desert and monster.helmet is None and random.random() < MONSTER_MUMMY_HELMET_CHANCE:
        info = HELMETS["mummy_helmet"]
        monster.helmet = {
            "item_id": "mummy_helmet",
            "name": info["name"],
            "defense": info["defense"],
            "color": info["color"],
            "level": level,
        }
        monster.helmet_drop_id = "mummy_helmet"
    # 航天怪额外穿戴航天头盔（未命中普通头盔时）
    if is_space and monster.helmet is None and random.random() < 0.30:
        info = HELMETS["space_helmet"]
        monster.helmet = {
            "item_id": "space_helmet",
            "name": info["name"],
            "defense": info["defense"],
            "color": info["color"],
            "level": level,
        }
        monster.helmet_drop_id = "space_helmet"
