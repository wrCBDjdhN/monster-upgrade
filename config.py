"""游戏全局配置常量，所有数值集中管理"""

# ── 窗口 ──
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
WINDOW_TITLE = "打怪升级"
FULLSCREEN = False  # 按F11切换

# ── 地图 ──
MAP_WIDTH = 3200
MAP_HEIGHT = 3200
TILE_SIZE = 64
ROOM_MIN = 4
ROOM_MAX = 8
CORRIDOR_WIDTH = 2

# ── 玩家 ──
PLAYER_SPEED = 4             # 像素/帧（PhysicsEngineSimple 不乘 delta_time）
PLAYER_HP = 100
PLAYER_SIZE = 16             # 半径（总大小 32x32）
PLAYER_COLOR = (80, 180, 255)

# ── 战斗 ──
ATTACK_COOLDOWN = 0.4          # 秒
MELEE_RANGE = 50               # 近战命中半径
MELEE_ARC_DEGREES = 120        # 近战扇形角度
PROJECTILE_SPEED = 400
PROJECTILE_SIZE = 6
PROJECTILE_LIFETIME = 1.5      # 秒

# ── 怪物 ──
ZOMBIE_HP = 30
ZOMBIE_DAMAGE = 5
ZOMBIE_SPEED = 60
ZOMBIE_ATTACK_DELAY = 1.0      # 秒
ZOMBIE_SIZE = 22
ZOMBIE_COLOR = (80, 160, 60)
ZOMBIE_AGGRO_RANGE = 250       # 索敌距离

SKELETON_HP = 20
SKELETON_DAMAGE = 4
SKELETON_SPEED = 40
SKELETON_ATTACK_DELAY = 1.8
SKELETON_SIZE = 20
SKELETON_COLOR = (220, 220, 200)
SKELETON_AGGRO_RANGE = 300     # 索敌距离（远程更远）

# ── 掉落 ──
DROP_PICKUP_RADIUS = 40
DROP_LIFETIME = 120.0          # 秒后消失（原30秒太短，玩家来不及捡）

# ── 撤离 ──
EVAC_CHANNEL_TIME = 3.0        # 秒
EVAC_RADIUS = 48
EVAC_COLOR = (0, 255, 100)

# ── 升级公式 ──
UPGRADE_BASE_COST = 50         # 基础升级费用
# 属性缩放采用「平方根亚线性」曲线（用户反馈：原连乘方案百级属性爆炸）
# 倍率(level) = 1 + UPGRADE_MULT_GROWTH × √(level-1)
#   - 低等级成长明显（Lv5≈1.9倍、Lv10≈2.4倍）
#   - 高等级逐渐收敛（Lv50≈4.2倍、Lv100≈5.5倍，不再指数爆炸）
UPGRADE_MULT_GROWTH = 0.45     # 亚线性增长系数（调大=高等级更强，调小=更弱）
FORGE_BASE_COST = 200          # 锻造基础费用
FORGE_PER_LEVEL_COST = 20      # 锻造每级递增费用
SELL_COST_RECOVERY_RATIO = 0.6 # 售卖价 = 累计升级成本 × 该比例


def upgrade_mult_product(level):
    """返回从 Lv1 升到指定等级的累计属性倍率（创建高等级物品时属性缩放）"""
    if level <= 1:
        return 1.0
    # 平方根亚线性：低等级增速快，高等级收敛，百级约 5.5 倍（原连乘可达数百倍）
    return 1.0 + UPGRADE_MULT_GROWTH * (level - 1) ** 0.5


def upgrade_mult_for_level(level):
    """返回升到指定等级时该次升级使用的属性倍率（= 相邻累计倍率之比，保证逐步升级与直接创建数值一致）"""
    prev = upgrade_mult_product(level - 1)
    cur = upgrade_mult_product(level)
    return cur / prev if prev else 1.0

# ── 掉落表 ──
# 格式: (类型, id, 概率, 数量min, 数量max)
# 说明：不再随机掉落武器/装备（用户需求：怪物只掉落自己身上的装备，装备掉落走怪物穿戴分配）
ZOMBIE_LOOT_TABLE = [
    ("resource", "wood",    0.40, 1, 3),
    ("resource", "stone",   0.25, 1, 2),
    ("resource", "ore",     0.10, 1, 1),
    ("gold",     None,      0.15, 5, 15),
]

