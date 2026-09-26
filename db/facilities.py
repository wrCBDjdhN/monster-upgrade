"""设施等级持久化（阶段10：开始页市场/锻造坊的建造与升级）

表 facilities 由 db/database.py 的 init_db() 创建
（PRIMARY KEY(player_id, facility_id)，level 0 = 未建造，最高 config.FACILITY_MAX_LEVEL）。

费用口径全部取自 config（config.MARKET_BUILD_COST / *_UPGRADE_COSTS / FORGE_*），
经 entities.facility_defs.facility_cost_at 按「目标等级」取，本模块不硬编码数值。

扣费铁律：**先校验后扣**——金币与每种材料都确认够之后才开始扣除，
任一项不足直接返回 False 且**不扣任何费用**（杜绝「扣了金币没扣到料」的半扣）。
材料走 db.warehouse.spend_warehouse_item，金币走 db.players.spend_gold。
"""
from db.connection import _conn


# ── 内部工具：库存/金币校验（先校验后扣的统一口径）──────────────────

def _resource_stock(player_id: int, item_id: str) -> int:
    """查玩家仓库中某资源（wood/stone/ore）的持有量（无记录视为 0）"""
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM warehouse_items "
            "WHERE player_id=? AND item_id=?",
            (player_id, item_id),
        ).fetchone()
        return int(row[0]) if row else 0


def _stock_of(player_id: int, key: str) -> int:
    """按费用项 key 取玩家持有量：gold 走玩家金币，其余按仓库资源 item_id 取"""
    # 函数级延迟 import：db → entities 依赖链（防循环导入，同 db/ 层其他模块惯例）
    if key == "gold":
        from db.players import get_gold
        return get_gold(player_id)
    return _resource_stock(player_id, key)


def _cost_label(key: str) -> str:
    """费用项 key → 中文名（金币 / 木材 / 石材 / 矿石）"""
    if key == "gold":
        return "金币"
    from entities.resource_defs import RESOURCES
    return str(RESOURCES.get(key, {}).get("name", key))


def _missing_desc(player_id: int, cost: dict) -> str:
    """列出费用中所有不足的项，形如 "缺 木材×12、矿石×3"（全部充足则返回空串）"""
    missing = []
    for key, need in cost.items():
        need = int(need)
        if _stock_of(player_id, key) < need:
            missing.append(f"{_cost_label(key)}×{need}")
    if not missing:
        return ""
    return "缺 " + "、".join(missing)


# ── 查询 ────────────────────────────────────────────────────────

def get_facilities(player_id: int) -> dict[str, int]:
    """返回该玩家全部设施等级 {facility_id: level}（无记录视为 0 级 = 未建造）

    缺失的设施 id 也会以 0 补齐（返回集始终等于 entities.facility_defs.FACILITIES 的键集），
    调用方（如开始页按钮渲染）无需自行判存在性。
    """
    # 函数级延迟 import：db → entities 依赖链
    from entities.facility_defs import FACILITIES
    levels = {fid: 0 for fid in FACILITIES}
    with _conn() as c:
        rows = c.execute(
            "SELECT facility_id, level FROM facilities WHERE player_id=?",
            (player_id,),
        ).fetchall()
    for fid, lv in rows:
        if fid in levels:
            levels[str(fid)] = int(lv)
    return levels


def get_facility_level(player_id: int, facility_id: str) -> int:
    """查单个设施等级（无记录/非法 id 视为 0 = 未建造）"""
    with _conn() as c:
        row = c.execute(
            "SELECT level FROM facilities WHERE player_id=? AND facility_id=?",
            (player_id, facility_id),
        ).fetchone()
        return int(row[0]) if row else 0


# ── 建造 / 升级 ─────────────────────────────────────────────────

