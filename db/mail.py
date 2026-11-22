"""邮件系统：系统奖励与溢出物品的统一收件箱

设计口径（用户口径 2026-11-07）：
- 系统奖励（任务/成就、图鉴档位）不再直接入账，统一生成邮件；
- 撤离时仓库装不下的战利品转为邮件附件，不再丢弃；
- 领取邮件时先校验仓库剩余容量（口径复用 db.warehouse.warehouse_item_capacity，
  金币/经验/药水均 0 格），不足则整封拒绝领取，避免「领一半」。

表结构（db/database.py init_db 创建）：
- mails: 每封邮件一行（player_id/标题/正文/金币/经验/来源/创建时间/领取标记）
- mail_attachments: 附件逐条（类型/item_id/等级/数量）

附件类型：resource / weapon / helmet / armor / backpack / potion / box
（与 warehouse_item_capacity 的 item_type 口径完全一致；box 为武器箱/装备箱，
领取后进入 warehouse_boxes，每箱占 1 格容量）
"""
from db.connection import _conn
from db.warehouse import warehouse_item_capacity, warehouse_remaining_capacity

# 允许的附件类型（与仓库容量口径、装备槽位保持一致）
ITEM_TYPES = ("resource", "weapon", "helmet", "armor", "backpack", "potion", "box")


def _normalize_attachments(attachments) -> list[dict]:
    """把调用方传入的附件清单规整为 (item_type,item_id,level,quantity) 列表

    非法类型/数量直接跳过（比整封邮件写入失败更安全：奖励至少保留其余合法部分），
    跳过项在调用方不可见不影响主流程；正常调用方（evac/任务/图鉴）只传合法类型。
    """
    out: list[dict] = []
    for a in attachments or []:
        item_type = str(a.get("item_type", ""))
        item_id = str(a.get("item_id", ""))
        qty = int(a.get("quantity", 0) or 0)
        if item_type not in ITEM_TYPES or not item_id or qty <= 0:
            continue
        out.append({
            "item_type": item_type,
            "item_id": item_id,
            "level": int(a.get("level", 1) or 1),
            "quantity": qty,
        })
    return out


def create_mail(pid: int, title: str, body: str = "", *, gold: int = 0,
                exp: int = 0, attachments=None, source: str = "") -> int:
    """创建一封邮件（主表 + 附件），返回邮件 id

    邮件在创建时即落库，领取动作由 claim_mail 完成；金币/经验只是待领数值，
    不会在创建时入账（避免「已发奖励但玩家没点领取」的口径混乱）。
    """
    items = _normalize_attachments(attachments)
    with _conn() as c:
        c.execute(
            "INSERT INTO mails(player_id, title, body, gold, exp, source) "
            "VALUES(?,?,?,?,?,?)",
            (pid, str(title), str(body), int(gold), int(exp), str(source)),
        )
        mail_id = int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        for a in items:
            c.execute(
                "INSERT INTO mail_attachments(mail_id, item_type, item_id, level, quantity) "
                "VALUES(?,?,?,?,?)",
                (mail_id, a["item_type"], a["item_id"], a["level"], a["quantity"]),
            )
    return mail_id


