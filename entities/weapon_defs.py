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
- set: 所属套装 id（阶段9；仅套装武器有，缺省表示无套装，见 config.SET_BONUSES）

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
        "set": "mummy",  # 套装：木乃伊套（头盔+护甲+诅咒弯刀，3 件）
        "debuff": "poison",  # 命中附加中毒
        "market_restricted": True,  # 市场禁购标记
    },
    # === 图鉴配方专属武器（集齐怪物图鉴后由锻造坊"配方"页制作，唯一来源）===
    "codex_monster_blade": {
        "kind": "melee",
        "item_id": "codex_monster_blade",
        "name": "屠魔者之刃",
        "damage": 72,  # 强于常规神器近战最高（和道一文字60 / 雷神之锤58）
        "attack_speed": 1.1,
        "range": 62,
        "price": 0,  # 价格为0：不可在市场购买，仅配方制作获得
        "color": (200, 60, 60),  # 血红=屠魔
        "shape": "sword",
        "capacity_cost": 3,
        "debuff": "poison",  # 命中附加中毒（怪物图鉴主题）
        "artifact": True,           # 神器标记（图鉴按 ★ 显示）
        "market_restricted": True,  # 市场禁购标记（市场列表跳过）
        "recipe_only": True,        # 配方专属：不进普通锻造神器池，仅"配方"页可制作
    },
    # === 神器武器（仅锻造获得，不可购买）===
    "wado_ichimonji": {
        "kind": "melee",
        "item_id": "wado_ichimonji",
        "name": "和道一文字",
        "damage": 60,  # 平衡调整：原100，降低避免秒杀（常规最高狙击枪35）
        "attack_speed": 1.0,
        "range": 150,
        "price": 0,  # 价格为0：不可在市场购买，仅锻造获得
        "color": (255, 255, 255),
        "shape": "sword",
        "capacity_cost": 3,
        "artifact": True,  # 神器标记
    },
    "storm_hammer": {
        "kind": "melee",
        "item_id": "storm_hammer",
        "name": "雷神之锤",
        "damage": 58,  # 平衡调整：原95
        "attack_speed": 0.6,  # 慢速重击流
        "range": 110,
        "price": 0,
        "color": (180, 200, 255),  # 蓝白色=雷电
        "shape": "mace",
        "capacity_cost": 3,
        "debuff": "stun",  # 命中眩晕目标（控场）
        "artifact": True,
    },
    "frost_blade": {
        "kind": "melee",
        "item_id": "frost_blade",
        "name": "霜之哀伤",
        "damage": 42,  # 平衡调整：原70
        "attack_speed": 1.4,  # 中速风筝流
        "range": 130,
        "price": 0,
        "color": (100, 180, 255),  # 冰蓝色=冰冻
        "shape": "sword",
        "capacity_cost": 3,
        "debuff": "freeze",  # 命中冰冻目标（减速50%）
        "artifact": True,
    },
    "flame_blade": {
        "kind": "melee",
        "item_id": "flame_blade",
        "name": "赤焰魔剑",
        "damage": 45,  # 平衡调整：原75
        "attack_speed": 1.6,  # 高攻速输出流
        "range": 120,
        "price": 0,
        "color": (255, 100, 40),  # 红橙色=火焰
        "shape": "sword",
        "capacity_cost": 3,
        "debuff": "burn",  # 命中点燃目标（DOT伤害）
        "artifact": True,
    },
    "vampiric_blade": {
        "kind": "melee",
        "item_id": "vampiric_blade",
        "name": "吸血剑",
        "damage": 52,  # 平衡调整：原85
        "attack_speed": 1.2,
        "range": 120,
        "price": 0,
        "color": (200, 30, 60),  # 血红色
        "shape": "sword",
        "capacity_cost": 3,
        "lifesteal": 0.10,  # 平衡调整：原0.15，削弱自愈避免几乎不死
        "artifact": True,
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
        "attack_speed": 6.0,  # 全自动武器，高射速（冷却 ≈ 0.17s）
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
        "set": "space",  # 套装：航天套（头盔+护甲+航天枪械，4 件）
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
        "set": "space",  # 套装：航天套（头盔+护甲+航天枪械，4 件）
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
        "damage": 48,  # 平衡调整：原80（激光持续伤害，降幅略大）
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
    "annihilation_cannon": {
        "kind": "ranged",
        "item_id": "annihilation_cannon",
        "name": "湮灭炮",
        "damage": 55,  # 平衡调整：原90
        "attack_speed": 0.5,  # 慢速重型攻坚
        "projectile_speed": PROJECTILE_SPEED + 150,
        "range": 320,
        "price": 0,
        "color": (160, 40, 40),  # 暗红色=毁灭
        "shape": "gun",
        "capacity_cost": 4,
        "special": "explosive",  # 爆炸范围伤害（AOE清群）
        "artifact": True,
    },
    "piercing_bow": {
        "kind": "ranged",
        "item_id": "piercing_bow",
        "name": "贯穿之弓",
        "damage": 30,  # 平衡调整：原45（攻速2.5，DPS仍居前）
        "attack_speed": 2.5,  # 极速连射
        "projectile_speed": PROJECTILE_SPEED + 150,
        "range": 400,
        "price": 0,
        "color": (0, 220, 180),  # 青绿色
        "shape": "bow",
        "capacity_cost": 2,
        "special": "penetrating",  # 穿透弹丸（清线）
        "artifact": True,
    },
    "plague_staff": {
        "kind": "ranged",
        "item_id": "plague_staff",
        "name": "瘟疫法杖",
        "damage": 26,  # 平衡调整：原40
        "attack_speed": 1.5,
        "projectile_speed": PROJECTILE_SPEED + 80,
        "range": 350,
        "price": 0,
        "color": (90, 160, 60),  # 墨绿色=瘟疫
        "shape": "staff",
        "capacity_cost": 3,
        "random_debuff": True,  # 每颗子弹随机附带一种 debuff（全异常流）
        "artifact": True,
    },
    "tri_shot_cannon": {
        "kind": "ranged",
        "item_id": "tri_shot_cannon",
        "name": "三连散射炮",
        "damage": 20,  # 平衡调整：原30（一次3发，合计60）
        "attack_speed": 1.5,
        "projectile_speed": PROJECTILE_SPEED + 100,
        "range": 300,
        "price": 0,
        "color": (255, 170, 60),  # 橙黄色
        "shape": "gun",
        "capacity_cost": 3,
        "spread_count": 3,  # 一次发射3发弹丸
        "spread_angle": 8.0,  # 相邻弹丸夹角（度）
        "artifact": True,
    },
    "frost_aura_staff": {
        "kind": "ranged",
        "item_id": "frost_aura_staff",
        "name": "冰霜领域",
        "damage": 32,  # 平衡调整：原50
        "attack_speed": 0.9,
        "projectile_speed": PROJECTILE_SPEED + 50,
        "range": 280,
        "price": 0,
        "color": (140, 220, 255),  # 淡蓝色=冰霜
        "shape": "staff",
        "capacity_cost": 3,
        "aura_slow": True,  # 攻速光环：持续减速周围怪物
        "aura_radius": 220,  # 光环生效半径
        "artifact": True,
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
