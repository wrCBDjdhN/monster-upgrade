"""怪物注册表 —— 所有怪物的数值、渲染、掉落配置集中定义。

新增怪物只需在此文件添加一条 MONSTER_CONFIGS 条目 + 一条 MONSTER_METADATA 条目，
无需修改 monsters.py / entity_callbacks.py / monster_utils.py / game_view.py。
"""

# 骷髅/BOSS骷髅弹丸复用基础弹丸速度与尺寸（与 config.py 保持一致，避免数值漂移）
from config import PROJECTILE_SPEED, PROJECTILE_SIZE

# ─────────────────────────── 怪物组合刷新配置 ───────────────────────────
# 定义每种主题下怪物的组合刷新规则
# 组合类型：normal=普通组合, elite=精英组合, swarm=虫群组合, boss_support=BOSS支援
MONSTER_COMPOSITIONS = {
    "forest": [
        # 普通组合：2-3只僵尸 + 1-2只骷髅
        {"type": "normal", "monsters": [("Zombie", 2, 3), ("Skeleton", 1, 2)], "weight": 60},
        # 精英组合：1只精英僵尸 + 2只骷髅
        {"type": "elite", "monsters": [("Zombie", 1, 1), ("Skeleton", 2, 2)], "weight": 30},
        # 虫群组合：4-5只骷髅
        {"type": "swarm", "monsters": [("Skeleton", 4, 5)], "weight": 10},
    ],
    "desert": [
        # 普通组合：1-2只近战木乃伊 + 1-2只远程木乃伊
        {"type": "normal", "monsters": [("MummyMelee", 1, 2), ("MummyRanged", 1, 2)], "weight": 50},
        # 骆驼护卫：1只骆驼 + 2只木乃伊
        {"type": "elite", "monsters": [("Camel", 1, 1), ("MummyMelee", 1, 2), ("MummyRanged", 1, 2)], "weight": 30},
        # 沙漠风暴：3只骆驼
        {"type": "swarm", "monsters": [("Camel", 3, 3)], "weight": 20},
    ],
    "space": [
        # 普通组合：1只狙击兵 + 2只突击兵
        {"type": "normal", "monsters": [("Sniper", 1, 1), ("Assault", 2, 2)], "weight": 40},
        # 土匪小队：3-5只土匪（已内置group_count）
        {"type": "swarm", "monsters": [("Bandit", 3, 5)], "weight": 30},
        # 火箭部队：1只火箭兵 + 2只突击兵
        {"type": "elite", "monsters": [("RocketTroop", 1, 1), ("Assault", 2, 2)], "weight": 30},
    ],
}

# BOSS召唤配置：定义每个BOSS可以召唤的怪物类型和数量
BOSS_SUMMON_CONFIG = {
    "BossZombie": {
        "summons": [("Zombie", 2, 3)],  # 召唤2-3只僵尸
        "cooldown": 15.0,  # 15秒冷却
        "phase_thresholds": [0.6, 0.3],  # 血量60%和30%时触发召唤
    },
    "BossSkeleton": {
        "summons": [("Skeleton", 3, 4)],  # 召唤3-4只骷髅
        "cooldown": 12.0,
        "phase_thresholds": [0.6, 0.3],
    },
    "BossMummy": {
        "summons": [("MummyMelee", 2, 2), ("MummyRanged", 2, 2)],  # 召唤2近战+2远程木乃伊
        "cooldown": 18.0,
        "phase_thresholds": [0.6, 0.3],
    },
    "BossSpace": {
        "summons": [("Sniper", 1, 2), ("Assault", 2, 3), ("RocketTroop", 1, 1)],  # 召唤混合部队
        "cooldown": 20.0,
        "phase_thresholds": [0.7, 0.4, 0.1],  # 三次召唤
    },
}

