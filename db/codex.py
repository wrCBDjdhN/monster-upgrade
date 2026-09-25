"""图鉴解锁持久化：记录玩家已解锁的怪物/武器/装备/药水条目

表 codex_unlocks 由 db/database.py 的 init_db() 创建（UNIQUE(player_id, category, item_id)）。
本模块提供幂等的解锁写入与查询，供击杀/拾取/入库等触发点调用。
"""
from db.connection import _conn


def unlock_codex_entry(player_id: int, category: str, item_id: str) -> bool:
    """解锁图鉴条目（幂等：重复调用不报错、不产生重复行）

    返回 True 表示本次为新解锁（INSERT 实际插入），False 表示已解锁过。
    调用方可据返回值弹「图鉴解锁」提示，避免重复提示。

    category: "monster" | "weapon" | "equipment" | "potion"
    item_id: 怪物类名（如 "Zombie"）或物品 item_id（如 "wado_ichimonji"）
    """
    with _conn() as c:
        # INSERT OR IGNORE：UNIQUE(player_id, category, item_id) 冲突时静默跳过，
        # 保证重复击杀/拾取/入库不会崩溃也不会产生重复记录
        cur = c.execute(
            "INSERT OR IGNORE INTO codex_unlocks(player_id, category, item_id) VALUES(?,?,?)",
            (player_id, category, item_id),
        )
        # rowcount：新插入=1，IGNORE 跳过=0 → 是否新解锁
        return cur.rowcount > 0


def get_codex_unlocks(player_id: int) -> set[tuple[str, str]]:
    """查询玩家已解锁的全部图鉴条目，返回 {(category, item_id), ...}"""
    with _conn() as c:
        rows = c.execute(
            "SELECT category, item_id FROM codex_unlocks WHERE player_id=?",
            (player_id,),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}