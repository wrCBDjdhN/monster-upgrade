"""游戏全局配置常量，所有数值集中管理"""

import random

# ── 窗口 ──
# 逻辑分辨率：全部视图绘制/布局均以 WINDOW_WIDTH×WINDOW_HEIGHT 为坐标系，
# 窗口实际尺寸变化（最大化/全屏/拖拽缩放）时由 main.GameWindow 等比缩放渲染
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
WINDOW_TITLE = "打怪升级"
WINDOW_RESIZABLE = True   # 允许窗口最大化/拖拽调整尺寸
FULLSCREEN = False        # 启动即全屏（False=窗口模式）；游戏内按 F11 随时切换

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

# ── 怪物数值定义（hp/damage/speed/color 等）已统一迁移到 entities/monster_defs.py ──

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
# 锻造神器概率（按材料中神器数量动态判定）：
FORGE_ARTIFACT_CHANCE_NONE = 0.2    # 两件普通材料：20% 出神器
FORGE_ARTIFACT_CHANCE_MIXED = 0.6   # 一件神器 + 一件普通：60% 出神器
FORGE_ARTIFACT_CHANCE_DOUBLE = 1.0  # 两件神器：100% 出神器
# 武器扩展机制（吸血/散射/光环）：
LIFESTEAL_DEFAULT = 0.0        # 武器默认吸血比例（0=无吸血，武器定义 lifesteal 字段覆盖）
SPREAD_COUNT_DEFAULT = 1       # 武器默认弹丸数（1=无散射，武器定义 spread_count 字段覆盖）
SPREAD_ANGLE_DEFAULT = 0.0     # 武器默认散射夹角（度）
AURA_SLOW_TICK = 0.5           # 攻速光环减速结算周期（秒）：每 0.5s 对半径内怪物施加一次减速
AURA_SLOW_LEVEL = 1            # 光环减速效果等级（slow 效果 Lv1=25% 减速）
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

# ── 开箱系统（按主题区分掉落池，用户需求 2026-08）──
# 每主题配置项：
#   main_chance / potion_chance / elite_chance / artifact_chance：主掉落各档概率（合计 1.0）
#   main_level / elite_level / artifact_level：(min, max) 各档装备等级区间（区间内均匀随机）
#   main_pool / elite_pool：[(slot, item_id), ...] 装备池（slot ∈ weapon/helmet/armor/backpack）
# 神器池不写死：为 None 时由 chest.py 动态筛取 artifact 标记物品（新增神器自动纳入）
# 各主题掉落规则：
#   幽暗森林：45% 常规装备(1-5级) / 45% 药水 / 10% 高级装备(1级)，不掉落神器
#   沙漠荒地：45% 常规装备(1-5级) / 45% 药水 / 10% 高级装备(1级)，不掉落神器
#   航天基地：60% 常规装备(5-50级) / 20% 药水 / 20% 神器(5-10级)
CHEST_THEME_CONFIGS = {
    "forest": {
        "main_chance": 0.45,
        "potion_chance": 0.45,
        "elite_chance": 0.10,
        "artifact_chance": 0.0,
        "main_level": (1, 5),
        "elite_level": (1, 1),
        "artifact_level": (5, 10),
        # 常规装备池：皮质/铁质/金质 + 木剑铁剑石锤 + 弓杖 + 背包（用户需求）
        "main_pool": [
            ("helmet", "leather_helm"), ("armor", "leather_armor"),   # 皮质
            ("helmet", "iron_helm"), ("armor", "chain_mail"),         # 铁质
            ("helmet", "golden_helm"), ("armor", "plate_armor"),      # 金质
            ("weapon", "wood_sword"), ("weapon", "iron_sword"), ("weapon", "stone_mace"),
            ("weapon", "short_bow"), ("weapon", "long_bow"), ("weapon", "fire_staff"),
            ("backpack", "small_bag"), ("backpack", "medium_bag"), ("backpack", "large_bag"),
        ],
        # 高级装备池：枪械 + 木乃伊/航天套装（固定 1 级，用户需求）
        "elite_pool": [
            ("weapon", "sniper"), ("weapon", "pistol"), ("weapon", "rifle"),
            ("helmet", "mummy_helmet"), ("armor", "mummy_armor"),
            ("helmet", "space_helmet"), ("armor", "space_armor"),
        ],
    },
    "desert": {
        "main_chance": 0.45,
        "potion_chance": 0.45,
        "elite_chance": 0.10,
        "artifact_chance": 0.0,
        "main_level": (1, 5),
        "elite_level": (1, 1),
        "artifact_level": (5, 10),
        # 常规装备池：枪械 + 木乃伊/航天套装 + 背包（用户需求）
        "main_pool": [
            ("weapon", "sniper"), ("weapon", "pistol"), ("weapon", "rifle"),
            ("helmet", "mummy_helmet"), ("armor", "mummy_armor"),
            ("helmet", "space_helmet"), ("armor", "space_armor"),
            ("backpack", "medium_bag"), ("backpack", "large_bag"), ("backpack", "huge_bag"),
        ],
        # 高级装备池：沙漠特色强力武器（固定 1 级，用户需求）
        "elite_pool": [
            ("weapon", "rocket_launcher"), ("weapon", "laser_gun"),
            ("weapon", "cursed_scimitar"), ("weapon", "scepter"),
        ],
    },
    "space": {
        "main_chance": 0.60,
        "potion_chance": 0.20,
        "elite_chance": 0.0,
        "artifact_chance": 0.20,
        "main_level": (5, 50),
        "elite_level": (5, 10),
        "artifact_level": (5, 10),
        # 常规装备池：沙漠+航天全部特色装备 + 背包（用户需求）
        "main_pool": [
            ("weapon", "rocket_launcher"), ("weapon", "laser_gun"),
            ("weapon", "cursed_scimitar"), ("weapon", "scepter"),
            ("weapon", "sniper"), ("weapon", "pistol"), ("weapon", "rifle"),
            ("helmet", "mummy_helmet"), ("armor", "mummy_armor"),
            ("helmet", "space_helmet"), ("armor", "space_armor"),
            ("backpack", "large_bag"), ("backpack", "huge_bag"),
        ],
    },
}
# 宝箱必定掉落金币数量范围（所有主题一致）
CHEST_GOLD_MIN = 5
CHEST_GOLD_MAX = 15

