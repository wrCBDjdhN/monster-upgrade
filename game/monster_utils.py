"""怪物工具函数：武器距离查找、武器/护甲/头盔穿戴"""

import random
from entities.equipment_defs import ARMORS, HELMETS, MONSTER_ARMOR_DROP, MONSTER_HELMET_DROP
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

    每类怪物只携带与自身视觉表现一致的武器（与 rendering 中武器名标签对应）：
    - 僵尸/BOSS僵尸：近战武器（木剑/铁剑/石锤，不含弯刀）→ 对应「铁剑」
    - 骷髅/BOSS骷髅：弓类武器（短弓/长弓）→ 对应「短弓」
    - 木乃伊近战/BOSS木乃伊：近战武器（含弯刀）→ 对应「弯刀」
    - 木乃伊弓手：法杖/权杖 → 对应「权杖」
    - 骆驼：无武器（吐口水），直接返回

    level 为 None 时按 MONSTER_WEAPON_LEVEL_RANGE 随机；is_desert 参数保留
    兼容调用点（分池后木乃伊系武器已由类别天然限定，不再需要额外过滤）。
    is_space 参数为 space 主题怪物预留（目前无额外武器规则）。
    """
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    cls_name = monster.__class__.__name__
    if cls_name in ("Zombie", "BossZombie"):
        # 僵尸系：近战武器，排除拳头/弯刀/神器（与「铁剑」视觉一致）
        pool = {k: v for k, v in MELEE_WEAPONS.items()
                if k not in ("fist", "cursed_scimitar") and not v.get("artifact")}
    elif cls_name in ("Skeleton", "BossSkeleton"):
        # 骷髅系：弓类武器（与「短弓」视觉一致）
        pool = {k: v for k, v in RANGED_WEAPONS.items()
                if k in ("short_bow", "long_bow")}
    elif cls_name in ("MummyMelee", "BossMummy"):
        # 木乃伊近战：近战武器含弯刀（与「弯刀」视觉一致）
        pool = {k: v for k, v in MELEE_WEAPONS.items()
                if k != "fist" and not v.get("artifact")}
    elif cls_name == "MummyRanged":
        # 木乃伊弓手：法杖/权杖（与「权杖」视觉一致）
        pool = {k: v for k, v in RANGED_WEAPONS.items()
                if k in ("fire_staff", "scepter")}
    elif cls_name in ("Sniper", "BossSpace"):
        # 狙击兵/BOSS航天兵：远程武器（狙击枪/激光枪）
        pool = {k: v for k, v in RANGED_WEAPONS.items()
                if k in ("sniper", "laser_gun")}
    elif cls_name == "Assault":
        # 突击兵：近战武器（步枪近战用）
        pool = {k: v for k, v in MELEE_WEAPONS.items()
                if k not in ("fist", "cursed_scimitar") and not v.get("artifact")}
    elif cls_name == "RocketTroop":
        # 火箭兵：远程武器（火箭筒）
        pool = {k: v for k, v in RANGED_WEAPONS.items()
                if k in ("rocket_launcher",)}
    elif cls_name == "Bandit":
        # 土匪：远程武器（手枪）或近战武器（石锤）
        import random as _rand
        if _rand.random() < 0.5:
            pool = {k: v for k, v in RANGED_WEAPONS.items() if k in ("pistol",)}
        else:
            pool = {k: v for k, v in MELEE_WEAPONS.items() if k in ("stone_mace",)}
    else:
        # 骆驼等无武器怪物：不分配武器
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
