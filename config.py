"""游戏全局配置常量，所有数值集中管理"""

import math
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
# 倒地/救援系统（联机模式）
DOWNED_TIMEOUT = 60.0        # 倒地超时（秒），超时未被救则真死
RESCUE_DISTANCE = 80         # 救援触发距离（像素）
RESCUE_DURATION = 3.0        # 救援读条时长（秒）
REVIVE_HP = 10               # 复活后血量

# ── 战斗 ──
ATTACK_COOLDOWN = 0.4          # 秒
MELEE_RANGE = 50               # 近战命中半径
MELEE_ARC_DEGREES = 120        # 近战扇形角度
PROJECTILE_SPEED = 400
PROJECTILE_SIZE = 6
PROJECTILE_LIFETIME = 1.5      # 秒
CRIT_DAMAGE_MULT = 1.5         # 暴击伤害倍率
PLAYER_EXPLOSION_RADIUS = 80   # 玩家弹丸爆炸半径
MELEE_HIT_BUFFER = 30          # 近战命中范围缓冲（武器射程外额外判定距离）
RANGED_KEEP_MIN = 120          # 远程怪物最小保持距离
RANGED_KEEP_MAX = 200          # 远程怪物最大保持距离
DEBUFF_TICK_INTERVAL = 0.5     # debuff 结算周期（秒）
HIT_FLASH_DURATION = 0.15      # 受击闪白时长（秒）
CHEST_WELL_INTERACT_RANGE = 40 # 宝箱/水井交互距离

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

# ── 怪物持有装备等级范围（按主题区分，用户需求 v1.2.0）──
# 普通怪物按主题使用不同等级范围；Boss 仍使用全局范围
MONSTER_WEAPON_LEVEL_RANGE = (1, 10)    # 兜底默认（不应被使用，主题覆盖）
BOSS_WEAPON_LEVEL_RANGE = (20, 30)      # Boss 持有武器等级范围
MONSTER_GEAR_LEVEL_RANGE = (1, 10)      # 兜底默认
BOSS_GEAR_LEVEL_RANGE = (20, 30)        # Boss 持有头盔/护甲等级范围

# ── 主题级怪物装备配置（用户需求：按地图主题分策略）──
# 每主题装备池按权重列表 [(item_id, weight), ...]，weight 为相对权重（内部归一化）
# helmet_chance / armor_chance：穿戴概率（0~1）
# weapon_level / gear_level：(min, max) 等级范围
# artifact_weapon_chance / artifact_weapon_level：神器武器概率与等级范围
# artifact 条目从 equipment_defs/weapon_defs 的 artifact=True 自动筛取
MONSTER_THEME_EQUIP = {
    "forest": {
        "helmet_chance": 0.30,
        "armor_chance": 0.30,
        "helmet_pool": [("leather_helm", 80), ("iron_helm", 15), ("golden_helm", 5)],
        "armor_pool":  [("leather_armor", 80), ("chain_mail", 15), ("plate_armor", 5)],
        "weapon_level": (1, 5),
        "gear_level":  (1, 5),
        "artifact_weapon_chance": 0.0,
        "artifact_weapon_level": (1, 5),
    },
    "desert": {
        "helmet_chance": 0.50,
        "armor_chance": 0.80,
        "helmet_pool": [("mummy_helmet", 80), ("golden_helm", 20)],
        "armor_pool":  [("mummy_armor", 80), ("plate_armor", 20)],
        "weapon_level": (5, 10),
        "gear_level":  (5, 10),
        "artifact_weapon_chance": 0.10,
        "artifact_weapon_level": (1, 5),
    },
    "space": {
        "helmet_chance": 1.00,
        "armor_chance": 1.00,
        "helmet_pool": [("space_helmet", 90), ("strong_force_helm", 10)],
        "armor_pool":  [("space_armor", 90), ("strong_force_armor", 10)],
        "weapon_level": (10, 50),
        "gear_level":  (10, 50),
        "artifact_weapon_chance": 0.30,
        "artifact_weapon_level": (5, 10),
    },
}

# ── 沙漠怪穿戴木乃伊系装备概率（保留兼容，新逻辑由 MONSTER_THEME_EQUIP 驱动）──
MONSTER_MUMMY_ARMOR_CHANCE = 0.35
MONSTER_MUMMY_HELMET_CHANCE = 0.35

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
    {"key": "bonus_atk_speed", "name": "攻击速度", "value": 0.1,  "desc": "攻击速度 +10%", "color": (255, 220, 120)},
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
    "build": ["B"],         # 建造模式开关（局内建造系统）
}

# ── 刷新距离（怪物/环境物生成时与玩家的最小欧几里得距离）──
MONSTER_SPAWN_MIN_DIST = 600      # 怪物刷新最小距离（超出屏幕可视范围，避免在玩家身边二次刷新）
HARVEST_SPAWN_MIN_DIST = 400      # 环境物刷新最小距离

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

# ── 局内建造系统 ──
BUILD_RANGE = 220.0        # 放置时玩家与目标格的最大距离（像素）
BUILD_GRID = 32            # 放置网格吸附粒度（像素）
TOWER_TARGET_MAX = 2       # 箭塔单次射击最大目标数
BUILDING_ATTACK_SEARCH_RANGE = 96.0  # 怪物受阻时搜索可攻击建筑的最大距离（像素）

