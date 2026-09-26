"""阶段3 精英词缀怪：词缀应用与怪物行为钩子。

数据驱动说明：
- 所有词缀参数读自 entities/monster_defs.py 的 ELITE_AFFIXES（数值源头 config.py），
  本模块只做行为编排，禁止在此硬编码数值。
- 三个入口函数（apply_affix / on_affix_death / on_affix_update）开头统一先判
  `monster.affix is None` 直接返回：非精英怪每帧只有一次属性判定的开销。

词缀一览（详见 ELITE_AFFIXES 的 name/desc）：
- frenzy  狂暴：血量/速度加成；生命低于阈值时一次性再提速提伤
- split   分裂：死亡时原地刷出若干只小怪
- firewall 火墙：存活期周期性在脚下留下持续燃烧地面，死亡时再留一大片；对近身玩家周期性施加灼烧
- summon  召唤：周期性召唤小怪支援
- shield  护盾：拥有可吸收伤害的护盾值（吸收在 monster_base.take_damage 内结算）
"""

import math
import random

from config import (ELITE_SPLIT_SPAWN_OFFSET, ELITE_FIRE_ZONE_TICK, ELITE_FIREWALL_BURN_LEVEL,
                    ELITE_FIRE_WALL_INTERVAL, ELITE_FIRE_WALL_RADIUS, ELITE_FIRE_WALL_LIFE)
from entities.monster_defs import ELITE_AFFIXES


def _players_in_view(view):
    """收集视图内可被词缀命中的玩家（本地玩家 + 主机侧客户端幽灵）

    联机幽灵同样参与近身灼烧判定；仅在存在该容器时遍历（solo 无 remote_players）。
    """
    players = []
    player = getattr(view, "player", None)
    if player is not None and getattr(player, "alive", True):
        players.append(player)
    for ghost in (getattr(view, "remote_players", None) or {}).values():
        if getattr(ghost, "alive", True):
            players.append(ghost)
    return players


def apply_affix(monster, affix_id) -> None:
    """给精英怪挂上词缀并立即结算「常驻」加成（血量/速度/护盾）

    affix_id 非法或数据缺失时静默返回，怪物保持普通怪状态。
    """
    if monster.affix is None:
        data = ELITE_AFFIXES.get(affix_id)
        if not data:
            return
        monster.affix = affix_id
        # 狂暴：额外血量/速度（在 ELITE_HP_MULT 之上再叠一层，参考 BOSS 内联倍率的写法）
        hp_mult = data.get("hp_bonus_mult")
        if hp_mult:
            monster.max_hp = int(monster.max_hp * hp_mult)
            monster.hp = monster.max_hp
        speed_mult = data.get("speed_bonus_mult")
        if speed_mult:
            monster.speed = monster.speed * speed_mult
        # 护盾：吸收伤害的缓冲值（take_damage 内优先扣护盾）
        # max_shield 同步记录初始值，供 HUD 绘制护盾条比例
        shield_hp = data.get("shield_hp")
        if shield_hp:
            monster.shield = shield_hp
            monster.max_shield = shield_hp


def on_affix_death(monster, view) -> None:
    """精英怪死亡时触发词缀的「身后效果」

    - split：原地刷出 spawn_count 只 spawn_kind 小怪；
    - firewall：在脚下留下持续燃烧区域（view.fire_zones，由 game_view 结算/渲染）。
    """
    if monster.affix is None:
        return
    data = ELITE_AFFIXES.get(monster.affix)
    if not data:
        return
    if monster.affix == "split":
        from game.respawn import spawn_minion_group
        spawn_minion_group(
            view, data["spawn_kind"], data["spawn_count"],
            (monster.center_x, monster.center_y), ELITE_SPLIT_SPAWN_OFFSET,
        )
    elif monster.affix == "firewall":
        # 11.1 接线核查：client 不自行生成——区域建/删由主机经 MAP_CHANGE
        # change_type="fire_zone" 同步（zid 由主机首次见到时分配，见 network_sync
        # 的 _broadcast_fire_zones）；此处只在主机/单机生成
        view.fire_zones.append({
            "x": monster.center_x,
            "y": monster.center_y,
            "r": data["fire_zone_radius"],
            "life": data["fire_zone_life"],
            "dps": data["burn_dps"],
            "burn_duration": data["burn_duration"],
            # 伤害/灼烧的结算节拍（update_fire_zones 按此节拍扣血，不逐帧结算）
            "tick": ELITE_FIRE_ZONE_TICK,
        })


