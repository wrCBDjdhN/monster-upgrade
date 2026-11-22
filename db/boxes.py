"""仓库宝箱：武器箱/装备箱先入库，再在仓库中按数量开启

用户口径 2026-11-21：
- 市场购买与任务奖励的武器箱/装备箱不再立即开启，先存入仓库；
- 每箱占 1 格仓库容量（warehouse_used_capacity 汇总本表 quantity）；
- 开启入口在仓库「宝箱」Tab，可选择开启数量。

表 warehouse_boxes 由 db/database.py init_db 创建，复合主键保证同箱叠加一行。
"""
from db.connection import _conn


def box_type_of(box_id: str) -> str:
    """box_id → 'weapon' / 'equipment'；未知返回空串（箱名与类型口径全在 config）"""
    from config import EQUIPMENT_BOXES, WEAPON_BOXES
    if box_id in WEAPON_BOXES:
        return "weapon"
    if box_id in EQUIPMENT_BOXES:
        return "equipment"
    return ""


def add_warehouse_box(pid: int, box_type: str, box_id: str, quantity: int = 1) -> bool:
    """宝箱入库（同类型同箱叠加数量），非法类型/数量返回 False

    本函数不校验容量：调用方（市场购买/邮件领取）必须先经
    warehouse_remaining_capacity 预检，保证不会塞爆仓库。
    """
    from config import EQUIPMENT_BOXES, WEAPON_BOXES
    qty = int(quantity)
    if qty <= 0:
        return False
    if box_type == "weapon":
        if box_id not in WEAPON_BOXES:
            return False
    elif box_type == "equipment":
        if box_id not in EQUIPMENT_BOXES:
            return False
    else:
        return False
    with _conn() as c:
        c.execute(
            "INSERT INTO warehouse_boxes(player_id, box_type, box_id, quantity) VALUES(?,?,?,?) "
            "ON CONFLICT(player_id, box_type, box_id) "
            "DO UPDATE SET quantity=quantity+excluded.quantity",
            (pid, box_type, box_id, qty),
        )
    return True


def get_warehouse_boxes(pid: int) -> list[dict]:
    """取仓库宝箱列表（含中文名/颜色/等级档），按类型与箱子档位排序"""
    from config import EQUIPMENT_BOXES, WEAPON_BOXES
    with _conn() as c:
        rows = c.execute(
            "SELECT box_type, box_id, quantity FROM warehouse_boxes "
            "WHERE player_id=? AND quantity>0",
            (pid,),
        ).fetchall()
    out: list[dict] = []
    for box_type, box_id, qty in rows:
        defs = WEAPON_BOXES if box_type == "weapon" else EQUIPMENT_BOXES
        info = defs.get(str(box_id), {})
        out.append({
            "box_type": str(box_type),
            "box_id": str(box_id),
            "quantity": int(qty),
            "name": str(info.get("name", box_id)),
            "color": info.get("color", (150, 150, 150)),
            "tier": int(info.get("tier", 0)),
        })
    out.sort(key=lambda b: (b["box_type"] != "weapon", b["tier"]))
    return out


def consume_warehouse_box(pid: int, box_type: str, box_id: str, quantity: int = 1) -> bool:
    """扣除宝箱数量；库存不足返回 False 且不改动库存（开箱前先扣再产出）"""
    qty = int(quantity)
    if qty <= 0:
        return False
    with _conn() as c:
        row = c.execute(
            "SELECT quantity FROM warehouse_boxes "
            "WHERE player_id=? AND box_type=? AND box_id=?",
            (pid, box_type, box_id),
        ).fetchone()
        if row is None or int(row[0]) < qty:
            return False
        left = int(row[0]) - qty
        if left <= 0:
            c.execute(
                "DELETE FROM warehouse_boxes WHERE player_id=? AND box_type=? AND box_id=?",
                (pid, box_type, box_id),
            )
        else:
            c.execute(
                "UPDATE warehouse_boxes SET quantity=? "
                "WHERE player_id=? AND box_type=? AND box_id=?",
                (left, pid, box_type, box_id),
            )
    return True


def warehouse_box_count(pid: int) -> int:
    """仓库宝箱总数量（容量统计用，每箱 1 格）"""
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM warehouse_boxes WHERE player_id=?",
            (pid,),
        ).fetchone()
    return int(row[0]) if row else 0