# ── 本局药水槽（run_potions）──
RUN_POTION_SLOTS = 5             # 本局可携带药水上限（不占背包容量，热键 1-3 优先使用）
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

# ── 航天基地主题 ──
SPACE_THEME = {
    "bg": (8, 12, 20),             # 深空背景
    "room_floor": (25, 30, 40),    # 房间地板（金属质感）
    "wall": (50, 55, 65),          # 墙壁
}

# ── 怪物行为参数（被 respawn.py / combat.py 直接引用） ──
BANDIT_GROUP_COUNT_MIN = 3        # 土匪每次刷新最少数量
BANDIT_GROUP_COUNT_MAX = 5        # 土匪每次刷新最多数量
ROCKET_TROOP_AOE_RADIUS = 60      # 火箭兵 AOE 爆炸半径
# 索敌距离配置：
# - MONSTER_AGGRO_RANGE_MULT：在 monster_defs.py 各怪物 aggro_range 基础上放大的倍率
# - MONSTER_AGGRO_RANGE_BASE：索敌保底距离（≈屏幕半对角线，保证"玩家能看到怪物→怪物就能索敌"）
# 实际索敌距离 = max(BASE, aggro_range × MULT)；配合视线检测（隔墙不索敌）与边缘视线
# （拐角露出部分身体即可被看到），实现 360° 视野被墙遮挡的索敌模型
MONSTER_AGGRO_RANGE_MULT = 2.2
MONSTER_AGGRO_RANGE_BASE = 700

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
ROCKET_PAD_DESTROY_REWARD_GOLD_MIN = 100     # 炸毁奖励金币下限
ROCKET_PAD_DESTROY_REWARD_GOLD_MAX = 500     # 炸毁奖励金币上限
ROCKET_PAD_DESTROY_REWARD_RESOURCE_MIN = 20  # 炸毁奖励资源总量下限
ROCKET_PAD_DESTROY_REWARD_RESOURCE_MAX = 50  # 炸毁奖励资源总量上限
ROCKET_PAD_DESTROY_LOOT_COUNT = 2            # 炸毁奖励武器/装备件数
ROCKET_PAD_DESTROY_NORMAL_CHANCE = 0.7       # 每件武器/装备为普通掉落（Lv20-50）的概率
ROCKET_PAD_DESTROY_NORMAL_LV_MIN = 20        # 普通武器/装备等级下限
ROCKET_PAD_DESTROY_NORMAL_LV_MAX = 50        # 普通武器/装备等级上限
ROCKET_PAD_DESTROY_ARTIFACT_LV_MIN = 10      # 神器等级下限
ROCKET_PAD_DESTROY_ARTIFACT_LV_MAX = 20      # 神器等级上限

# ── 行动时间 ──
ACTION_TIME_SPACE = 480.0          # 航天基地行动时间（8 分钟）
ACTION_TIME_FOREST = 300.0         # 森林行动时间（5 分钟）
ACTION_TIME_DESERT = 300.0         # 沙漠行动时间（5 分钟）

