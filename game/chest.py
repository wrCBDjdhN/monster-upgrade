"""房间宝箱：打开后随机掉落武器装备和资源

宝箱机制：
1. 每个房间生成 1 个宝箱（出生房间除外）
2. 靠近宝箱按 E 键打开
3. 打开后掉落武器装备、资源、金币（水井首次开启复用本类）
4. 打开后从障碍物列表移除（不再阻挡移动）

掉落概率（用户需求：宝箱/水井可开出任何武器装备含神器）：
- 必定掉落 1 件武器或装备（含神器），等级按概率分布：
  Lv1-10 占 80% / Lv10-20 占 10% / Lv20-50 占 9% / Lv50-100 占 1%
- 25% 掉落背包（让玩家有机会获得容器）
- 100% 掉落资源（2组，每组1-3个）
- 30% 掉落金币（5-15个）
"""

import random
import arcade
from config import CHEST_LEVEL_RANGES, CHEST_EQUIPMENT_CHANCE, CHEST_BACKPACK_CHANCE, CHEST_GOLD_CHANCE
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS


class Chest(arcade.SpriteSolidColor):
    """宝箱精灵，靠近按E打开"""

    def __init__(self, center_x: float, center_y: float):
        super().__init__(24, 24, color=(200, 170, 50))
        self.center_x = center_x
        self.center_y = center_y
        self.opened = False

    def open_chest(self) -> dict:
        """打开宝箱，返回战利品"""
        if self.opened:
            return {}
        self.opened = True
        self.color = (80, 60, 30)
        return self._generate_loot()

    def _roll_equipment_level(self) -> int:
        """按概率分布掷出装备等级：
        Lv1-10 占 80% / Lv10-20 占 10% / Lv20-50 占 9% / Lv50-100 占 1%
        """
        r = random.random()
        cumulative = 0.0
        for prob, lo, hi in CHEST_LEVEL_RANGES:
            cumulative += prob
            if r < cumulative:
                return random.randint(lo, hi)
        # 浮点误差兜底：落入最后一档
        return random.randint(*CHEST_LEVEL_RANGES[-1][1:])

    def _generate_loot(self) -> dict:
        """生成随机战利品

        返回结构：
        - weapons:   [{"item_id": str, "level": int}, ...]
        - equipment: [(item_id, slot, level), ...]（slot 为 helmet/armor）
        - resources: [(资源id, 数量), ...]
        - gold:      金币数量
        - backpack:  背包 id 或 None
        """
        loot = {"weapons": [], "equipment": [], "resources": [], "gold": 0, "backpack": None}
        # 必定掉落 1 件武器或装备（含神器），等级按概率分布
        level = self._roll_equipment_level()
        if random.random() < CHEST_EQUIPMENT_CHANCE:
            # 武器池 = 近战+远程（含神器 wado_ichimonji/meteor_cannon），排除"拳头"
            all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
            wid = random.choice([k for k in all_weapons if k != "fist"])
            loot["weapons"].append({"item_id": wid, "level": level})
        else:
            # 装备池 = 头盔+护甲（含神器），随机一个槽位
            slot = random.choice(["helmet", "armor"])
            defs = HELMETS if slot == "helmet" else ARMORS
            item_id = random.choice(list(defs.keys()))
            loot["equipment"].append((item_id, slot, level))
        # 25% 概率掉落背包（让玩家有机会获得容器以拾取资源/武器/装备）
        if random.random() < CHEST_BACKPACK_CHANCE:
            # 排除神器背包（背包神器只能通过锻造获得）
            bag_id = random.choice([k for k, v in BACKPACKS.items() if not v.get("artifact")])
            loot["backpack"] = bag_id
        # 必定掉落资源
        res_types = ["wood", "stone", "ore"]
        for _ in range(2):
            rtype = random.choice(res_types)
            qty = random.randint(1, 3)
            loot["resources"].append((rtype, qty))
        # 30% 概率掉落金币
        if random.random() < CHEST_GOLD_CHANCE:
            loot["gold"] = random.randint(5, 15)
        return loot


def spawn_chests(rooms, rng, count_per_room: int = 1) -> list[tuple]:
    """在每个房间内生成宝箱位置，返回 [(x, y)]"""
    result = []
    for room in rooms:
        for _ in range(count_per_room):
            x = rng.randint(room.x + 64, room.x + room.w - 64)
            y = rng.randint(room.y + 64, room.y + room.h - 64)
            result.append((x, y))
    return result