SKELETON_LOOT_TABLE = [
    ("resource", "wood",    0.25, 1, 2),
    ("resource", "stone",   0.30, 1, 3),
    ("resource", "ore",     0.15, 1, 1),
    ("gold",     None,      0.18, 5, 15),
]

# ── 怪物掉落武器/装备概率 ──
MONSTER_WEAPON_DROP_CHANCE = 0.15   # 15% 掉落武器
MONSTER_HELMET_DROP_CHANCE = 0.08   # 8% 掉落头盔
MONSTER_ARMOR_DROP_CHANCE = 0.08    # 8% 掉落护甲

# ── 怪物持有装备等级范围 ──
# 普通怪物使用 Lv1~10 的武器/装备；Boss 使用 Lv20~30 的武器/装备（用户需求：等级体系）
MONSTER_WEAPON_LEVEL_RANGE = (1, 10)    # 普通怪物持有武器等级范围
BOSS_WEAPON_LEVEL_RANGE = (20, 30)      # Boss 持有武器等级范围
MONSTER_GEAR_LEVEL_RANGE = (1, 10)      # 普通怪物持有头盔/护甲等级范围
BOSS_GEAR_LEVEL_RANGE = (20, 30)        # Boss 持有头盔/护甲等级范围

# ── 沙漠怪穿戴木乃伊系装备概率 ──
# 木乃伊系装备不再进入随机掉落表（用户需求：只掉自身装备），
# 改为沙漠怪（木乃伊/骆驼/BOSS木乃伊）穿戴后掉落，保持木乃伊装备产出
MONSTER_MUMMY_ARMOR_CHANCE = 0.35   # 沙漠怪穿戴木乃伊护甲概率
MONSTER_MUMMY_HELMET_CHANCE = 0.35  # 沙漠怪穿戴木乃伊头盔概率

# ── 宝箱/水井开出武器装备的等级分布（用户需求）──
# 每项为 (概率, 最低等级, 最高等级)，先按概率选档再在区间内取整：
#   Lv1~10 概率 80%、Lv10~20 概率 10%、Lv20~50 概率 9%、Lv50~100 概率 1%
# 说明：边界等级（10/20/50）会同时落在相邻两档，仅使该单点等级概率略高，属设计取舍
CHEST_LEVEL_RANGES = [
    (0.80, 1, 10),
    (0.10, 10, 20),
    (0.09, 20, 50),
    (0.01, 50, 100),
]

# ── 开箱系统 ──
# 宝箱掉落概率（原 chest.py 硬编码值，集中到配置便于调平衡）
CHEST_EQUIPMENT_CHANCE = 0.5    # 50% 概率掉落装备（头盔/护甲）
CHEST_BACKPACK_CHANCE = 0.25    # 25% 概率掉落背包
CHEST_GOLD_CHANCE = 0.3         # 30% 概率掉落金币（5~15）
# 武器箱：所有武器池（根据等级区间决定开出的武器等级）
ALL_WEAPON_IDS = [
    "wood_sword", "iron_sword", "stone_mace",
    "short_bow", "long_bow", "fire_staff",
    "pistol", "rifle", "sniper", "laser_gun", "rocket_launcher",
    # 沙漠新武器：市场禁购，但保留在随机池中（宝箱/锻造可开出）
    "cursed_scimitar", "scepter",
]

WEAPON_BOXES = {
    "weapon_box_basic": {
        "name": "武器箱(初级)",
        "tier": 1,
        "price": 150,
        "color": (200, 150, 50),
        "description": "随机武器 Lv.1",
        "level_range": (1, 1),
    },
    "weapon_box_advanced": {
        "name": "武器箱(高级)",
        "tier": 2,
        "price": 500,
        "color": (180, 100, 200),
        "description": "随机武器 Lv.2~5",
        "level_range": (2, 5),
    },
    "weapon_box_supreme": {
        "name": "武器箱(特级)",
        "tier": 3,
        "price": 1000,
        "color": (255, 80, 30),
        "description": "随机武器 Lv.5~10",
        "level_range": (5, 10),
    },
}

