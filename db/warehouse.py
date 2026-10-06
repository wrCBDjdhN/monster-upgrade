"""仓库管理：存储/售卖/消耗资源和武器

仓库等级与容量（阶段11新增，此前仓库**完全没有容量限制**）：
- 等级存 warehouse_levels 表（db/database.py 的 init_db 建表，复合主键 player_id，
  level 默认 1），容量公式与费用表在 config.WAREHOUSE_*，本模块只做读取与落库。
- 容量**计数口径与背包完全一致**（用户口径：物品的占用容量和在背包中的容量一样），
  权威实现是 game/loot.py:_calc_carried_capacity —— 本模块**只读复用、不修改**该函数，
  仅按同样的 capacity_cost 逐类查表求和。

逐类容量映射（逐行读 loot.py 得出，禁凭印象改口径）：
| 仓库内容                      | 存放表                       | 单件占用容量                                          |
|-------------------------------|------------------------------|-------------------------------------------------------|
| 资源 wood/stone/ore           | warehouse_items('resource')  | **1**（loot.py:116 `sum(res.values())` = 逐件 1 格）   |
| 武器实例                      | weapons                      | weapon_defs.capacity_cost，缺省 2（loot.py:125 同缺省） |
| 武器叠加条目（schema CHECK 允许）| warehouse_items('weapon')  | 同上 × quantity                                        |
| 头盔                          | equipment(slot='helmet')     | HELMETS.capacity_cost，缺省 1（loot.py:131 同缺省）     |
| 护甲                          | equipment(slot='armor')      | ARMORS.capacity_cost，缺省 2（loot.py:136 同缺省）      |
| 背包                          | equipment(slot='backpack')   | BACKPACKS.capacity_cost，缺省 1（loot.py:142 同缺省）   |
| 药水                          | potions                      | **0**（config.RUN_POTION_SLOTS 注释：药水不占容量）     |
| 金币                          | players.gold（不入仓表）      | **0**                                                  |
"""
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


def spend_warehouse_item(player_id: int, item_id: str, qty: int = 1) -> bool:
    """从仓库扣除指定数量的物品（锻造配方制作等局外界面消耗资源用）

    返回 True=扣除成功；False=库存不足或数量非法（不改动库存）。
    库存按 warehouse_items 的 (player_id, item_type, item_id) 定位，
    扣至 0 时删除该行（不留 0 库存残行）。
    """
    if qty <= 0:
        return False
    with _conn() as c:
        row = c.execute(
            "SELECT id, quantity FROM warehouse_items "
            "WHERE player_id=? AND item_id=?",
            (player_id, item_id),
        ).fetchone()
        if not row:
            return False
        wid, quantity = int(row[0]), int(row[1])
        if quantity < qty:
            return False
        if quantity == qty:
            c.execute("DELETE FROM warehouse_items WHERE id=?", (wid,))
        else:
            c.execute("UPDATE warehouse_items SET quantity=? WHERE id=?", (quantity - qty, wid))
        return True


def sell_warehouse_item(pid: int, item_id: int, qty: int | None = None) -> tuple[int, str]:
    """售卖仓库物品，返回 (获得金币数, item_id)

    价格：
    - 资源：sell_price × 数量
    - 武器：伤害 × 2（quantity × 10）

    qty 参数（2026-10-04 新增，仓库售卖选数量面板）：
    - None：整栈售出（保持旧行为，向后兼容所有既有调用方）
    - 正整数：按指定数量部分售出，gold = 单价 × 实售数量；
      实售数钳位在 1..quantity，部分售出时 UPDATE quantity 减量，
      整栈售完才 DELETE 行（与 spend_warehouse_item 的减量口径一致）
    """
    with _conn() as c:
        row = c.execute(
            "SELECT id, item_type, item_id, quantity FROM warehouse_items WHERE id=? AND player_id=?",
            (item_id, pid),
        ).fetchone()
        if not row:
            return 0, ""
        wid, item_type, db_item_id, quantity = row
        # 部分售出数量解析：None=整栈；否则钳位到 1..库存（0/负数/超量一律按合法值收敛）
        sell_qty = quantity if qty is None else max(1, min(int(qty), quantity))
        if item_type == "resource":
            from entities.resource_defs import RESOURCES
            info = RESOURCES.get(db_item_id, {})
            sell_price = info.get("sell_price", 1)
            gold_earned = sell_price * sell_qty
        else:
            gold_earned = sell_qty * 10  # 武器默认价格
        if sell_qty >= quantity:
            c.execute("DELETE FROM warehouse_items WHERE id=?", (wid,))
        else:
            c.execute("UPDATE warehouse_items SET quantity=? WHERE id=?",
                      (quantity - sell_qty, wid))
        c.execute("UPDATE players SET gold=gold+? WHERE id=?", (gold_earned, pid))
        return gold_earned, db_item_id


