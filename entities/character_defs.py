"""角色基础模板定义

定义 4 个可选角色：
1. initial 初始角色（免费，无技能增益，数值沿用现有玩家默认值）
2. mage    法师（购买解锁：奥术爆发技能 + 法力亲和被动）
3. knight  骑士（购买解锁：圣盾庇护技能 + 钢铁之躯/逆境反击被动）
4. assassin 刺客（购买解锁：影袭技能 + 致命一击/疾影被动）

每种角色包含：
- name: 中文显示名
- hp: 基础生命值
- defense: 基础防御力
- speed: 基础移速（像素/帧，PhysicsEngineSimple 不乘 delta_time）
- color: 渲染颜色 RGB
- price: 解锁价格（金币，0 = 初始角色免费）
- skill: 特殊技能定义（None = 无技能）
  - id: 技能标识
  - name: 技能中文名
  - cooldown: 冷却时间（秒）
  - damage_mult: 技能伤害倍率（相对武器伤害）
  - duration / damage_reduce / range / stun 等按技能类型附加字段
- passive: 被动效果定义（None = 无被动）
  - damage_mult: 攻击伤害倍率（法师法力亲和）
  - flat_reduce / low_hp_threshold / low_hp_damage_mult: 骑士钢铁之躯/逆境反击
  - crit_chance / crit_mult: 刺客致命一击

相关配置从 config.py 导入（PLAYER_SPEED/PLAYER_HP 作为初始角色基准值）。
"""

from config import PLAYER_SPEED, PLAYER_HP

# 角色定义（键 = 角色 id，跨层契约：DB/界面/联机共用，禁改键名）
CHARACTERS = {
    "initial": {
        "name": "初始角色",
        "hp": PLAYER_HP,
        "defense": 0,
        "speed": PLAYER_SPEED,
        "color": (80, 180, 255),
        "price": 0,
        "skill": None,
        "passive": None,
    },
    "mage": {
        "name": "法师",
        "hp": 80,
        "defense": 0,
        "speed": 3.6,
        "color": (160, 100, 255),
        "price": 1000,
        "skill": {
            "id": "arcane_blast",
            "name": "奥术爆发",
            "cooldown": 8.0,
            "damage_mult": 2.5,
            "desc": "释放穿透能量波，造成 250% 武器伤害",
        },
        "passive": {
            "damage_mult": 1.2,  # 法力亲和：攻击伤害 +20%
        },
    },
    "knight": {
        "name": "骑士",
        "hp": 150,
        "defense": 10,
        "speed": 3.4,
        "color": (180, 200, 220),
        "price": 1500,
        "skill": {
            "id": "holy_shield",
            "name": "圣盾庇护",
            "cooldown": 12.0,
            "duration": 3.0,
            "damage_reduce": 0.7,
            "desc": "3 秒圣盾护体，受到的伤害减少 70%",
        },
        "passive": {
            "flat_reduce": 5,            # 钢铁之躯：受到伤害额外 -5
            "low_hp_threshold": 0.3,     # 逆境反击：血量低于 30% 时触发
            "low_hp_damage_mult": 1.3,   # 逆境反击：攻击伤害 +30%
        },
    },
    "assassin": {
        "name": "刺客",
        "hp": 100,
        "defense": 0,
        "speed": 4.8,
        "color": (200, 60, 60),
        "price": 2000,
        "skill": {
            "id": "shadow_strike",
            "name": "影袭",
            "cooldown": 6.0,
            "range": 250,
            "damage_mult": 1.5,
            "stun": 1.0,
            "desc": "瞬移到鼠标方向落点，对落点范围敌人造成 150% 伤害并眩晕 1 秒",
        },
        "passive": {
            "crit_chance": 0.15,  # 致命一击：15% 概率暴击
            "crit_mult": 2.0,     # 致命一击：2 倍伤害
        },
    },
}

# 角色展示顺序（初始角色在前，后续角色按解锁难度递增）
CHARACTER_ORDER = ["initial", "mage", "knight", "assassin"]