# ── 局内建造选择面板（建造模式弹层；行数随 BUILDS 条目数自适应，禁硬编码行数）──
# 视觉沿用 EVENT_PANEL_* 弹层语言（同屏弹层风格统一）；面板贴屏幕右侧，避开中心瞄准区
BUILD_PANEL_WIDTH = 320           # 面板宽度（像素）
BUILD_PANEL_SIDE_MARGIN = 16      # 面板距屏幕右边缘距离（像素）
BUILD_PANEL_MARGIN = 16           # 面板内边距（标题区上下 + 行块左右，像素）
BUILD_PANEL_TITLE_H = 30          # 标题条高度（像素）
BUILD_PANEL_ROW_H = 26            # 每个建筑行高（像素）
BUILD_PANEL_ROW_GAP = 4           # 行块实心块之间的间隙（像素；行块实高 = 行高 - 间隙）
BUILD_PANEL_HINT_H = 18           # 底部操作提示条高度（像素）
BUILD_PANEL_KEY_W = 22            # 行首序号实心块宽度（像素）
BUILD_PANEL_KEY_PAD = 4           # 行首序号块内缩 + 序号后文字缩进（像素）
BUILD_PANEL_TITLE_SIZE = 14       # 标题字号（像素）
BUILD_PANEL_TEXT_SIZE = 12        # 行文字字号（像素）
BUILD_PANEL_KEY_SIZE = 11         # 行首序号字号（像素）
BUILD_PANEL_HINT_SIZE = 10        # 底部操作提示字号（像素）
BUILD_PANEL_BG = (26, 22, 34)     # 面板底色（同 EVENT_PANEL_BG）
BUILD_PANEL_ROW_BG = (44, 38, 58)  # 面板行底色（同 EVENT_PANEL_ROW_BG）
BUILD_PANEL_ROW_HL = (78, 66, 104)  # 当前选中行底色（同 EVENT_PANEL_ROW_HL）
BUILD_PANEL_KEY_BG = (58, 50, 76)  # 行首序号实心块底色
BUILD_PANEL_TEXT = (232, 228, 240)  # 面板普通文字色
BUILD_PANEL_TEXT_HL = (255, 226, 120)  # 当前选中行文字色
BUILD_PANEL_TEXT_DIM = (150, 110, 110)  # 材料不足行暗色（必须同时输出中文提示，禁只靠颜色区分）
BUILD_PANEL_TEXT_WARN = (255, 120, 110)  # 材料不足中文提示色

# ── 防守式撤离（三图统一）──
EVAC_POINT_HP = 600                                   # 撤离点血量
# 撤离点激活/修复消耗（三图分造价：用户拍板 2026-09-26）
# 修复与激活同比，故只存一份表；键为地图主题（forest/desert/space），见 map_select_view.MAPS
EVAC_COST_BY_THEME = {
    "forest": {"wood": 3, "stone": 3, "ore": 1},
    "desert": {"wood": 6, "stone": 6, "ore": 3},
    "space": {"wood": 8, "stone": 8, "ore": 4},
}
EVAC_DEFAULT_THEME = "forest"   # 未知主题回落用（最低档，防缺键崩溃）


def evac_activate_cost(theme: str) -> dict[str, int]:
    """按地图主题返回撤离点激活/修复消耗（修复与激活同比，用户拍板 2026-09-26）

    theme 取地图主题键（forest/desert/space）；未知主题回落 EVAC_DEFAULT_THEME（forest）造价。
    返回新 dict 副本，避免调用方就地改写全局表。
    """
    return dict(EVAC_COST_BY_THEME.get(theme, EVAC_COST_BY_THEME[EVAC_DEFAULT_THEME]))
EVAC_DEFEND_TIME = {"forest": 45.0, "desert": 60.0, "space": 75.0}  # 防守倒计时
EVAC_WAVE_INTERVAL = 10.0          # 防守期间进攻波次间隔（秒）
EVAC_WAVE_SIZE = 4                 # 每波基础进攻怪数量
EVAC_WAVE_GROWTH = 1               # 每波额外数量（递增）
EVAC_INTERACT_RANGE = 90.0         # 激活/修复交互距离（E 键）
EVAC_WAVE_HP_GROWTH = 0.15         # 每波血量加成比例（按已打波数线性累加）
EVAC_WAVE_DAMAGE_GROWTH = 0.10     # 每波伤害加成比例
EVAC_WAVE_SPEED_GROWTH = 0.03      # 每波移速加成比例
EVAC_WAVE_SPEED_CAP = 1.6          # 移速加成封顶倍率
# 波次怪从撤离点外围环带进场（内圈不再绑定 MONSTER_SPAWN_MIN_DIST=600：
# 玩家守在撤离点上时 620px 的下限会让每波怪步行 9~18 秒才到，45 秒防守期里第 3/4 波根本赶不到；
# 波次怪进场改由 _find_valid_spawn_position(min_player_dist=) 单独给一个小值约束，
# 环带收紧到 300~520 使波次在防守窗口内能真正抵达撤离点并造成伤害——用户缺陷⑦修复 2026-09-26）
EVAC_WAVE_SPAWN_MIN_DIST = 300.0
EVAC_WAVE_SPAWN_MAX_DIST = 520.0
# 波次怪与玩家的最小距离（覆盖 _find_valid_spawn_position 的全局 MONSTER_SPAWN_MIN_DIST 口径）
EVAC_WAVE_SPAWN_MIN_PLAYER_DIST = 120.0