# 装备箱：所有装备池（头盔+护甲）
ALL_EQUIP_POOL = [
    ("helmet", "leather_helm"), ("helmet", "iron_helm"), ("helmet", "golden_helm"),
    ("armor", "leather_armor"), ("armor", "chain_mail"), ("armor", "plate_armor"),
    # 沙漠新装备：市场禁购，但保留在随机池中（宝箱/锻造可开出）
    ("helmet", "mummy_helmet"), ("armor", "mummy_armor"),
    # 航天新装备：市场禁购，但保留在随机池中（宝箱/锻造可开出）
    ("helmet", "space_helmet"), ("armor", "space_armor"),
]

EQUIPMENT_BOXES = {
    "equip_box_basic": {
        "name": "装备箱(初级)",
        "tier": 1,
        "price": 150,
        "color": (150, 180, 200),
        "description": "随机装备 Lv.1",
        "level_range": (1, 1),
    },
    "equip_box_advanced": {
        "name": "装备箱(高级)",
        "tier": 2,
        "price": 500,
        "color": (100, 150, 200),
        "description": "随机装备 Lv.2~5",
        "level_range": (2, 5),
    },
    "equip_box_supreme": {
        "name": "装备箱(特级)",
        "tier": 3,
        "price": 1000,
        "color": (255, 200, 50),
        "description": "随机装备 Lv.5~10",
        "level_range": (5, 10),
    },
}

# ── 沙漠荒地主题配色 ──
DESERT_THEME = {
    "bg": (25, 20, 12),          # 背景
    "room_floor": (60, 50, 30),  # 房间地板
    "wall": (90, 70, 40),        # 墙壁
}

# ── 木乃伊（近战型） ──
MUMMY_HP = 45
MUMMY_DAMAGE = 8
MUMMY_SPEED = 50
MUMMY_ATTACK_DELAY = 1.2        # 秒
MUMMY_SIZE = 24
MUMMY_COLOR = (210, 190, 120)
MUMMY_AGGRO_RANGE = 260         # 索敌距离
MUMMY_DEBUFF_ID = "poison"      # 近战攻击附加的中毒效果

# ── 木乃伊（远程型） ──
MUMMY_RANGED_HP = 35
MUMMY_RANGED_DAMAGE = 6
MUMMY_RANGED_SPEED = 45
MUMMY_RANGED_ATTACK_DELAY = 1.8
MUMMY_RANGED_SIZE = 24
MUMMY_RANGED_COLOR = (225, 205, 140)
MUMMY_RANGED_AGGRO_RANGE = 320  # 索敌距离（远程更远）
MUMMY_RANGED_PROJECTILE_SPEED = 350
MUMMY_RANGED_PROJECTILE_SIZE = 6
MUMMY_RANGED_DEBUFF_ID = "poison"  # 弹丸附加的中毒效果

# ── 骆驼 ──
CAMEL_HP = 80
CAMEL_DAMAGE = 5
CAMEL_SPEED = 70
CAMEL_ATTACK_DELAY = 1.5
CAMEL_SIZE = 28
CAMEL_COLOR = (180, 120, 60)
CAMEL_AGGRO_RANGE = 280
CAMEL_PROJECTILE_SPEED = 300    # 吐口水弹速（略慢，可躲）
CAMEL_PROJECTILE_SIZE = 8

# ── BOSS 倍率（僵尸/骷髅/木乃伊 BOSS 共用） ──
BOSS_HP_MULT = 8                # HP 为普通版 8 倍
BOSS_DAMAGE_MULT = 4            # 伤害为普通版 4 倍
BOSS_SPEED_MULT = 0.7           # 移速为普通版 70%（行动缓慢）
BOSS_REQUIRED_LEVEL = 5         # 需要武器/头盔/护甲 Lv5+ 才有效
BOSS_ZOMBIE_DEBUFF_ID = "burn"      # BOSS 僵尸攻击附加燃烧
BOSS_SKELETON_DEBUFF_ID = "freeze"  # BOSS 骷髅弹丸附加冰冻
BOSS_MUMMY_DEBUFF_ID = "poison"     # 木乃伊 BOSS 攻击附加中毒

