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
from config import (
    CHEST_THEME_CONFIGS, CHEST_GOLD_MIN, CHEST_GOLD_MAX,
    EVENT_AIRDROP_CHEST_COLOR, EVENT_AIRDROP_EXTRA_GEAR, EVENT_AIRDROP_GOLD_MAX,
    EVENT_AIRDROP_GOLD_MIN, EVENT_AIRDROP_LEVEL_SHIFT, EVENT_AIRDROP_MIN_LEVEL,
    EVENT_AIRDROP_POTION_CHANCE,
)
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
        # 阶段4 神器低语事件（relic）注入的神器等级加成比例（0 = 无事件加成）。
        # 由 map_events.trigger_event 在事件生效时统一写入本局所有普通宝箱，
        # 神器档命中时按此比例上调等级（见 _apply_artifact_bonus）。
        self.artifact_bonus = 0.0

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

    def _is_artifact(self, slot: str, item_id: str) -> bool:
        """判定槽位物品是否为神器（artifact 标记，与 _build_artifact_pool 同源）"""
        pools = {"weapon": {**MELEE_WEAPONS, **RANGED_WEAPONS}, "helmet": HELMETS, "armor": ARMORS}
        return bool(pools.get(slot, {}).get(item_id, {}).get("artifact"))

    def _apply_artifact_bonus(self, level: int) -> int:
        """神器等级加成：按 artifact_bonus 比例取整上调（至少 +1 级，保证加成可感知）

        阶段4 神器低语事件（relic）专用；无事件时 artifact_bonus=0，原样返回。
        """
        bonus = max(0.0, float(self.artifact_bonus or 0.0))
        if bonus <= 0.0:
            return level
        return max(level + 1, int(round(level * (1.0 + bonus))))

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
            # 阶段4 神器低语事件：artifact_bonus>0 时按比例上调神器等级
            pool = self._build_artifact_pool()
            if pool:
                slot, item_id = random.choice(pool)
                level = self._apply_artifact_bonus(self._roll_level(cfg["artifact_level"]))
                self._append_loot_item(loot, slot, item_id, level)
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


class AirdropChest(Chest):
    """空投补给箱（阶段 4 airdrop 事件）

    继承 Chest，因此开箱交互（E）、障碍物碰撞、图鉴解锁、渲染与网络同步
    全部沿用现有宝箱路径，无需另起一套机制。相对普通宝箱的差异：

    - 主掉落强制取"高级装备池"（主题 elite_pool，缺失时回落 main_pool），
      等级区间按 EVENT_AIRDROP_LEVEL_SHIFT 上浮，并保底 1 件等级 ≥ EVENT_AIRDROP_MIN_LEVEL；
    - 必定附带金币（EVENT_AIRDROP_GOLD_MIN~MAX，高于普通宝箱）与资源；
    - artifact_bonus（神器低语事件的 view.event_flags.artifact_bonus）按比例抬高神器等级；
    - is_airdrop=True 供渲染层区分配色（橙色系 + 高亮饰条，仍为一律实心矩形）。
    """

    def __init__(self, center_x: float, center_y: float, theme: str = "forest",
                 artifact_bonus: float = 0.0):
        super().__init__(center_x, center_y, theme=theme)
        self.is_airdrop = True          # 渲染/小地图据此用空投配色与标记
        self.artifact_bonus = artifact_bonus  # 神器等级加成比例（0 = 无加成）
        self.color = EVENT_AIRDROP_CHEST_COLOR

    def open_chest(self) -> dict:
        """打开空投箱（复用父类状态机，仅战利品生成走空投口径）"""
        return super().open_chest()

    def _airdrop_level(self, cfg: dict) -> int:
        """空投主装备等级：主题高级档区间上浮后，再保底到 EVENT_AIRDROP_MIN_LEVEL"""
        lo, hi = cfg.get("elite_level") or cfg.get("main_level", (1, 1))
        lo = int(lo) + EVENT_AIRDROP_LEVEL_SHIFT
        hi = int(hi) + EVENT_AIRDROP_LEVEL_SHIFT
        return max(EVENT_AIRDROP_MIN_LEVEL, self._roll_level((lo, max(lo, hi))))

    def _generate_loot(self) -> dict:
        """生成空投战利品（高级装备池 + 保底 Lv≥10 + 附赠药水/资源/更多金币）"""
        cfg = CHEST_THEME_CONFIGS.get(self.theme, CHEST_THEME_CONFIGS["forest"])
        loot = {"weapons": [], "equipment": [], "resources": [], "gold": 0,
                "backpack": None, "potions": []}
        pool = cfg.get("elite_pool") or cfg.get("main_pool") or []
        # 主装备 1 件（保底 Lv≥10；神器按 artifact_bonus 抬等级）
        for _ in range(1 + max(0, EVENT_AIRDROP_EXTRA_GEAR)):
            if not pool:
                break
            slot, item_id = random.choice(pool)
            level = self._airdrop_level(cfg)
            if self._is_artifact(slot, item_id):
                level = self._apply_artifact_bonus(level)
            self._append_loot_item(loot, slot, item_id, level)
        # 附带 1 瓶药水（果实 price=0 属怪物专属掉落，不进补给箱池）
        if random.random() < EVENT_AIRDROP_POTION_CHANCE:
            loot["potions"].append(random.choice([k for k, v in POTIONS.items() if v.get("price", 0) > 0]))
        # 必定掉落资源（2 组，每组 1-3 个）
        for _ in range(2):
            loot["resources"].append((random.choice(["wood", "stone", "ore"]), random.randint(1, 3)))
        # 必定掉落金币（空投档显著高于普通宝箱）
        loot["gold"] = random.randint(EVENT_AIRDROP_GOLD_MIN, EVENT_AIRDROP_GOLD_MAX)
        return loot
