"""房间宝箱：打开后随机掉落武器装备和资源

宝箱机制：
1. 每个房间生成 1 个宝箱（出生房间除外）
2. 靠近宝箱按 E 键打开
3. 打开后掉落武器装备、资源、金币（水井首次开启复用本类）
4. 打开后从障碍物列表移除（不再阻挡移动）

掉落规则（按主题区分掉落池/等级，用户需求 2026-08，数值见 config.CHEST_THEME_CONFIGS）：
- 必定掉落资源（2组，每组1-3个）与金币（5-15）
- 主掉落 3 选 1：
  幽暗森林 forest：45% 常规装备（皮质/铁质/金质、木剑铁剑石锤、弓杖、背包，1-5级）
                    / 45% 药水 / 10% 高级装备（枪械、木乃伊/航天套装，1级），不掉神器
  沙漠荒地 desert：45% 常规装备（枪械、木乃伊/航天套装、背包，1-5级）
                    / 45% 药水 / 10% 高级装备（火箭筒、激光枪、诅咒弯刀、权杖，1级），不掉神器
  航天基地 space： 60% 常规装备（沙漠+航天全部特色装备、背包，5-50级）
                    / 20% 药水 / 20% 神器（artifact 标记武器/装备，5-10级）
"""

import random
import arcade
from config import CHEST_THEME_CONFIGS, CHEST_GOLD_MIN, CHEST_GOLD_MAX
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS, POTIONS
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS


class Chest(arcade.SpriteSolidColor):
    """宝箱精灵，靠近按E打开"""

    def __init__(self, center_x: float, center_y: float, theme: str = "forest"):
        super().__init__(24, 24, color=(200, 170, 50))
        self.center_x = center_x
        self.center_y = center_y
        self.opened = False
        self.theme = theme  # 主题：forest/desert/space，决定掉落池与等级区间

    def open_chest(self) -> dict:
        """打开宝箱，返回战利品"""
        if self.opened:
            return {}
        self.opened = True
        self.color = (80, 60, 30)
        return self._generate_loot()

    def _roll_level(self, level_range: tuple) -> int:
        """在 (min, max) 等级区间内均匀随机掷出装备等级"""
        return random.randint(level_range[0], level_range[1])

    def _append_loot_item(self, loot: dict, slot: str, item_id: str, level: int) -> None:
        """按槽位把单件掉落写入战利品字典（weapon 入 weapons 列表，backpack 独立键，其余入 equipment）"""
        if slot == "weapon":
            loot["weapons"].append({"item_id": item_id, "level": level})
        elif slot == "backpack":
            loot["backpack"] = item_id
        else:  # helmet / armor
            loot["equipment"].append((item_id, slot, level))

    def _build_artifact_pool(self) -> list:
        """神器池：动态筛取 artifact 标记的武器/头盔/护甲（新增神器自动纳入，无需改配置）"""
        all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
        pool = (
            [("weapon", k) for k in all_weapons if all_weapons[k].get("artifact")]
            + [("helmet", k) for k in HELMETS if HELMETS[k].get("artifact")]
            + [("armor", k) for k in ARMORS if ARMORS[k].get("artifact")]
        )
        return pool

    def _generate_loot(self) -> dict:
        """生成随机战利品（按 self.theme 对应 CHEST_THEME_CONFIGS 的池/概率/等级）

        返回结构：
        - weapons:   [{"item_id": str, "level": int}, ...]
        - equipment: [(item_id, slot, level), ...]（slot 为 helmet/armor）
        - backpack:  背包 id 或 None
        - potions:   [item_id, ...]
        - resources: [(资源id, 数量), ...]
        - gold:      金币数量
        """
        cfg = CHEST_THEME_CONFIGS.get(self.theme, CHEST_THEME_CONFIGS["forest"])
        loot = {"weapons": [], "equipment": [], "resources": [], "gold": 0,
                "backpack": None, "potions": []}
        # 主掉落 3 选 1：常规装备 / 药水 / 高级装备或神器（概率合计 1.0，见配置）
        r = random.random()
        if r < cfg["main_chance"]:
            # 常规装备池（按主题）
            slot, item_id = random.choice(cfg["main_pool"])
            self._append_loot_item(loot, slot, item_id, self._roll_level(cfg["main_level"]))
        elif r < cfg["main_chance"] + cfg["potion_chance"]:
            # 药水：POTIONS 随机一种（仙人掌果实 price=0 属怪物专属掉落，不参与宝箱池）
            pot_id = random.choice([k for k, v in POTIONS.items() if v.get("price", 0) > 0])
            loot["potions"].append(pot_id)
        elif r < cfg["main_chance"] + cfg["potion_chance"] + cfg["elite_chance"]:
            # 高级装备池（森林/沙漠 10% 档；航天该档为 0）
            slot, item_id = random.choice(cfg["elite_pool"])
            self._append_loot_item(loot, slot, item_id, self._roll_level(cfg["elite_level"]))
        else:
            # 神器（仅航天 20% 档；森林/沙漠 artifact_chance=0 时不可达）
            pool = self._build_artifact_pool()
            if pool:
                slot, item_id = random.choice(pool)
                self._append_loot_item(loot, slot, item_id, self._roll_level(cfg["artifact_level"]))
        # 必定掉落资源（2组，每组1-3个）
        res_types = ["wood", "stone", "ore"]
        for _ in range(2):
            rtype = random.choice(res_types)
            qty = random.randint(1, 3)
            loot["resources"].append((rtype, qty))
        # 必定掉落金币
        loot["gold"] = random.randint(CHEST_GOLD_MIN, CHEST_GOLD_MAX)
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
