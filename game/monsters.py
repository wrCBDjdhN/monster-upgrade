"""怪物薄类 —— 13 种具体怪物（数据驱动，继承 game.monster_base 的参数化基类）

包含 13 种怪物：
- 近战（继承 _MeleeMonsterBase）：Zombie / MummyMelee / BossZombie / BossMummy / Assault
- 远程（继承 _RangedMonsterBase）：Skeleton / MummyRanged / Camel / BossSkeleton / Sniper / Bandit / RocketTroop / BossSpace

数据驱动说明：
- 所有怪物数值（hp/damage/speed/size/color/aggro_range/弹丸参数/debuff_id/boss 标记等）
  统一在 entities/monster_defs.py 的 MONSTER_CONFIGS 中定义，禁止在此文件重复硬编码数值。
- 各怪物类 __init__ 只需 super().__init__(**MONSTER_CONFIGS["类名"]) 从配置取数。
- RocketTroop 保留 try_attack 覆写：发射带爆炸属性的 AOE 弹丸。
- 基类与基础设施（_WallGrid/Projectile/_MeleeMonsterBase/_RangedMonsterBase/辅助函数）
  已拆分至 game/monster_base.py，本文件经 re-export 保持向后兼容。

AI 行为：
- 怪物在索敌距离内追踪玩家；近战直接冲向玩家攻击
- 远程怪物保持 120~200 距离，远了靠近、近了后退
- 超出索敌距离则停止追击

碰撞检测：
- 使用 _can_move_to() 进行墙壁碰撞检测
- 怪物不能穿墙，但可以通过门离开房间
"""


import math
from config import MAP_WIDTH, MAP_HEIGHT, PLAYER_SIZE, BOSS_PHASES, BOSS_BERSERK_DURATION
from entities.monster_defs import (
    BOSS_SKILLS, BOSS_SUMMON_CONFIG, MONSTER_CONFIGS, get_boss_skill, get_boss_skill_cooldowns,
)
from game.monster_base import _WallGrid, Projectile, _MeleeMonsterBase, _RangedMonsterBase, _select_target, _has_line_of_sight, _ranged_attack_distance, _mark_attack_vfx

# Re-export for backward compatibility
from game.monster_base import _WallGrid, Projectile, _MeleeMonsterBase, _RangedMonsterBase


class Zombie(_MeleeMonsterBase):
    """僵尸（近战）：血薄攻低但成群刷新，追踪玩家并近距离攻击"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Zombie"])


class Skeleton(_RangedMonsterBase):
    """骷髅（远程）：保持 120~200 距离并发射弹丸"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Skeleton"])


class MummyMelee(_MeleeMonsterBase):
    """木乃伊（近战）：血厚攻高，攻击附加中毒"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["MummyMelee"])


class MummyRanged(_RangedMonsterBase):
    """木乃伊（远程）：发射附中毒的弹丸"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["MummyRanged"])


