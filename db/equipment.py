"""装备管理：头盔/护甲/背包 - 装备/卸下/升级/售卖"""
from db.connection import _conn


def get_equipment(pid: int) -> dict:
    """获取玩家已装备物品，返回 {slot: {id, item_id, name, defense, capacity, level, effects}}"""
    from entities.effects_defs import parse_effects
    with _conn() as c:
        rows = c.execute(
            "SELECT id, slot, item_id, name, defense, capacity, level, effects FROM equipment WHERE player_id=? AND is_equipped=1",
            (pid,),
        ).fetchall()
        result = {}
        for r in rows:
            result[r[1]] = {"id": r[0], "item_id": r[2], "name": r[3], "defense": r[4], "capacity": r[5], "level": r[6],
                            "effects": parse_effects(r[7])}
        return result


def get_equipment_inventory(pid: int) -> list[dict]:
    """获取玩家所有装备（含未装备），返回列表"""
    from entities.effects_defs import parse_effects
    with _conn() as c:
        rows = c.execute(
            "SELECT id, slot, item_id, name, defense, capacity, level, is_equipped, effects FROM equipment WHERE player_id=?",
            (pid,),
        ).fetchall()
        return [
            {"id": r[0], "slot": r[1], "item_id": r[2], "name": r[3],
             "defense": r[4], "capacity": r[5], "level": r[6], "is_equipped": r[7],
             "effects": parse_effects(r[8])}
            for r in rows
        ]


def equip_item(pid: int, slot: str, item_id: str, name: str, defense: int = 0, capacity: int = 0, level: int = 1, effects: list | None = None) -> int:
    """装备物品（替换旧装备，旧装备标记为未装备）"""
    from entities.effects_defs import roll_effects_for_slot, serialize_effects
    if effects is None:
        effects = roll_effects_for_slot(level, slot)
    effects_str = serialize_effects(effects)
    with _conn() as c:
        # 卸下旧装备
        c.execute("UPDATE equipment SET is_equipped=0 WHERE player_id=? AND slot=?", (pid, slot))
        # 插入新装备
        c.execute(
            "INSERT INTO equipment(player_id, slot, item_id, name, defense, capacity, level, is_equipped, effects) VALUES(?,?,?,?,?,?,?,1,?)",
            (pid, slot, item_id, name, defense, capacity, level, effects_str),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def equip_from_inventory(pid: int, equip_id: int) -> bool:
    """从库存中装备指定装备"""
    with _conn() as c:
        row = c.execute(
            "SELECT slot FROM equipment WHERE id=? AND player_id=?", (equip_id, pid)
        ).fetchone()
        if not row:
            return False
        slot = row[0]
        # 卸下当前装备
        c.execute("UPDATE equipment SET is_equipped=0 WHERE player_id=? AND slot=?", (pid, slot))
        # 装备指定物品
        c.execute("UPDATE equipment SET is_equipped=1 WHERE id=?", (equip_id,))
        return True


def unequip_slot(pid: int, slot: str):
    """卸下指定槽位装备（标记为未装备，物品保留在库存，不会删除）"""
    with _conn() as c:
        c.execute("UPDATE equipment SET is_equipped=0 WHERE player_id=? AND slot=?", (pid, slot))


def get_total_defense(pid: int) -> int:
    """获取玩家总防御力（头盔 + 护甲）"""
    equip = get_equipment(pid)
    defense = 0
    if "helmet" in equip:
        defense += equip["helmet"]["defense"]
    if "armor" in equip:
        defense += equip["armor"]["defense"]
    return defense


def get_backpack_capacity(pid: int) -> int:
    """获取背包容量（0 = 没有背包，不能拾取资源）"""
    equip = get_equipment(pid)
    if "backpack" in equip:
        return equip["backpack"]["capacity"]
    return 0


def add_equipment(pid: int, item_id: str, slot: str, level: int = 1, effects: list | None = None):
    """添加装备到库存

    如果该槽位已有装备，则新装备标记为未装备（存入库存）。
    如果该槽位为空，则直接装备。
    effects: 附加效果id列表；为 None 时按等级自动随机生成（Lv.5+）
    返回装备 ID
    """
    from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
    from entities.effects_defs import roll_effects_for_slot, serialize_effects
    from config import upgrade_mult_product
    defs = {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}
    info = defs.get(slot, {}).get(item_id)
    if not info:
        return None
    if effects is None:
        effects = roll_effects_for_slot(level, slot)
    effects_str = serialize_effects(effects)
    # 防御按等级缩放（平方根亚线性倍率），等级越高防御越高但不爆炸
    base_defense = info.get("defense", 0)
    defense = round(base_defense * upgrade_mult_product(level))
    # 检查是否已有装备在该部位
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM equipment WHERE player_id=? AND slot=? AND is_equipped=1",
            (pid, slot),
        ).fetchone()
        if existing:
            # 部位已被占，加入库存（未装备）
            c.execute(
                "INSERT INTO equipment(player_id, slot, item_id, name, defense, capacity, level, is_equipped, effects) VALUES(?,?,?,?,?,?,?,0,?)",
                (pid, slot, item_id, info["name"], defense, info.get("capacity", 0), level, effects_str),
            )
            return c.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            # 部位为空，直接装备
            return equip_item(pid, slot, item_id, info["name"], defense, info.get("capacity", 0), level, effects)


