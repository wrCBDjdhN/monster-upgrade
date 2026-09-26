"""怪物工具函数：武器距离查找、武器/护甲/头盔穿戴（按主题分策略）+ 被动效果应用"""

import random
from entities.equipment_defs import ARMORS, HELMETS
from entities.monster_defs import MONSTER_METADATA, SKILL_PROMPT_CONFIG
from config import (
    MONSTER_THEME_EQUIP,
    MUMMY_HEAL_SKILL_CD,
    SKILL_CD_DEFAULT,
    SKILL_COOLDOWNS,
    SKILL_DIST_NEAR,
    SKILL_VFX_COUNT_MULT,
    SKILL_VFX_LIFE_MULT,
)


def lookup_weapon_range(kind: str, name: str) -> float:
    """根据中文名查找武器攻击距离"""
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    table = MELEE_WEAPONS if kind == "melee" else RANGED_WEAPONS
    for wdef in table.values():
        if wdef["name"] == name:
            return wdef.get("range", 50 if kind == "melee" else 250)
    return 50 if kind == "melee" else 250


def _weighted_choice(pool: list[tuple[str, int]]) -> str:
    """按权重列表 [(item_id, weight), ...] 随机选取一个 item_id"""
    total = sum(w for _, w in pool)
    r = random.uniform(0, total)
    cumulative = 0
    for item_id, weight in pool:
        cumulative += weight
        if r <= cumulative:
            return item_id
    return pool[-1][0]


def _equip_dict(table: dict, item_id: str, level: int) -> dict:
    """从装备表构建怪物穿戴字典（统一格式，含 effects 字段）"""
    info = table[item_id]
    return {
        "item_id": item_id,
        "name": info["name"],
        "defense": info["defense"],
        "color": info["color"],
        "level": level,
        "effects": list(info.get("effects", [])),
    }


def _apply_passive_effects(monster, eq: dict):
    """将装备的被动效果应用到怪物属性

    支持的 passive 效果：
    - max_hp：增加怪物最大生命值并回满
    - regen：增加每秒回血量
    - speed：乘算移速加成
    - defense：增加防御值（累加到 armor/helmet 的 defense 上）
    - damage：增加攻击伤害百分比（累加到 monster.damage 基础值）
    - lifesteal：攻击吸血比例（存储供 combat.py 读取）
    - thorns：荆棘反伤比例（存储供 combat.py 读取）
    - crit_chance：暴击率（存储供 combat.py 读取）
    """
    from entities.effects_defs import parse_effect_item, effect_params
    for e in eq.get("effects", []):
        eid, elvl = parse_effect_item(e)
        pdata = effect_params(eid, elvl)
        if eid == "max_hp":
            bonus = int(pdata.get("value", 25))
            monster.max_hp += bonus
            monster.hp += bonus
        elif eid == "regen":
            monster.regen_per_sec += pdata.get("value", 1)
        elif eid == "speed":
            monster.speed *= (1.0 + pdata.get("value", 0.30))
        elif eid == "defense":
            # 防御加成：累加到装备字典的 defense 字段（take_damage 会读取）
            eq["defense"] = eq.get("defense", 0) + int(pdata.get("value", 3))
        elif eid == "damage":
            # 伤害加成：按百分比增加怪物基础伤害
            monster.damage = round(monster.damage * (1.0 + pdata.get("value", 0.08)))
        elif eid == "lifesteal":
            # 吸血：累加到怪物属性，供 combat.py 近战命中时读取
            current = getattr(monster, "equip_lifesteal", 0.0)
            monster.equip_lifesteal = current + pdata.get("value", 0.03)
        elif eid == "thorns":
            # 荆棘：累加到怪物属性，供 combat.py 近战受击时读取
            current = getattr(monster, "equip_thorns", 0.0)
            monster.equip_thorns = current + pdata.get("value", 0.10)
        elif eid == "crit_chance":
            # 暴击率：累加到怪物属性，供 combat.py 攻击时读取
            current = getattr(monster, "crit_chance", 0.0)
            monster.crit_chance = current + pdata.get("value", 0.05)


