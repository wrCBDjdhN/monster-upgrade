"""药水管理：购买/使用/删除"""
from db.connection import _conn


def add_potion(pid: int, item_id: str, name: str, effect: str, value: float, duration: float = 0):
    """添加药水（自动叠加数量）"""
    with _conn() as c:
        existing = c.execute(
            "SELECT id, quantity FROM potions WHERE player_id=? AND item_id=?",
            (pid, item_id),
        ).fetchone()
        if existing:
            c.execute("UPDATE potions SET quantity=quantity+1 WHERE id=?", (existing[0],))
        else:
            c.execute(
                "INSERT INTO potions(player_id, item_id, name, effect, value, duration, quantity) VALUES(?,?,?,?,?,?,1)",
                (pid, item_id, name, effect, value, duration),
            )


def get_potions(pid: int) -> list[dict]:
    """获取玩家药水列表"""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, item_id, name, effect, value, duration, quantity FROM potions WHERE player_id=?",
            (pid,),
        ).fetchall()
        return [
            {"id": r[0], "item_id": r[1], "name": r[2], "effect": r[3],
             "value": r[4], "duration": r[5], "quantity": r[6]}
            for r in rows
        ]


def use_potion(pid: int, potion_id: int) -> dict | None:
    """使用一瓶药水，返回药水信息（effect/value/duration），数量减 1，为 0 则删除"""
    with _conn() as c:
        p = c.execute(
            "SELECT id, effect, value, duration, quantity FROM potions WHERE id=? AND player_id=?",
            (potion_id, pid),
        ).fetchone()
        if not p or p[4] <= 0:
            return None
        result = {"effect": p[1], "value": p[2], "duration": p[3]}
        if p[4] <= 1:
            c.execute("DELETE FROM potions WHERE id=?", (potion_id,))
        else:
            c.execute("UPDATE potions SET quantity=quantity-1 WHERE id=?", (potion_id,))
        return result


def remove_potion(pid: int, potion_id: int):
    """删除药水"""
    with _conn() as c:
        c.execute("DELETE FROM potions WHERE id=? AND player_id=?", (potion_id, pid))
