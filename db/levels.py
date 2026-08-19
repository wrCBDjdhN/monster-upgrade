"""角色等级管理：等级/经验/待选升级次数/永久属性加成

按 (player_id, character_id) 独立存储——同一玩家的不同职业（initial/mage/knight/assassin）
各自有独立的等级与永久加成。升级次数累积在 pending_choices（3 选 1 面板消费）。

表结构（character_levels）：
    player_id      INTEGER 玩家ID（FK players.id）
    character_id   TEXT    角色ID（initial/mage/knight/assassin）
    level          INTEGER 当前等级（1 起步）
    exp            INTEGER 当前经验（升级所需 = exp_needed_for_level(level)）
    pending_choices INTEGER 待消费的 3 选 1 升级次数（连升可累积）
    bonus_hp        REAL    永久生命加成（累计值）
    bonus_damage    REAL    永久伤害加成
    bonus_defense   REAL    永久防御加成
    bonus_speed     REAL    永久移速加成（像素/帧）
    bonus_atk_speed REAL    永久攻速加成
"""
from db.connection import _conn
from config import PLAYER_MAX_LEVEL, LEVEL_BONUS_POOL, exp_needed_for_level

# 加成列白名单（= LEVEL_BONUS_POOL 的 key，用于 choose_bonus 防注入）
_BONUS_COLUMNS = tuple(b["key"] for b in LEVEL_BONUS_POOL)


def _default_level_data() -> dict:
    """无记录时的默认等级数据（Lv.1 / 0 经验 / 无加成）"""
    return {
        "level": 1,
        "exp": 0,
        "pending_choices": 0,
        "bonus_hp": 0.0,
        "bonus_damage": 0.0,
        "bonus_defense": 0.0,
        "bonus_speed": 0.0,
        "bonus_atk_speed": 0.0,
    }


def get_character_levels(pid: int, character_id: str) -> dict:
    """读取指定角色的等级数据；无记录时返回默认数据（不写库）"""
    with _conn() as c:
        row = c.execute(
            "SELECT level, exp, pending_choices, bonus_hp, bonus_damage,"
            " bonus_defense, bonus_speed, bonus_atk_speed"
            " FROM character_levels WHERE player_id=? AND character_id=?",
            (pid, character_id),
        ).fetchone()
    if row is None:
        return _default_level_data()
    return {
        "level": row[0],
        "exp": row[1],
        "pending_choices": row[2],
        "bonus_hp": row[3],
        "bonus_damage": row[4],
        "bonus_defense": row[5],
        "bonus_speed": row[6],
        "bonus_atk_speed": row[7],
    }


def add_exp(pid: int, character_id: str, amount: int) -> dict:
    """增加经验并自动处理升级（可连升多级），返回最新等级数据

    - 每次升级 pending_choices +1（由 3 选 1 面板消费）；
    - 满级（PLAYER_MAX_LEVEL）后经验清零，不再累积。
    """
    with _conn() as c:
        row = c.execute(
            "SELECT level, exp FROM character_levels WHERE player_id=? AND character_id=?",
            (pid, character_id),
        ).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO character_levels(player_id, character_id) VALUES(?,?)",
                (pid, character_id),
            )
            level, exp = 1, 0
        else:
            level, exp = row[0], row[1]
        # 经验累积 + 自动升级循环（连升多级时 pending_choices 逐级累加）
        exp += amount
        level_ups = 0
        while level < PLAYER_MAX_LEVEL and exp >= exp_needed_for_level(level):
            exp -= exp_needed_for_level(level)
            level += 1
            level_ups += 1
        if level >= PLAYER_MAX_LEVEL:
            exp = 0  # 满级后经验清零，避免溢出累积
        c.execute(
            "UPDATE character_levels SET level=?, exp=?, pending_choices=pending_choices+?"
            " WHERE player_id=? AND character_id=?",
            (level, exp, level_ups, pid, character_id),
        )
    return get_character_levels(pid, character_id)


def choose_bonus(pid: int, character_id: str, bonus_key: str) -> dict:
    """消费一次待选升级（pending_choices-1）并应用对应永久加成

    bonus_key 必须为 LEVEL_BONUS_POOL 中的 key（白名单校验后拼列名，防注入）。
    返回最新等级数据。
    """
    if bonus_key not in _BONUS_COLUMNS:
        raise ValueError(f"未知升级加成: {bonus_key}")
    # 取该加成的配置数值（LEVEL_BONUS_POOL 的 value）。
    # 修复：旧版硬编码 +1 导致写库数值错误——升级面板当场生效 +20、DB 只记 +1，
    # 下一局 setup 时只补 1 血 → 血上限变成 101（HP=101 bug）。damage/defense/speed/atk_speed 同理。
    bonus_value = next((b["value"] for b in LEVEL_BONUS_POOL if b["key"] == bonus_key), 1)
    with _conn() as c:
        # WHERE 追加 pending_choices>0 保证无待选时不会误扣为负
        c.execute(
            f"UPDATE character_levels SET {bonus_key}={bonus_key}+?,"
            " pending_choices=pending_choices-1"
            " WHERE player_id=? AND character_id=? AND pending_choices>0",
            (bonus_value, pid, character_id),
        )
    return get_character_levels(pid, character_id)