"""建筑定义：局内建造系统 7 种建筑（木路障/石墙/箭塔/瞭望塔/霜冻塔/缠绕陷阱/捕兽夹）。
cost 键名=资源名契约（wood/stone/ore，禁止改名）。
行为能力化：有 fire_cd = 塔类（自动索敌射击），有 trigger_range = 陷阱类（踩中即生效并耗尽），
可选 slow_mult（减速）/ stun_duration（眩晕）由 game/build_system.py 按字段存在性调度，勿在逻辑层硬编码 kind。
"""

BUILDS: dict[str, dict] = {
    "barricade": {  # 木路障：阻挡怪物移动，怪物受阻会转攻它
        "name": "木路障", "desc": "阻挡怪物移动，受阻怪物会转攻它",
        "cost": {"wood": 6, "stone": 2},
        "hp": 180, "color": (140, 96, 48), "size": 56,
        "blocks_monsters": True,
    },
    "stone_wall": {  # 石墙：纯阻挡型，比木路障更耐打（无 fire_cd / trigger_range）
        "name": "石墙", "desc": "坚固石墙，比木路障更耐打",
        "cost": {"stone": 10},
        "hp": 450, "color": (150, 150, 158), "size": 56,
        "blocks_monsters": True,
    },
    "tower": {  # 箭塔：hitscan 攻击射程内最近怪物
        "name": "箭塔", "desc": "自动射击射程内最近怪物",
        "cost": {"wood": 8, "ore": 4},
        "hp": 220, "color": (196, 164, 96), "size": 56,
        "damage": 12, "range": 240.0, "fire_cd": 0.9, "target_max": 2,
        "blocks_monsters": True,
    },
    "sniper_tower": {  # 瞭望塔：远程单体高伤，射程远攻速慢（塔类，能力同箭塔）
        "name": "瞭望塔", "desc": "远程单体高伤，射程远但攻速慢",
        "cost": {"wood": 6, "ore": 8},
        "hp": 200, "color": (178, 132, 74), "size": 56,
        "damage": 40, "range": 420.0, "fire_cd": 2.4, "target_max": 1,
        "blocks_monsters": True,
    },
    "frost_tower": {  # 霜冻塔：塔类 + slow_mult 命中附带减速（控场）
        "name": "霜冻塔", "desc": "攻击附带减速，控场专用",
        "cost": {"wood": 7, "ore": 5},
        "hp": 180, "color": (118, 168, 222), "size": 56,
        "damage": 4, "range": 200.0, "fire_cd": 1.4, "target_max": 1,
        "slow_mult": 0.5, "slow_duration": 2.0,
        "blocks_monsters": True,
    },
    "trap": {  # 缠绕陷阱：怪物踩中受伤害+减速，触发后耗尽（不阻挡移动）
        "name": "缠绕陷阱", "desc": "怪物踩中受伤并减速，触发后耗尽",
        "cost": {"wood": 4, "stone": 4},
        "hp": 60, "color": (96, 140, 72), "size": 48,
        "damage": 25, "trigger_range": 36.0,
        "slow_mult": 0.45, "slow_duration": 2.5,
        "blocks_monsters": False,
    },
    "bear_trap": {  # 捕兽夹：踩中受伤害并眩晕，触发后耗尽（无 slow_mult）
        "name": "捕兽夹", "desc": "踩中被眩晕，触发后耗尽",
        "cost": {"wood": 3, "stone": 6},
        "hp": 60, "color": (108, 102, 118), "size": 44,
        "damage": 10, "trigger_range": 36.0, "stun_duration": 2.0,
        "blocks_monsters": False,
    },
}