def show_skill_prompt(monster, skill_index=0):
    """显示怪物技能提示（视觉效果）
    
    Args:
        monster: 怪物对象
        skill_index: 技能索引（0=第一个技能，1=第二个技能）
    
    Returns:
        dict: 技能提示信息 {"color": tuple, "duration": float, "radius": float, "name": str}
              如果怪物没有配置技能，返回 None
    """
    class_name = monster.__class__.__name__
    config = SKILL_PROMPT_CONFIG.get(class_name)
    if not config or not config.get("skills"):
        return None
    
    # 循环选择技能
    skill = config["skills"][skill_index % len(config["skills"])]
    
    # 创建视觉提示效果
    prompt_info = {
        "color": skill["color"],
        "duration": skill["duration"],
        "radius": skill["radius"],
        "name": skill["name"],
        "x": monster.center_x,
        "y": monster.center_y,
    }
    
    # 在怪物头顶显示技能名称（临时漂浮文字）
    _create_floating_text(monster, skill["name"], skill["color"])
    
    return prompt_info


def collect_skill_nearby_players(monster, skill_index: int = 0, players=None, fallback=None):
    """按技能提示半径收集怪物附近的存活玩家（供沙尘暴/手雷投掷等范围技能使用）

    范围技能此前拿到的 nearby_players 恒为 None（调用方未传），只能命中单一目标。
    本函数把「本次技能可覆盖的玩家列表」按 SKILL_PROMPT_CONFIG 的 radius 过滤出来，
    半径取技能提示同源配置，保证判定范围与视觉提示一致。

    Args:
        monster: 怪物对象
        skill_index: 技能索引（0=第一个技能，1=第二个技能）
        players: 玩家列表（联机主机模式 = AI 目标全量；为 None 时回退到 fallback）
        fallback: players 缺省时的兜底玩家（单机路径只传入当前目标）

    Returns:
        list: 命中半径的存活玩家列表（可能为空列表，空列表时调用方回退到单目标语义）
    """
    config = SKILL_PROMPT_CONFIG.get(monster.__class__.__name__)
    if not config or not config.get("skills"):
        return []
    skill = config["skills"][skill_index % len(config["skills"])]
    radius = float(skill.get("radius", 0) or 0)
    if radius <= 0:
        return []
    if players:
        candidates = list(players)
    elif fallback is not None:
        candidates = [fallback]
    else:
        return []
    near = []
    radius_sq = radius * radius
    for p in candidates:
        if p is None:
            continue
        # 存活判断与 monster_base._select_target 同一口径（alive 优先，回退 hp>0）
        alive = getattr(p, "alive", None)
        if alive is None:
            alive = getattr(p, "hp", 1) > 0
        if not alive:
            continue
        dx = p.center_x - monster.center_x
        dy = p.center_y - monster.center_y
        if dx * dx + dy * dy <= radius_sq:  # 平方距离比较，避免开根号
            near.append(p)
    return near


def pick_skill_index(monster, dist: float) -> int | None:
    """按「距玩家距离档位 + 技能冷却」挑选本次要释放的技能下标

    档位判定取 entities/monster_defs.SKILL_PROMPT_CONFIG 中每个技能的 range_pref：
    - dist <= config.SKILL_DIST_NEAR 视为近档（near），否则远档（far）
    - 只有该档位存在候选技能才出手；候选技能仍在冷却中则本回合不出手（返回 None）

    冷却表惰性建在 monster._skill_cds（键 = 技能中文名），递减由
    update_skill_cooldowns 每帧负责；冷却时长查 config.SKILL_COOLDOWNS。

    Args:
        monster: 怪物对象
        dist: 怪物与目标玩家的距离（像素）

    Returns:
        int | None: 命中的技能下标（供 apply_skill_prompt_and_effect 使用）；
                    无技能配置 / 该档位无候选 / 候选仍在冷却中时返回 None
    """
    config = SKILL_PROMPT_CONFIG.get(monster.__class__.__name__)
    if not config or not config.get("skills"):
        return None

    # 冷却字典惰性初始化：怪物未走过 pick_skill_index（如联机快照重建）时也能安全写入
    cds = getattr(monster, "_skill_cds", None)
    if cds is None:
        cds = {}
        monster._skill_cds = cds

    pref = "near" if dist <= SKILL_DIST_NEAR else "far"
    skills = config["skills"]
    candidates = [i for i, s in enumerate(skills) if s.get("range_pref") == pref]
    if not candidates:
        return None
    # 同档位存在多个候选时随机取一个，避免固定顺序导致只会放第一个技能
    idx = random.choice(candidates)
    if cds.get(skills[idx].get("name"), 0.0) > 0:
        return None
    return idx