# 阶段2 渲染常量：主撤离点实心方块 + 血条 + 倒计时标签（一律不透明实心填充）
EVAC_BLOCK_HALF = 20            # 撤离点方块半边长（像素）
EVAC_BAR_WIDTH = 44             # 血条总宽（像素）
EVAC_BAR_HEIGHT = 5             # 血条总高（像素）
# 撤离点状态配色：dormant 待激活 / defending 防守中 / secured 已守住 / destroyed 被拆
EVAC_STATE_COLORS = {
    "dormant": (60, 170, 255),
    "defending": (255, 150, 40),
    "secured": (0, 255, 100),
    "destroyed": (110, 110, 110),
}
EVAC_STATE_BCAST_SEC = 1.0        # 主机广播 EVAC_POINT_STATE 的周期（秒）；状态变化时立即广播

# ── 阶段3：精英词缀怪 ──
# 精英刷新：开局 ELITE_SPAWN_INTERVAL 秒后首刷，之后按同周期刷新
ELITE_SPAWN_INTERVAL = 60.0   # 精英刷新周期（秒）
ELITE_SPAWN_MIN_DIST = 500.0  # 精英刷新时与玩家的最小距离（像素）
ELITE_MAX_ALIVE = 1           # 场上同时存活的精英上限（<1 才刷新）
ELITE_HP_MULT = 3.0           # 精英血量倍率（在词缀 hp_bonus_mult 之外）
ELITE_DAMAGE_MULT = 1.5       # 精英伤害倍率
ELITE_GOLD_BONUS = 3          # 精英死亡金币倍率
ELITE_MIN_WEAPON_LEVEL = 10   # 精英必掉武器/装备的最低等级
ELITE_EXP_MULT = 2            # 精英死亡经验倍率

# 词缀·狂暴：半血一次性提速提伤
ELITE_FRENZY_HP_THRESHOLD = 0.5   # 触发阈值（当前血量 / 最大血量）
ELITE_FRENZY_SPEED_MULT = 1.6      # 触发后速度倍率
ELITE_FRENZY_DAMAGE_MULT = 1.5     # 触发后伤害倍率

# 词缀·分裂：死亡时原地刷小怪
ELITE_SPLIT_SPAWN_OFFSET = 30      # 小怪相对精英的散布半径（像素）

# 词缀·火墙：死亡留燃烧地面（区域实体，联机同步留阶段11）
ELITE_FIRE_ZONE_RADIUS = 90        # 燃烧区半径（像素）
ELITE_FIRE_ZONE_LIFE = 8.0         # 燃烧区持续时间（秒）
ELITE_FIRE_ZONE_TICK = 0.5         # 燃烧区扣血节奏（秒，复用 DEBUFF_TICK_INTERVAL）
ELITE_FIREWALL_CONTACT_RANGE = 60  # 精英自身火墙词缀对近身玩家施加灼烧的距离（像素）
ELITE_FIREWALL_CONTACT_INTERVAL = 2.0  # 近身灼烧施加间隔（秒）
ELITE_FIREWALL_BURN_DURATION = 3.0      # 近身灼烧持续时间（秒）
ELITE_FIREWALL_BURN_LEVEL = 1           # 近身灼烧等级
ELITE_FIRE_ZONE_COLOR = (255, 110, 40)  # 燃烧区橙红实心填充色

# 词缀·召唤：周期性召小怪
ELITE_SUMMON_INTERVAL = 8.0        # 召唤间隔（秒）
ELITE_SUMMON_MAX_ALIVE = 6         # 场上召唤怪数量上限（防止刷屏）

# 词缀·护盾：吸收伤害，血条显示在血条下方
ELITE_SHIELD_BAR_WIDTH = 34        # 护盾条总宽（像素）
ELITE_SHIELD_BAR_HEIGHT = 4        # 护盾条总高（像素）
ELITE_SHIELD_BAR_OFFSET = 7        # 护盾条相对血条下移（像素）
ELITE_SHIELD_BAR_COLOR = (120, 190, 255)  # 护盾条填充色

# 精英标记：头顶词缀名前缀 + 小地图专属色点（一律不透明实心填充）
ELITE_LABEL_COLOR = (200, 120, 255)   # 精英词缀名前缀色（紫）
ELITE_MINIMAP_COLOR = (170, 80, 255)  # 小地图精英点色（紫）
ELITE_MINIMAP_DOT_RADIUS = 3          # 小地图精英点半径（像素）
ELITE_LABEL_OFFSET = 16               # 词缀名前缀相对怪物头顶的额外上移（像素）

