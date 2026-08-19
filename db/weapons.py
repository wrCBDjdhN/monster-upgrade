"""武器管理：创建/获取/升级/售卖/删除"""
from db.connection import _conn


def create_weapon(pid: int, item_id: str, kind: str, name: str, damage: float, attack_speed: float, level: int = 1, effects: list | None = None) -> int:
    """创建武器实例，返回武器 ID

    item_id: 武器物品ID（如 "iron_sword"）
    kind: "melee"(近战) 或 "ranged"(远程)
    level: 武器等级（默认1）
    effects: 附加效果id列表；为 None 时按等级自动随机生成（Lv.5+）
    """
    from entities.effects_defs import roll_effects, serialize_effects
    if effects is None:
        effects = roll_effects(level)
    effects_str = serialize_effects(effects)
    with _conn() as c:
        c.execute(
            "INSERT INTO weapons(player_id,item_id,kind,name,damage,attack_speed,level,effects) VALUES(?,?,?,?,?,?,?,?)",
            (pid, item_id, kind, name, damage, attack_speed, level, effects_str),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def add_weapon(pid: int, item_id: str, name: str, kind: str, damage: float, attack_speed: float = 1.0, level: int = 1, effects: list | None = None) -> int:
    """通过 item_id 创建武器（市场购买/开箱用）"""
    return create_weapon(pid, item_id, kind, name, damage, attack_speed, level, effects)


def get_weapons(pid: int) -> list[dict]:
    """获取玩家所有武器"""
    from entities.effects_defs import parse_effects
    with _conn() as c:
        rows = c.execute(
            "SELECT id,item_id,kind,name,damage,attack_speed,level,effects FROM weapons WHERE player_id=?",
            (pid,),
        ).fetchall()
        return [
            {"id": r[0], "item_id": r[1], "kind": r[2], "name": r[3],
             "damage": r[4], "attack_speed": r[5], "level": r[6],
             "effects": parse_effects(r[7])}
            for r in rows
        ]


def upgrade_weapon(pid: int, wid: int) -> bool:
    """升级武器：消耗一把同名同级武器 + 金币

    升级条件：
    1. 拥有同名同级的另一把武器作为材料
    2. 金币足够（基础费用 × 当前等级）

    升级效果：
    - 伤害 ×该等级分段倍率（递减：低等级升得快，高等级衰减）
    - 攻速 ×该等级分段倍率
    - 等级 +1
    - 已有效果等级只升不降，并按新等级补齐新效果
    """
    from config import UPGRADE_BASE_COST, upgrade_mult_for_level
    with _conn() as c:
        w = c.execute(
            "SELECT name,level,damage,attack_speed,effects FROM weapons WHERE id=? AND player_id=?",
            (wid, pid),
        ).fetchone()
        if not w:
            return False
        name, level, dmg, spd, effects = w
        cost = UPGRADE_BASE_COST * level
        gold = c.execute("SELECT gold FROM players WHERE id=?", (pid,)).fetchone()[0]
        if gold < cost:
            return False
        # 查找同名同级的另一把武器作为材料
        donor = c.execute(
            "SELECT id FROM weapons WHERE player_id=? AND name=? AND level=? AND id!=? LIMIT 1",
            (pid, name, level, wid),
        ).fetchone()
        if not donor:
            return False
        # 消耗材料武器
        c.execute("DELETE FROM weapons WHERE id=?", (donor[0],))
        # 提升主武器：按新等级查亚线性倍率（相邻累计倍率之比，与创建时数值一致）
        new_level = level + 1
        mult = upgrade_mult_for_level(new_level)
        new_dmg = round(dmg * mult, 2)
        new_spd = round(spd * mult, 2)
        # 升级后效果：已有效果等级只升不降（refresh），并按新等级补齐新效果（roll）
        from entities.effects_defs import roll_effects, serialize_effects, parse_effects, refresh_effect_levels
        new_effects = serialize_effects(roll_effects(new_level, refresh_effect_levels(parse_effects(effects), new_level)))
        c.execute(
            "UPDATE weapons SET damage=?, attack_speed=?, level=level+1, effects=? WHERE id=?",
            (new_dmg, new_spd, new_effects, wid),
        )
        c.execute("UPDATE players SET gold=gold-? WHERE id=?", (cost, pid))
        return True


def sell_weapon(pid: int, wid: int) -> int:
    """售卖武器，返回获得金币数

    修复：售卖价以仓库页显示价为准（显示 = 伤害 × 2），不再按升级成本折算，
    保证玩家在仓库页看到的售价与实际售得金币一致。
    """
    with _conn() as c:
        w = c.execute(
            "SELECT damage FROM weapons WHERE id=? AND player_id=?",
            (wid, pid),
        ).fetchone()
        if not w:
            return 0
        # 售卖价 = 伤害 × 2（与仓库页显示价同一口径）
        gold_earned = int(w[0]) * 2
        c.execute("DELETE FROM weapons WHERE id=?", (wid,))
        c.execute("UPDATE players SET gold=gold+? WHERE id=?", (gold_earned, pid))
        return gold_earned


def delete_weapon(pid: int, wid: int):
    """删除武器"""
    with _conn() as c:
        c.execute("DELETE FROM weapons WHERE id=? AND player_id=?", (wid, pid))
