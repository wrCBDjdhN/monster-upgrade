"""武器基础模板定义

定义游戏中所有武器的属性模板，分为两大类：
1. 近战武器 (MELEE_WEAPONS): 拳头、木剑、铁剑、石锤
2. 远程武器 (RANGED_WEAPONS): 短弓、长弓、火杖、手枪、步枪、狙击枪、激光枪、火箭筒

每种武器包含：
- kind: 武器类型 (melee/ranged)
- item_id: 唯一标识符
- name: 中文显示名
- damage: 基础伤害值
- attack_speed: 攻击速度倍率（越高越快）
- range: 攻击距离（近战=挥砍半径，远程=射程）
- price: 购买价格（金币）
- color: 渲染颜色 RGB
- shape: 武器形状标识（用于绘制）
- special: 特殊属性（penetrating=穿透, explosive=爆炸）

相关配置从 config.py 导入：
- PROJECTILE_SPEED: 远程弹丸基础速度
"""

from config import PROJECTILE_SPEED

# 近战武器：damage, attack_speed, range(挥砍半径)
MELEE_WEAPONS = {
    "fist": {
        "kind": "melee",
        "item_id": "fist",
        "name": "拳头",
        "damage": 8,
        "attack_speed": 1.2,
        "range": 40,
        "price": 0,
        "color": None,
        "shape": "none",
        "capacity_cost": 0,  # 拳头不占背包容量
    },
    "wood_sword": {
        "kind": "melee",
        "item_id": "wood_sword",
        "name": "木剑",
        "damage": 10,
        "attack_speed": 1.2,
        "range": 50,
        "price": 80,
        "color": (165, 110, 60),
        "shape": "sword",
        "capacity_cost": 2,  # 占2格背包容量
    },
    "iron_sword": {
        "kind": "melee",
        "item_id": "iron_sword",
        "name": "铁剑",
        "damage": 16,
        "attack_speed": 1.0,
        "range": 55,
        "price": 150,
        "color": (205, 205, 215),
        "shape": "sword",
        "capacity_cost": 2,
    },
    "stone_mace": {
        "kind": "melee",
        "item_id": "stone_mace",
        "name": "石锤",
        "damage": 22,
        "attack_speed": 0.7,
        "range": 45,
        "price": 220,
        "color": (150, 150, 158),
        "shape": "mace",
        "capacity_cost": 2,
    },
    # === 木乃伊系近战武器（不可在市场购买，仅开箱/锻造/怪物掉落）===
    "cursed_scimitar": {
        "kind": "melee",
        "item_id": "cursed_scimitar",
        "name": "诅咒弯刀",
        "damage": 25,  # 高于石锤22，木乃伊系特色近战
        "attack_speed": 1.0,
        "range": 55,
        "price": 400,  # 保留价格供售卖（市场不可购买，由 MARKET_RESTRICTED 控制）
        "color": (140, 200, 90),  # 绿色=诅咒毒系
        "shape": "sword",
        "capacity_cost": 2,
        "debuff": "poison",  # 命中附加中毒
        "market_restricted": True,  # 市场禁购标记
    },
    # === 神器武器（仅锻造获得，不可购买）===
    "wado_ichimonji": {
        "kind": "melee",
        "item_id": "wado_ichimonji",
        "name": "和道一文字",
        "damage": 100,
        "attack_speed": 1.0,
        "range": 150,
        "price": 0,  # 价格为0：不可在市场购买，仅锻造获得
        "color": (255, 255, 255),
        "shape": "sword",
        "capacity_cost": 3,
        "artifact": True,  # 神器标记
    },
}