# ── 仓库等级 / 容量上限 / 升级（阶段11）─────────────────────────────
# 说明：上面 4 个原有函数的**语义一律不变**（只做存取与售卖/扣除），
# 容量校验只出现在**新增**的 add_warehouse_item_checked 与升级链路上，
# 售卖/扣除天然会释放容量，故不需要（也不应）在里面加校验。

def warehouse_capacity_for(level: int) -> int:
    """取某等级的仓库容量上限（纯函数，数值口径全在 config.WAREHOUSE_*）

    公式 = WAREHOUSE_BASE_CAPACITY + (level-1) × WAREHOUSE_CAPACITY_STEP
    （Lv1=50 / Lv2=75 / Lv3=100 / Lv4=125 / Lv5=150）。
    等级 < 1 一律按 Lv1 算（0=未初始化）；高于 WAREHOUSE_MAX_LEVEL 按最高等级算，
    越界不抛异常，避免脏数据把 UI 打死。
    """
    # 函数级延迟 import：db → entities/config 依赖链（防循环导入，同本层其他模块惯例）
    from config import WAREHOUSE_BASE_CAPACITY, WAREHOUSE_CAPACITY_STEP, WAREHOUSE_MAX_LEVEL
    lv = max(1, min(int(level), int(WAREHOUSE_MAX_LEVEL)))
    return int(WAREHOUSE_BASE_CAPACITY) + (lv - 1) * int(WAREHOUSE_CAPACITY_STEP)


def get_warehouse_level(pid: int) -> int:
    """取玩家仓库等级（无记录视为 1 = 初始等级，容量 config.WAREHOUSE_BASE_CAPACITY）"""
    with _conn() as c:
        row = c.execute(
            "SELECT level FROM warehouse_levels WHERE player_id=?", (pid,),
        ).fetchone()
    return int(row[0]) if row else 1


def get_warehouse_capacity(pid: int) -> int:
    """取玩家当前仓库容量上限（= warehouse_capacity_for(get_warehouse_level(pid))）"""
    return warehouse_capacity_for(get_warehouse_level(pid))


def warehouse_item_capacity(item_type: str, item_id: str) -> int:
    """单件物品的仓库容量占用（逐类映射见模块 docstring 的口径表）

    item_type 取 resource / weapon / helmet / armor / backpack / potion
    （金币等非物品类型传 "gold"，恒返回 0）。
    查不到定义时退 loot.py 使用的缺省值，保证与背包对同一件物品算出同样的容量。
    """
    if item_type == "resource":
        return 1  # 与 loot.py:116「资源逐件 1 格」同口径
    if item_type in ("potion", "gold"):
        return 0  # config.RUN_POTION_SLOTS：药水不占容量；金币不是物品
    if item_type in ("helmet", "armor", "backpack"):
        from entities.equipment_defs import ARMORS, BACKPACKS, HELMETS
        defs = {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}[item_type]
        default = 2 if item_type == "armor" else 1  # 与 loot.py:131/136/142 的缺省一致
        return int(defs.get(item_id, {}).get("capacity_cost", default))
    if item_type == "weapon":
        from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
        all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
        return int(all_weapons.get(item_id, {}).get("capacity_cost", 2))  # 同 loot.py:125 缺省
    return 0  # 未知类型按不占容量处理（宁可少算也不误拦玩家入库）