def update_skill_cooldowns(monster, delta_time: float) -> None:
    """逐帧递减怪物各技能冷却（与 update_skill_buffs 同批每帧调用）

    冷却字典由 pick_skill_index / apply_skill_prompt_and_effect 惰性创建；
    从未释放过技能的怪物没有该字段，直接返回。
    """
    cds = getattr(monster, "_skill_cds", None)
    if not cds:
        return
    for skill_name, remain in list(cds.items()):
        cds[skill_name] = max(0.0, remain - delta_time)


def apply_skill_prompt_and_effect(monster, skill_index=0, target=None, nearby_players=None):
    """显示技能提示 + 应用实际战斗效果 + 粒子特效（攻击时调用的统一入口）
    
    结合视觉提示与实际 debuff/buff 效果，一次调用完成三件事。
    
    Args:
        monster: 怪物对象
        skill_index: 技能索引（0=第一个技能，1=第二个技能）
        target: 目标玩家对象
        nearby_players: 附近玩家列表（范围技能用，可选）
    """
    # 接住提示信息：把 duration/radius/color 转存成渲染层可直接读取的
    # _skill_vfx_* 字段（渲染层据此画技能范围圈，timer 由 monster_base 逐帧递减），
    # 并在同一处启动该技能冷却（键 = 技能中文名，时长查 config.SKILL_COOLDOWNS）。
    # 无技能配置时 show_skill_prompt 返回 None，跳过写入，其余流程照旧。
    info = show_skill_prompt(monster, skill_index)
    if info is not None:
        monster._skill_vfx_timer = float(info["duration"])
        monster._skill_vfx_duration = float(info["duration"])
        monster._skill_vfx_radius = float(info["radius"])
        monster._skill_vfx_color = tuple(info["color"])
        skill_name = info.get("name")
        cds = getattr(monster, "_skill_cds", None)
        if cds is None:
            cds = {}
            monster._skill_cds = cds
        cds[skill_name] = SKILL_COOLDOWNS.get(skill_name, SKILL_CD_DEFAULT)
    apply_skill_effect(monster, skill_index, target=target, nearby_players=nearby_players)
    _emit_skill_vfx(monster, skill_index, target, nearby_players)