def facility_can_afford(player_id: int, facility_id: str, target_level: int) -> tuple[bool, str]:
    """校验能否支付升到 target_level 的费用，返回 (是否可支付, 缺失中文描述)

    缺失描述形如 "缺 木材×12、矿石×3"（全部充足时为空串），供 UI 直接飘字提示。
    target_level 非法或已达满级时返回 (False, 原因文案)——**不查库存不扣费**。
    """
    # 函数级延迟 import：db → entities 依赖链
    from entities.facility_defs import facility_cost_at
    cost = facility_cost_at(facility_id, target_level)
    if not cost:
        return False, "该等级不可建造"
    missing = _missing_desc(player_id, cost)
    return (not missing), missing


def build_facility(player_id: int, facility_id: str) -> bool:
    """建造设施（仅限 0 级）：校验金币+材料 → 全部扣除 → INSERT level=1

    返回 True=建造成功；False=已建造过 / 无此设施 / 费用不足（**失败时一分不扣**）。
    """
    # 函数级延迟 import：db → entities 依赖链
    from entities.facility_defs import facility_cost_at
    if get_facility_level(player_id, facility_id) != 0:
        return False
    cost = facility_cost_at(facility_id, 1)  # 建造费 = 目标等级 1
    if not cost:
        return False
    # ── 先校验（全部充足才开始扣，防半扣）──
    if _missing_desc(player_id, cost):
        return False
    return _pay_and_set(player_id, facility_id, cost, 1)


def upgrade_facility(player_id: int, facility_id: str) -> bool:
    """升级设施一级：当前等级须在 1..FACILITY_MAX_LEVEL-1 → 校验费用 → 扣除 → 等级+1

    返回 True=升级成功；False=未建造 / 已达满级 / 费用不足（**失败时一分不扣**）。
    """
    # 函数级延迟 import：db → entities 依赖链
    from config import FACILITY_MAX_LEVEL
    from entities.facility_defs import facility_cost_at
    cur = get_facility_level(player_id, facility_id)
    if cur < 1 or cur >= FACILITY_MAX_LEVEL:
        return False
    cost = facility_cost_at(facility_id, cur + 1)  # 升级费表键 = 目标等级
    if not cost:
        return False
    if _missing_desc(player_id, cost):
        return False
    return _pay_and_set(player_id, facility_id, cost, cur + 1)


def _pay_and_set(player_id: int, facility_id: str, cost: dict, new_level: int) -> bool:
    """扣费并落库（仅在 _missing_desc 校验通过后调用）

    金币走 spend_gold、材料走 spend_warehouse_item，最后 UPSERT 设施等级。
    扣费中途失败时回滚已扣部分（防半扣的最后一道保险）。
    """
    from db.players import spend_gold
    from db.warehouse import spend_warehouse_item
    # 先扣材料、再扣金币：任一环节失败则把已扣的材料加回，保证不出现半扣
    paid_materials: list[tuple[str, int]] = []
    for key, need in cost.items():
        if key == "gold":
            continue
        need = int(need)
        if not spend_warehouse_item(player_id, key, need):
            for done_key, done_qty in paid_materials:  # 回滚已扣材料
                _refund_material(player_id, done_key, done_qty)
            return False
        paid_materials.append((key, need))
    if int(cost.get("gold", 0)) > 0 and not spend_gold(player_id, int(cost["gold"])):
        for done_key, done_qty in paid_materials:  # 回滚已扣材料
            _refund_material(player_id, done_key, done_qty)
        return False
    with _conn() as c:
        c.execute(
            "INSERT INTO facilities(player_id, facility_id, level) VALUES(?,?,?) "
            "ON CONFLICT(player_id, facility_id) DO UPDATE SET level=excluded.level",
            (player_id, facility_id, new_level),
        )
    return True


def _refund_material(player_id: int, item_id: str, qty: int) -> None:
    """回滚已扣的仓库材料（_pay_and_set 失败补偿用，正常流程不会触发）"""
    from db.warehouse import add_warehouse_item
    add_warehouse_item(player_id, "resource", item_id, qty)