class Camel(_RangedMonsterBase):
    """骆驼：高血量高移速，吐口水远程攻击（弹丸略慢可躲避）"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Camel"])


def _boss_summon_minions(boss, summon_types: list[tuple[str, int, int]], theme: str = "forest"):
    """通用BOSS召唤逻辑：在BOSS周围生成小怪并装备

    Args:
        boss: BOSS实例（需有 _summon_callback 或 _walls 属性）
        summon_types: 召唤列表 [(怪物类名, 最小数, 最大数), ...]
        theme: 地图主题（装备分配用）

    Returns:
        list: 召唤出的怪物实例列表
    """
    import random
    from config import MONSTER_GEAR_LEVEL_RANGE, MONSTER_WEAPON_LEVEL_RANGE, TILE_SIZE
    from entities.monster_defs import MONSTER_CONFIGS
    from game.monster_utils import assign_monster_weapon, assign_monster_armor, assign_monster_helmet

    # 怪物类名 → 类对象映射（延迟 import 避免循环依赖）
    _CLASS_MAP = None
    summoned = []

    for cls_name, min_count, max_count in summon_types:
        cfg = MONSTER_CONFIGS.get(cls_name)
        if not cfg:
            continue
        count = random.randint(min_count, max_count)
        for _ in range(count):
            # 在BOSS周围随机偏移生成
            angle = random.uniform(0, 6.283)
            dist = random.randint(60, 120)
            sx = boss.center_x + math.cos(angle) * dist
            sy = boss.center_y + math.sin(angle) * dist
            # 简单边界裁剪（不穿墙判定由调用方确保，BOSS 周围通常是开阔区域）
            sx = max(TILE_SIZE * 2, min(sx, MAP_WIDTH - TILE_SIZE * 2))
            sy = max(TILE_SIZE * 2, min(sy, MAP_HEIGHT - TILE_SIZE * 2))

            # 建立类映射（首次调用时一次性构建）
            if _CLASS_MAP is None:
                from game.monsters import (Zombie, Skeleton, MummyMelee, MummyRanged,
                                           Camel, Sniper, Assault, Bandit, RocketTroop)
                _CLASS_MAP = {
                    "Zombie": Zombie, "Skeleton": Skeleton,
                    "MummyMelee": MummyMelee, "MummyRanged": MummyRanged,
                    "Camel": Camel, "Sniper": Sniper, "Assault": Assault,
                    "Bandit": Bandit, "RocketTroop": RocketTroop,
                }
            klass = _CLASS_MAP.get(cls_name)
            if klass is None:
                continue
            m = klass(center_x=sx, center_y=sy)
            m.set_on_death(boss._on_death_cb)
            m._walls = boss._walls
            # 装备分配（等级略低于BOSS本体）
            eq_level = random.randint(max(1, MONSTER_GEAR_LEVEL_RANGE[0] - 2),
                                      max(1, MONSTER_GEAR_LEVEL_RANGE[1] - 2))
            wp_level = random.randint(max(1, MONSTER_WEAPON_LEVEL_RANGE[0] - 2),
                                      max(1, MONSTER_WEAPON_LEVEL_RANGE[1] - 2))
            assign_monster_armor(m, level=eq_level, theme=theme)
            assign_monster_helmet(m, level=eq_level, theme=theme)
            assign_monster_weapon(m, level=wp_level, theme=theme)
            # 通知调用方（回调或返回列表由外部处理）
            summoned.append(m)

    return summoned


def _boss_state_init(monster, theme: str) -> None:
    """BOSS 附加状态初始化（阶段B 收口）：召唤与转阶段的**运行态**字段

    数值类字段（cooldown/血量阈值/召唤列表/提示文案）不在这里缓存——
    _boss_tick 每帧现读 config，修复 4 个 Boss 类 __init__ 各自硬编码
    重复值、而 BOSS_SUMMON_CONFIG 同名字段无人消费（改配置不生效）的问题。
    """
    monster._summon_timer = 0.0
    monster._summon_count = 0
    monster._summoned_phases = set()
    monster._summon_callback = None
    monster._theme = theme
    monster._boss_phase_idx = 0  # 已进入的转阶段数（BOSS_PHASES 下标）


def boss_phase_tick(monster, class_name: str) -> None:
    """BOSS 转阶段强化（阶段B）：血量首次跌过阈值即进入该阶段并一次性应用强化

    数据唯一来源 config.BOSS_PHASES[class_name]（阈值降序，每阶段只触发一次）：
    - speed/damage：对「首次进阶段时快照的基准值」按已进入阶段的乘数累计连乘
      重算（base × ∏mult）→ 重复调用结果一致、不漂移；
    - cd：技能冷却倍率写 monster._skill_cd_mult，_boss_try_skill 置冷却时消费；
    - berserk=True 的末阶段：复用 _skill_buff_* "berserk" 标记触发狂暴表现
      （快照同步字段 + 光晕渲染现成，客户端零新协议），
      _skill_buff_original 存强化后的攻击力 → 到期恢复值一致、不回退。
    """
    if not getattr(monster, "alive", False):
        return
    phases = BOSS_PHASES.get(class_name)
    if not phases:
        return
    idx = getattr(monster, "_boss_phase_idx", 0)
    if idx >= len(phases):
        return
    nxt = phases[idx]
    if monster.hp / monster.max_hp > float(nxt["hp_below"]):
        return
    if idx == 0:
        # 首次进阶段：快照基准（此前 speed/damage 未被本系统改过）
        monster._boss_base_speed = monster.speed
        monster._boss_base_damage = monster.damage
    monster._boss_phase_idx = idx + 1
    # 累计倍率 = 已进入各阶段的乘积
    speed_mult = damage_mult = cd_mult = 1.0
    for p in phases[:monster._boss_phase_idx]:
        speed_mult *= float(p.get("speed_mult", 1.0) or 1.0)
        damage_mult *= float(p.get("damage_mult", 1.0) or 1.0)
        cd_mult *= float(p.get("cd_mult", 1.0) or 1.0)
    monster.speed = monster._boss_base_speed * speed_mult
    monster.damage = round(monster._boss_base_damage * damage_mult)
    monster._skill_cd_mult = cd_mult
    if nxt.get("berserk"):
        monster._skill_buff_timer = BOSS_BERSERK_DURATION
        monster._skill_buff_type = "berserk"
        monster._skill_buff_original = monster.damage
    from game.effects import floating_texts
    floating_texts.add(monster.center_x, monster.center_y + 50,
                       "狂暴!" if nxt.get("berserk") else "强化!",
                       (255, 60, 60), life=1.5, font_size=16, vy=60)


def _boss_tick(monster, class_name: str, delta_time: float) -> None:
    """BOSS 每帧公共逻辑（阶段B 收口，替代 4 个 Boss 类 update 里的三段重复代码）

    1) 技能冷却递减（_skill_timers）
    2) 转阶段强化（boss_phase_tick）
    3) 血量阈值召唤（cooldown/阈值/召唤列表/提示全部现读 BOSS_SUMMON_CONFIG）

    调用口径：各 Boss.update 在 super().update(...) **之后**调用一次。
    原实现召唤段本就在 super 之后；冷却递减移到 super 之后与 try_attack
    （game_view 每帧先 update 后 try_attack）的相对顺序不变，无行为差异。
    """
    # 1) 技能冷却递减
    for k in monster._skill_timers:
        monster._skill_timers[k] = max(0.0, monster._skill_timers[k] - delta_time)
    # 2) 转阶段强化（死亡/未达标 no-op）
    boss_phase_tick(monster, class_name)
    # 3) 血量阈值召唤
    cfg = BOSS_SUMMON_CONFIG.get(class_name, {})
    thresholds = cfg.get("phase_thresholds") or ()
    if not monster.alive or monster._summon_count >= len(thresholds):
        return
    monster._summon_timer += delta_time
    if monster._summon_timer < float(cfg.get("cooldown", 0.0) or 0.0):
        return
    hp_ratio = monster.hp / monster.max_hp
    for threshold in thresholds:
        if hp_ratio <= threshold and threshold not in monster._summoned_phases:
            monster._summoned_phases.add(threshold)
            monster._summon_timer = 0.0
            monster._summon_count += 1
            summoned = _boss_summon_minions(monster, cfg.get("summons", []), monster._theme)
            if monster._summon_callback and summoned:
                monster._summon_callback(monster, summoned)
            # 召唤提示文字（文案/颜色取 BOSS_SUMMON_CONFIG，禁按类名硬编码分支）
            from game.effects import floating_texts
            floating_texts.add(monster.center_x, monster.center_y + 40,
                               cfg.get("text", "召唤援军!"),
                               cfg.get("text_color", (255, 80, 30)),
                               life=1.0, font_size=14, vy=50)
            break


# ══════════════════════ BOSS 技能效果落地（数值唯一来源 = entities/monster_defs.BOSS_SKILLS）══════════════════════
# 设计约定：
# 1) 冷却 / 射程 / 伤害倍率 / AOE 半径 / 弹丸数 / 眩晕时长 / debuff 强度全部读 BOSS_SKILLS，
#    本模块不硬编码任何平衡数值（改平衡只改数据表）。
# 2) 释放入口统一在 try_attack 覆写里（与 BossSpace 原有实现同口径），
#    而 try_attack 只被 views/game_view.py 的怪物 AI 循环调用，该循环整体在
#    `if gs.net_mode != "client"` 守卫内 → 客户端天然不会本地触发 BOSS 技能（主机权威）。
# 3) 伤害走 player.take_damage 唯一入口（护甲/护盾/角色被动/易伤/荆棘反伤口径与普攻完全一致），
#    受击钩子按 _pending_debuff* 广播附带效果给受击方客户端（与怪物普攻/弹丸同一口径）。

def _boss_skill_damage(monster, cfg: dict) -> int:
    """技能伤害 = BOSS 基础攻击 × damage_mult（至少 1 点；damage_mult<=0 = 纯控制技能返回 0）"""
    mult = float(cfg.get("damage_mult", 0) or 0)
    if mult <= 0:
        return 0
    return max(1, round(monster.damage * mult))


def _boss_skill_hit_targets(monster, cfg: dict, player, players) -> list:
    """按 aoe_radius 收集技能命中的玩家列表

    - aoe_radius 缺省/<=0：单目标语义（只打当前目标）
    - aoe_center="target"：以目标脚下为圆心（箭雨/导弹等落在玩家位置的技能）
    - aoe_center="self"（缺省）：以 BOSS 自身为圆心（咆哮/新星/毒雾等自身释放技能）
    - 存活判断与 monster_base._select_target 同一口径（alive 优先，回退 hp>0）
    """
    if player is None and not players:
        return []
    radius = float(cfg.get("aoe_radius", 0) or 0)
    if radius <= 0:
        return [player] if player is not None else []
    if str(cfg.get("aoe_center", "self")) == "target" and player is not None:
        center_x, center_y = player.center_x, player.center_y
    else:
        center_x, center_y = monster.center_x, monster.center_y
    candidates = list(players) if players else ([player] if player is not None else [])
    radius_sq = radius * radius
    hit = []
    for p in candidates:
        if p is None:
            continue
        alive = getattr(p, "alive", None)
        if alive is None:
            alive = getattr(p, "hp", 1) > 0
        if not alive:
            continue
        dx = p.center_x - center_x
        dy = p.center_y - center_y
        if dx * dx + dy * dy <= radius_sq:  # 平方距离比较，避免开根号
            hit.append(p)
    return hit


def _boss_skill_debuff_pairs(cfg: dict) -> list:
    """技能附带的 debuff 列表 [(id, level)]：debuff_id 字段 + stun 字段（眩晕）"""
    pairs = []
    debuff_id = cfg.get("debuff_id")
    if debuff_id:
        pairs.append((debuff_id, int(cfg.get("debuff_level", 1) or 1)))
    if cfg.get("stun"):
        # stun_level 取效果表上限等级：效果表 stun 时长上限 1.6s，
        # 实际时长随后由 _boss_skill_force_duration 按 BOSS_SKILLS 的 stun 秒数覆盖
        pairs.append(("stun", int(cfg.get("stun_level", 1) or 1)))
    return pairs


def _boss_skill_force_duration(target, debuff_id: str, seconds: float):
    """把玩家身上某个 debuff 的持续时间覆盖为技能配置值

    player.apply_debuff 的时长固定取 effects_defs 效果表（stun 最高 1.6s），
    而 BOSS_SKILLS 声明的眩晕时长（2.0/1.5/2.5s）超出该上限；
    故施加后就地改写 duration（仅改时长，不动已生效的 stun 标记，无需重算）。
    """
    if seconds <= 0 or not hasattr(target, "debuffs"):
        return
    for d in target.debuffs:
        if d.get("id") == debuff_id:
            d["duration"] = seconds
            return


def _boss_skill_apply_hit(monster, target, cfg: dict) -> int:
    """对单个玩家结算 BOSS 技能：伤害 + debuff + 眩晕，返回实际扣血量

    伤害为「聚合口径」：projectile_count 只决定视觉弹丸数量，
    伤害按 damage_mult 一次性结算（避免同帧多次 take_damage 造成数字刷屏与秒放）。
    """
    if target is None:
        return 0
    amount = _boss_skill_damage(monster, cfg)
    actual = 0
    if amount > 0:
        debuffs = _boss_skill_debuff_pairs(cfg)
        # 联机主机：受击钩子按 _pending_debuff* 广播附带效果（与怪物普攻/弹丸同一口径）
        target._pending_debuff = debuffs[0][0] if debuffs else None
        target._pending_debuff_level = debuffs[0][1] if debuffs else 1
        target._pending_debuff_effects = debuffs
        actual = target.take_damage(amount)
        # 玩家荆棘反伤：按实际承受伤害的百分比反弹给技能来源（与近战普攻 monster_base 同口径）
        thorns_pct = float(getattr(target, "equip_thorns", 0.0) or 0.0)
        if thorns_pct > 0 and actual > 0:
            monster.take_damage(max(1, round(actual * thorns_pct)))
    # 伤害数字不另弹：本地玩家掉血由 game_view 的 hp 差值检测统一显示（2308-2315），
    # 远端幽灵由受击方客户端的 PLAYER_HURT 事件显示，此处重复弹会双重显示。
    for debuff_id, level in _boss_skill_debuff_pairs(cfg):
        if not hasattr(target, "apply_debuff"):
            continue
        target.apply_debuff(debuff_id, level)
        if debuff_id == "stun":
            _boss_skill_force_duration(target, "stun", float(cfg.get("stun", 0) or 0))
    return actual


def _boss_skill_strike(monster, class_name: str, skill_id: str, player, players):
    """BOSS 技能命中结算：按 BOSS_SKILLS 的 AOE 规则找出目标并逐个结算效果"""
    cfg = get_boss_skill(class_name, skill_id)
    for target in _boss_skill_hit_targets(monster, cfg, player, players):
        _boss_skill_apply_hit(monster, target, cfg)


def _boss_try_skill(monster, class_name: str, emitters: dict, player, players) -> bool:
    """BOSS 技能释放统一入口：按 BOSS_SKILLS 顺序逐个判定「冷却就绪 + 目标在射程内」

    emitters: {技能id: 释放函数}，函数签名 (player, players, cfg)
    命中第一个可用技能即进入释放流程并返回 True（同一帧只放一个技能）。

    前摇（阶段A）：cfg.cast_time > 0 时先置预警（头顶提示 + AOE 范围圈，
    写既有 _skill_prompt_* / _skill_vfx_* 字段 → 已入 MONSTER_SNAPSHOT 同步，
    客户端零新协议可见），冷却在前摇开始即扣除；前摇归零才由
    monster_utils.update_skill_cast 调 emitter 真正结算。
    cast_time <= 0 的技能保持旧的立即释放行为。
    """
    if player is None or not getattr(monster, "alive", False):
        return False
    # 已在前摇中：视为忙（调用方据此跳过普攻），不重复起手
    if getattr(monster, "_skill_cast", None):
        return True
    dist = math.hypot(player.center_x - monster.center_x, player.center_y - monster.center_y)
    for cfg in BOSS_SKILLS.get(class_name, []):
        skill_id = cfg.get("id")
        emitter = emitters.get(skill_id)
        if emitter is None:
            continue
        # 阶段门槛（阶段C）：phases>0 表示需已转阶段数达到该值才解锁（未解锁跳过）
        if getattr(monster, "_boss_phase_idx", 0) < int(cfg.get("phases", 0) or 0):
            continue
        if monster._skill_timers.get(skill_id, 0.0) > 0:
            continue  # 冷却中
        if dist > float(cfg.get("range", 0) or 0):
            continue  # 目标不在射程内
        # 置冷却 = BOSS_SKILLS.cooldown × 转阶段 cd_mult（阶段B；未转阶段默认 1.0）
        monster._skill_timers[skill_id] = (
            float(cfg.get("cooldown", 0.0) or 0.0)
            * float(getattr(monster, "_skill_cd_mult", 1.0)))
        cast_time = float(cfg.get("cast_time", 0) or 0)
        if cast_time <= 0:
            emitter(player, players, cfg)
            return True
        # 前摇预警：快照 emitter/目标/配置，归零后由 update_skill_cast 结算
        monster._skill_cast = {"left": cast_time, "emitter": emitter,
                               "player": player, "players": players, "cfg": cfg}
        # 头顶预警文字（计时器与前摇同步递减，提示恰好在结算瞬间消失）
        monster._skill_prompt_text = f"{cfg.get('name', '')} 蓄力!"
        monster._skill_prompt_color = (255, 96, 96)
        monster._skill_prompt_timer = cast_time
        # 脚底范围圈：仅 **以自身为圆心**的 AOE 技能画圈（半径取技能 aoe_radius）；
        # aoe_center="target" 的落点在目标脚下、圈画在 BOSS 身上会误导，故只留头顶文字
        radius = float(cfg.get("aoe_radius", 0) or 0)
        if radius > 0 and str(cfg.get("aoe_center", "self")) != "target":
            monster._skill_vfx_timer = cast_time
            monster._skill_vfx_duration = cast_time
            monster._skill_vfx_radius = radius
            monster._skill_vfx_color = (255, 96, 96)
        return True
    return False


class BossZombie(_MeleeMonsterBase):
    """BOSS 僵尸：血厚攻高移速慢，攻击附加燃烧，需要 Lv5+ 武器/装备

    召唤机制：血量降至 60%/30% 时各召唤一波僵尸（使用 BOSS_SUMMON_CONFIG）。
    通过 _summon_callback 回调将小怪加入游戏（由 entity_callbacks 或 game_view 注入）。
    技能系统：烈焰冲击（扇形火焰弹幕）+ 僵尸咆哮（范围眩晕）
    """

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossZombie"])
        _boss_state_init(self, "forest")
        # 技能系统：冷却计时器（冷却秒数取自 BOSS_SKILLS，本类不硬编码）
        # 计时器键按 BOSS_SKILLS 派生（阶段C：含阶段解锁新技能，本类不硬编码技能id）
        self._skill_timers = {c["id"]: 0.0 for c in BOSS_SKILLS["BossZombie"]}
        self._skill_cooldowns = get_boss_skill_cooldowns("BossZombie")

    def _try_use_skill(self, player, dt, players=None) -> bool:
        """尝试释放技能，返回是否释放了技能（冷却/射程全部读 BOSS_SKILLS）"""
        return _boss_try_skill(
            self, "BossZombie",
            {"flame_charge": self._emit_flame_charge, "zombie_roar": self._emit_zombie_roar,
             "quake_slam": self._emit_quake_slam},
            player, players,
        )

    def _emit_flame_charge(self, player, players, cfg):
        """烈焰冲击：向前方扇形范围发射火焰弹幕（命中目标受伤 + 点燃）"""
        from game.effects import particle_system, floating_texts
        angle = math.atan2(player.center_y - self.center_y, player.center_x - self.center_x)
        # 扇形火焰弹幕
        for i in range(-3, 4):
            a = angle + math.radians(i * 15)
            particle_system.emit(
                self.center_x + math.cos(a) * 30,
                self.center_y + math.sin(a) * 30,
                8, (255, 100, 30), speed=180, life=0.4, size=5, spread=10)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "烈焰冲击!", (255, 120, 40), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossZombie", "flame_charge", player, players)

    def _emit_zombie_roar(self, player, players, cfg):
        """僵尸咆哮：以自身为中心释放冲击波（范围内玩家眩晕 stun 秒）"""
        from game.effects import particle_system, floating_texts
        particle_system.emit(self.center_x, self.center_y, 20, (255, 80, 80),
                           speed=120, life=0.5, size=6, spread=360)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "僵尸咆哮!", (255, 60, 60), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossZombie", "zombie_roar", player, players)

    def _emit_quake_slam(self, player, players, cfg):
        """震地猛击：重击地面震晕周围玩家（阶段C 新技能，phases=1 解锁）"""
        from game.effects import particle_system, floating_texts
        # 冲击尘土环
        particle_system.emit(self.center_x, self.center_y, 26, (160, 110, 60),
                           speed=170, life=0.5, size=6, spread=360)
        particle_system.emit(self.center_x, self.center_y - 8, 18, (120, 80, 40),
                           speed=90, life=0.7, size=8, spread=360, gravity=200)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "震地猛击!", (255, 140, 60), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossZombie", "quake_slam", player, players)

    def try_attack(self, player=None, players=None) -> bool:
        """BOSS 僵尸攻击：优先尝试技能释放，否则走近战基类攻击

        与 BossSpace.try_attack 同一口径（存活/眩晕守卫 → 选目标 → 先放技能）；
        该方法仅由 game_view 的主机/单机 AI 循环调用，客户端不执行（无本地重复触发）。
        """
        if not self.alive or self._stunned:
            return False
        if players is not None:
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            return False
        if self._try_use_skill(player, 0.0, players):
            return False
        return super().try_attack(player, players)

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        super().update(player_x, player_y, delta_time, players)
        # 阶段B 收口：技能冷却递减 + 转阶段强化 + 血量阈值召唤（原三段重复代码）
        _boss_tick(self, "BossZombie", delta_time)


class BossSkeleton(_RangedMonsterBase):
    """BOSS 骷髅：远程弹丸附加冰冻，需要 Lv5+ 武器/装备

    召唤机制：血量降至 60%/30% 时各召唤一波骷髅。
    技能系统：骷髅箭雨（AOE 箭矢）+ 冰冻新星（范围冰冻）
    """

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossSkeleton"])
        _boss_state_init(self, "forest")
        # 技能系统（冷却秒数取自 BOSS_SKILLS，本类不硬编码）
        # 计时器键按 BOSS_SKILLS 派生（阶段C：含阶段解锁新技能，本类不硬编码技能id）
        self._skill_timers = {c["id"]: 0.0 for c in BOSS_SKILLS["BossSkeleton"]}
        self._skill_cooldowns = get_boss_skill_cooldowns("BossSkeleton")

    def _try_use_skill(self, player, dt, players=None) -> bool:
        """尝试释放技能，返回是否释放了技能（冷却/射程全部读 BOSS_SKILLS）"""
        return _boss_try_skill(
            self, "BossSkeleton",
            {"skeleton_rain": self._emit_skeleton_rain, "frost_nova": self._emit_frost_nova,
             "bone_storm": self._emit_bone_storm},
            player, players,
        )

    def _emit_skeleton_rain(self, player, players, cfg):
        """骷髅箭雨：在目标脚下召唤箭矢（范围内玩家受伤，projectile_count 只管视觉弹丸数）"""
        from game.effects import particle_system, floating_texts
        for i in range(8):
            angle = math.radians(i * 45)
            ox = player.center_x + math.cos(angle) * 60
            oy = player.center_y + math.sin(angle) * 60
            particle_system.emit(ox, oy + 100, 3, (200, 200, 220),
                               speed=0, life=0.8, size=4, spread=5)
            particle_system.emit(ox, oy, 5, (180, 180, 200),
                               speed=60, life=0.3, size=3, spread=360)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "骷髅箭雨!", (200, 200, 255), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossSkeleton", "skeleton_rain", player, players)

    def _emit_frost_nova(self, player, players, cfg):
        """冰冻新星：以自身为中心释放冰冻波（范围内玩家受伤 + 冰冻减速 + 眩晕）"""
        from game.effects import particle_system, floating_texts
        particle_system.emit(self.center_x, self.center_y, 25, (100, 180, 255),
                           speed=150, life=0.6, size=5, spread=360)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "冰冻新星!", (120, 200, 255), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossSkeleton", "frost_nova", player, players)

    def _emit_bone_storm(self, player, players, cfg):
        """白骨旋风：环绕自身的骨片风暴（阶段C 新技能，phases=1 解锁）"""
        from game.effects import particle_system, floating_texts
        # 环状骨片（8 方向）
        for i in range(8):
            a = math.radians(i * 45)
            particle_system.emit(self.center_x + math.cos(a) * 70,
                               self.center_y + math.sin(a) * 70,
                               4, (230, 230, 240), speed=120, life=0.4, size=4, spread=40)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "白骨旋风!", (220, 220, 240), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossSkeleton", "bone_storm", player, players)

    def try_attack(self, player=None, players=None) -> Projectile | None:
        """BOSS 骷髅攻击：优先尝试技能释放，否则走远程基类开火

        与 BossSpace.try_attack 同一口径（存活/眩晕守卫 → 选目标 → 先放技能）；
        该方法仅由 game_view 的主机/单机 AI 循环调用，客户端不执行（无本地重复触发）。
        """
        if not self.alive or self._stunned:
            return None
        if players is not None:
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            return None
        if self._try_use_skill(player, 0.0, players):
            return None
        return super().try_attack(player, players)

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        super().update(player_x, player_y, delta_time, players)
        # 阶段B 收口：技能冷却递减 + 转阶段强化 + 血量阈值召唤（原三段重复代码）
        _boss_tick(self, "BossSkeleton", delta_time)


class BossMummy(_MeleeMonsterBase):
    """木乃伊 BOSS（金字塔守护者）：血厚攻高移速慢，攻击附加中毒，需要 Lv5+ 武器/装备

    召唤机制：血量降至 60%/30% 时各召唤一波木乃伊（近战+远程混合）。
    技能系统：毒雾弥漫（范围中毒）+ 木乃伊缠绕（拉近+眩晕）
    """

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossMummy"])
        _boss_state_init(self, "desert")
        # 技能系统（冷却秒数取自 BOSS_SKILLS，本类不硬编码）
        # 计时器键按 BOSS_SKILLS 派生（阶段C：含阶段解锁新技能，本类不硬编码技能id）
        self._skill_timers = {c["id"]: 0.0 for c in BOSS_SKILLS["BossMummy"]}
        self._skill_cooldowns = get_boss_skill_cooldowns("BossMummy")

    def _try_use_skill(self, player, dt, players=None) -> bool:
        """尝试释放技能，返回是否释放了技能（冷却/射程全部读 BOSS_SKILLS）"""
        return _boss_try_skill(
            self, "BossMummy",
            {"poison_fog": self._emit_poison_fog, "mummy_grab": self._emit_mummy_grab,
             "scarab_swarm": self._emit_scarab_swarm},
            player, players,
        )

    def _emit_poison_fog(self, player, players, cfg):
        """毒雾弥漫：以自身为中心释放毒雾（范围内玩家受伤 + 持续中毒）"""
        from game.effects import particle_system, floating_texts
        particle_system.emit(self.center_x, self.center_y, 30, (100, 200, 60),
                           speed=80, life=1.0, size=7, spread=360)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "毒雾弥漫!", (120, 220, 80), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossMummy", "poison_fog", player, players)

    def _emit_mummy_grab(self, player, players, cfg):
        """木乃伊缠绕：范围缠绕（受伤 + 眩晕 stun 秒 + 减速）

        降级说明：本项目无位移机制（玩家位移只在 player.update 内结算，且联机由快照同步），
        故 BOSS_SKILLS 的 pull=True 降级为「眩晕 + 减速」组合控制，不改动玩家移动代码。
        （旧实现直接改写 player.center_x 会穿墙且联机不同步；旧 debuffs.append(("stun", 2.5))
          写的是元组而 debuffs 存字典，下一帧结算会抛异常，一并去掉。）
        """
        from game.effects import particle_system, floating_texts
        # 缠绕粒子（以目标为中心）
        particle_system.emit(player.center_x, player.center_y, 15, (180, 160, 100),
                           speed=60, life=0.5, size=4, spread=360)
        floating_texts.add(player.center_x, player.center_y + 30,
                          "缠绕!", (200, 180, 120), life=0.7, font_size=14, vy=60)
        _boss_skill_strike(self, "BossMummy", "mummy_grab", player, players)

    def _emit_scarab_swarm(self, player, players, cfg):
        """圣甲虫群：放出虫群噬咬目标并中毒（阶段C 新技能，phases=1 解锁）"""
        from game.effects import particle_system, floating_texts
        angle = math.atan2(player.center_y - self.center_y, player.center_x - self.center_x)
        # 扇形虫群粒子飞向目标
        for i in range(6):
            a = angle + math.radians(i * 12 - 30)
            particle_system.emit(self.center_x + math.cos(a) * 24,
                               self.center_y + math.sin(a) * 24,
                               5, (90, 140, 60), speed=200, life=0.5, size=3, spread=12)
        floating_texts.add(player.center_x, player.center_y + 30,
                          "圣甲虫群!", (140, 190, 80), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossMummy", "scarab_swarm", player, players)

    def try_attack(self, player=None, players=None) -> bool:
        """BOSS 木乃伊攻击：优先尝试技能释放，否则走近战基类攻击

        与 BossSpace.try_attack 同一口径（存活/眩晕守卫 → 选目标 → 先放技能）；
        该方法仅由 game_view 的主机/单机 AI 循环调用，客户端不执行（无本地重复触发）。
        """
        if not self.alive or self._stunned:
            return False
        if players is not None:
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            return False
        if self._try_use_skill(player, 0.0, players):
            return False
        return super().try_attack(player, players)

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        super().update(player_x, player_y, delta_time, players)
        # 阶段B 收口：技能冷却递减 + 转阶段强化 + 血量阈值召唤（原三段重复代码）
        _boss_tick(self, "BossMummy", delta_time)


class Sniper(_RangedMonsterBase):
    """狙击兵（远程型，高伤低血）：使用狙击枪，索敌距离超远"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Sniper"])