def _emit_skill_vfx(monster, skill_index: int, target=None, nearby_players=None):
    """为怪物技能释放发射粒子特效
    
    根据怪物类型和技能索引，发射对应的视觉粒子效果：
    - 投掷类技能：朝目标方向发射飞弹拖尾粒子
    - 范围类技能：在怪物周围爆发全向粒子
    - 增益类技能：在怪物身上爆发光环粒子
    - 治疗类技能：在怪物身上爆发上升绿色粒子
    """
    import math
    from game.effects import particle_system
    import arcade

    # ── 粒子放大包装（局部闭包）──
    # 技能特效统一按 config 倍率放大：数量 × SKILL_VFX_COUNT_MULT、寿命 × SKILL_VFX_LIFE_MULT。
    # 收口在这一层实现，下方各怪物分支的发射点只调 _emit / _emit_dir，
    # 调平衡只改 config，不必在 20 处调用点各写一遍倍率。
    def _emit(x, y, count, color, *, life=0.5, **kw):
        """全向粒子发射（数量与寿命按 config 倍率放大，数量取整且至少 1）"""
        particle_system.emit(x, y, max(1, int(count * SKILL_VFX_COUNT_MULT)), color,
                             life=life * SKILL_VFX_LIFE_MULT, **kw)

    def _emit_dir(x, y, count, angle, color, *, life=0.5, **kw):
        """定向粒子发射（数量与寿命同样按 config 倍率放大，数量取整且至少 1）"""
        particle_system.emit_directional(x, y, max(1, int(count * SKILL_VFX_COUNT_MULT)),
                                         angle, color,
                                         life=life * SKILL_VFX_LIFE_MULT, **kw)

    sx, sy = monster.center_x, monster.center_y
    # 计算朝向目标的方向角（用于投掷/方向性技能）
    angle = 0.0
    if target and hasattr(target, "center_x"):
        dx = target.center_x - sx
        dy = target.center_y - sy
        if dx != 0 or dy != 0:
            angle = math.degrees(math.atan2(dy, dx))
    
    class_name = monster.__class__.__name__
    
    # ── 僵尸 ──
    if class_name == "Zombie":
        if skill_index == 0:
            # 腐烂光环：绿色毒雾环绕爆发
            _emit(sx, sy, 18, (80, 200, 50),
                  speed=45, life=0.8, size=5, spread=360, gravity=15)
        else:
            # 狂暴：红色怒气向上喷发
            _emit(sx, sy, 20, (255, 60, 40),
                  speed=120, life=0.5, size=4, spread=360, gravity=-80)
    
    # ── 骷髅 ──
    elif class_name == "Skeleton":
        if skill_index == 0:
            # 骨盾：白色/淡蓝盾形光环向外扩散
            _emit(sx, sy, 22, (200, 210, 255),
                  speed=70, life=0.6, size=4, spread=360)
        else:
            # 骨矛投掷：白色飞弹拖尾朝目标方向射出
            _emit_dir(sx, sy, 14, angle, (220, 220, 255),
                      speed=250, life=0.35, size=3, spread=15)
    
    # ── 近战木乃伊 ──
    elif class_name == "MummyMelee":
        if skill_index == 0:
            # 毒雾释放：大面积绿色毒雾扩散
            _emit(sx, sy, 25, (60, 180, 30),
                  speed=55, life=1.0, size=6, spread=360, gravity=10)
        else:
            # 木乃伊缠绕：黄色绷带条状粒子朝目标方向飞出
            _emit_dir(sx, sy, 12, angle, (220, 190, 80),
                      speed=180, life=0.4, size=4, spread=25)
    
    # ── 远程木乃伊 ──
    elif class_name == "MummyRanged":
        if skill_index == 0:
            # 治愈祷言：绿色上升粒子（治疗感）
            _emit(sx, sy, 16, (80, 255, 100),
                  speed=60, life=0.7, size=4, spread=360, gravity=-120)
        else:
            # 诅咒标记：紫色暗能量朝目标飞出
            _emit_dir(sx, sy, 10, angle, (160, 50, 220),
                      speed=200, life=0.4, size=4, spread=20)
    
    # ── 骆驼 ──
    elif class_name == "Camel":
        if skill_index == 0:
            # 沙尘暴：棕色沙粒全向爆发（大范围）
            _emit(sx, sy, 30, (180, 140, 60),
                  speed=90, life=0.8, size=5, spread=360, gravity=20)
        else:
            # 储水：蓝色水滴上升粒子
            _emit(sx, sy, 14, (60, 160, 255),
                  speed=50, life=0.6, size=4, spread=360, gravity=-100)
    
    # ── 狙击兵 ──
    elif class_name == "Sniper":
        if skill_index == 0:
            # 激光瞄准：红色细束朝目标方向射出
            _emit_dir(sx, sy, 8, angle, (255, 40, 40),
                      speed=400, life=0.2, size=2, spread=5)
        else:
            # 战术撤退：蓝色加速残影向后散开
            retreat_angle = (angle + 180) % 360
            _emit_dir(sx, sy, 12, retreat_angle, (80, 160, 255),
                      speed=180, life=0.4, size=3, spread=30)
    
    # ── 土匪 ──
    elif class_name == "Bandit":
        if skill_index == 0:
            # 投掷匕首：暗色飞镖拖尾朝目标方向射出
            _emit_dir(sx, sy, 10, angle, (100, 80, 60),
                      speed=300, life=0.3, size=3, spread=10)
        else:
            # 群体呼叫：橙色信号弹向上爆发
            _emit(sx, sy, 16, (255, 160, 40),
                  speed=100, life=0.5, size=4, spread=360, gravity=-150)
    
    # ── 火箭兵 ──
    elif class_name == "RocketTroop":
        if skill_index == 0:
            # 追踪导弹：橙红色火焰拖尾朝目标方向射出
            _emit_dir(sx, sy, 16, angle, (255, 120, 30),
                      speed=280, life=0.4, size=5, spread=12)
            # 尾焰粒子
            _emit_dir(sx, sy, 8, (angle + 180) % 360, (255, 200, 80),
                      speed=120, life=0.25, size=3, spread=20)
        else:
            # 弹幕射击：多方向黄色弹丸粒子散射
            for offset_angle in [-30, -15, 0, 15, 30]:
                _emit_dir(sx, sy, 4, angle + offset_angle,
                          (255, 220, 60),
                          speed=350, life=0.25, size=3, spread=8)
    
    # ── 突击兵 ──
    elif class_name == "Assault":
        if skill_index == 0:
            # 冲锋：蓝白色速度线朝目标方向喷射
            _emit_dir(sx, sy, 18, angle, (180, 200, 255),
                      speed=320, life=0.3, size=3, spread=20)
        else:
            # 手雷投掷：橙色抛物线拖尾朝目标方向射出
            _emit_dir(sx, sy, 12, angle, (255, 140, 40),
                      speed=200, life=0.5, size=4, spread=15)