# ── 仙人掌资源 ──
CACTUS_HP = 50
CACTUS_THORN_DAMAGE = 10        # 攻击者自身受到的反弹伤害（受防御减免）

# ── 果实（仙人掌掉落的药水效果） ──
FRUIT_HEAL = 25
FRUIT_SPEED_MULT = 1.3          # 移速倍率
FRUIT_SPEED_DURATION = 30.0     # 秒

# ── 水井 ──
WELL_HEAL = 30                  # 开箱后再次使用的回复量
WELL_SPEED_MULT = 1.5           # 移速倍率
WELL_SPEED_DURATION = 10.0      # 秒
WELL_GUARD_COUNT = 3            # 水井周围固定刷新的木乃伊守卫数量

# ── 沙漠掉落表 ──
# 格式同森林表: (类型, id, 概率, 数量min, 数量max)
# 说明：木乃伊系装备不再随机掉落（用户需求：只掉自身装备），
# 改由怪物穿戴分配（monster_utils 的沙漠变体概率）产出
MUMMY_LOOT_TABLE = [
    ("resource", "wood",         0.30, 1, 2),
    ("resource", "stone",        0.25, 1, 2),
    ("resource", "ore",          0.15, 1, 1),
    ("gold",     None,           0.20, 8, 20),
]

CAMEL_LOOT_TABLE = [
    ("resource", "wood",   0.20, 1, 2),
    ("resource", "stone",  0.20, 1, 2),
    ("resource", "ore",    0.25, 1, 2),
    ("gold",     None,     0.35, 10, 25),
]

# BOSS 掉落：资源+金币；武器/装备不再随机掉落（用户需求：只掉自身装备，由穿戴分配产出）
BOSS_LOOT_TABLE = [
    ("resource",  "ore",             0.50, 2, 4),
    ("gold",      None,              1.00, 30, 60),
]

# 航天基地BOSS掉落：更丰厚（资源+金币必掉，额外矿石）
SPACE_BOSS_LOOT_TABLE = [
    ("resource",  "ore",             1.00, 5, 10),  # 必掉矿石5-10
    ("gold",      None,              1.00, 80, 150),  # 必掉金币80-150
    ("resource",  "stone",           0.50, 3, 6),  # 50%掉石头3-6
]

# ── 市场购买限制 ──
# 以下武器/装备无法在市场购买（仍可通过开箱/锻造/怪物掉落获得）
MARKET_RESTRICTED_WEAPON_IDS = [
    "pistol", "rifle", "sniper", "laser_gun", "rocket_launcher",
    "scepter", "cursed_scimitar",
]
MARKET_RESTRICTED_EQUIPMENT_IDS = [
    "mummy_helmet", "mummy_armor",
    "space_helmet", "space_armor",
]

# ── 航天基地主题 ──
SPACE_THEME = {
    "bg": (8, 12, 20),             # 深空背景
    "room_floor": (25, 30, 40),    # 房间地板（金属质感）
    "wall": (50, 55, 65),          # 墙壁
}

# ── 狙击兵（远程型，高伤低血） ──
SNIPER_HP = 20
SNIPER_DAMAGE = 15
SNIPER_SPEED = 35
SNIPER_ATTACK_DELAY = 2.5         # 秒（慢速开镜）
SNIPER_SIZE = 20
SNIPER_COLOR = (100, 120, 160)
SNIPER_AGGRO_RANGE = 400          # 索敌距离（超远）
SNIPER_PROJECTILE_SPEED = 600
SNIPER_PROJECTILE_SIZE = 4

# ── 突击兵（近战型，高血高甲） ──
ASSAULT_HP = 60
ASSAULT_DAMAGE = 10
ASSAULT_SPEED = 55
ASSAULT_ATTACK_DELAY = 1.0
ASSAULT_SIZE = 24
ASSAULT_COLOR = (70, 80, 100)
ASSAULT_AGGRO_RANGE = 280

