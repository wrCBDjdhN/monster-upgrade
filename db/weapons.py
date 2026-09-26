"""武器管理：创建/获取/升级/售卖/删除"""
from db.connection import _conn
# 图鉴解锁：直接 import db.codex（禁经 db.database，否则 database re-export weapons 形成循环导入）
from db.codex import unlock_codex_entry


def create_weapon(pid: int, item_id: str, kind: str, name: str, damage: float, attack_speed: float, level: int = 1, effects: list | None = None) -> int:
    """创建武器实例，返回武器 ID

    item_id: 武器物品ID（如 "iron_sword"）
    kind: "melee"(近战) 或 "ranged"(远程)
    level: 武器等级（默认1）
    effects: 附加效果id列表；为 None 时按等级自动随机生成（Lv.5+）
    """
    from entities.effects_defs import roll_effects_for_slot, serialize_effects
    if effects is None:
        effects = roll_effects_for_slot(level, "weapon")
    effects_str = serialize_effects(effects)
    with _conn() as c:
        c.execute(
            "INSERT INTO weapons(player_id,item_id,kind,name,damage,attack_speed,level,effects) VALUES(?,?,?,?,?,?,?,?)",
            (pid, item_id, kind, name, damage, attack_speed, level, effects_str),
        )
        wid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    # 图鉴解锁：武器写入数据库后解锁对应条目（事务提交后再写，避免嵌套连接锁冲突）
    unlock_codex_entry(pid, "weapon", item_id)
    return wid


def add_weapon(pid: int, item_id: str, name: str, kind: str, damage: float, attack_speed: float = 1.0, level: int = 1, effects: list | None = None) -> int:
    """通过 item_id 创建武器（市场购买/开箱用）"""
    # 图鉴解锁由 create_weapon 统一处理（本函数委托其完成插入）
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
        from entities.effects_defs import roll_effects_for_slot, serialize_effects, parse_effects, refresh_effect_levels
        new_effects = serialize_effects(roll_effects_for_slot(new_level, "weapon", refresh_effect_levels(parse_effects(effects), new_level)))
        c.execute(
            "UPDATE weapons SET damage=?, attack_speed=?, level=level+1, effects=? WHERE id=?",
            (new_dmg, new_spd, new_effects, wid),
        )
        c.execute("UPDATE players SET gold=gold-? WHERE id=?", (cost, pid))
        return True


def weapon_sell_price(damage, sell_bonus: float = 0.0) -> int:
    """武器售卖价（纯函数，不查库）：int(伤害 × 2 × (1 + 市场回收加成))

    sell_bonus 取 entities.facility_defs.market_sell_bonus(市场设施等级)：
    Lv0（未建造）/Lv1 = 0.0 → 与旧口径 `int(伤害 × 2)` **完全一致**（零回归）；
    Lv2 = +5%、Lv3 = +10%。取整向下，保证显示价 == 实际入账金币。
    """
    return int(int(damage) * 2 * (1.0 + sell_bonus))


def sell_weapon(pid: int, wid: int, sell_bonus: float = 0.0) -> int:
    """售卖武器，返回获得金币数

    售卖价以仓库页显示价为准（显示 = 伤害 × 2，见 weapon_sell_price），
    保证玩家在仓库页看到的售价与实际售得金币一致。

    sell_bonus：市场设施的售出回收加成（阶段10，Lv0/Lv1 传 0.0 即旧口径）。
    """
    with _conn() as c:
        w = c.execute(
            "SELECT damage FROM weapons WHERE id=? AND player_id=?",
            (wid, pid),
        ).fetchone()
        if not w:
            return 0
        # 售卖价 = 伤害 × 2 × (1 + 回收加成)（与仓库页显示价同一口径）
        gold_earned = weapon_sell_price(w[0], sell_bonus)
        c.execute("DELETE FROM weapons WHERE id=?", (wid,))
        c.execute("UPDATE players SET gold=gold+? WHERE id=?", (gold_earned, pid))
        return gold_earned


def delete_weapon(pid: int, wid: int):
    """删除武器"""
    with _conn() as c:
        c.execute("DELETE FROM weapons WHERE id=? AND player_id=?", (wid, pid))


def reforge_weapon(weapon_id: int, new_effects: str) -> None:
    """重铸武器词条：只把 effects 列整体覆盖为 new_effects，**等级/伤害/攻速一律不动**

    new_effects 为 entities.effects_defs.serialize_effects() 产出的 "id:level" 逗号分隔字符串
    （由调用方按物品等级 roll 生成，武器走 debuff 池）。

    语义说明：重铸是**完全重新随机**，结果可能比原词条更差（词条种类、组合均为随机）；
    这与「升级只升不降」的 refresh_effect_levels 口径互不冲突——升级只负责抬升已有词条等级，
    重铸只负责整体替换词条内容，两者永不互相覆盖对方的保证。
    武器 id 为全局自增主键，无需再按 player_id 过滤（调用方从玩家自己的武器列表取值）。
    """
    with _conn() as c:
        c.execute("UPDATE weapons SET effects=? WHERE id=?", (new_effects or "", weapon_id))
