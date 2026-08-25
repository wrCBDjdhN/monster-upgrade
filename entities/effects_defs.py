"""物品附加效果系统定义
Lv.5 以上的武器/装备随机附带效果：
- passive 类：装备（头盔/护甲）穿戴时生效（生命上限、持续回血、移速、防御、伤害、吸血、荆棘、暴击）
- debuff 类：武器攻击命中时施加（中毒、燃烧、冰冻、减速、眩晕、易伤、破甲、流血、虹吸）
效果数量随等级提升：Lv.5-10 一个、Lv.10-20 两个、Lv.20-30 三个、Lv.30-40 四个、Lv.40 以上五个

效果分级系统（锻造改造）：
- 每个效果分 1-5 级，等级越高数值越强（见 EFFECT_LEVELS）
- 效果等级随物品等级派生：Lv.1-9→1、Lv.10-19→2、Lv.20-29→3、Lv.30-39→4、Lv.40+→5
- effects 列表元素格式升级为 "id:level"（如 "poison:3"），兼容旧纯 id（视为等级1）
"""
import random

# ── 效果池定义 ──
# passive 类（装备）：使用 value 字段（数值加成）
# debuff 类（武器）：使用 value(每秒伤害/百分比) + duration(持续秒数)，部分含 slow/stun/vuln 等特殊字段
EFFECTS = {
    # ── passive 装备效果（穿戴时永久生效）──
    "max_hp": {
        "name": "生命强化",
        "type": "passive",
        "value": 25,
        "desc": "生命上限+25",
    },
    "regen": {
        "name": "自然回复",
        "type": "passive",
        "value": 1,
        "desc": "每秒回复1点生命",
    },
    "speed": {
        "name": "疾风之足",
        "type": "passive",
        "value": 0.30,
        "desc": "移动速度+30%",
    },
    "defense": {
        "name": "铁壁",
        "type": "passive",
        "value": 3,
        "desc": "防御+3",
    },
    "damage": {
        "name": "力量",
        "type": "passive",
        "value": 0.08,
        "desc": "攻击伤害+8%",
    },
    "lifesteal": {
        "name": "吸血",
        "type": "passive",
        "value": 0.03,
        "desc": "攻击命中回复造成伤害的3%",
    },
    "thorns": {
        "name": "荆棘",
        "type": "passive",
        "value": 0.10,
        "desc": "被近战攻击时反弹10%伤害",
    },
    "crit_chance": {
        "name": "致命",
        "type": "passive",
        "value": 0.05,
        "desc": "暴击率+5%",
    },
    # ── debuff 武器效果（攻击命中施加给目标）──
    "poison": {
        "name": "剧毒",
        "type": "debuff",
        "value": 2,          # 每秒伤害
        "duration": 3.0,     # 持续秒数
        "desc": "攻击使目标中毒，3秒内每秒受到2点伤害",
        "color": (80, 200, 80),
    },
    "burn": {
        "name": "燃烧",
        "type": "debuff",
        "value": 4,
        "duration": 3.0,
        "desc": "攻击点燃目标，3秒内每秒受到4点伤害",
        "color": (255, 120, 40),
    },
    "freeze": {
        "name": "冰冻",
        "type": "debuff",
        "value": 0,
        "duration": 2.0,
        "slow": 0.5,         # 减速50%
        "desc": "攻击冻结目标，2秒内减速50%",
        "color": (100, 180, 255),
    },
    "slow": {
        "name": "减速",
        "type": "debuff",
        "value": 0,
        "duration": 3.0,
        "slow": 0.25,        # 减速25%
        "desc": "攻击使目标减速25%，持续3秒",
        "color": (140, 140, 160),
    },
    "stun": {
        "name": "眩晕",
        "type": "debuff",
        "value": 0,
        "duration": 1.0,
        "stun": True,        # 眩晕：无法移动和攻击
        "desc": "攻击眩晕目标1秒",
        "color": (255, 255, 100),
    },
    "vulnerable": {
        "name": "易伤",
        "type": "debuff",
        "value": 0.10,       # 受到伤害增加10%
        "duration": 3.0,
        "vulnerable": 0.10,  # 受击时伤害增幅比例
        "desc": "攻击使目标易伤，3秒内受到的所有伤害增加10%",
        "color": (255, 100, 100),
    },
    "armor_break": {
        "name": "破甲",
        "type": "debuff",
        "value": 3,          # 减少防御3点
        "duration": 3.0,
        "armor_break": 3,    # 减少目标防御值
        "desc": "攻击破甲目标，3秒内防御减少3点",
        "color": (200, 160, 100),
    },
    "bleed": {
        "name": "流血",
        "type": "debuff",
        "value": 3,          # 每秒伤害
        "duration": 3.0,
        "stacks": True,      # 可叠加层数（最多3层）
        "desc": "攻击使目标流血，3秒内每秒受到3点伤害（可叠加，最多3层）",
        "color": (180, 30, 30),
    },
    "leech": {
        "name": "虹吸",
        "type": "debuff",
        "value": 0.08,       # 回复造成伤害的8%
        "duration": 0,       # 瞬发效果，无持续时间
        "desc": "攻击命中回复造成伤害的8%",
        "color": (160, 60, 180),
    },
}

