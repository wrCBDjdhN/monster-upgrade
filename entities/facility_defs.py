"""设施定义：开始页市场/锻造坊的建造与升级（局外持久，各等级中文增益描述）

- 设施是**局外持久成长**（主基地建设雏形）：等级存各玩家本地 DB（db/facilities.py），
  建造消耗仓库材料+金币（阶段 1 的局内建造是防守手段，与本阶段互不串账）。
- 数值单一来源在 config.py（FACILITY_* / MARKET_* / FORGE_*），本模块只做数据与取值，
  **禁在此处硬编码任何费用或增益数值**。
- 等级口径：0 = 未建造（开始页按钮置灰🔒，点击进 views/facility_view.py 建造）；
  上限 config.FACILITY_MAX_LEVEL（3）。升级费用表的键 = **目标等级**（2/3）。
"""

from config import (  # 数值单一来源在 config
    FACILITY_MAX_LEVEL,
    MARKET_BUILD_COST, MARKET_UPGRADE_COSTS,
    MARKET_DISCOUNT, MARKET_SELL_BONUS,
    FORGE_BUILD_COST, FORGE_UPGRADE_COSTS,
    FORGE_COST_DISCOUNT, FORGE_ARTIFACT_BONUS,
)

# 设施数据表：id → {name 名称, desc 介绍, build_cost 建造费用,
#                  upgrade_costs {目标等级: 费用}, perks {等级: [增益中文文案, ...]}}
FACILITIES: dict[str, dict] = {
    "market": {
        "name": "市场",
        "desc": "买卖物品的集市。建造后开放，升级解锁折扣与资源回购。",
        "build_cost": MARKET_BUILD_COST,
        "upgrade_costs": MARKET_UPGRADE_COSTS,
        "perks": {  # 逐级增益描述（facility_view 列表渲染 + 开始页按钮 tooltip）
            1: ["开放市场（购买武器/装备/药水/宝箱、出售战利品）"],
            2: ["全场购买 9 折", "售出回收比 +5%（0.6 → 0.65）"],
            3: ["全场购买 85 折", "售出回收比 +10%（0.6 → 0.70）",
                "解锁【资源回购】：可用金币购买 木材/石材/矿石"],
        },
    },
    "forge": {
        "name": "锻造坊",
        "desc": "合成与强化装备的熔炉。升级降低费用并提升神器概率。",
        "build_cost": FORGE_BUILD_COST,
        "upgrade_costs": FORGE_UPGRADE_COSTS,
        "perks": {
            1: ["开放锻造坊（两件合成、装备升级）"],
            2: ["锻造费与装备升级费 8 折", "神器产出概率 +10%"],
            3: ["锻造费与装备升级费 7 折", "神器产出概率 +15%",
                "熔炉余温：锻造时 20% 概率返还 50% 锻造费"],
        },
    },
}


def facility_cost_at(facility_id: str, target_level: int) -> dict:
    """返回升到 target_level 的费用 dict（空 dict = 该等级不可建造/不存在）

    target_level == 1 取 build_cost；>= 2 取 upgrade_costs[target_level]。
    等级非法（<1、> FACILITY_MAX_LEVEL）或设施 id 不存在时返回 {}，
    调用方据此判定「已达满级 / 无此设施」并跳过扣费。
    """
    fdef = FACILITIES.get(facility_id)
    if fdef is None:
        return {}
    if target_level <= 1:
        return dict(fdef["build_cost"])
    if target_level > FACILITY_MAX_LEVEL:
        return {}
    return dict(fdef["upgrade_costs"].get(target_level, {}))


def _level_value(table: dict, level: int) -> float:
    """按等级查增益表，越界（<0 或 > FACILITY_MAX_LEVEL）一律回 0.0

    表内 0/1 级键值为 0.0（未建造/Lv1 无折扣），与本函数回退值同值，
    保证任何非法等级都不会拿到折扣。
    """
    if level < 0 or level > FACILITY_MAX_LEVEL:
        return 0.0
    return float(table.get(level, 0.0))


def market_discount(level: int) -> float:
    """市场购买折扣率（0=无折扣）；未建造/等级越界返回 0.0"""
    return _level_value(MARKET_DISCOUNT, level)


def market_sell_bonus(level: int) -> float:
    """市场售出回收比加成（叠加在 config.SELL_COST_RECOVERY_RATIO 之上）；越界返回 0.0"""
    return _level_value(MARKET_SELL_BONUS, level)


def forge_cost_discount(level: int) -> float:
    """锻造费与装备升级费折扣率（0=无折扣）；未建造/等级越界返回 0.0"""
    return _level_value(FORGE_COST_DISCOUNT, level)


def forge_artifact_bonus(level: int) -> float:
    """锻造神器（artifact）概率加成（叠加在基础概率之上）；越界返回 0.0"""
    return _level_value(FORGE_ARTIFACT_BONUS, level)
