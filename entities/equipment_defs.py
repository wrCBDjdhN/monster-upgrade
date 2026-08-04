"""装备与药品定义：头盔、护甲、背包、药水"""

# ── 头盔 ──
HELMETS = {
    "leather_helm": {
        "name": "皮盔",
        "defense": 2,
        "price": 30,
        "color": (139, 90, 43),
        "description": "减少2点伤害",
        "capacity_cost": 1,  # 占1格背包容量
    },
    "iron_helm": {
        "name": "铁盔",
        "defense": 5,
        "price": 80,
        "color": (160, 160, 170),
        "description": "减少5点伤害",
        "capacity_cost": 1,
    },
    "golden_helm": {
        "name": "金盔",
        "defense": 8,
        "price": 200,
        "color": (255, 200, 50),
        "description": "减少8点伤害",
        "capacity_cost": 1,
    },
    # === 木乃伊头盔（沙漠掉落，不可在市场购买）===
    "mummy_helmet": {
        "name": "木乃伊头盔",
        "defense": 10,  # 略高于金盔(8)
        "price": 180,  # 保留价格供售卖，市场通过 market_restricted 跳过
        "color": (210, 190, 120),
        "description": "减少10点伤害",
        "capacity_cost": 1,
        "market_restricted": True,  # 不可在市场购买
    },
    # === 航天头盔（航天基地掉落，不可在市场购买）===
    "space_helmet": {
        "name": "航天头盔",
        "defense": 12,  # 略高于板甲(12)，低于强相互作用力头盔(60)
        "price": 250,  # 保留价格供售卖，市场通过 market_restricted 跳过
        "color": (80, 100, 140),
        "description": "减少12点伤害",
        "capacity_cost": 1,
        "market_restricted": True,  # 不可在市场购买
    },
    # === 神器头盔（仅锻造获得，不可购买）===
    "strong_force_helm": {
        "name": "强相互作用力头盔",
        "defense": 60,
        "price": 0,  # 价格为0：不可在市场购买，仅锻造获得
        "color": (200, 200, 255),
        "description": "由强相互作用力材料制成，防御极高",
        "capacity_cost": 3,
        "artifact": True,  # 神器标记
    },
}

# ── 护甲 ──
ARMORS = {
    "leather_armor": {
        "name": "皮甲",
        "defense": 3,
        "price": 40,
        "color": (120, 80, 40),
        "description": "减少3点伤害",
        "capacity_cost": 2,  # 占2格背包容量
    },
    "chain_mail": {
        "name": "锁子甲",
        "defense": 7,
        "price": 120,
        "color": (140, 140, 150),
        "description": "减少7点伤害",
        "capacity_cost": 2,
    },
    "plate_armor": {
        "name": "板甲",
        "defense": 12,
        "price": 300,
        "color": (100, 100, 120),
        "description": "减少12点伤害",
        "capacity_cost": 2,
    },
    # === 木乃伊护甲（沙漠掉落，不可在市场购买）===
    "mummy_armor": {
        "name": "木乃伊护甲",
        "defense": 15,  # 略高于板甲(12)
        "price": 300,  # 保留价格供售卖，市场通过 market_restricted 跳过
        "color": (200, 180, 110),
        "description": "减少15点伤害",
        "capacity_cost": 2,
        "market_restricted": True,  # 不可在市场购买
    },
    # === 航天护甲（航天基地掉落，不可在市场购买）===
    "space_armor": {
        "name": "航天护甲",
        "defense": 18,  # 高于木乃伊护甲(15)，低于强相互作用力护甲(100)
        "price": 400,  # 保留价格供售卖，市场通过 market_restricted 跳过
        "color": (60, 80, 120),
        "description": "减少18点伤害",
        "capacity_cost": 2,
        "market_restricted": True,  # 不可在市场购买
    },
    # === 神器护甲（仅锻造获得，不可购买）===
    "strong_force_armor": {
        "name": "强相互作用力护甲",
        "defense": 100,
        "price": 0,  # 价格为0：不可在市场购买，仅锻造获得
        "color": (200, 200, 255),
        "description": "由强相互作用力材料制成，防御力极高",
        "capacity_cost": 3,
        "artifact": True,  # 神器标记
    },
}

# ── 背包 ──
BACKPACKS = {
    "small_bag": {
        "name": "小布袋",
        "capacity": 5,
        "price": 20,
        "color": (100, 70, 40),
        "description": "可携带5个资源",
        "capacity_cost": 1,  # 占1格背包容量
    },
    "medium_bag": {
        "name": "中背包",
        "capacity": 12,
        "price": 60,
        "color": (80, 60, 35),
        "description": "可携带12个资源",
        "capacity_cost": 2,  # 占2格背包容量
    },
    "large_bag": {
        "name": "大背包",
        "capacity": 25,
        "price": 150,
        "color": (60, 50, 30),
        "description": "携带25个资源",
        "capacity_cost": 3,  # 占3格背包容量
    },
    "huge_bag": {
        "name": "巨背包",
        "capacity": 50,
        "price": 350,
        "color": (40, 35, 25),
        "description": "携带50个资源",
        "capacity_cost": 5,  # 占5格背包容量
    },
    # === 神器背包（仅锻造获得，不可购买）===
    "sky_swallowing_bag": {
        "name": "吞天包",
        "capacity": 500,
        "price": 0,  # 价格为0：不可在市场购买，仅锻造获得
        "color": (150, 200, 255),
        "description": "传说中能吞下天空的宝袋，容量高达500",
        "capacity_cost": 1,
        "artifact": True,  # 神器标记
    },
}

# ── 药水 ──
POTIONS = {
    "heal_potion_s": {
        "name": "小回复药水",
        "effect": "heal",
        "value": 30,
        "price": 15,
        "color": (220, 50, 50),
        "description": "回复30点生命",
        "stackable": True,
    },
    "heal_potion_l": {
        "name": "大回复药水",
        "effect": "heal",
        "value": 70,
        "price": 40,
        "color": (255, 30, 30),
        "description": "回复70点生命",
        "stackable": True,
    },
    "speed_potion": {
        "name": "疾跑药水",
        "effect": "speed",
        "value": 1.5,  # 移速倍率
        "duration": 60.0,  # 持续秒数
        "price": 25,
        "color": (50, 200, 255),
        "description": "移速提升50%，持续60秒",
        "stackable": True,
    },
    # === 仙人掌果实（沙漠仙人掌掉落，不可购买）===
    "fruit_potion": {
        "name": "仙人掌果实",
        "effect": "fruit",  # 组合效果：回复 + 加速
        "value": 25,  # 回复量
        "duration": 30.0,  # 加速持续秒数
        "price": 0,  # 价格为0：不可在市场购买
        "color": (200, 255, 100),
        "description": "回复25点生命并提升移速30%持续30秒",
        "stackable": True,
    },
}

# ── 怪物可穿戴护甲（掉落用）──
MONSTER_ARMOR_DROP = [
    ("leather_armor", 0.15),
    ("chain_mail", 0.05),
    ("plate_armor", 0.02),
]

# ── 怪物可穿戴头盔（掉落用）──
MONSTER_HELMET_DROP = [
    ("leather_helm", 0.12),
    ("iron_helm", 0.04),
    ("golden_helm", 0.01),
]


def get_item_def(item_type: str, item_id: str) -> dict | None:
    """根据类型和ID获取物品定义"""
    tables = {
        "helmet": HELMETS,
        "armor": ARMORS,
        "backpack": BACKPACKS,
        "potion": POTIONS,
    }
    return tables.get(item_type, {}).get(item_id)