# ── 怪物技能释放改造：距离分档 + 冷却 + 特效 ──
# 纯数据地基：距玩家阈值内/外分 near / far 两档，各技能在对应档位才被选中释放。
SKILL_DIST_NEAR = 150.0        # 技能距离分档阈值（像素）：dist <= 阈值视为近距离档，否则远距离档
SKILL_CD_DEFAULT = 4.0         # 技能冷却缺省（秒）：SKILL_COOLDOWNS 未命中时回退
SKILL_COOLDOWNS = {            # 各技能冷却（秒），键 = SKILL_PROMPT_CONFIG 技能中文名
    "腐烂光环": 6.0, "狂暴": 8.0,
    "骨盾": 7.0, "骨矛投掷": 4.0,
    "毒雾释放": 6.0, "木乃伊缠绕": 5.0,
    "治愈祷言": 10.0, "诅咒标记": 5.0,
    "沙尘暴": 8.0, "储水": 9.0,
    "激光瞄准": 6.0, "战术撤退": 7.0,
    "投掷匕首": 4.0, "群体呼叫": 9.0,
    "追踪导弹": 6.0, "弹幕射击": 8.0,
    "冲锋": 5.0, "手雷投掷": 6.0,
}
SKILL_VFX_COUNT_MULT = 2.0     # 技能粒子数量倍率（_emit_skill_vfx 统一放大）
SKILL_VFX_LIFE_MULT = 1.5      # 技能粒子寿命倍率
SKILL_CIRCLE_INNER_RATIO = 0.45  # 技能范围圈内圈半径比例（相对技能 radius）
SKILL_CIRCLE_OUTER_ALPHA = 90    # 技能范围圈外圈 alpha（RGBA 第 4 位）

# ── 精英「火墙」词缀：存活期周期生成燃烧区（死亡区仍用 ELITE_FIRE_ZONE_*） ──
ELITE_FIRE_WALL_INTERVAL = 6.0   # 存活期火区生成间隔（秒）
ELITE_FIRE_WALL_RADIUS = 70      # 存活期火区半径（像素，比死亡区 90 小）
ELITE_FIRE_WALL_LIFE = 5.0       # 存活期火区持续（秒，比死亡区 8 短）

# ── 每日任务 / 成就（任务板系统，db/missions.py + entities/mission_defs.py 消费）──
DAILY_COUNT = 3             # 每日任务条数（从 entities.mission_defs.DAILY_POOL 随机抽取，写入 slot 0..DAILY_COUNT-1）
DAILY_REFRESH_HOUR = 0      # 每日任务刷新时刻（0 = 自然日 0 点，与 db/missions.get_daily 的 date.today() 比较口径一致）

# ── 阶段4：随机地图事件（entities/event_defs.py 定义 + game/map_events.py 驱动）──
# 事件 id 四选一（等权随机）：tide 尸潮 / airdrop 空投 / caravan 商队 / relic 神器低语
EVENT_BANNER_SEC = 3.0            # 开局事件横幅显示时长（秒，渲染层到期自动隐藏）
EVENT_PICK_RNG_SALT = 977         # 抽选随机盐（与地图种子组合派生本局事件，保证 host/client 同事件）

# 尸潮：怪物上限与掉落奖励同时放大（倍率写进 view.event_flags，反算 _monster_cap）
EVENT_TIDE_CAP_MULT = 1.5         # 尸潮：野外怪物上限倍率（怪物更密集）
EVENT_TIDE_REWARD_MULT = 1.5      # 尸潮：击杀掉落倍率（金币/资源数量取整放大）

# 空投：延迟落地 + 高级补给箱
EVENT_AIRDROP_DELAY = 60.0        # 空投落地延迟（秒，自开局计时；仅 airdrop 事件）
EVENT_AIRDROP_CRATES = 2          # 空投落地生成的补给箱数量
EVENT_AIRDROP_MIN_LEVEL = 10      # 空投箱必出 1 件的最低装备等级
EVENT_AIRDROP_LEVEL_SHIFT = 5     # 空投箱掉落等级区间相对主题配置的上浮量（级）
EVENT_AIRDROP_MIN_DIST = 220.0    # 空投箱与玩家的最小距离（像素，避免糊脸）
EVENT_AIRDROP_TRIES = 12          # 空投箱找位最大尝试次数（超次数则本次跳过，下个周期重试）
EVENT_AIRDROP_GOLD_MIN = 40       # 空投箱必掉金币下限（高于普通宝箱 CHEST_GOLD_MIN）
EVENT_AIRDROP_GOLD_MAX = 80       # 空投箱必掉金币上限
EVENT_AIRDROP_POTION_CHANCE = 0.6 # 空投箱附带 1 瓶药水的概率
EVENT_AIRDROP_EXTRA_GEAR = 1      # 除保底主装备外额外附带的装备件数

# 神器低语：神器掉落等级加成
EVENT_RELIC_ARTIFACT_BONUS = 0.15 # 神器低语：神器掉落等级加成比例（+15% 等级）