class Assault(_MeleeMonsterBase):
    """突击兵（近战型，高血高甲）：使用步枪近距离作战"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Assault"])


class Bandit(_RangedMonsterBase):
    """土匪（远程型，低血低甲，成群刷新）：使用手枪或石锤"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Bandit"])


class RocketTroop(_RangedMonsterBase):
    """火箭兵（远程型，高血高甲，AOE 弹丸）：使用火箭筒"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["RocketTroop"])

    def try_attack(self, player=None, players=None) -> Projectile | None:
        """火箭兵攻击：发射带爆炸属性的弹丸（命中玩家时触发 AOE 范围伤害）

        单目标模式传 player（单机路径）；多目标模式传 players 列表，
        自动选取最近存活玩家作为攻击目标。
        """
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
            return None
        # 阶段 2：进攻撤离点期间不在这里攻击玩家（伤害已在 _update_evac_aggro 结算）
        # 放行口径同基类：aggro_point 非空 = 本帧仍以撤离点为目标；防守期插队已在
        # _update_evac_aggro 清空 aggro_point → 自然落到下面的玩家开火分支
        if self.aggro_point is not None:
            return None
        if players is not None:
            # 多目标模式：重新选取最近存活玩家作为攻击目标
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            # 无目标（多目标模式下无存活玩家，或单目标未传入玩家）无法攻击
            return None
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        # 开火距离 = 武器射程真实生效（_ranged_attack_distance：武器射程，无武器回退 _aggro_range）
        # 隔墙（无视线）不开火：与索敌规则一致（边缘视线），防止火箭兵隔着墙射击
        if dist < _ranged_attack_distance(self) and self._attack_timer <= 0 and _has_line_of_sight(
                self.center_x, self.center_y, player.center_x, player.center_y, self._walls,
                self._size, PLAYER_SIZE):
            self._attack_timer = self._attack_delay
            # 攻击表现：开火闪白（kind="ranged" 只闪白，弹丸经 PROJECTILE_SNAPSHOT 同步）
            _mark_attack_vfx(self, "ranged", player.center_x, player.center_y)
            # 汇总弹丸附带效果：怪物自带 debuff + 武器携带效果
            combined_debuffs = []
            if self.debuff_id:
                combined_debuffs.append((self.debuff_id, 1))
            wdebuff = (self.weapon or {}).get("debuff")
            if wdebuff:
                combined_debuffs.append((wdebuff, 1))
            for eid, lvl in (self.weapon or {}).get("effects", []):
                if eid not in ("max_hp", "regen", "speed"):
                    combined_debuffs.append((eid, lvl))
            return Projectile(
                self.center_x, self.center_y,
                player.center_x, player.center_y,
                self._proj_speed, self.damage,
                color=self._proj_color, debuff_id=self.debuff_id, size=self._proj_size,
                special="explosive",  # 火箭弹丸爆炸属性
                debuffs=combined_debuffs,
                # 射程上限 = 本次开火所用的武器射程（爆炸属性与射程上限互不影响：
                # 仍先碰墙触发 AOE，飞满射程才消散）
                max_range=_ranged_attack_distance(self),
            )
        return None


class BossSpace(_RangedMonsterBase):
    """航天 BOSS（远程型，激光枪）：血厚攻高移速慢，攻击附加燃烧，需要 Lv5+ 武器/装备

    召唤机制：血量降至 70%/40%/10% 时各召唤一波混合部队（3次召唤）。
    技能系统：激光扫射（扇形激光）+ 导弹齐射（3枚追踪导弹）
    """

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossSpace"])
        _boss_state_init(self, "space")
        # 技能系统（冷却秒数取自 BOSS_SKILLS，本类不硬编码）
        # 计时器键按 BOSS_SKILLS 派生（阶段C：含阶段解锁新技能，本类不硬编码技能id）
        self._skill_timers = {c["id"]: 0.0 for c in BOSS_SKILLS["BossSpace"]}
        self._skill_cooldowns = get_boss_skill_cooldowns("BossSpace")

    def _try_use_skill(self, player, dt, players=None) -> bool:
        """尝试释放技能，返回是否释放了技能（冷却/射程全部读 BOSS_SKILLS）"""
        return _boss_try_skill(
            self, "BossSpace",
            {"laser_sweep": self._emit_laser_sweep, "missile_barrage": self._emit_missile_barrage,
             "orbital_strike": self._emit_orbital_strike},
            player, players,
        )

    def _emit_laser_sweep(self, player, players, cfg):
        """激光扫射：向目标方向发射扇形激光（命中目标承受 damage_mult 倍伤害）"""
        from game.effects import particle_system, floating_texts
        angle = math.atan2(player.center_y - self.center_y, player.center_x - self.center_x)
        # 扇形激光粒子
        for i in range(-5, 6):
            a = angle + math.radians(i * 12)
            particle_system.emit(
                self.center_x + math.cos(a) * 40,
                self.center_y + math.sin(a) * 40,
                6, (255, 80, 255), speed=250, life=0.3, size=4, spread=8)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "激光扫射!", (255, 120, 255), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossSpace", "laser_sweep", player, players)

    def _emit_missile_barrage(self, player, players, cfg):
        """导弹齐射：在目标位置生成导弹（命中目标承受 damage_mult 倍伤害）"""
        from game.effects import particle_system, floating_texts
        for i in range(3):
            angle = math.radians(i * 120 + 30)
            ox = player.center_x + math.cos(angle) * 40
            oy = player.center_y + math.sin(angle) * 40
            particle_system.emit(ox, oy, 10, (255, 100, 50),
                               speed=0, life=0.6, size=6, spread=20)
        floating_texts.add(self.center_x, self.center_y + 40,
                          "导弹齐射!", (255, 130, 60), life=0.8, font_size=14, vy=50)
        _boss_skill_strike(self, "BossSpace", "missile_barrage", player, players)

    def _emit_orbital_strike(self, player, players, cfg):
        """轨道打击：轰击目标位置（阶段C 新技能，phases=1 解锁）"""
        from game.effects import particle_system, floating_texts
        # 光束落点闪光（以目标为圆心）
        particle_system.emit(player.center_x, player.center_y, 20, (255, 240, 120),
                           speed=140, life=0.6, size=5, spread=360)
        particle_system.emit(player.center_x, player.center_y, 14, (255, 200, 80),
                           speed=60, life=0.9, size=8, spread=360)
        floating_texts.add(player.center_x, player.center_y + 40,
                          "轨道打击!", (255, 230, 100), life=1.0, font_size=14, vy=60)
        _boss_skill_strike(self, "BossSpace", "orbital_strike", player, players)

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        super().update(player_x, player_y, delta_time, players)
        # 阶段B 收口：技能冷却递减 + 转阶段强化 + 血量阈值召唤（原三段重复代码）
        _boss_tick(self, "BossSpace", delta_time)

    def try_attack(self, player=None, players=None) -> Projectile | None:
        """航天BOSS攻击：优先尝试技能，否则发射激光弹丸（带燃烧效果）"""
        if not self.alive:
            return None
        if self._stunned:
            return None
        if players is not None:
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            return None
        # 优先尝试技能释放
        if self._try_use_skill(player, 0.0, players):
            return None
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        # 开火距离 = 武器射程真实生效（_ranged_attack_distance：武器射程，无武器回退 _aggro_range）
        if dist < _ranged_attack_distance(self) and self._attack_timer <= 0 and _has_line_of_sight(
                self.center_x, self.center_y, player.center_x, player.center_y, self._walls,
                self._size, PLAYER_SIZE):
            self._attack_timer = self._attack_delay
            # 攻击表现：开火闪白（kind="ranged" 只闪白，弹丸经 PROJECTILE_SNAPSHOT 同步）
            _mark_attack_vfx(self, "ranged", player.center_x, player.center_y)
            combined_debuffs = []
            if self.debuff_id:
                combined_debuffs.append((self.debuff_id, 1))
            wdebuff = (self.weapon or {}).get("debuff")
            if wdebuff:
                combined_debuffs.append((wdebuff, 1))
            for eid, lvl in (self.weapon or {}).get("effects", []):
                if eid not in ("max_hp", "regen", "speed"):
                    combined_debuffs.append((eid, lvl))
            return Projectile(
                self.center_x, self.center_y,
                player.center_x, player.center_y,
                self._proj_speed, self.damage,
                color=self._proj_color, debuff_id=self.debuff_id, size=self._proj_size,
                debuffs=combined_debuffs,
                # 射程上限 = 本次开火所用的武器射程
                max_range=_ranged_attack_distance(self),
            )
        return None