def _create_floating_text(monster, text, color):
    """在怪物头顶创建漂浮文字效果"""
    # 暂时存储在怪物对象上，供渲染层读取
    monster._skill_prompt_text = text
    monster._skill_prompt_color = color
    monster._skill_prompt_timer = 1.5  # 显示1.5秒（原0.8秒太短）


# 自身 buff 类技能 → update_skill_buffs 使用的 buff 类型标识
# （键为技能中文名，与 apply_skill_effect 的匹配键一致）
_SELF_BUFF_SKILL_TYPES = {
    "狂暴": "berserk",
    "骨盾": "bone_shield",
    "战术撤退": "tactical_retreat",
}


def apply_skill_effect(monster, skill_index: int, target=None, nearby_players=None):
    """应用怪物技能的实际战斗效果
    
    根据怪物类型和技能索引，对目标玩家施加 debuff 或对怪物自身施加 buff。
    
    Args:
        monster: 怪物对象
        skill_index: 技能索引（0=第一个技能，1=第二个技能）
        target: 目标玩家对象（近战/远程攻击的目标）
        nearby_players: 附近玩家列表（范围技能用，可选）
    
    Returns:
        bool: 是否成功应用了技能效果
    """
    class_name = monster.__class__.__name__
    config = SKILL_PROMPT_CONFIG.get(class_name)
    if not config or not config.get("skills"):
        return False
    
    skill = config["skills"][skill_index % len(config["skills"])]
    skill_name = skill["name"]
    
    # 自身 buff 类技能（狂暴/骨盾/战术撤退）：buff 生效期间禁止再次施加。
    # 否则第二次触发会把「已乘过倍率」的 damage/speed 再乘一次（复利式雪球），
    # 而 update_skill_buffs 到期只还原一层，导致属性永久虚高。
    # 判定复用 buff 自身的状态字段（_skill_buff_type/_skill_buff_timer），不另建并行状态。
    self_buff_type = _SELF_BUFF_SKILL_TYPES.get(skill_name)
    if self_buff_type is not None \
            and getattr(monster, "_skill_buff_type", None) == self_buff_type \
            and getattr(monster, "_skill_buff_timer", 0) > 0:
        return True  # 同类 buff 仍在生效，保持现状不叠加
    
    # ── 僵尸技能 ──
    if class_name == "Zombie":
        if skill_name == "腐烂光环":
            # 对目标施加中毒效果（2秒，每秒3点伤害）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("poison", 1)
                return True
        elif skill_name == "狂暴":
            # 怪物自身攻击力+50%，持续3秒（临时 buff）
            original_damage = monster.damage
            monster.damage = round(monster.damage * 1.5)
            # 3秒后恢复（通过 _skill_buff_timer 实现）
            monster._skill_buff_timer = 3.0
            monster._skill_buff_type = "berserk"
            monster._skill_buff_original = original_damage
            return True
    
    # ── 骷髅技能 ──
    elif class_name == "Skeleton":
        if skill_name == "骨盾":
            # 怪物自身获得临时护盾（防御+5，持续3秒）
            # 注意：不直接修改 monster.armor（死亡掉落依赖 armor 字典判定），
            # 改用独立的临时防御加成字段，update_skill_buffs 到期自动清除。
            monster._skill_buff_timer = 3.0
            monster._skill_buff_type = "bone_shield"
            monster._skill_buff_original = getattr(monster, '_skill_defense_bonus', 0)
            monster._skill_defense_bonus = 5
            return True
        elif skill_name == "骨矛投掷":
            # 对目标施加减速效果（2秒，减速30%）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("slow", 1)
                return True
    
    # ── 近战木乃伊技能 ──
    elif class_name == "MummyMelee":
        if skill_name == "毒雾释放":
            # 对目标施加中毒（3秒，每秒4点伤害）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("poison", 2)
                return True
        elif skill_name == "木乃伊缠绕":
            # 对目标施加眩晕（1.5秒）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("stun", 1)
                return True
    
    # ── 远程木乃伊技能 ──
    elif class_name == "MummyRanged":
        if skill_name == "治愈祷言":
            # 怪物自身回血（回复最大生命的20%）
            # 独立冷却：原实现每次 50% 命中判定都回血，近身对拼时等于不死；
            # 冷却未就绪时只跳过治疗，不影响同分支的提示/特效（返回 False）
            if getattr(monster, "_skill_heal_cd", 0.0) > 0:
                return False
            monster._skill_heal_cd = MUMMY_HEAL_SKILL_CD
            heal_amount = round(monster.max_hp * 0.2)
            monster.hp = min(monster.max_hp, monster.hp + heal_amount)
            return True
        elif skill_name == "诅咒标记":
            # 对目标施加易伤（3秒，受到伤害+20%）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("vulnerable", 2)
                return True
    
    # ── 骆驼技能 ──
    elif class_name == "Camel":
        if skill_name == "沙尘暴":
            # 对附近所有玩家施加减速（2秒，减速40%）
            if nearby_players:
                for p in nearby_players:
                    if hasattr(p, "apply_debuff"):
                        p.apply_debuff("slow", 2)
                return True
            elif target and hasattr(target, "apply_debuff"):
                target.apply_debuff("slow", 2)
                return True
        elif skill_name == "储水":
            # 怪物自身回血（回复最大生命的15%）
            heal_amount = round(monster.max_hp * 0.15)
            monster.hp = min(monster.max_hp, monster.hp + heal_amount)
            return True
    
    # ── 狙击兵技能 ──
    elif class_name == "Sniper":
        if skill_name == "激光瞄准":
            # 对目标施加破甲（3秒，防御-5）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("armor_break", 2)
                return True
        elif skill_name == "战术撤退":
            # 怪物自身移速+50%，持续2秒
            original_speed = monster.speed
            monster.speed = round(monster.speed * 1.5)
            monster._skill_buff_timer = 2.0
            monster._skill_buff_type = "tactical_retreat"
            monster._skill_buff_original = original_speed
            return True
    
    # ── 土匪技能 ──
    elif class_name == "Bandit":
        if skill_name == "投掷匕首":
            # 对目标施加流血（3秒，每秒3点伤害，可叠加）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("bleed", 1)
                return True
        elif skill_name == "群体呼叫":
            # 对目标施加易伤（2秒，受到伤害+15%）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("vulnerable", 1)
                return True
    
    # ── 火箭兵技能 ──
    elif class_name == "RocketTroop":
        if skill_name == "追踪导弹":
            # 对目标施加燃烧（3秒，每秒5点伤害）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("burn", 1)
                return True
        elif skill_name == "弹幕射击":
            # 对目标施加易伤（2秒，受到伤害+25%）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("vulnerable", 2)
                return True
    
    # ── 突击兵技能 ──
    elif class_name == "Assault":
        if skill_name == "冲锋":
            # 对目标施加眩晕（1秒）
            if target and hasattr(target, "apply_debuff"):
                target.apply_debuff("stun", 1)
                return True
        elif skill_name == "手雷投掷":
            # 对附近所有玩家施加燃烧（2秒，每秒4点伤害）
            if nearby_players:
                for p in nearby_players:
                    if hasattr(p, "apply_debuff"):
                        p.apply_debuff("burn", 1)
                return True
            elif target and hasattr(target, "apply_debuff"):
                target.apply_debuff("burn", 1)
                return True
    
    return False