def warehouse_used_capacity(pid: int) -> int:
    """统计玩家仓库**已占用**容量（跨仓库内容分散的 4 张表）

    - warehouse_items：资源按 1/件、武器按 capacity_cost/件，quantity 相乘；
    - weapons：每件武器实例按 weapon_defs.capacity_cost；
    - equipment：按 slot 查 HELMETS/ARMORS/BACKPACKS 的 capacity_cost；
    - potions：**计 0**（药水不占容量，见模块 docstring 口径表）→ 本就不必查这张表；
    - 金币存在 players.gold，走 add_gold 不入仓表，同样不计入。
    """
    total = 0
    with _conn() as c:
        item_rows = c.execute(
            "SELECT item_type, item_id, quantity FROM warehouse_items WHERE player_id=?",
            (pid,),
        ).fetchall()
        weapon_rows = c.execute(
            "SELECT item_id FROM weapons WHERE player_id=?", (pid,),
        ).fetchall()
        equip_rows = c.execute(
            "SELECT slot, item_id FROM equipment WHERE player_id=?", (pid,),
        ).fetchall()
    for item_type, item_id, qty in item_rows:
        total += warehouse_item_capacity(str(item_type), str(item_id)) * int(qty)
    for (item_id,) in weapon_rows:
        total += warehouse_item_capacity("weapon", str(item_id))
    for slot, item_id in equip_rows:
        total += warehouse_item_capacity(str(slot), str(item_id))
    return total


def warehouse_remaining_capacity(pid: int) -> int:
    """剩余可用容量（= 上限 - 已用，下限钳到 0；已超限时返回 0）"""
    return max(0, get_warehouse_capacity(pid) - warehouse_used_capacity(pid))


def warehouse_upgrade_cost_at(target_level: int) -> dict:
    """取升到 target_level 的费用 dict（空 dict = 已满级 / 等级非法）

    费用表键 = **目标等级**（同 MARKET_UPGRADE_COSTS / FORGE_UPGRADE_COSTS 口径），
    数值全部来自 config.WAREHOUSE_UPGRADE_COSTS，本函数不硬编码任何费用。
    """
    # 函数级延迟 import：db → config 依赖链
    from config import WAREHOUSE_MAX_LEVEL, WAREHOUSE_UPGRADE_COSTS
    if target_level <= 1 or target_level > WAREHOUSE_MAX_LEVEL:
        return {}
    return dict(WAREHOUSE_UPGRADE_COSTS.get(target_level, {}))


# ── 升级费用校验内部工具（先校验后扣的统一口径，同 db/facilities.py）──────

def _cost_label(key: str) -> str:
    """费用项 key → 中文名（金币 / 木材 / 石材 / 矿石）"""
    if key == "gold":
        return "金币"
    from entities.resource_defs import RESOURCES
    return str(RESOURCES.get(key, {}).get("name", key))


def _stock_of(player_id: int, key: str) -> int:
    """按费用项 key 取玩家持有量：gold 走玩家金币，其余按仓库资源 item_id 取"""
    if key == "gold":
        from db.players import get_gold
        return get_gold(player_id)
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM warehouse_items "
            "WHERE player_id=? AND item_id=?",
            (player_id, key),
        ).fetchone()
    return int(row[0]) if row else 0


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


def warehouse_can_afford(pid: int) -> tuple[bool, str]:
    """校验能否支付「仓库升一级」的费用，返回 (是否可支付, 缺失中文描述)

    缺失描述形如 "缺 木材×12、矿石×3"（全部充足时为空串），供 UI 直接飘字提示。
    已满级 / 等级非法时返回 (False, 原因文案)——**不查库存不扣费**。
    """
    from config import WAREHOUSE_MAX_LEVEL
    cur = get_warehouse_level(pid)
    if cur >= WAREHOUSE_MAX_LEVEL:
        return False, f"仓库已满级（Lv.{WAREHOUSE_MAX_LEVEL}）"
    cost = warehouse_upgrade_cost_at(cur + 1)
    if not cost:
        return False, "该等级不可升级"
    missing = _missing_desc(pid, cost)
    return (not missing), missing