def get_equipment_materials(pid: int, slot: str, name: str, level: int, exclude_id: int = None) -> list[int]:
    """获取可用于升级的同名同级未装备物品 ID 列表

    exclude_id: 排除正在升级的装备自身（避免把要升级的装备自己当材料删掉）
    """
    with _conn() as c:
        if exclude_id is None:
            rows = c.execute(
                "SELECT id FROM equipment WHERE player_id=? AND slot=? AND name=? AND level=? AND is_equipped=0",
                (pid, slot, name, level),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id FROM equipment WHERE player_id=? AND slot=? AND name=? AND level=? AND is_equipped=0 AND id!=?",
                (pid, slot, name, level, exclude_id),
            ).fetchall()
        return [r[0] for r in rows]


def upgrade_equipment(pid: int, equip_id: int = None, material_id: int = None) -> bool:
    """升级指定装备

    升级条件：
    1. 拥有同名同级的装备作为材料
    2. 金币足够（防御力 × 30）
    3. 材料不能是装备自身

    升级效果：
    - 防御力 ×该等级分段倍率（递减：低等级升得快，高等级衰减）
    - 等级 +1
    - 已有效果等级只升不降，并按新等级补齐新效果
    """
    from config import upgrade_mult_for_level
    if equip_id is None:
        return False
    with _conn() as c:
        row = c.execute(
            "SELECT name,level,defense,effects FROM equipment WHERE id=? AND player_id=?",
            (equip_id, pid),
        ).fetchone()
        if not row:
            return False
        name, level, defense, effects = row
        cost = defense * 30
        gold = c.execute("SELECT gold FROM players WHERE id=?", (pid,)).fetchone()[0]
        if gold < cost:
            return False
        # 查找同名同级的另一件装备作为材料
        if material_id is not None:
            donor = c.execute(
                "SELECT id FROM equipment WHERE id=? AND player_id=? AND slot=(SELECT slot FROM equipment WHERE id=?) AND name=? AND level=? AND is_equipped=0",
                (material_id, pid, equip_id, name, level),
            ).fetchone()
        else:
            donor = c.execute(
                "SELECT id FROM equipment WHERE player_id=? AND slot=(SELECT slot FROM equipment WHERE id=?) AND name=? AND level=? AND is_equipped=0 AND id!=? LIMIT 1",
                (pid, equip_id, name, level, equip_id),
            ).fetchone()
        if not donor:
            return False
        # 消耗材料装备
        c.execute("DELETE FROM equipment WHERE id=?", (donor[0],))
        # 提升主装备：按新等级查亚线性倍率（相邻累计倍率之比，与创建时数值一致）
        new_level = level + 1
        new_defense = round(defense * upgrade_mult_for_level(new_level))
        # 升级后效果：已有效果等级只升不降（refresh），并按新等级补齐新效果（roll）
        from entities.effects_defs import roll_effects_for_slot, serialize_effects, parse_effects, refresh_effect_levels
        # 需要获取 slot 用于分类抽取
        slot_row = c.execute("SELECT slot FROM equipment WHERE id=?", (equip_id,)).fetchone()
        item_slot = slot_row[0] if slot_row else "armor"
        new_effects = serialize_effects(roll_effects_for_slot(new_level, item_slot, refresh_effect_levels(parse_effects(effects), new_level)))
        c.execute(
            "UPDATE equipment SET defense=?, level=level+1, effects=? WHERE id=?",
            (new_defense, new_effects, equip_id),
        )
        c.execute("UPDATE players SET gold=gold-? WHERE id=?", (cost, pid))
        return True


def sell_equipment(pid: int, equip_id: int) -> int:
    """售卖装备（头盔/护甲/背包），返回获得金币数

    修复：售卖价以仓库页显示价为准（显示 = 背包容量/其余防御 × 2），不再按升级成本折算，
    保证玩家在仓库页看到的售价与实际售得金币一致。
    """
    with _conn() as c:
        row = c.execute(
            "SELECT slot, defense, capacity FROM equipment WHERE id=? AND player_id=?",
            (equip_id, pid),
        ).fetchone()
        if not row:
            return 0
        slot, defense, capacity = row
        # 售卖价 = 基础值（背包取容量，其余取防御）× 2（与仓库页显示价同一口径）
        base = capacity if slot == "backpack" else defense
        gold_earned = int(base) * 2
        c.execute("DELETE FROM equipment WHERE id=?", (equip_id,))
        c.execute("UPDATE players SET gold=gold+? WHERE id=?", (gold_earned, pid))
        return gold_earned


def delete_equipment(pid: int, equip_id: int) -> bool:
    """删除指定装备"""
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM equipment WHERE id=? AND player_id=?", (equip_id, pid)
        ).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM equipment WHERE id=?", (equip_id,))
        return True