# ── 航天装备穿戴概率（怪物穿戴分配） ──
MONSTER_SPACE_ARMOR_CHANCE = 0.30   # 航天怪穿戴航天护甲概率
MONSTER_SPACE_HELMET_CHANCE = 0.30  # 航天怪穿戴航天头盔概率

# ── 角色等级系统 ──
# 经验获取：击杀普通怪 EXP_KILL_BASE / BOSS ×EXP_BOSS_MULT / 采集资源 / 开宝箱 / 撤离成功
PLAYER_MAX_LEVEL = 10               # 角色最高等级（Lv.1 起步，升满后经验不再累积）
EXP_PER_LEVEL_BASE = 100            # 升级经验基数：升到 Lv.n+1 需 EXP_PER_LEVEL_BASE × n 经验（递增）
EXP_KILL_BASE = 20                  # 击杀普通怪物经验
EXP_BOSS_MULT = 5                   # BOSS 击杀经验倍率（20×5=100，火箭台 BOSS 同规则）
EXP_HARVEST = 8                     # 采集资源经验（树/矿石/仙人掌等）
EXP_CHEST = 30                      # 开宝箱经验
EXP_EVAC = 100                      # 撤离成功经验（撤离点/火箭台撤离均计）
# 升级 3 选 1 永久属性加成池：key 为 character_levels 表加成列名，value 为每次加成数值
LEVEL_BONUS_POOL = [
    {"key": "bonus_hp",        "name": "生命上限", "value": 20,   "desc": "最大生命 +20",  "color": (255, 90, 90)},
    {"key": "bonus_damage",    "name": "攻击伤害", "value": 3,    "desc": "攻击伤害 +3",   "color": (255, 160, 80)},
    {"key": "bonus_defense",   "name": "防御力",   "value": 3,    "desc": "防御力 +3",     "color": (120, 180, 255)},
    {"key": "bonus_speed",     "name": "移动速度", "value": 0.2,  "desc": "移动速度 +5%", "color": (140, 255, 140)},
    {"key": "bonus_atk_speed", "name": "攻击速度", "value": 0.1,  "desc": "攻击速度 +0.1", "color": (255, 220, 120)},
]


def exp_needed_for_level(level):
    """返回从 Lv.level 升到 Lv.level+1 所需经验（100 × level，逐级递增）

    level >= PLAYER_MAX_LEVEL 时返回 0（满级无需经验）。
    """
    if level >= PLAYER_MAX_LEVEL:
        return 0
    return EXP_PER_LEVEL_BASE * level


def roll_level_up_options(count=3):
    """随机抽取 count 个不重复的升级加成选项（升级 3 选 1 面板使用）"""
    return random.sample(LEVEL_BONUS_POOL, k=min(count, len(LEVEL_BONUS_POOL)))


# ── 按键绑定（默认键位，settings_view 可重绑并持久化到 db settings 表）──
# 键名 = arcade.key 的属性名（字符串），运行时 getattr(arcade.key, name) 解析；
# 每个动作一个键（移动动作 WASD 各自独立），方向键暂未实现移动（保持现状）。
KEY_BINDINGS = {
    "move_up": ["W"],       # 向上移动
    "move_down": ["S"],     # 向下移动
    "move_left": ["A"],     # 向左移动
    "move_right": ["D"],    # 向右移动
    "interact": ["E"],      # 交互（宝箱/水井/发射台/拾取）
    "skill": ["F"],         # 角色技能
    "backpack": ["TAB"],    # 背包/升级面板
    "potion_1": ["KEY_1"],   # 药水快捷键 1
    "potion_2": ["KEY_2"],   # 药水快捷键 2
    "potion_3": ["KEY_3"],   # 药水快捷键 3
    "rocket_destroy": ["KEY_7"],  # 发射台菜单-炸毁
    "rocket_evac": ["KEY_8"],     # 发射台菜单-启用撤离
    "spectate": ["V"],      # 观战视角切换
    "minimap_zoom": ["M"],  # 小地图放大（周围视野 ⇄ 全图）
}

# ── 小地图（游戏 HUD 右上角）──
MINIMAP_SIZE = 200             # 小地图边长（像素）
MINIMAP_PADDING = 20           # 距屏幕右/上边缘间距（像素）
MINIMAP_VIEW_RADIUS = 700      # 小地图默认「周围视野」半径（世界像素，±700px）

# ── 联机网络 ──
NET_SNAPSHOT_HZ = 20                # 状态快照广播频率（Hz）
NET_PORT = 8765                     # 局域网联机默认端口
NET_HEARTBEAT_SEC = 1.0             # 心跳间隔（秒）
NET_TIMEOUT_SEC = 5.0               # 断线判定超时（秒）
NET_SPAWN_OFFSET = 40               # 玩家出生点槽位偏移（像素）
NET_ACTION_TIME_BCAST_SEC = 1.0     # 行动时间广播间隔（秒）
