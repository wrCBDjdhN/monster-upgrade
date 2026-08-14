"""掉落系统：怪物死亡掉落 + 玩家拾取

掉落机制：
1. 怪物死亡时根据掉落表掷骰（资源/金币）
2. 怪物自身携带的武器/装备按穿戴情况掉落（entity_callbacks.on_monster_death）
3. 掉落物在地面停留 30 秒后消失
4. 玩家靠近自动拾取（需背包才能拾取资源）

拾取规则：
- 金币：直接增加携带金币
- 资源：需要背包，容量有限
- 武器/装备/背包：优先判断能否免费装备——若玩家缺少该槽位装备（无背包时亦可），直接拾取并装备、不占容量；否则需背包且占容量
"""

import random
import arcade
from config import (
    ZOMBIE_LOOT_TABLE, SKELETON_LOOT_TABLE, MUMMY_LOOT_TABLE, CAMEL_LOOT_TABLE, BOSS_LOOT_TABLE,
    SPACE_BOSS_LOOT_TABLE, DROP_PICKUP_RADIUS, DROP_LIFETIME,
)
from entities.resource_defs import RESOURCES

# 木乃伊系专属掉落物：仅沙漠怪物（木乃伊/骆驼/BOSS木乃伊）可掉落，
# 僵尸/骷髅（含 BOSS 变体）不掉落这些物品（用户需求：木乃伊相关武器只有木乃伊和骆驼可掉落）
MUMMY_ONLY_DROP_IDS = {"cursed_scimitar", "scepter", "mummy_helmet", "mummy_armor"}


class DropItem(arcade.SpriteSolidColor):
    """地面掉落物精灵"""
    def __init__(self, x, y, item_type: str, item_id: str, quantity: int = 1,
                 lifetime: float | None = DROP_LIFETIME, level: int = 1,
                 net_id: str | None = None):
        # 联机网络 id（Todo 18）：主机分配的唯一标识，客户端拾取请求/掉落物生成同步以此定位；
        # solo 模式恒为 None，不影响单机逻辑
        self.net_id = net_id
        if item_type == "gold":
            color = (255, 215, 0)
        elif item_type == "weapon":
            color = (100, 200, 255)
        elif item_type in ("helmet", "armor"):
            from entities.equipment_defs import HELMETS, ARMORS
            defs = HELMETS if item_type == "helmet" else ARMORS
            color = defs.get(item_id, {}).get("color", (150, 150, 150))
        elif item_type == "potion":
            from entities.equipment_defs import POTIONS
            color = POTIONS.get(item_id, {}).get("color", (200, 255, 100))
        else:
            color = RESOURCES.get(item_id, {}).get("color", (200, 200, 200))
        super().__init__(16, 16, color=color)
        self.center_x = x
        self.center_y = y
        self.item_type = item_type
        self.item_id = item_id
        self.quantity = quantity
        # 物品等级：武器装备/背包带等级（默认 1），
        # 玩家拾取时以 (item_id, level) 为键存入 run_carried，撤离入库时保留等级
        self.level = level
        # lifetime 为 None 表示永不消失（地图初始放置的金币/资源）；
        # 否则按秒倒计时（怪物掉落的战利品）
        self._lifetime = lifetime

    def update(self, delta_time: float = 0):
        if self._lifetime is not None:
            self._lifetime -= delta_time

    @property
    def expired(self) -> bool:
        # _lifetime 为 None 时永不失效
        return self._lifetime is not None and self._lifetime <= 0