def on_affix_update(monster, delta_time, view) -> None:
    """每帧推进词缀的「存活期行为」

    - frenzy：血量低于阈值时一次性提速提伤（只触发一次）；
    - firewall：对近身玩家按间隔施加灼烧，并按存活期间隔在脚下生成燃烧区；
    - summon：按间隔召唤小怪（受 summon_max_alive 上限约束）。
    """
    if monster.affix is None:
        return
    data = ELITE_AFFIXES.get(monster.affix)
    if not data:
        return
    # 词缀运行时状态（计时器/一次性触发标记）惰性初始化
    state = getattr(monster, "_affix_state", None)
    if state is None:
        state = {}
        monster._affix_state = state

    if monster.affix == "frenzy":
        _update_frenzy(monster, data, state)
    elif monster.affix == "firewall":
        _update_firewall_contact(monster, data, state, delta_time, view)
    elif monster.affix == "summon":
        _update_summon(monster, data, state, delta_time, view)


def _update_frenzy(monster, data, state) -> None:
    """狂暴：血量跌破阈值时一次性提速提伤（不再重复触发）"""
    if state.get("frenzy_triggered"):
        return
    threshold = data.get("frenzy_threshold", 0.5)
    if threshold <= 0 or monster.hp > monster.max_hp * threshold:
        return
    state["frenzy_triggered"] = True
    monster.speed = monster.speed * data.get("frenzy_speed_mult", 1.0)
    monster.damage = monster.damage * data.get("frenzy_damage_mult", 1.0)
    from game.effects import floating_texts
    floating_texts.add(monster.center_x, monster.center_y - 30,
                       "狂暴!", (255, 80, 80), life=1.5)
    # 红色爆发粒子：光靠一行漂浮文字玩家感知不到狂暴已触发（用户 bug 2026-09-26），
    # 这里补一簇高速外扩粒子作为触发瞬间的视觉反馈
    from game.effects import particle_system
    particle_system.emit(monster.center_x, monster.center_y, 20, (255, 60, 60),
                         speed=180, life=0.5, size=4, spread=360)


def _update_firewall_contact(monster, data, state, delta_time, view) -> None:
    """火墙：按 contact_interval 对近身玩家施加灼烧 debuff；并按存活期间隔在脚下刷燃烧区

    存活期火区与死亡火区**并存不互斥**：死亡仍由 on_affix_death 生成 ELITE_FIRE_ZONE_*
    （90 半径/8s）大区，存活期这里按 ELITE_FIRE_WALL_*（70 半径/5s）刷小区，
    字段键名与 on_affix_death 完全一致（update_fire_zones 与 rendering 只认这套格式）。
    """
    if view is None:
        return
    # 存活期周期火区（玩家 bug 2026-09-26：此前火墙只在死亡时才刷区，导致"技能都在死后触发"的错觉）
    # 11.1 接线核查：与 on_affix_death 同口径，client 不自行生成——精英怪只在 host/solo 存在，
    # 区域建/删由主机经 MAP_CHANGE change_type="fire_zone" 同步（见 network_sync 的
    # _broadcast_fire_zones），故此处无需再判 net_mode；view 缺 fire_zones 容器时整体跳过
    zones = getattr(view, "fire_zones", None)
    if zones is not None:
        state["fire_wall_timer"] = state.get("fire_wall_timer", ELITE_FIRE_WALL_INTERVAL) - delta_time
        if state["fire_wall_timer"] <= 0:
            state["fire_wall_timer"] = ELITE_FIRE_WALL_INTERVAL
            zones.append({
                "x": monster.center_x,
                "y": monster.center_y,
                "r": ELITE_FIRE_WALL_RADIUS,
                "life": ELITE_FIRE_WALL_LIFE,
                "dps": data["burn_dps"],
                "burn_duration": data["burn_duration"],
                # 伤害/灼烧的结算节拍（update_fire_zones 按此节拍扣血，不逐帧结算）
                "tick": ELITE_FIRE_ZONE_TICK,
            })
            # 橙色火星：让玩家看到"精英在持续铺火"，而非只有死亡那一下
            from game.effects import particle_system
            particle_system.emit(monster.center_x, monster.center_y, 14, (255, 140, 40),
                                 speed=90, life=0.5, size=4, spread=360)
    interval = data.get("contact_interval", 2.0)
    state["contact_timer"] = state.get("contact_timer", interval) - delta_time
    if state["contact_timer"] > 0:
        return
    state["contact_timer"] = interval
    reach = data.get("contact_range", 60)
    level = data.get("contact_burn_level", 1)
    for player in _players_in_view(view):
        if math.hypot(player.center_x - monster.center_x,
                      player.center_y - monster.center_y) > reach:
            continue
        if hasattr(player, "apply_debuff"):
            player.apply_debuff("burn", level)