# 商队：交互点换购表（键名复用现有 item_id：药水取 POTIONS 键，资源取资源 id）
# 值为局内金币价，购买直接写 gs.run_carried（扣 gold，加 potion/resource）
EVENT_CARAVAN_PRICES: dict[str, int] = {
    # 药水（entities.equipment_defs.POTIONS 的 item_id）
    "heal_potion_s": 50,     # 小回复药水
    "speed_potion": 60,      # 疾跑药水
    "shield_potion": 80,     # 护盾药水
    "power_potion": 120,     # 狂暴药水
    # 资源（entities.resource_defs 的资源 id）
    "wood": 8,               # 木材
    "stone": 12,             # 石材
    "ore": 20,               # 矿石
}
EVENT_CARAVAN_RANGE = 90.0        # 商队交互距离（像素，靠近按 E 弹出换购列表）
EVENT_CARAVAN_SIGN_SIZE = 40      # 商队招牌实心方块边长（像素）
EVENT_CARAVAN_PROMPT_CD = 0.6     # 「按E交易」提示节流（秒，防每帧刷屏）
EVENT_CARAVAN_MIN_DIST = 260.0    # 商队与玩家的最小距离（像素，避免开局糊脸）

# 事件横幅渲染（屏幕层，一律不透明实心矩形 + 文本，禁线框/空心）
EVENT_BANNER_HEIGHT = 44          # 横幅底板高度（像素）
EVENT_BANNER_Y = 56               # 横幅底板中心 Y（屏幕坐标，顶部行动倒计时下方）
EVENT_BANNER_BG = (34, 26, 48)    # 横幅底板实心色
EVENT_BANNER_EDGE = (128, 104, 190)  # 横幅底板描边替代色（内层实心条，非线框）
EVENT_BANNER_TEXT_COLOR = (255, 215, 0)  # 横幅文字色（金黄）

# 商队换购弹层（屏幕层，实心面板）
EVENT_PANEL_BG = (26, 22, 34)     # 面板底色
EVENT_PANEL_ROW_BG = (44, 38, 58)  # 面板行底色
EVENT_PANEL_ROW_HL = (78, 66, 104)  # 高亮行底色
EVENT_PANEL_TEXT = (232, 228, 240)  # 面板普通文字色
EVENT_PANEL_TEXT_HL = (255, 226, 120)  # 面板高亮文字色
EVENT_PANEL_ROW_H = 26            # 面板行高（像素）
EVENT_PANEL_MARGIN = 40           # 面板边距（像素）
EVENT_PANEL_WIDTH = 260           # 商队换购弹层宽度（像素）
EVENT_BANNER_MAX_HALF_W = 300     # 事件横幅最大半宽（像素，文案过长时截断）

# 事件实体渲染配色（空投箱/商队招牌 + 小地图标记，全部不透明实心）
EVENT_AIRDROP_CHEST_COLOR = (255, 150, 40)     # 空投箱箱体色（橙，区别普通宝箱金黄）
EVENT_AIRDROP_CHEST_TRIM = (255, 226, 150)     # 空投箱高亮饰条色
EVENT_AIRDROP_MINIMAP_COLOR = (255, 140, 30)   # 小地图空投标记色
EVENT_CARAVAN_COLOR = (90, 210, 230)          # 商队招牌实心色（青）
EVENT_CARAVAN_SIGN_TEXT = "流浪商队"           # 商队招牌世界标签文案
EVENT_CARAVAN_SIGN_TEXT_COLOR = (12, 30, 36)  # 商队招牌文字色（深色压在高亮底上）
EVENT_CARAVAN_MINIMAP_COLOR = (90, 210, 230)  # 小地图商队标记色
EVENT_MINIMAP_MARK_SIZE = 7                    # 小地图事件标记边长（像素）

# ── 阶段7：图鉴档位奖励（db/codex.py 判定 + views/codex_view.py 展示/领取）──
# 档位阈值口径：**按该类别图鉴「条目总数」的比例取档**（CODEX_TIER_RATIOS），
# 阈值随各类条目数自动缩放（怪物13/武器25/装备19/药水8 → 各自三档）。
# 历史说明：早期用固定条数 CODEX_TIERS=[10, 25, 50]，与各类实际条目数不匹配 ——
# 25/50 档在多数类别永久不可达、药水类连 10 档都拿不到，故改为比例制
# （原 CODEX_TIERS 已删除，禁再被引用）。
CODEX_TIER_RATIOS = (0.4, 0.7, 1.0)   # 三档比例（40%/70%/100%），对总数向上取整
# 奖励按「档位序号」（第 1/2/3 档 = 下标 0/1/2）索引，**不按条数阈值做键**：
# 比例制下各档阈值随类别变化（如武器第 2 档=18、怪物第 2 档=10），
# 只有序号是跨类别稳定口径，奖励数值本身保持不变。
CODEX_TIER_REWARD_GOLD = (200, 500, 1000)   # 第 1/2/3 档奖励金币
# 图鉴档位资源奖励，替代经验——用户拍板 2026-09-26
# 键为资源 item_id（entities/resource_defs.RESOURCES），数量入仓库（db.add_warehouse_item）
CODEX_TIER_REWARD_RES: tuple[dict[str, int], ...] = (
    {"wood": 15, "stone": 10, "ore": 5},
    {"wood": 30, "stone": 20, "ore": 10},
    {"wood": 60, "stone": 40, "ore": 20},
)
CODEX_CLAIM_COOLDOWN = 0.6    # 图鉴档位领奖冷却（秒）：领取后短暂禁点，防连点重复提交