# BOSS 技能配置：每个 BOSS 拥有 2 个主动技能
# id: 唯一标识; name: 中文名; cooldown: 冷却秒数; damage_mult: 伤害倍率;
# range: 技能范围; type: 技能类型(cone/aoe/projectile/missile)
# stun: 眩晕时长(秒); aoe_radius: AOE 半径; projectile_count: 弹幕数量
BOSS_SKILLS = {
    "BossZombie": [
        {
            "id": "flame_charge", "name": "烈焰冲击", "cooldown": 8.0,
            "damage_mult": 1.5, "range": 200, "type": "cone",
            "description": "向前方扇形范围发射火焰弹幕",
        },
        {
            "id": "zombie_roar", "name": "僵尸咆哮", "cooldown": 15.0,
            "damage_mult": 0, "range": 120, "type": "aoe",
            "stun": 2.0, "aoe_radius": 120,
            "description": "咆哮眩晕周围玩家 2 秒",
        },
    ],
    "BossSkeleton": [
        {
            "id": "skeleton_rain", "name": "骷髅箭雨", "cooldown": 10.0,
            "damage_mult": 1.2, "range": 350, "type": "aoe",
            "aoe_radius": 80, "projectile_count": 8,
            "description": "在目标位置召唤 8 支箭从天而降",
        },
        {
            "id": "frost_nova", "name": "冰冻新星", "cooldown": 18.0,
            "damage_mult": 1.0, "range": 150, "type": "aoe",
            "aoe_radius": 150, "debuff_id": "freeze", "stun": 1.5,
            "description": "以自身为中心释放冰冻波",
        },
    ],
    "BossMummy": [
        {
            "id": "poison_fog", "name": "毒雾弥漫", "cooldown": 12.0,
            "damage_mult": 0.8, "range": 180, "type": "aoe",
            "aoe_radius": 180, "debuff_id": "poison",
            "description": "释放大范围毒雾区域，持续中毒",
        },
        {
            "id": "mummy_grab", "name": "木乃伊缠绕", "cooldown": 20.0,
            "damage_mult": 1.3, "range": 100, "type": "aoe",
            "stun": 2.5, "pull": True,
            "description": "拉近玩家并眩晕 2.5 秒",
        },
    ],
    "BossSpace": [
        {
            "id": "laser_sweep", "name": "激光扫射", "cooldown": 10.0,
            "damage_mult": 2.0, "range": 400, "type": "cone",
            "description": "发射旋转激光（扇形范围持续伤害）",
        },
        {
            "id": "missile_barrage", "name": "导弹齐射", "cooldown": 16.0,
            "damage_mult": 1.5, "range": 350, "type": "missile",
            "projectile_count": 3,
            "description": "向玩家位置发射 3 枚追踪导弹",
        },
    ],
}

# 技能提示配置：定义每种怪物使用技能时的视觉提示
SKILL_PROMPT_CONFIG = {
    "Zombie": {
        "skills": [
            {"name": "腐烂光环", "color": (100, 180, 60), "duration": 0.5, "radius": 50},
            {"name": "狂暴", "color": (255, 50, 50), "duration": 0.8, "radius": 30},
        ],
    },
    "Skeleton": {
        "skills": [
            {"name": "骨盾", "color": (200, 200, 220), "duration": 0.6, "radius": 25},
            {"name": "骨矛投掷", "color": (180, 180, 200), "duration": 0.4, "radius": 20},
        ],
    },
    "MummyMelee": {
        "skills": [
            {"name": "毒雾释放", "color": (150, 200, 100), "duration": 0.7, "radius": 80},
            {"name": "木乃伊缠绕", "color": (180, 160, 100), "duration": 0.5, "radius": 40},
        ],
    },
    "MummyRanged": {
        "skills": [
            {"name": "治愈祷言", "color": (100, 255, 100), "duration": 0.8, "radius": 30},
            {"name": "诅咒标记", "color": (200, 50, 200), "duration": 0.6, "radius": 35},
        ],
    },
    "Camel": {
        "skills": [
            {"name": "沙尘暴", "color": (200, 180, 100), "duration": 1.0, "radius": 100},
            {"name": "储水", "color": (100, 150, 255), "duration": 0.6, "radius": 25},
        ],
    },
    "Sniper": {
        "skills": [
            {"name": "激光瞄准", "color": (255, 50, 50), "duration": 0.3, "radius": 15},
            {"name": "战术撤退", "color": (150, 170, 200), "duration": 0.5, "radius": 20},
        ],
    },
    "Bandit": {
        "skills": [
            {"name": "投掷匕首", "color": (180, 140, 110), "duration": 0.4, "radius": 20},
            {"name": "群体呼叫", "color": (255, 200, 100), "duration": 0.8, "radius": 60},
        ],
    },
    "RocketTroop": {
        "skills": [
            {"name": "追踪导弹", "color": (255, 100, 50), "duration": 0.6, "radius": 25},
            {"name": "弹幕射击", "color": (255, 150, 50), "duration": 0.8, "radius": 80},
        ],
    },
    "Assault": {
        "skills": [
            {"name": "冲锋", "color": (120, 130, 150), "duration": 0.5, "radius": 30},
            {"name": "手雷投掷", "color": (200, 100, 50), "duration": 0.7, "radius": 60},
        ],
    },
}

# ─────────────────────────── 近战怪物数值表 ───────────────────────────
# 键名 = 怪物类名（与 monsters.py 中的类一一对应）
# melee 怪物共用字段：hp, damage, speed, attack_delay, size, color, aggro_range
# 可选字段：debuff_id, is_boss, required_weapon_level, base_name（Boss 继承用）