def _update_summon(monster, data, state, delta_time, view) -> None:
    """召唤：按 summon_interval 召出小怪，场上召唤怪达 summon_max_alive 后不再召唤"""
    if view is None:
        return
    interval = data.get("summon_interval", 8.0)
    state["summon_timer"] = state.get("summon_timer", interval) - delta_time
    if state["summon_timer"] > 0:
        return
    state["summon_timer"] = interval
    max_alive = data.get("summon_max_alive", 6)
    # 只统计存活召唤怪：本层死亡怪物不立即从 view.monsters 移除（渲染/快照按 alive 过滤），
    # 若不判活，尸体也会占用 summon_max_alive 配额，导致整局召唤几次后彻底停摆
    alive_summoned = sum(1 for m in getattr(view, "monsters", ())
                         if getattr(m, "alive", False) and getattr(m, "affix_summoned", False))
    count = min(data.get("summon_count", 2), max_alive - alive_summoned)
    if count <= 0:
        return
    from game.respawn import spawn_minion_group
    spawn_minion_group(
        view, data["summon_kind"], count,
        (monster.center_x, monster.center_y), ELITE_SPLIT_SPAWN_OFFSET,
    )
    from game.effects import floating_texts
    floating_texts.add(monster.center_x, monster.center_y - 30,
                       f"召唤 x{count}", (200, 120, 255), life=1.5)
    # 紫色召唤阵粒子：与漂浮文字并存，让玩家在乱战中也能察觉"精英在叫援兵"
    from game.effects import particle_system
    particle_system.emit(monster.center_x, monster.center_y, 18, (180, 100, 255),
                         speed=110, life=0.55, size=4, spread=360)


def roll_affix() -> str:
    """随机抽一个词缀 id（供 spawn_elite 使用）"""
    return random.choice(list(ELITE_AFFIXES))


def update_fire_zones(view, delta_time) -> None:
    """推进火墙词缀留下的燃烧地面区域（view.fire_zones）

    每帧递减 life；到期即移除；对圈内玩家按 ELITE_FIRE_ZONE_TICK 节奏扣血并施加灼烧。
    伤害/灼烧的权威判定只在主机与单机执行（客户端由阶段 11 同步区域本身）。

    11.1 接线核查：客户端经 MAP_CHANGE change_type="fire_zone" 接收区域建/删；
    客户端分支只做**纯表现层**的 life 递减与到期移除（渲染按 life 收缩，见
    game/rendering.py），绝不本地结算扣血/灼烧——否则会出现主机没打中、客户端自己掉血
    的本地仲裁事故（host 权威铁律）。
    """
    zones = getattr(view, "fire_zones", None)
    if not zones:
        return
    if getattr(getattr(view.window, "game_state", None), "net_mode", "solo") == "client":
        for zone in list(zones):
            zone["life"] -= delta_time
            if zone["life"] <= 0:
                zones.remove(zone)
        return
    players = _players_in_view(view)
    for zone in list(zones):
        zone["life"] -= delta_time
        if zone["life"] <= 0:
            zones.remove(zone)
            continue
        if not players:
            continue
        # 扣血/灼烧节奏计时（与 debuff 结算同节奏）
        zone["tick"] = zone.get("tick", ELITE_FIRE_ZONE_TICK) - delta_time
        if zone["tick"] > 0:
            continue
        zone["tick"] = ELITE_FIRE_ZONE_TICK
        for player in players:
            if math.hypot(player.center_x - zone["x"], player.center_y - zone["y"]) > zone["r"]:
                continue
            if hasattr(player, "take_damage"):
                player.take_damage(round(zone["dps"] * ELITE_FIRE_ZONE_TICK))
            if hasattr(player, "apply_debuff"):
                player.apply_debuff("burn", ELITE_FIREWALL_BURN_LEVEL)