def codex_tiers_for(total: int) -> list[int]:
    """按某类图鉴条目总数取该类的三档阈值（升序）

    total = 该类别「计入进度」的条目总数（口径同 entities/forge_recipes.get_codex_category_total）
    取档 = max(1, ceil(total × 比例))，并去重保序：
    总数很小时不同比例可能落到同一数值（如 total=2 → 1/2/2），去重后档位行数自然变少，
    避免出现两行同阈值导致重复领取歧义。
    """
    tiers: list[int] = []
    for ratio in CODEX_TIER_RATIOS:
        t = max(1, math.ceil(total * ratio))
        if t not in tiers:
            tiers.append(t)
    return tiers


def codex_tier_reward(tier: int, total: int) -> dict | None:
    """取某类图鉴的指定档位奖励，返回 {"gold": 金币数, "res": {资源 item_id: 数量}}

    tier 不在该类的比例档位表内（非法档位 / 小类别去重后已折叠）→ 返回 None，
    调用方据此拒绝领取，保证每一档都取得到奖励、且不会凭空多发。
    资源部分（CODEX_TIER_REWARD_RES）替代原经验奖励：本层只给数据，
    金币与仓库资源入库均由上层发放（views/codex_view._claim_tier）。
    """
    tiers = codex_tiers_for(total)
    if tier not in tiers:
        return None
    idx = tiers.index(tier)
    # 下标越界兜底：档位表被外部改动时不抛异常，金币回退 0、资源给空表
    gold = CODEX_TIER_REWARD_GOLD[idx] if idx < len(CODEX_TIER_REWARD_GOLD) else 0
    res = dict(CODEX_TIER_REWARD_RES[idx]) if idx < len(CODEX_TIER_REWARD_RES) else {}
    return {"gold": gold, "res": res}

# ── 阶段7：锻造配方（图鉴集齐解锁，entities/forge_recipes.py + views/forge_view.py 消费）──
CODEX_RECIPE_GOLD = 800       # 单次配方制作的金币费用
CODEX_RECIPE_RESOURCES = {"wood": 30, "stone": 20, "ore": 15}  # 单次配方的仓库资源消耗（键为资源 item_id）
CODEX_RECIPE_LEVEL = 20       # 配方产出物品的固定等级（4 件专属装备均按此等级入账）

# ── 阶段9：词条重铸费用（config.reforge_cost 计算 + db.reforge_weapon/equipment 落库 + views/market_view.py 弹层消费）──
# 语义：重铸 = 词条完全重新随机，**可能变差**（与「升级只升不降」不冲突，两者互不干涉）
REFORGE_GOLD_BASE = 100       # 重铸基础金币费（固定项，与物品等级无关）
REFORGE_GOLD_PER_LEVEL = 15   # 重铸每级金币增量（金币 = BASE + PER_LEVEL × 物品等级）
REFORGE_ORE_COST = 3          # 单次重铸消耗的仓库矿石数量（资源 item_id = "ore"）


def reforge_cost(level: int) -> dict:
    """按物品等级计算单次词条重铸费用，返回 {"gold": 金币数, "ore": 矿石数}

    金币 = REFORGE_GOLD_BASE + REFORGE_GOLD_PER_LEVEL × 等级（等级越高重铸越贵）
    矿石 = REFORGE_ORE_COST（固定值，与等级无关）
    等级非法（None/0/负数）时按 1 级兜底，避免出现负费用
    """
    lv = max(1, int(level or 1))
    return {"gold": REFORGE_GOLD_BASE + REFORGE_GOLD_PER_LEVEL * lv, "ore": REFORGE_ORE_COST}


# ── 阶段5：局内祝福（肉鸽词条；entities/blessing_defs.py 数据 + game/blessings.py 聚合消费）──
BLESSING_CHEST_CHANCE = 0.30     # 开箱时触发一次祝福选择的概率（0~1）
BLESSING_ELITE_GUARANTEE = True  # 精英怪被击杀时必定触发一次祝福选择
BLESSING_MAX = 6                 # 单局可同时持有的祝福数量上限（超出后 add 失败）


