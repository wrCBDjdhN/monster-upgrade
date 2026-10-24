"""玩家管理：创建/查询/金币操作"""
from db.connection import _conn


def get_or_create_player(name: str) -> int:
    """获取或创建玩家

    如果玩家已存在则返回其 ID，否则创建新玩家（初始金币 50，并赠送一个小布袋）。
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
        pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    # 事务提交后再赠送背包：equip_item 自带连接，嵌套在上方事务内会撞 SQLite 写锁
    _grant_starter_backpack(pid)
    return pid


def _grant_starter_backpack(pid: int) -> None:
    """给新玩家赠送并装备一个小布袋（用户决策 2026-09-26）

    防死锁：全新存档 equipment 表为空 → get_backpack_capacity 返回 0
    → game/loot.py 拒绝一切资源拾取（skipped_no_bag）→ 永远攒不够撤离点激活
    与局内建造所需材料，首局无解。故在「创建玩家」这一步就按 BACKPACKS 数据表
    赠送（容量读表，不硬编码 10），局域网访客走的也是这条 INSERT 分支，一并受益。

    赠送失败只告警不抛出：宁可让玩家无背包启动，也不能因背包赠送异常导致创建失败。
    """
    # 函数级延迟导入：遵循 db → entities 依赖链的防循环导入约定
    try:
        from db.equipment import equip_item
        from entities.equipment_defs import BACKPACKS

        info = BACKPACKS["small_bag"]
        # effects=[]：赠送的 Lv1 布袋不带随机词条（与 roll_effects_for_slot(1) 结果一致，显式写死更稳）
        equip_item(pid, "backpack", "small_bag", info["name"],
                   capacity=info["capacity"], level=1, effects=[])
    except Exception as e:  # noqa: BLE001 - 赠送失败不得阻断玩家创建
        print(f"[警告] 新玩家赠送小布袋失败（pid={pid}）：{e}")


def ensure_starter_backpack(pid: int) -> None:
    """防死锁兜底：equipment 表无任何背包行时补发并装备 small_bag（用户报告 2026-10-24）

    两处调用：
    - get_or_create_player：新玩家创建后（必无背包，否则首局无解死锁）；
    - db.database.clear_run_equipment：死亡/撤离失败清装后——清装会删掉已装备背包，
      若玩家尚未解锁市场（无法购买）、宝箱背包又只是随机掉（main_pool 普通一员，
      forest 每箱仅约 7.9%），就会复现「无背包 → 不能拾取资源 → 永远攒不够撤离
      激活材料 → 无限死亡循环」的死锁。背包是「游戏进行权」而非强度道具，
      故清装后保底一只（武器/头盔/护甲/药水照丢，死亡惩罚主体不变）。

    判定口径：只看 equipment 表是否有背包行（含未装备行）——玩家仓库里还有
    背包时不去装备界面穿上即可，不白送。补发失败只告警（沿用 _grant_starter_backpack
    的容错口径），不得抛出打断清装流程。
    """
    from db.connection import _conn
    with _conn() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM equipment WHERE player_id=? AND slot='backpack'",
            (pid,)).fetchone()[0]
    if n > 0:
        return
    _grant_starter_backpack(pid)


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