# 随机抽取池（按用途区分：武器 roll debuff，装备 roll passive）
PASSIVE_POOL = ["max_hp", "regen", "speed", "defense", "damage", "lifesteal", "thorns", "crit_chance"]
DEBUFF_POOL = ["poison", "burn", "freeze", "slow", "stun", "vulnerable", "armor_break", "bleed", "leech"]
EFFECT_POOL = PASSIVE_POOL + DEBUFF_POOL

# ── 效果分级表（锻造改造：等级1-5，数值非线性增强）──
# 每个效果可覆盖 value（每秒伤害/生命/回血/移速/百分比）、duration、slow、armor_break 等字段
EFFECT_LEVELS = {
    # debuff 武器效果
    "poison": {"value": (2, 3, 5, 8, 12)},
    "burn": {"value": (4, 6, 9, 13, 18)},
    "freeze": {"slow": (0.5, 0.6, 0.7, 0.8, 0.9)},
    "slow": {"slow": (0.25, 0.30, 0.40, 0.50, 0.60)},
    "stun": {"duration": (1.0, 1.0, 1.2, 1.4, 1.6)},
    "vulnerable": {"value": (0.10, 0.15, 0.20, 0.25, 0.30), "vulnerable": (0.10, 0.15, 0.20, 0.25, 0.30)},
    "armor_break": {"value": (3, 5, 8, 12, 16), "armor_break": (3, 5, 8, 12, 16)},
    "bleed": {"value": (3, 4, 6, 8, 10)},
    "leech": {"value": (0.08, 0.12, 0.16, 0.20, 0.25)},
    # passive 装备效果
    "max_hp": {"value": (25, 40, 60, 85, 115)},
    "regen": {"value": (1, 2, 3, 5, 7)},
    "speed": {"value": (0.30, 0.36, 0.43, 0.50, 0.60)},
    "defense": {"value": (3, 5, 8, 12, 16)},
    "damage": {"value": (0.08, 0.12, 0.16, 0.20, 0.25)},
    "lifesteal": {"value": (0.03, 0.05, 0.07, 0.10, 0.13)},
    "thorns": {"value": (0.10, 0.15, 0.20, 0.25, 0.30)},
    "crit_chance": {"value": (0.05, 0.08, 0.11, 0.14, 0.17)},
}

EFFECT_LEVEL_MAX = 5  # 效果最高等级（锻造同名合并最多提升到此）


def effect_level_for_item_level(item_level: int) -> int:
    """物品等级 -> 效果等级：Lv.1-9→1、Lv.10-19→2、Lv.20-29→3、Lv.30-39→4、Lv.40+→5"""
    if item_level < 10:
        return 1
    if item_level < 20:
        return 2
    if item_level < 30:
        return 3
    if item_level < 40:
        return 4
    return 5