# 远程武器：damage, attack_speed, projectile_speed, range(射程)
RANGED_WEAPONS = {
    "short_bow": {
        "kind": "ranged",
        "item_id": "short_bow",
        "name": "短弓",
        "damage": 7,
        "attack_speed": 0.8,
        "projectile_speed": PROJECTILE_SPEED,
        "range": 250,
        "price": 120,
        "color": (155, 105, 55),
        "shape": "bow",
        "capacity_cost": 2,
    },
    "long_bow": {
        "kind": "ranged",
        "item_id": "long_bow",
        "name": "长弓",
        "damage": 12,
        "attack_speed": 0.6,
        "projectile_speed": PROJECTILE_SPEED + 100,
        "range": 350,
        "price": 200,
        "color": (120, 80, 40),
        "shape": "bow",
        "capacity_cost": 2,
    },
    "fire_staff": {
        "kind": "ranged",
        "item_id": "fire_staff",
        "name": "火杖",
        "damage": 18,
        "attack_speed": 0.5,
        "projectile_speed": PROJECTILE_SPEED - 50,
        "range": 280,
        "price": 280,
        "color": (225, 90, 45),
        "shape": "staff",
        "capacity_cost": 2,
    },
    # === 新增远程武器 ===
    "pistol": {
        "kind": "ranged",
        "item_id": "pistol",
        "name": "手枪",
        "damage": 10,
        "attack_speed": 1.0,
        "projectile_speed": PROJECTILE_SPEED + 50,
        "range": 220,
        "price": 100,
        "color": (180, 180, 185),
        "shape": "gun",
        "capacity_cost": 2,
        "market_restricted": True,  # 市场禁购标记（枪械类：仅宝箱/锻造/怪物掉落）
    },
    "rifle": {
        "kind": "ranged",
        "item_id": "rifle",
        "name": "步枪",
        "damage": 8,
        "attack_speed": 1.5,
        "projectile_speed": PROJECTILE_SPEED + 100,
        "range": 280,
        "price": 180,
        "color": (140, 120, 90),
        "shape": "gun",
        "capacity_cost": 2,
        "auto_fire": True,  # 全自动武器，按住左键可连发
        "market_restricted": True,  # 市场禁购标记（枪械类：仅宝箱/锻造/怪物掉落）
    },
    "sniper": {
        "kind": "ranged",
        "item_id": "sniper",
        "name": "狙击枪",
        "damage": 35,
        "attack_speed": 0.3,
        "projectile_speed": PROJECTILE_SPEED + 200,
        "range": 450,
        "price": 400,
        "color": (60, 60, 70),
        "shape": "gun",
        "capacity_cost": 3,
        "market_restricted": True,  # 市场禁购标记（枪械类：仅宝箱/锻造/怪物掉落）
    },
    "laser_gun": {
        "kind": "ranged",
        "item_id": "laser_gun",
        "name": "激光枪",
        "damage": 12,
        "attack_speed": 0.8,
        "projectile_speed": PROJECTILE_SPEED + 300,
        "range": 300,
        "price": 350,
        "color": (0, 200, 255),
        "shape": "gun",
        "capacity_cost": 3,
        "special": "penetrating",  # 穿透敌人
        "market_restricted": True,  # 市场禁购标记（枪械类：仅宝箱/锻造/怪物掉落）
    },
    "rocket_launcher": {
        "kind": "ranged",
        "item_id": "rocket_launcher",
        "name": "火箭筒",
        "damage": 25,
        "attack_speed": 0.4,
        "projectile_speed": PROJECTILE_SPEED - 100,
        "range": 250,
        "price": 500,
        "color": (200, 50, 50),
        "shape": "gun",
        "capacity_cost": 4,
        "special": "explosive",  # 爆炸范围伤害
        "market_restricted": True,  # 市场禁购标记（枪械类：仅宝箱/锻造/怪物掉落）
    },
    # === 木乃伊系远程武器（不可在市场购买，仅开箱/锻造/怪物掉落）===
    "scepter": {
        "kind": "ranged",
        "item_id": "scepter",
        "name": "权杖",
        "damage": 18,
        "attack_speed": 0.8,
        "projectile_speed": PROJECTILE_SPEED + 50,
        "range": 300,
        "price": 450,  # 保留价格供售卖（市场不可购买，由 MARKET_RESTRICTED 控制）
        "color": (240, 200, 80),  # 金色=法杖系
        "shape": "staff",
        "capacity_cost": 2,
        "random_debuff": True,  # 每颗子弹随机附带一种 debuff（中毒/燃烧/冰冻/减速/眩晕）
        "market_restricted": True,  # 市场禁购标记
    },
    # === 神器武器（仅锻造获得，不可购买）===
    "meteor_cannon": {
        "kind": "ranged",
        "item_id": "meteor_cannon",
        "name": "陨星炮",
        "damage": 80,
        "attack_speed": 1.0,
        "projectile_speed": 0,  # 激光武器不走弹丸
        "range": 600,
        "price": 0,  # 价格为0：不可在市场购买，仅锻造获得
        "color": (255, 100, 255),
        "shape": "gun",
        "capacity_cost": 3,
        "special": "laser",  # 持续激光，实时跟随鼠标方向，可穿透墙壁
        "artifact": True,  # 神器标记
    },
}

# 默认起始武器
DEFAULT_WEAPON = "fist"

# 合并近战/远程，便于按 item_id 查表
ALL_WEAPONS = {**MELEE_WEAPONS, **RANGED_WEAPONS}


def get_weapon_visual(item_id: str) -> tuple:
    """返回 (color, shape, kind)，用于绘制玩家/怪物手持武器。
    color 可能为 None（如拳头，不绘制）。"""
    w = ALL_WEAPONS.get(item_id)
    if not w:
        return (None, "none", "melee")
    return (w.get("color"), w.get("shape", "none"), w.get("kind", "melee"))