# ── 阶段9：套装效果（entities/*_defs.py 的 `set` 字段 + game/player.py 聚合消费 + 背包装备栏展示）──
# 套装分组（`set` 字段取值 → 成员件数）：
#   "mummy"        木乃伊套    = 木乃伊头盔 + 木乃伊护甲 + 诅咒弯刀（3 件）
#   "space"        航天套      = 航天头盔 + 航天护甲 + 激光枪 + 火箭筒（4 件）
#   "strong_force" 强相互作用力套 = 强相互作用力头盔 + 强相互作用力护甲（2 件，无 3 件档）
# 结构：{(套装 id, 穿戴件数阈值): [(效果字段, 数值), ...]}；同套装多档可同时生效
#（穿满 3 件 = 2 件档 + 3 件档）。效果字段名复用 entities/effects_defs.py 的 EFFECTS 口径：
#   平铺加值：max_hp / regen / defense；比率加值：speed / crit_chance / lifesteal / thorns / damage
#   （比率字段 0.05 = +5%，与阶段5 祝福同一套算术，见 Player.apply_bonus_stats）
# 注意：**不含 atk_speed**（武器攻速在 GameState 侧结算，套装不跨层叠加，只在 Player 内收口）
SET_BONUSES: dict[tuple[str, int], list[tuple[str, float]]] = {
    # ── 木乃伊套：沙漠主题，2 件 = 血肉坚韧，3 件 = 吸取生命 ──
    ("mummy", 2): [("max_hp", 40), ("regen", 1.0)],
    ("mummy", 3): [("lifesteal", 0.05)],
    # ── 航天套：航天基地，2 件 = 装甲机动，3 件 = 精准火力 ──
    ("space", 2): [("defense", 6), ("speed", 0.05)],
    ("space", 3): [("damage", 0.10), ("crit_chance", 0.05)],
    # ── 强相互作用力套：神器，2 件 = 物质护盾（防御 + 反伤）──
    ("strong_force", 2): [("defense", 12), ("thorns", 0.15)],
}

# 套装中文名（背包装备栏显示"套装名 进度/件数"，缺省回退为套装 id）
SET_NAMES: dict[str, str] = {
    "mummy": "木乃伊套",
    "space": "航天套",
    "strong_force": "强相互作用力套",
}

# 套装效果字段 → (中文标签, 是否按百分比显示)：SET_BONUSES 的效果文案化口径（展示层消费）
SET_EFFECT_LABELS: dict[str, tuple[str, bool]] = {
    "max_hp": ("生命上限", False),
    "regen": ("每秒回复", False),
    "defense": ("防御", False),
    "speed": ("移动速度", True),
    "crit_chance": ("暴击率", True),
    "lifesteal": ("吸血", True),
    "thorns": ("反伤", True),
    "damage": ("攻击伤害", True),
}


# ── 阶段8：地图难度星级（判定在 game/level_progress.py，存取在 db/map_progress.py）──
# 星级语义：单局撤离结算时按本表**逐条**判定条件，**按顺序全部达成才升星**
# （第 n 条未达成即停在 n-1 星）——三图首条均为「成功撤离」，故撤离失败直接 0 星。
# 判定入参 stats（由 views/game_view.py 的 run_stats 汇总，口径见计划 Task 8.1）：
#   {"evac": bool, "kills": int, "elite_kills": int, "boss_kills": int, "carried_gold": int}
# 条件 dict 字段：
#   stat  —— stats 里取值的键（"evac" 为布尔型条件，真值即达成）
#   value —— 阈值（缺省 1）
#   op    —— 比较符（缺省 ">="，即「≥」语义；数值条件可写 ">"/"<="/"=="/"<="）
#   desc  —— 中文条件文案（结算/地图选择界面展示用，不参与判定）
MAP_MAX_STARS = 3   # 单图星级上限（每图固定 3 条条件，此值同时作 db 层写入钳位上限）
MAP_STAR_CRITERIA: dict[str, list[dict]] = {
    # ── 幽暗森林（普通）：撤离 → 击杀量 → 精英猎手 ──
    "forest": [
        {"stat": "evac", "value": 1, "desc": "成功撤离"},
        {"stat": "kills", "op": ">=", "value": 30, "desc": "击杀 ≥30"},
        {"stat": "elite_kills", "op": ">=", "value": 1, "desc": "击杀精英 ≥1"},
    ],
    # ── 沙漠荒地（困难）：撤离 → 击杀量 → 暴利撤离 ──
    "desert": [
        {"stat": "evac", "value": 1, "desc": "成功撤离"},
        {"stat": "kills", "op": ">=", "value": 40, "desc": "击杀 ≥40"},
        {"stat": "carried_gold", "op": ">=", "value": 300, "desc": "撤离时携带金币 ≥300"},
    ],
    # ── 航天基地（极难）：撤离 → 击杀 BOSS → 巨额撤离 ──
    "space": [
        {"stat": "evac", "value": 1, "desc": "成功撤离"},
        {"stat": "boss_kills", "op": ">=", "value": 1, "desc": "击杀 BOSS"},
        {"stat": "carried_gold", "op": ">=", "value": 800, "desc": "携带金币 ≥800"},
    ],
}

# 解锁链：进入某图所需的**累计星数**（views/map_select_view.py 消费）
#   forest 0 星（初始解锁）→ desert 需累计 ≥1 星 → space 需累计 ≥2 星
MAP_UNLOCK_STARS: dict[str, int] = {
    "forest": 0,
    "desert": 1,
    "space": 2,
}


# ── 阶段10：主界面设施系统（局外基地建设；entities/facility_defs.py 数据 + db/facilities.py 持久化 + views/facility_view.py 建造/升级页）──
# 设施等级 0 = 未建造（按钮置灰🔒，点击进设施页建造）；最高 FACILITY_MAX_LEVEL 级。
# 建造消耗「仓库材料 + 金币」，因此玩家必须先打几局攒材料才能开放市场/锻造坊。
FACILITY_MAX_LEVEL = 3
FACILITY_IDS = ("market", "forge")