def roll_loot(monster_type: str, x: float, y: float) -> list[DropItem]:
    """根据怪物类型掷骰，返回掉落物列表"""
    # 木乃伊系物品仅限沙漠怪物（木乃伊/骆驼/BOSS木乃伊）掉落；僵尸/骷髅（含 BOSS 变体）不掉落
    is_desert = monster_type in ("mummy_melee", "mummy_ranged", "camel", "boss_mummy")
    # 新怪物掉落表映射：木乃伊/骆驼/BOSS 各有专属掉落表（config.py 定义）
    if monster_type in ("mummy_melee", "mummy_ranged"):
        table = MUMMY_LOOT_TABLE
    elif monster_type == "camel":
        table = CAMEL_LOOT_TABLE
    elif monster_type == "boss_space":
        table = SPACE_BOSS_LOOT_TABLE  # 航天BOSS专属掉落表（更丰厚）
    elif monster_type in ("boss_zombie", "boss_skeleton", "boss_mummy"):
        table = BOSS_LOOT_TABLE
    else:
        table = ZOMBIE_LOOT_TABLE if monster_type == "zombie" else SKELETON_LOOT_TABLE
    drops = []
    for item_type, item_id, prob, qty_min, qty_max in table:
        # 木乃伊系专属物品：非沙漠怪物（僵尸/骷髅等）跳过，不参与掉落
        if not is_desert and item_id in MUMMY_ONLY_DROP_IDS:
            continue
        if random.random() < prob:
            qty = random.randint(qty_min, qty_max)
            # 掉落表里的 equipment 类型按 ID 后缀映射为具体装备槽位
            # （helmet/armor 才能被 DropItem 上色与 try_pickup 识别）
            if item_type == "equipment" and item_id:
                if item_id.endswith("_armor"):
                    item_type = "armor"
                elif item_id.endswith("_helmet"):
                    item_type = "helmet"
            drops.append(DropItem(x, y, item_type, item_id or "gold", qty))
    # 说明：不再随机掉落武器/装备（用户需求：怪物只掉落自己身上的装备，
    # 自身 weapon/armor/helmet 由 entity_callbacks.on_monster_death 按穿戴情况掉落）
    return drops


def _calc_carried_capacity(run_carried: dict) -> int:
    """计算 run_carried 中所有物品占用的背包容量总和"""
    total = 0
    # 资源占用容量
    res = run_carried.get("resource", {})
    total += sum(res.values())
    # 药水占用容量（果实等掉落药水，每瓶 1 容量）
    potions = run_carried.get("potion", {})
    total += sum(potions.values())
    # 武器占用容量（run_carried 键为 (item_id, level) 元组，容量只与类型有关）
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
    weapons = run_carried.get("weapon", {})
    for (wid, _level), qty in weapons.items():
        cost = all_weapons.get(wid, {}).get("capacity_cost", 2)
        total += cost * qty
    # 头盔占用容量
    from entities.equipment_defs import HELMETS, ARMORS
    helmets = run_carried.get("helmet", {})
    for (hid, _level), qty in helmets.items():
        cost = HELMETS.get(hid, {}).get("capacity_cost", 1)
        total += cost * qty
    # 护甲占用容量
    armors = run_carried.get("armor", {})
    for (aid, _level), qty in armors.items():
        cost = ARMORS.get(aid, {}).get("capacity_cost", 2)
        total += cost * qty
    # 背包占用容量（背包本身也占空间）
    from entities.equipment_defs import BACKPACKS
    backpacks = run_carried.get("backpack", {})
    for (bid, _level), qty in backpacks.items():
        cost = BACKPACKS.get(bid, {}).get("capacity_cost", 1)
        total += cost * qty
    return total


