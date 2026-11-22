"""任务/成就奖励池抽取（纯随机，数据来自 entities/mission_defs.REWARD_POOLS）

输出格式与邮件附件完全一致（item_type/item_id/level/quantity），
上层 views/mission_view.py 直接把结果写进 db.mail.create_mail 的 attachments。
"""
import random

from config import ALL_EQUIP_POOL, ALL_WEAPON_IDS
from entities.equipment_defs import ARMORS, BACKPACKS, HELMETS, POTIONS
from entities.mission_defs import REWARD_POOLS
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS


def _artifact_pool() -> list[tuple[str, str]]:
    """神器池：动态筛取 artifact 标记的武器/头盔/护甲/背包

    排除 recipe_only 图鉴专属神器（避免任务奖励绕过配方解锁）。
    """
    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
    pool = [("weapon", k) for k, v in all_weapons.items()
            if v.get("artifact") and not v.get("recipe_only")]
    pool += [("helmet", k) for k, v in HELMETS.items()
             if v.get("artifact") and not v.get("recipe_only")]
    pool += [("armor", k) for k, v in ARMORS.items()
             if v.get("artifact") and not v.get("recipe_only")]
    pool += [("backpack", k) for k, v in BACKPACKS.items()
             if v.get("artifact") and not v.get("recipe_only")]
    return pool


def _random_gear(level_range: tuple, count: int) -> list[dict]:
    """随机 Lv.1-10 武器/头盔/护甲/背包（排除神器与配方专属）"""
    slots: list[tuple[str, str]] = [("weapon", i) for i in ALL_WEAPON_IDS]
    slots += list(ALL_EQUIP_POOL)
    slots += [("backpack", k) for k, v in BACKPACKS.items()
              if not v.get("artifact") and not v.get("recipe_only")]
    out: list[dict] = []
    for _ in range(max(1, int(count))):
        slot, item_id = random.choice(slots)
        out.append({
            "item_type": slot,
            "item_id": item_id,
            "level": random.randint(int(level_range[0]), int(level_range[1])),
            "quantity": 1,
        })
    return out


def _qty(value) -> int:
    """数量字段：int 原样；(min,max) 元组随机区间"""
    if isinstance(value, (tuple, list)):
        return random.randint(int(value[0]), int(value[1]))
    return max(1, int(value))


def roll_reward(pool_id: str) -> list[dict]:
    """从奖励池随机抽 1 项，返回邮件附件格式列表；未知池返回空列表"""
    pool = REWARD_POOLS.get(str(pool_id))
    if not pool or not pool.get("entries"):
        return []
    entries = pool["entries"]
    total = sum(int(e.get("weight", 1)) for e in entries)
    pick = random.uniform(0, total)
    acc = 0.0
    entry = entries[-1]
    for e in entries:
        acc += int(e.get("weight", 1))
        if pick <= acc:
            entry = e
            break

    kind = str(entry.get("type", ""))
    if kind == "resource":
        return [{
            "item_type": "resource",
            "item_id": str(entry["item_id"]),
            "quantity": _qty(entry.get("qty", 1)),
        }]
    if kind == "box":
        return [{
            "item_type": "box",
            "item_id": str(entry["box_id"]),
            "quantity": _qty(entry.get("qty", 1)),
        }]
    if kind == "potion":
        pot_ids = [k for k, v in POTIONS.items() if v.get("price", 0) > 0]
        if not pot_ids:
            return []
        return [{
            "item_type": "potion",
            "item_id": random.choice(pot_ids),
            "quantity": _qty(entry.get("qty", 1)),
        }]
    if kind == "random_gear":
        return _random_gear(entry.get("level", (1, 10)), int(entry.get("count", 1)))
    if kind == "artifact":
        pool_items = _artifact_pool()
        if not pool_items:
            return []
        level_range = entry.get("level", (1, 3))
        out: list[dict] = []
        for _ in range(max(1, int(entry.get("count", 1)))):
            slot, item_id = random.choice(pool_items)
            out.append({
                "item_type": slot,
                "item_id": item_id,
                "level": random.randint(int(level_range[0]), int(level_range[1])),
                "quantity": 1,
            })
        return out
    return []