def _attachments_for(c, mail_ids: list[int]) -> dict[int, list[dict]]:
    """批量取附件：{mail_id: [附件dict, ...]}（避免逐封查库）"""
    if not mail_ids:
        return {}
    marks = ",".join("?" for _ in mail_ids)
    rows = c.execute(
        f"SELECT mail_id, item_type, item_id, level, quantity "
        f"FROM mail_attachments WHERE mail_id IN ({marks}) ORDER BY id",
        tuple(mail_ids),
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for mail_id, item_type, item_id, level, quantity in rows:
        out.setdefault(int(mail_id), []).append({
            "item_type": str(item_type),
            "item_id": str(item_id),
            "level": int(level),
            "quantity": int(quantity),
        })
    return out


def get_mails(pid: int, include_claimed: bool = True) -> list[dict]:
    """取玩家邮件列表（未领取在前、新的在前），附带附件明细

    返回字段：id/title/body/gold/exp/source/created_at/claimed/attachments
    """
    sql = ("SELECT id, title, body, gold, exp, source, created_at, claimed "
           "FROM mails WHERE player_id=?")
    if not include_claimed:
        sql += " AND claimed=0"
    sql += " ORDER BY claimed ASC, id DESC"
    with _conn() as c:
        rows = c.execute(sql, (pid,)).fetchall()
        att = _attachments_for(c, [int(r[0]) for r in rows])
    return [
        {
            "id": int(r[0]),
            "title": str(r[1]),
            "body": str(r[2]),
            "gold": int(r[3]),
            "exp": int(r[4]),
            "source": str(r[5]),
            "created_at": str(r[6]),
            "claimed": bool(r[7]),
            "attachments": att.get(int(r[0]), []),
        }
        for r in rows
    ]


def get_mail(pid: int, mail_id: int) -> dict | None:
    """取单封邮件（含附件）；不存在返回 None"""
    with _conn() as c:
        row = c.execute(
            "SELECT id, title, body, gold, exp, source, created_at, claimed "
            "FROM mails WHERE id=? AND player_id=?",
            (int(mail_id), pid),
        ).fetchone()
        att = _attachments_for(c, [int(row[0])]) if row else {}
    if not row:
        return None
    return {
        "id": int(row[0]),
        "title": str(row[1]),
        "body": str(row[2]),
        "gold": int(row[3]),
        "exp": int(row[4]),
        "source": str(row[5]),
        "created_at": str(row[6]),
        "claimed": bool(row[7]),
        "attachments": att.get(int(row[0]), []),
    }


def delete_claimed_mails(pid: int) -> int:
    """删除玩家所有已领取邮件（连同附件），返回删除封数

    附件表没有级联删除，必须先按 mail_id 清附件再删主表，避免孤儿行。
    只删 claimed=1，未领取邮件不受影响。
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM mails WHERE player_id=? AND claimed=1", (pid,),
        ).fetchall()
        mail_ids = [int(r[0]) for r in rows]
        if not mail_ids:
            return 0
        marks = ",".join("?" for _ in mail_ids)
        c.execute(f"DELETE FROM mail_attachments WHERE mail_id IN ({marks})", tuple(mail_ids))
        c.execute("DELETE FROM mails WHERE player_id=? AND claimed=1", (pid,))
    return len(mail_ids)


def get_unclaimed_mail_count(pid: int) -> int:
    """未领取邮件数（开始界面邮件按钮角标/列表提示用）"""
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM mails WHERE player_id=? AND claimed=0", (pid,),
        ).fetchone()
    return int(row[0]) if row else 0


def _capacity_need(attachments: list[dict]) -> int:
    """附件总占格（口径与仓库完全同源：db.warehouse.warehouse_item_capacity）"""
    return sum(
        warehouse_item_capacity(a["item_type"], a["item_id"]) * int(a["quantity"])
        for a in attachments
    )


def can_claim_mail(pid: int, mail_id: int) -> tuple[bool, str, int, int]:
    """领取预检：返回 (可领, 原因, 需要格数, 剩余格数)

    不可领的原因只有两种：邮件不存在/已领取、仓库剩余容量不足。
    容量口径与 add_warehouse_item_checked 一致（药水/金币 0 格，资源 1 格/件，
    武器/装备按 capacity_cost）。
    """
    mail = get_mail(pid, mail_id)
    if mail is None:
        return False, "邮件不存在", 0, 0
    if mail["claimed"]:
        return False, "该邮件已领取", 0, 0
    need = _capacity_need(mail["attachments"])
    remain = warehouse_remaining_capacity(pid)
    if need > remain:
        return (False,
                f"仓库容量不足（剩 {remain} 格，需 {need} 格），请先升级仓库或清理空间",
                need, remain)
    return True, "", need, remain


def claim_mail(pid: int, mail_id: int) -> tuple[dict | None, str]:
    """领取邮件：先预检容量 → 标记已领 → 返回待发放载荷

    返回 (mail_dict, 失败原因)。**只标记领取并返回载荷，实际入库由调用方执行**
    （金币/经验/物品分别走 db.players.add_gold / db.levels.add_exp / add_*，
    与任务/图鉴领奖的现有链路一致）。容量预检已保证后续入库不会因满仓失败。
    """
    ok, reason, _need, _remain = can_claim_mail(pid, mail_id)
    if not ok:
        return None, reason
    with _conn() as c:
        cur = c.execute(
            "UPDATE mails SET claimed=1, claimed_at=datetime('now','localtime') "
            "WHERE id=? AND player_id=? AND claimed=0",
            (int(mail_id), pid),
        )
        if cur.rowcount == 0:
            return None, "该邮件已领取"
    return get_mail(pid, mail_id), ""