def parse_effect_item(e) -> tuple:
    """解析效果元素：'poison:3' -> ('poison', 3)；纯 id 'poison' -> ('poison', 1)"""
    s = str(e).strip()
    if ":" in s:
        eid, _, lvl = s.partition(":")
        try:
            return eid, max(1, min(EFFECT_LEVEL_MAX, int(lvl)))
        except ValueError:
            return eid, 1
    return s, 1


def effect_params(effect_id: str, level: int = 1) -> dict:
    """获取某等级下的效果参数：基础定义合并分级覆盖（未分级字段沿用基础值）"""
    base = dict(EFFECTS.get(effect_id, {}))
    lvl = max(1, min(EFFECT_LEVEL_MAX, int(level or 1)))
    leveled = EFFECT_LEVELS.get(effect_id, {})
    for k, vals in leveled.items():
        base[k] = vals[lvl - 1]
    return base


def refresh_effect_levels(effects, item_level: int) -> list:
    """效果等级刷新（升级/锻造后调用）：等级不低于物品等级对应等级（只升不降），
    返回统一 "id:level" 格式列表"""
    base = effect_level_for_item_level(item_level)
    out = []
    for e in effects or []:
        eid, lvl = parse_effect_item(e)
        out.append(f"{eid}:{max(lvl, base)}")
    return out


def effects_count_for_level(level: int) -> int:
    """根据物品等级返回应有效果数量：
    Lv.5-10 一个、Lv.10-20 两个、Lv.20-30 三个、Lv.30-40 四个、Lv.40 以上五个"""
    if level < 5:
        return 0
    if level < 10:
        return 1
    if level < 20:
        return 2
    if level < 30:
        return 3
    if level < 40:
        return 4
    return 5


def roll_effects(level: int, existing=None) -> list:
    """按等级生成效果列表（在已有效果基础上补齐，效果可重复）
    补齐的效果元素为 "id:等级" 格式（等级由物品等级派生），已有效果元素原样保留
    注意：此函数混合抽取，仅用于兼容旧调用；新代码应使用 roll_effects_for_slot"""
    count = effects_count_for_level(level)
    effects = list(existing) if existing else []
    base_lvl = effect_level_for_item_level(level)
    while len(effects) < count:
        eid = random.choice(EFFECT_POOL)
        effects.append(f"{eid}:{base_lvl}")
    return effects


def roll_effects_for_slot(level: int, slot: str, existing=None) -> list:
    """按物品类型（slot）生成效果列表：武器只抽取 debuff，装备只抽取 passive

    slot: "weapon" / "helmet" / "armor"
    已有效果元素原样保留，新效果从对应池中随机抽取补齐
    """
    count = effects_count_for_level(level)
    effects = list(existing) if existing else []
    base_lvl = effect_level_for_item_level(level)
    pool = DEBUFF_POOL if slot == "weapon" else PASSIVE_POOL
    while len(effects) < count:
        eid = random.choice(pool)
        effects.append(f"{eid}:{base_lvl}")
    return effects


def serialize_effects(effects) -> str:
    """效果列表 -> 逗号分隔字符串（元素可为 'id' 或 'id:level'，可重复）"""
    return ",".join(effects or [])


def parse_effects(text) -> list:
    """逗号分隔字符串 -> 效果元素列表（支持 'poison:3,burn' 新旧两种格式）"""
    if not text:
        return []
    return [e for e in str(text).split(",") if e]


def effects_label(effects) -> str:
    """效果列表（元素可为 'id' 或 'id:level'）-> 中文名展示文本（'剧毒Lv3、燃烧'），空返回'无'"""
    if not effects:
        return "无"
    parts = []
    for e in effects:
        eid, lvl = parse_effect_item(e)
        name = EFFECTS.get(eid, {}).get("name")
        if not name:
            continue
        parts.append(f"{name}Lv{lvl}" if lvl > 1 else name)
    return "、".join(parts) if parts else "无"