MELEE_MONSTERS = {
    "Zombie": {
        "hp": 30, "damage": 5, "speed": 60,
        "attack_delay": 1.0, "size": 22,
        "color": (80, 160, 60), "aggro_range": 250,
    },
    "MummyMelee": {
        "hp": 45, "damage": 8, "speed": 50,
        "attack_delay": 1.2, "size": 24,
        "color": (210, 190, 120), "aggro_range": 260,
        "debuff_id": "poison",
    },
    "BossZombie": {
        "hp": 30 * 8, "damage": 5 * 4, "speed": int(60 * 0.7),
        "attack_delay": 1.0, "size": 22,
        "color": (80, 160, 60), "aggro_range": 250,
        "debuff_id": "burn", "is_boss": True, "required_weapon_level": 5,
    },
    "BossMummy": {
        "hp": 45 * 8, "damage": 8 * 4, "speed": int(50 * 0.7),
        "attack_delay": 1.2, "size": 24,
        "color": (210, 190, 120), "aggro_range": 260,
        "debuff_id": "poison", "is_boss": True, "required_weapon_level": 5,
    },
    "Assault": {
        "hp": 60, "damage": 10, "speed": 55,
        "attack_delay": 1.0, "size": 24,
        "color": (70, 80, 100), "aggro_range": 280,
    },
}

# ─────────────────────────── 远程怪物数值表 ───────────────────────────
# 远程怪物额外字段：proj_speed, proj_size, proj_color（弹丸颜色默认与怪物颜色相同）

RANGED_MONSTERS = {
    "Skeleton": {
        "hp": 20, "damage": 4, "speed": 40,
        "attack_delay": 1.8, "size": 20,
        "color": (220, 220, 200), "aggro_range": 300,
        # 弹丸参数：速度/尺寸与原 PROJECTILE_SPEED/PROJECTILE_SIZE 一致，颜色为弹丸默认色
        "proj_speed": PROJECTILE_SPEED, "proj_size": PROJECTILE_SIZE, "proj_color": (255, 100, 50),
    },
    "MummyRanged": {
        "hp": 35, "damage": 6, "speed": 45,
        "attack_delay": 1.8, "size": 24,
        "color": (225, 205, 140), "aggro_range": 320,
        "proj_speed": 350, "proj_size": 6,
        "proj_color": (225, 205, 140),
        "debuff_id": "poison",
    },
    "Camel": {
        "hp": 80, "damage": 5, "speed": 70,
        "attack_delay": 1.5, "size": 28,
        "color": (180, 120, 60), "aggro_range": 280,
        "proj_speed": 300, "proj_size": 8,
        "proj_color": (180, 120, 60),
    },
    "BossSkeleton": {
        "hp": 20 * 8, "damage": 4 * 4, "speed": int(40 * 0.7),
        "attack_delay": 1.8, "size": 20,
        "color": (220, 220, 200), "aggro_range": 300,
        # BOSS 骷髅弹丸：速度/尺寸与原 PROJECTILE_SPEED/PROJECTILE_SIZE 一致，颜色同怪物体色
        "proj_speed": PROJECTILE_SPEED, "proj_size": PROJECTILE_SIZE, "proj_color": (220, 220, 200),
        "debuff_id": "freeze", "is_boss": True, "required_weapon_level": 5,
    },
    "Sniper": {
        "hp": 20, "damage": 15, "speed": 35,
        "attack_delay": 2.5, "size": 20,
        "color": (100, 120, 160), "aggro_range": 400,
        "proj_speed": 600, "proj_size": 4,
        "proj_color": (100, 120, 160),
    },
    "Bandit": {
        "hp": 15, "damage": 4, "speed": 60,
        "attack_delay": 1.2, "size": 18,
        "color": (140, 100, 80), "aggro_range": 300,
        "proj_speed": 350, "proj_size": 5,
        "proj_color": (140, 100, 80),
    },
    "RocketTroop": {
        "hp": 55, "damage": 12, "speed": 40,
        "attack_delay": 2.0, "size": 24,
        "color": (120, 60, 60), "aggro_range": 350,
        "proj_speed": 300, "proj_size": 8,
        "proj_color": (120, 60, 60),
    },
    "BossSpace": {
        "hp": 280 * 8, "damage": 25 * 4, "speed": int(30 * 0.7),
        "attack_delay": 1.8, "size": 30,
        "color": (200, 50, 50), "aggro_range": 400,
        "proj_speed": 500, "proj_size": 5,
        "proj_color": (255, 80, 30),
        "debuff_id": "burn", "is_boss": True, "required_weapon_level": 5,
    },
}

# 合并为统一怪物表（Skeleton 同时存在于近战和远程，远程覆盖）
MONSTER_CONFIGS: dict[str, dict] = {**MELEE_MONSTERS, **RANGED_MONSTERS}


