"""仓库管理：存储/售卖资源和武器"""
from db.connection import _conn


def add_warehouse_item(pid: int, item_type: str, item_id: str, quantity: int = 1):
    """添加仓库物品（资源或武器），支持叠加数量"""
    with _conn() as c:
        existing = c.execute(
            "SELECT id, quantity FROM warehouse_items WHERE player_id=? AND item_type=? AND item_id=?",
            (pid, item_type, item_id),
        ).fetchone()
        if existing:
            c.execute("UPDATE warehouse_items SET quantity=quantity+? WHERE id=?", (quantity, existing[0]))
        else:
            c.execute(
                "INSERT INTO warehouse_items(player_id, item_type, item_id, quantity) VALUES(?,?,?,?)",
                (pid, item_type, item_id, quantity),
            )


def get_warehouse(pid: int) -> list[dict]:
    """获取玩家仓库物品列表"""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, item_type, item_id, quantity FROM warehouse_items WHERE player_id=?",
            (pid,),
        ).fetchall()
        return [
            {"id": r[0], "item_type": r[1], "item_id": r[2], "quantity": r[3]}
            for r in rows
        ]


def sell_warehouse_item(pid: int, item_id: int) -> tuple[int, str]:
    """售卖仓库物品，返回 (获得金币数, item_id)

    价格：
    - 资源：sell_price × 数量
    - 武器：伤害 × 2
    """
    with _conn() as c:
        row = c.execute(
            "SELECT id, item_type, item_id, quantity FROM warehouse_items WHERE id=? AND player_id=?",
            (item_id, pid),
        ).fetchone()
        if not row:
            return 0, ""
        wid, item_type, db_item_id, quantity = row
        if item_type == "resource":
            from entities.resource_defs import RESOURCES
            info = RESOURCES.get(db_item_id, {})
            sell_price = info.get("sell_price", 1)
            gold_earned = sell_price * quantity
        else:
            gold_earned = quantity * 10  # 武器默认价格
        c.execute("DELETE FROM warehouse_items WHERE id=?", (wid,))
        c.execute("UPDATE players SET gold=gold+? WHERE id=?", (gold_earned, pid))
        return gold_earned, db_item_id
