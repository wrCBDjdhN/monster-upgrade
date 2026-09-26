"""阶段5 祝福词条定义（纯数据层，18 条）

设计口径（与 entities/effects_defs.py 的 passive 类装备词条保持一致）：
- 平铺加值（直接加到绝对值）：``max_hp`` / ``regen`` / ``defense`` / ``shield``
- 比率加值（0.1 = +10%）：``speed``（移动速度）/ ``damage``（伤害倍率）
  / ``atk_speed``（武器攻速倍率，祝福专有）/ ``crit_chance`` / ``lifesteal`` / ``thorns``
- ``weight``：随机池权重，数值越大越常见（``game/blessings.py`` 的加权抽取消费）
- ``stack``：是否允许重复持有；``False`` 时已持有则 ``BlessingState.add`` 返回 False
- ``color``：祝福面板卡片主色（实心填充，禁止线框）

同层约定：本文件只放数据，不写任何逻辑；数值调整优先改本文件，全局开关/上限改 config.py。
"""

__all__ = ["BLESSINGS", "BLESSING_FIELDS", "BLESSING_RATIO_FIELDS", "get_blessing"]


# 参与属性聚合的字段全集（顺序即 ``BlessingState.aggregate()`` 汇总与面板展示顺序）
BLESSING_FIELDS: tuple[str, ...] = (
    "max_hp",      # 生命上限平铺加值
    "regen",       # 每秒回血平铺加值
    "defense",     # 护甲平铺加值
    "shield",      # 选择时一次性获得的护盾（不参与持续重算）
    "speed",       # 移动速度比率
    "atk_speed",   # 武器攻速比率
    "damage",      # 武器伤害比率
    "crit_chance", # 暴击率比率
    "lifesteal",   # 吸血比率
    "thorns",      # 反伤比率
)

# 其中按「比率」参与乘算的字段（其余字段按平铺加值处理）
BLESSING_RATIO_FIELDS: tuple[str, ...] = ("speed", "atk_speed", "damage", "crit_chance", "lifesteal", "thorns")


# 18 条祝福：键为 blessing_id（GameState.blessings / PLAYER_SNAPSHOT.stats 的契约键，禁改名）
BLESSINGS: dict[str, dict] = {
    # ── 生存系（生命 / 回血）──
    "titan_blood": {
        "name": "泰坦之血",
        "desc": "生命上限 +45",
        "max_hp": 45,
        "weight": 6, "stack": False, "color": (198, 66, 66),
    },
    "blood_pact": {
        "name": "血契",
        "desc": "生命上限 +20，每秒回血 +0.5",
        "max_hp": 20, "regen": 0.5,
        "weight": 10, "stack": True, "color": (176, 58, 92),
    },
    "life_bloom": {
        "name": "生命绽放",
        "desc": "每秒回血 +1.2",
        "regen": 1.2,
        "weight": 8, "stack": True, "color": (104, 178, 96),
    },
    "aegis_will": {
        "name": "守护意志",
        "desc": "立即获得 20 点护盾",
        "shield": 20,
        "weight": 7, "stack": True, "color": (86, 150, 210),
    },
    # ── 防御系 ──
    "iron_will": {
        "name": "钢铁意志",
        "desc": "护甲 +4",
        "defense": 4,
        "weight": 10, "stack": True, "color": (128, 140, 156),
    },
    "bulwark": {
        "name": "壁垒",
        "desc": "护甲 +9",
        "defense": 9,
        "weight": 6, "stack": False, "color": (98, 112, 132),
    },
    "thorn_soul": {
        "name": "荆棘之魂",
        "desc": "反伤 +10%",
        "thorns": 0.10,
        "weight": 8, "stack": True, "color": (150, 122, 70),
    },
    "molten_core": {
        "name": "熔核",
        "desc": "反伤 +18%，伤害倍率 +8%",
        "thorns": 0.18, "damage": 0.08,
        "weight": 4, "stack": False, "color": (206, 106, 40),
    },
    # ── 输出系（攻速 / 伤害 / 暴击 / 吸血）──
    "swift_hands": {
        "name": "疾手",
        "desc": "武器攻速 +10%",
        "atk_speed": 0.10,
        "weight": 10, "stack": True, "color": (222, 178, 62),
    },
    "war_cry": {
        "name": "战吼",
        "desc": "伤害倍率 +12%",
        "damage": 0.12,
        "weight": 9, "stack": True, "color": (198, 92, 44),
    },
    "berserker": {
        "name": "狂战士",
        "desc": "伤害倍率 +20%",
        "damage": 0.20,
        "weight": 5, "stack": False, "color": (188, 48, 48),
    },
    "keen_eye": {
        "name": "锐利之眼",
        "desc": "暴击率 +6%",
        "crit_chance": 0.06,
        "weight": 9, "stack": True, "color": (240, 200, 96),
    },
    "executioner": {
        "name": "斩首者",
        "desc": "暴击率 +12%，伤害倍率 +8%",
        "crit_chance": 0.12, "damage": 0.08,
        "weight": 5, "stack": False, "color": (214, 60, 40),
    },
    "hunters_mark": {
        "name": "猎手印记",
        "desc": "暴击率 +8%，武器攻速 +6%",
        "crit_chance": 0.08, "atk_speed": 0.06,
        "weight": 6, "stack": True, "color": (236, 160, 60),
    },
    "vampire_fang": {
        "name": "吸血獠牙",
        "desc": "吸血 +4%",
        "lifesteal": 0.04,
        "weight": 8, "stack": True, "color": (156, 44, 108),
    },
    "deep_blood": {
        "name": "深血",
        "desc": "吸血 +7%，每秒回血 +0.4",
        "lifesteal": 0.07, "regen": 0.4,
        "weight": 5, "stack": True, "color": (132, 36, 88),
    },
    # ── 机动系 ──
    "forest_walk": {
        "name": "林中疾行",
        "desc": "移动速度 +10%",
        "speed": 0.10,
        "weight": 9, "stack": True, "color": (96, 176, 128),
    },
    "ghost_step": {
        "name": "鬼步",
        "desc": "移动速度 +18%，暴击率 +4%",
        "speed": 0.18, "crit_chance": 0.04,
        "weight": 6, "stack": False, "color": (118, 132, 190),
    },
}


def get_blessing(blessing_id: str) -> dict:
    """按 blessing_id 取祝福定义；未知 id 返回空 dict（调用方须容忍空，勿直接索引）"""
    return BLESSINGS.get(blessing_id, {})
