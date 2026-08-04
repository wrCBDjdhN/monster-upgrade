"""玩家管理：创建/查询/金币操作"""
from db.connection import _conn


def get_or_create_player(name: str) -> int:
    """获取或创建玩家

    如果玩家已存在则返回其 ID，否则创建新玩家（初始金币 50）。
    返回: 玩家数据库 ID
    """
    with _conn() as c:
        row = c.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()
        if row:
            return row[0]
        c.execute(
            "INSERT INTO players(name, gold, created_at) VALUES(?, 50, ?)",
            (name, __import__("datetime").datetime.now().isoformat()),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_gold(pid: int) -> int:
    """获取玩家金币"""
    with _conn() as c:
        return c.execute("SELECT gold FROM players WHERE id=?", (pid,)).fetchone()[0]


def add_gold(pid: int, amount: int):
    """增加玩家金币"""
    with _conn() as c:
        c.execute("UPDATE players SET gold=gold+? WHERE id=?", (amount, pid))


def spend_gold(pid: int, amount: int) -> bool:
    """花费金币，余额不足返回 False"""
    with _conn() as c:
        gold = c.execute("SELECT gold FROM players WHERE id=?", (pid,)).fetchone()[0]
        if gold < amount:
            return False
        c.execute("UPDATE players SET gold=gold-? WHERE id=?", (amount, pid))
        return True