# ─────────────────────────── 怪物元数据（渲染/掉落/行为） ───────────────────────────
# weapon_color:  怪物手持武器颜色（渲染用）
# loot_key:      对应 config.py 中掉落表变量名的前缀（如 "zombie" → ZOMBIE_LOOT_TABLE）
# death_color:   死亡粒子颜色
# weapon_pool:   可装备的武器 ID 列表（用于 monster_utils.assign_monster_weapon）
# armor_key:     怪物护甲来源（"desert"= 沙漠木乃伊系，"space"= 航天系，None= 无特殊）
# group_count:   成群刷新数量范围（仅土匪使用）

MONSTER_METADATA: dict[str, dict] = {
    "Zombie": {
        "weapon_color": (200, 200, 200),
        "loot_key": "zombie",
        "death_color": (80, 160, 60),
        "weapon_pool": ["wood_sword", "iron_sword", "stone_mace"],
    },
    "Skeleton": {
        "weapon_color": (200, 200, 200),
        "loot_key": "skeleton",
        "death_color": (220, 220, 200),
        "weapon_pool": ["short_bow", "long_bow"],
    },
    "MummyMelee": {
        "weapon_color": (180, 160, 100),
        "loot_key": "mummy",
        "death_color": (210, 190, 120),
        "weapon_pool": ["cursed_scimitar"],  # 近战木乃伊只用近战武器
        "armor_key": "desert",
    },
    "MummyRanged": {
        "weapon_color": (180, 160, 100),
        "loot_key": "mummy",
        "death_color": (225, 205, 140),
        "weapon_pool": ["scepter"],  # 远程木乃伊只用远程武器
        "armor_key": "desert",
    },
    "Camel": {
        "weapon_color": (180, 120, 60),
        "loot_key": "camel",
        "death_color": (180, 120, 60),
        "weapon_pool": [],
    },
    "BossZombie": {
        "weapon_color": (255, 100, 30),
        "loot_key": "boss",
        "death_color": (255, 120, 40),
        "weapon_pool": ["flame_blade", "storm_hammer", "frost_blade"],  # 近战BOSS只用近战武器
    },
    "BossSkeleton": {
        "weapon_color": (100, 140, 255),
        "loot_key": "boss",
        "death_color": (120, 160, 255),
        "weapon_pool": ["long_bow", "fire_staff"],  # 远程BOSS只用远程武器
    },
    "BossMummy": {
        "weapon_color": (180, 160, 100),
        "loot_key": "boss",
        "death_color": (210, 190, 120),
        "weapon_pool": ["cursed_scimitar"],  # 近战BOSS只用近战武器
        "armor_key": "desert",
    },
    "Sniper": {
        "weapon_color": (150, 170, 200),
        "loot_key": "sniper",
        "death_color": (100, 120, 160),
        "weapon_pool": ["sniper", "rifle"],
    },
    "Assault": {
        "weapon_color": (120, 130, 150),
        "loot_key": "assault",
        "death_color": (70, 80, 100),
        "weapon_pool": ["iron_sword", "stone_mace"],
    },
    "Bandit": {
        "weapon_color": (180, 140, 110),
        "loot_key": "bandit",
        "death_color": (140, 100, 80),
        "weapon_pool": ["pistol", "rifle"],
        "group_count": (3, 5),
    },
    "RocketTroop": {
        "weapon_color": (180, 80, 80),
        "loot_key": "rocket_troop",
        "death_color": (120, 60, 60),
        "weapon_pool": ["rocket_launcher", "laser_gun"],
    },
    "BossSpace": {
        "weapon_color": (255, 80, 30),
        "loot_key": "space_boss",
        "death_color": (255, 100, 50),
        "weapon_pool": ["laser_gun", "rocket_launcher"],
    },
}


# ─────────────────────────── 辅助函数 ───────────────────────────

def get_all_monster_class_names() -> list[str]:
    """返回所有怪物类名列表（用于 game_view.py 自动映射）"""
    return list(MONSTER_CONFIGS.keys())


def is_melee_monster(class_name: str) -> bool:
    """判断怪物是否为近战类型"""
    return class_name in MELEE_MONSTERS


def is_ranged_monster(class_name: str) -> bool:
    """判断怪物是否为远程类型"""
    return class_name in RANGED_MONSTERS


def get_monster_config(class_name: str) -> dict:
    """获取怪物数值配置"""
    return MONSTER_CONFIGS[class_name]


def get_monster_metadata(class_name: str) -> dict:
    """获取怪物元数据（渲染/掉落/行为）"""
    return MONSTER_METADATA[class_name]


def get_boss_config(base_name: str) -> dict:
    """获取 Boss 怪物的数值配置（自动应用 BOSS 倍率）"""
    base = MONSTER_CONFIGS[base_name]
    return {
        **base,
        "hp": int(base["hp"] * 8),
        "damage": int(base["damage"] * 4),
        "speed": int(base["speed"] * 0.7),
        "is_boss": True,
        "required_weapon_level": 5,
    }