def try_pickup(player, drops: list[DropItem], run_carried: dict,
               equipped_weapon_id: int | None = None,
               equipped_helmet_id: str | None = None,
               equipped_armor_id: str | None = None,
               on_free_equip: callable | None = None) -> tuple[list[DropItem], list[DropItem], list[DropItem]]:
    """玩家拾取附近掉落物，更新 run_carried。

    拾取规则：
    - 金币：直接增加，不需要背包
    - 资源：需要背包，检查容量
    - 武器/头盔/护甲：需要背包，检查容量
      * 如果玩家缺少该类型的装备（没装备且run_carried中没有），直接装备到当前栏位，不占容量
      * 如果玩家已有该类型的装备，存入 run_carried，占用容量
    - 背包：无背包时可直接装备（获得容器），否则按普通物品占容量存入
    - on_free_equip：免费装备发生时回调（参数为被装备的 DropItem），
      供视图层在局内即时生效（穿戴/加防御/获得容量）

    返回 (picked, skipped_full, skipped_no_bag)：
    - picked          : 成功拾取的掉落物列表
    - skipped_full    : 因背包已满而未能拾取的掉落物列表
    - skipped_no_bag  : 因未携带背包而未能拾取的掉落物列表
    """
    picked = []
    skipped_full = []
    skipped_no_bag = []
    capacity = getattr(player, 'backpack_capacity', 0)

    for d in drops[:]:
        dist = ((player.center_x - d.center_x) ** 2 + (player.center_y - d.center_y) ** 2) ** 0.5
        if dist >= DROP_PICKUP_RADIUS:
            continue

        # 金币不需要背包
        if d.item_type == "gold":
            run_carried.setdefault("gold", 0)
            run_carried["gold"] += d.quantity
            picked.append(d)
            continue

        # 判断是否可以免费装备（玩家缺少该类型装备时；无背包也可直接装备空槽位）
        # 只检查当前装备槽位（equipped_*_id），不检查 run_carried 中的同类物品
        # 这样：没有同类装备 → 直接装备（不占容量）；已有同类装备 → 放入背包（占容量）
        can_free_equip = False
        if d.item_type == "weapon":
            # 如果玩家没有装备武器，可以免费装备（含无背包情况）
            if equipped_weapon_id is None:
                can_free_equip = True
        elif d.item_type in ("helmet", "armor"):
            # 如果玩家没有装备该类型，可以免费装备（含无背包情况）
            equipped_id = equipped_helmet_id if d.item_type == "helmet" else equipped_armor_id
            if equipped_id is None:
                can_free_equip = True
        elif d.item_type == "backpack":
            # 玩家当前没有背包（无容量）时，可将捡到的背包直接装备（获得容器）
            if capacity <= 0:
                can_free_equip = True

        if can_free_equip:
            # 免费装备：直接装备到装备栏（不存入 run_carried，不占容量）
            # 通过 on_free_equip 回调更新 GameState 和玩家属性
            picked.append(d)
            if on_free_equip is not None:
                on_free_equip(d)
            continue

        # 需要背包的物品：无背包一律不能拾取
        if capacity <= 0:
            skipped_no_bag.append(d)
            continue

        # 需要占用容量的物品：检查容量（统一用 _calc_carried_capacity 算出当前总占用）
        if d.item_type in ("resource", "potion"):
            current_capacity = _calc_carried_capacity(run_carried)
            # 资源/药水每个占 1 容量
            if current_capacity + d.quantity > capacity:
                skipped_full.append(d)
                continue
        elif d.item_type == "weapon":
            from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
            all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
            cost = all_weapons.get(d.item_id, {}).get("capacity_cost", 2)
            current_capacity = _calc_carried_capacity(run_carried)
            if current_capacity + cost * d.quantity > capacity:
                skipped_full.append(d)
                continue
        elif d.item_type in ("helmet", "armor"):
            from entities.equipment_defs import HELMETS, ARMORS
            defs = HELMETS if d.item_type == "helmet" else ARMORS
            cost = defs.get(d.item_id, {}).get("capacity_cost", 1)
            current_capacity = _calc_carried_capacity(run_carried)
            if current_capacity + cost * d.quantity > capacity:
                skipped_full.append(d)
                continue
        elif d.item_type == "backpack":
            from entities.equipment_defs import BACKPACKS
            cost = BACKPACKS.get(d.item_id, {}).get("capacity_cost", 1)
            current_capacity = _calc_carried_capacity(run_carried)
            if current_capacity + cost * d.quantity > capacity:
                skipped_full.append(d)
                continue

        # 存入 run_carried
        run_carried.setdefault(d.item_type, {})
        # 武器装备/背包以 (item_id, level) 为键（支持同名不同等级并存）；资源/药水仍以 id 为键
        if d.item_type in ("weapon", "helmet", "armor", "backpack"):
            key = (d.item_id, d.level)
        else:
            key = d.item_id
        run_carried[d.item_type][key] = run_carried[d.item_type].get(key, 0) + d.quantity
        picked.append(d)

    for d in picked:
        drops.remove(d)
    return picked, skipped_full, skipped_no_bag