def update_skill_buffs(monster, delta_time: float):
    """更新怪物的技能 buff 计时器（每帧调用）
    
    当 buff 到期时，恢复怪物的原始属性值。
    """
    # 技能专属冷却递减（当前仅治愈祷言，见 config.MUMMY_HEAL_SKILL_CD）：
    # 与 buff 计时器互不影响，故放在 buff 提前返回之前
    heal_cd = getattr(monster, "_skill_heal_cd", 0.0)
    if heal_cd > 0:
        monster._skill_heal_cd = max(0.0, heal_cd - delta_time)
    if not hasattr(monster, '_skill_buff_timer') or monster._skill_buff_timer <= 0:
        return
    
    monster._skill_buff_timer -= delta_time
    if monster._skill_buff_timer <= 0:
        # buff 到期，恢复原始值
        buff_type = getattr(monster, '_skill_buff_type', None)
        original = getattr(monster, '_skill_buff_original', 0)
        
        if buff_type == "berserk":
            # 恢复原始攻击力
            monster.damage = original
        elif buff_type == "bone_shield":
            # 移除临时防御加成（不再修改 monster.armor 字典）
            monster._skill_defense_bonus = 0
        elif buff_type == "tactical_retreat":
            # 恢复原始移速
            monster.speed = original
        
        # 清除 buff 状态
        monster._skill_buff_timer = 0
        monster._skill_buff_type = None
        monster._skill_buff_original = 0