# 市场：建造→Lv1；MARKET_UPGRADE_COSTS 的键 = 目标等级
MARKET_BUILD_COST = {"gold": 300, "wood": 15, "stone": 10}
MARKET_UPGRADE_COSTS = {
    2: {"gold": 600, "wood": 25, "stone": 20},
    3: {"gold": 1200, "wood": 40, "stone": 30, "ore": 10},
}
MARKET_DISCOUNT = {0: 0.0, 1: 0.0, 2: 0.10, 3: 0.15}   # 购买折扣率（0=未建造）
MARKET_SELL_BONUS = {0: 0.0, 1: 0.0, 2: 0.05, 3: 0.10}  # 售出回收比加成（基础 config.SELL_COST_RECOVERY_RATIO）
MARKET_RESOURCE_SHOP_LEVEL = 3                          # Lv3 解锁资源回购
MARKET_RESOURCE_PRICES = {"wood": 15, "stone": 25, "ore": 50}  # 回购单价（金币/个）

# 锻造台：建造→Lv1；FORGE_UPGRADE_COSTS 的键 = 目标等级
FORGE_BUILD_COST = {"gold": 500, "wood": 10, "stone": 15, "ore": 8}
FORGE_UPGRADE_COSTS = {
    2: {"gold": 800, "wood": 20, "stone": 25, "ore": 12},
    3: {"gold": 1500, "wood": 30, "stone": 40, "ore": 20},
}
FORGE_COST_DISCOUNT = {0: 0.0, 1: 0.0, 2: 0.20, 3: 0.30}  # 锻造费+装备升级费折扣
FORGE_ARTIFACT_BONUS = {0: 0.0, 1: 0.0, 2: 0.10, 3: 0.15}  # artifact 概率加成
FORGE_REFUND_LEVEL = 3               # Lv3 熔炉余温：锻造费返还触发等级
FORGE_REFUND_CHANCE = 0.20           # Lv3 返还概率
FORGE_REFUND_RATIO = 0.50            # Lv3 返还比例


# ── 怪物 troop 技能冷却（game/monster_utils.py 消费）──
# MummyRanged「治愈祷言」冷却（秒）：原实现每次 50% 命中判定都回血（回复最大生命 20%），
# 无冷却导致近身对拼时几乎每 2~3 秒回满一次，等于不死；此处给该技能独立冷却。
# 该计时器逐帧在 update_skill_buffs 内递减（与 _skill_buff_timer 同一 tick 点）。
MUMMY_HEAL_SKILL_CD = 6.0


# ── 怪物 BFS 寻路（game/monster_base.py 消费）──
# 用户缺陷⑦（防守波怪从不攻击撤离点）修复 2026-09-26：
#   原实现只做「直线逼近 + 分轴滑动」，房间只有一个门洞，贴墙怪的切向分量≈0 → 永久死锁，
#   永远进不了攻击距离。改为在墙体占位网格上 BFS 求航点，绕门进屋。
# 用户缺陷⑧（隔墙索敌卡墙进不了房间）修复 2026-09-26：
#   追击同样是「整步→X→Y」贪心，凹角处两次单轴探测都失败 → 位移永久为 0。
#   新增卡死检测（想动却几乎不动连续 N 帧）→ 转入 BFS 航点模式，畅通后自动退回原追击。
MONSTER_NAV_CELL_PAD = 26.0            # 墙体占位判定的膨胀边距（像素）；门洞=128px 时仍至少留 1 格可通行
MONSTER_NAV_REFRESH_INTERVAL = 0.25   # 单只怪物两次重算 BFS 的最短间隔（秒）
MONSTER_NAV_WAYPOINT_LOOKAHEAD = 2     # 航点取路径上第 N 格中心（每跳 2 格，兼顾顺滑与绕墙）
MONSTER_NAV_WAYPOINT_ARRIVE_RATIO = 0.6   # 航点到达判定半径 = ratio × cell
MONSTER_NAV_WAYPOINT_STUCK_FRAMES = 20     # 跟随某航点时位移≈0 连续 N 帧 → 丢弃该航点重规划
MONSTER_NAV_STUCK_FRAMES = 12          # 追击卡死判定：有移动意图但位移≈0 连续 N 帧 → 转航点模式
MONSTER_NAV_MOVE_EPS = 0.5             # 「几乎没动」的位移阈值（像素/帧）
# 卡死在墙体里的脱困搜索半径（单位=格）：刷新器只校验怪物的「中心点」不在墙内，
# 怪物身体（AABB 半宽 _size）仍可能嵌进墙体，此时 _can_move_to 对任意方向都判失败 →
# 直线滑动与 BFS 航点全部失效，怪物永久静止（用户缺陷⑦现场「240~343px 聚集不动」的真凶）。
# 脱困时按格逐环外扩找最近「身体合法」的点，只允许吸附到 _can_move_to 校验通过的位置，
# 不放宽任何碰撞判定。
MONSTER_NAV_UNSTICK_SEARCH_RING = 4

