"""角色管理：角色解锁（购买）持久化

记录玩家已购买解锁的角色。初始角色（initial）免费自带，无需记录；
仅「法师/骑士/刺客」等付费角色购买后写入本表。
"""
from db.connection import _conn


def get_unlocked_characters(pid: int) -> list[str]:
    """获取玩家已解锁的角色 id 列表

    初始角色永远在列表中（免费自带，无需购买）。
    返回: 角色 id 列表，如 ["initial", "mage"]
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT character_id FROM character_unlocks WHERE player_id=?",
            (pid,),
        ).fetchall()
    return ["initial"] + [r[0] for r in rows]


def is_character_unlocked(pid: int, character_id: str) -> bool:
    """判断角色是否已解锁（初始角色恒为 True）"""
    if character_id == "initial":
        return True
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM character_unlocks WHERE player_id=? AND character_id=?",
            (pid, character_id),
        ).fetchone()
    return row is not None


def unlock_character(pid: int, character_id: str) -> bool:
    """解锁角色（幂等：已解锁则直接返回 True）

    返回: 是否解锁成功（重复解锁也返回 True，不会重复插入）
    """
    if character_id == "initial":
        return True
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO character_unlocks(player_id, character_id, unlocked_at) VALUES(?, ?, ?)",
            (pid, character_id, __import__("datetime").datetime.now().isoformat()),
        )
    return True