def show_combo_prompt(monsters, combo_name, combo_color=(255, 200, 100)):
    """显示组合技能提示（多个怪物协同）
    
    Args:
        monsters: 怪物列表
        combo_name: 组合名称
        combo_color: 组合提示颜色
    
    Returns:
        dict: 组合提示信息
    """
    if not monsters:
        return None
    
    # 计算组合中心点
    avg_x = sum(m.center_x for m in monsters) / len(monsters)
    avg_y = sum(m.center_y for m in monsters) / len(monsters)
    
    # 在中心点显示组合名称
    prompt_info = {
        "color": combo_color,
        "duration": 1.0,
        "radius": 100,
        "name": combo_name,
        "x": avg_x,
        "y": avg_y,
    }
    
    return prompt_info


def assign_monster_armor(monster, level: int = 1, theme: str = "forest"):
    """按主题给怪物穿戴护甲

    主题装备池与概率由 config.MONSTER_THEME_EQUIP 驱动：
    - forest：皮甲(80%)/锁子甲(15%)/板甲(5%)，30% 概率穿戴，Lv1-5
    - desert：木乃伊护甲(80%)/板甲(20%)，80% 概率穿戴，Lv5-10
    - space：航天护甲(90%)/强相互作用力护甲(10%)，100% 概率穿戴，Lv10-50 / Lv5-10
    """
    cfg = MONSTER_THEME_EQUIP.get(theme, MONSTER_THEME_EQUIP["forest"])

    # 按主题概率判定是否穿戴
    if random.random() > cfg["armor_chance"]:
        return

    item_id = _weighted_choice(cfg["armor_pool"])
    monster.armor = _equip_dict(ARMORS, item_id, level)
    monster.armor_drop_id = item_id
    _apply_passive_effects(monster, monster.armor)