# ── 土匪（远程型，低血低甲，成群刷新） ──
BANDIT_HP = 15
BANDIT_DAMAGE = 4
BANDIT_SPEED = 60
BANDIT_ATTACK_DELAY = 1.2
BANDIT_SIZE = 18
BANDIT_COLOR = (140, 100, 80)
BANDIT_AGGRO_RANGE = 300
BANDIT_PROJECTILE_SPEED = 350
BANDIT_PROJECTILE_SIZE = 5
BANDIT_GROUP_COUNT_MIN = 3        # 土匪每次刷新最少数量
BANDIT_GROUP_COUNT_MAX = 5        # 土匪每次刷新最多数量

# ── 火箭兵（远程型，高血高甲，AOE 弹丸） ──
ROCKET_TROOP_HP = 55
ROCKET_TROOP_DAMAGE = 12
ROCKET_TROOP_SPEED = 40
ROCKET_TROOP_ATTACK_DELAY = 2.0
ROCKET_TROOP_SIZE = 24
ROCKET_TROOP_COLOR = (120, 60, 60)
ROCKET_TROOP_AGGRO_RANGE = 350
ROCKET_TROOP_PROJECTILE_SPEED = 300
ROCKET_TROOP_PROJECTILE_SIZE = 8
ROCKET_TROOP_AOE_RADIUS = 60      # AOE 爆炸半径

# ── BOSS 航天兵（远程型，激光枪） ──
BOSS_SPACE_HP = 280               # 基础 HP × BOSS_HP_MULT(8) = 2240
BOSS_SPACE_DAMAGE = 25
BOSS_SPACE_SPEED = 30
BOSS_SPACE_ATTACK_DELAY = 1.8
BOSS_SPACE_SIZE = 30
BOSS_SPACE_COLOR = (200, 50, 50)
BOSS_SPACE_AGGRO_RANGE = 400
BOSS_SPACE_DEBUFF_ID = "burn"     # BOSS 攻击附加燃烧

# ── 航天基地掉落表 ──
SNIPER_LOOT_TABLE = [
    ("resource", "ore",    0.35, 1, 2),
    ("resource", "stone",  0.20, 1, 1),
    ("gold",     None,     0.25, 8, 18),
]

ASSAULT_LOOT_TABLE = [
    ("resource", "ore",    0.40, 1, 3),
    ("resource", "stone",  0.25, 1, 2),
    ("gold",     None,     0.20, 10, 20),
]

BANDIT_LOOT_TABLE = [
    ("resource", "wood",   0.30, 1, 2),
    ("resource", "stone",  0.20, 1, 1),
    ("gold",     None,     0.15, 3, 10),
]

ROCKET_TROOP_LOOT_TABLE = [
    ("resource", "ore",    0.45, 2, 3),
    ("resource", "stone",  0.20, 1, 2),
    ("gold",     None,     0.25, 12, 25),
]

# BOSS 航天兵掉落：丰厚资源+金币
BOSS_SPACE_LOOT_TABLE = [
    ("resource",  "ore",             0.60, 3, 5),
    ("gold",      None,              1.00, 50, 80),
]

# ── 火箭发射台 ──
ROCKET_PAD_SIZE = 32               # 发射台精灵大小
ROCKET_PAD_INTERACT_RANGE = 48     # 交互距离
ROCKET_PAD_COUNT_MIN = 1           # 每局最少发射台数量
ROCKET_PAD_COUNT_MAX = 4           # 每局最多发射台数量
ROCKET_PAD_BOSS_SPAWN_DELAY = 3.0  # 激活后 BOSS 出现延迟（秒）
ROCKET_PAD_COUNTDOWN = 30.0        # 撤离倒计时（秒）
ROCKET_PAD_DESTROY_REWARD_GOLD = 30    # 炸毁奖励金币
ROCKET_PAD_DESTROY_REWARD_ORE = 2      # 炸毁奖励矿石

# ── 行动时间 ──
ACTION_TIME_SPACE = 480.0          # 航天基地行动时间（8 分钟）
ACTION_TIME_FOREST = 300.0         # 森林行动时间（5 分钟）
ACTION_TIME_DESERT = 300.0         # 沙漠行动时间（5 分钟）

# ── 航天装备穿戴概率（怪物穿戴分配） ──
MONSTER_SPACE_ARMOR_CHANCE = 0.30   # 航天怪穿戴航天护甲概率
MONSTER_SPACE_HELMET_CHANCE = 0.30  # 航天怪穿戴航天头盔概率