def upgrade_warehouse(pid: int) -> bool:
    """仓库升一级：校验费用 → 扣除（仓库材料 + 金币）→ UPSERT 等级+1

    返回 True=升级成功；False=已满级 / 费用不足（**失败时一分不扣**）。
    扣费铁律与 db/facilities.py 完全一致：先校验后扣，扣费中途失败回滚已扣部分，
    杜绝「扣了金币没扣到料」的半扣。
    """
    from config import WAREHOUSE_MAX_LEVEL
    cur = get_warehouse_level(pid)
    if cur >= WAREHOUSE_MAX_LEVEL:
        return False
    cost = warehouse_upgrade_cost_at(cur + 1)
    if not cost or _missing_desc(pid, cost):
        return False
    from db.players import spend_gold
    # 先扣材料、再扣金币：任一环节失败则把已扣的材料加回，保证不出现半扣
    paid_materials: list[tuple[str, int]] = []
    for key, need in cost.items():
        if key == "gold":
            continue
        need = int(need)
        if not spend_warehouse_item(pid, key, need):
            for done_key, done_qty in paid_materials:  # 回滚已扣材料
                add_warehouse_item(pid, "resource", done_key, done_qty)
            return False
        paid_materials.append((key, need))
    if int(cost.get("gold", 0)) > 0 and not spend_gold(pid, int(cost["gold"])):
        for done_key, done_qty in paid_materials:  # 回滚已扣材料
            add_warehouse_item(pid, "resource", done_key, done_qty)
        return False
    with _conn() as c:
        # 幂等 UPSERT：同玩家恒一行（PRIMARY KEY(player_id)），重复升级不会插出第二行
        c.execute(
            "INSERT INTO warehouse_levels(player_id, level) VALUES(?,?) "
            "ON CONFLICT(player_id) DO UPDATE SET level=excluded.level",
            (pid, cur + 1),
        )
    return True


def add_warehouse_item_checked(pid: int, item_type: str, item_id: str,
                               quantity: int = 1) -> tuple[bool, str]:
    """带容量校验的入仓（**局外整笔事务入口专用**：满仓一律整笔拒绝，不允许塞爆）

    与 add_warehouse_item 的区别：本函数先查剩余容量，装不下就**完全不写**并返回
    (False, 中文原因)；装得下才落库并返回 (True, "")。用于市场资源回购、图鉴档位
    领奖这类「一次买入 / 一次领奖」的整笔事务，避免出现买到一半、领奖领一半的半截结果。

    撤离结算**不走本函数**——那边要「能存多少存多少」的部分入仓口径，见
    game/evac.commit_run_to_warehouse。数量非法时按原语义拒绝。

    落库范围：只写 warehouse_items 的行（该表 CHECK 仅允许 'resource'/'weapon'）。
    药水 / 金币各有自己的表与写入函数（add_potion / add_gold），本函数显式拒绝而非
    透传给 add_warehouse_item——否则会撞 schema CHECK 抛 sqlite3.IntegrityError。
    """
    qty = int(quantity)
    if qty <= 0:
        return False, "数量非法"
    if item_type not in ("resource", "weapon"):
        # 禁静默忽略：明确告知调用方用错函数（药水/金币不走 warehouse_items）
        return False, f"不支持的入仓类型 {item_type}（药水/金币请用各自的写入函数）"
    need = warehouse_item_capacity(item_type, item_id) * qty
    if need <= 0:
        # 0 容量物品（如 capacity_cost=0 的拳头）不占仓库容量，直接放行
        add_warehouse_item(pid, item_type, item_id, qty)
        return True, ""
    remain = warehouse_remaining_capacity(pid)
    if need > remain:
        from entities.resource_defs import RESOURCES
        name = RESOURCES.get(item_id, {}).get("name", item_id)
        return False, f"仓库容量不足：{name}×{qty} 需 {need} 格，剩余 {remain} 格"
    add_warehouse_item(pid, item_type, item_id, qty)
    return True, ""