def assign_monster_helmet(monster, level: int = 1, theme: str = "forest"):
    """按主题给怪物穿戴头盔

    主题装备池与概率由 config.MONSTER_THEME_EQUIP 驱动。
    """
    cfg = MONSTER_THEME_EQUIP.get(theme, MONSTER_THEME_EQUIP["forest"])

    if random.random() > cfg["helmet_chance"]:
        return

    item_id = _weighted_choice(cfg["helmet_pool"])
    monster.helmet = _equip_dict(HELMETS, item_id, level)
    monster.helmet_drop_id = item_id
    _apply_passive_effects(monster, monster.helmet)


def assign_monster_weapon(monster, level: int = 1, theme: str = "forest"):
    """按怪物类型+主题分配武器（携带武器，击败后掉落自身武器）

    步骤：
    1. 从 MONSTER_METADATA.weapon_pool 获取该怪物的基础武器池
    2. 按主题概率决定是否追加神器武器（artifact=True）
    3. 神器武器按怪物近战/远程类型过滤，保证近战怪不拿远程武器
    4. 复制武器的 effects/debuff 到 monster.weapon，使攻击可触发特殊效果
    """
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    cfg = MONSTER_THEME_EQUIP.get(theme, MONSTER_THEME_EQUIP["forest"])

    cls_name = monster.__class__.__name__
    meta = MONSTER_METADATA.get(cls_name)
    if not meta:
        return
    weapon_ids = meta.get("weapon_pool") or []
    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
    base_pool = {k: v for k, v in all_weapons.items() if k in weapon_ids}
    if not base_pool:
        return

    # 判断怪物近战/远程类型（从 entities.monster_defs 查询）
    from entities.monster_defs import is_melee_monster
    monster_is_melee = is_melee_monster(cls_name)

    # 神器武器判定：按主题概率从匹配类型的神器武器中选取
    # 近战怪只从近战神器池选，远程怪只从远程神器池选
    if monster_is_melee:
        artifact_weapons = {k: v for k, v in MELEE_WEAPONS.items()
                            if v.get("artifact") and not v.get("special")}
    else:
        artifact_weapons = {k: v for k, v in RANGED_WEAPONS.items()
                            if v.get("artifact") and not v.get("special")}
    use_artifact = (random.random() < cfg["artifact_weapon_chance"]
                    and artifact_weapons)
    if use_artifact:
        wid = random.choice(list(artifact_weapons.keys()))
        wdef = artifact_weapons[wid]
        level = random.randint(*cfg["artifact_weapon_level"])
    else:
        wid = random.choice(list(base_pool.keys()))
        wdef = base_pool[wid]
        level = random.randint(*cfg["weapon_level"])

    # 构建 weapon 字典，包含 effects 和 debuff（使攻击可触发特殊效果）
    monster.weapon = {
        "item_id": wid,
        "name": wdef["name"],
        "color": wdef.get("color", (100, 200, 255)),
        "level": level,
        "effects": list(wdef.get("effects", [])),
        "debuff": wdef.get("debuff"),
        "kind": wdef.get("kind", "melee"),
    }
    # 武器被动效果应用到怪物属性（如速度加成、回血等）
    _apply_passive_effects(monster, monster.weapon)
