"""角色技能与被动效果实现

技能（F 键释放，见 game/input_handler.py）：
- 法师「奥术爆发」：向鼠标方向发射穿透能量波，造成 250% 武器伤害（复用弹丸系统，穿透属性）
- 骑士「圣盾庇护」：3 秒护盾，受到的伤害减少 70%（skill_active 计时，player.take_damage 内结算）
- 刺客「影袭」：瞬移到鼠标方向落点（≤250px 不穿墙），对落点范围敌人造成 150% 伤害并眩晕 1 秒

被动效果（常驻生效，定义见 entities/character_defs.py 的 passive 字段）：
- 法师「法力亲和」：攻击伤害 +20%（modify_attack_damage 在 combat 攻击入口统一修正）
- 骑士「钢铁之躯」：受到伤害 -5（player.take_damage 内实现，见 game/player.py）
- 骑士「逆境反击」：血量 <30% 时攻击伤害 +30%（modify_attack_damage 内实现）
- 刺客「致命一击」：15% 概率造成 2 倍伤害（modify_attack_damage 内实现）

联机（主机权威）：
- 客户端按 F 上报 SKILL_USE → 主机在对应幽灵上调用 use_skill 裁决（伤害/位移/护盾）；
- 技能效果（弹丸/位移）由主机快照同步到全房，客户端本地仅做纯表现（与攻击 ATTACK_EVENT 同构）。
"""

import math
import random
import arcade

from config import PROJECTILE_SPEED
from game.effects import particle_system, floating_texts
from game.sound_manager import sound_manager


def get_skill(player) -> dict | None:
    """获取玩家角色的技能定义（无技能角色返回 None）"""
    return getattr(player, "character_def", {}).get("skill")


def can_use_skill(player) -> bool:
    """技能是否可释放：有技能 + 冷却归零 + 未眩晕"""
    if getattr(player, "_stunned", False):
        return False
    skill = get_skill(player)
    return skill is not None and getattr(player, "skill_cd", 0.0) <= 0


def use_skill(view, player, mouse_x: float, mouse_y: float, damage: float,
              broadcast: bool = True) -> bool:
    """释放玩家角色技能（F 键 / 主机裁决客户端 SKILL_USE 共用入口）

    参数：
    - view: game_view 实例（访问 combat/monsters/wall_list 等）
    - player: 技能施放者（本地玩家或联机幽灵）
    - mouse_x/mouse_y: 鼠标目标点世界坐标（奥术爆发朝向 / 影袭落点方向）
    - damage: 当前武器伤害（技能伤害以武器伤害为基数，倍率见角色定义）
    - broadcast: 是否广播命中结果（主机裁决幽灵技能时为 True；单机本地释放传 True 无副作用，
      因为 solo 怪物无 net_id 广播函数自动跳过）
    返回: 是否成功释放（冷却中/眩晕/无技能返回 False）
    """
    if not can_use_skill(player):
        return False
    skill = get_skill(player)
    if skill is None:
        return False
    skill_id = skill.get("id", "")
    # 法师奥术爆发：穿透能量波
    if skill_id == "arcane_blast":
        _arcane_blast(view, player, mouse_x, mouse_y, damage, skill)
    # 骑士圣盾庇护：3 秒减伤护盾（结算在 player.take_damage，此处只启动计时）
    elif skill_id == "holy_shield":
        _holy_shield(player, skill)
    # 刺客影袭：瞬移 + 落点范围伤害 + 眩晕
    elif skill_id == "shadow_strike":
        _shadow_strike(view, player, mouse_x, mouse_y, damage, skill, broadcast)
    else:
        return False  # 未知技能 id：不消耗冷却
    # 释放成功：按技能定义设置冷却
    player.skill_cd = float(skill.get("cooldown", 6.0))
    return True


def _arcane_blast(view, player, mouse_x: float, mouse_y: float,
                  damage: float, skill: dict) -> None:
    """奥术爆发：向鼠标方向发射穿透能量波（250% 武器伤害，紫色弹丸）"""
    from game.combat import _PlayerProjectile  # 延迟导入避免与 combat 循环依赖
    dx = mouse_x - player.center_x
    dy = mouse_y - player.center_y
    dist = math.hypot(dx, dy)
    if dist == 0:
        return
    dmg = round(damage * float(skill.get("damage_mult", 2.5)))
    tx = player.center_x + (dx / dist) * 500.0
    ty = player.center_y + (dy / dist) * 500.0
    # 复用玩家弹丸（穿透属性不消失），owner_net_id 取施放者联机 id（联机快照去重用）
    owner_id = getattr(player, "net_player_id", 0)
    proj = _PlayerProjectile(
        player.center_x, player.center_y, tx, ty,
        PROJECTILE_SPEED, dmg,
        special="penetrating",
        owner_net_id=owner_id,
        owner_player=player,
    )
    proj.color = (160, 100, 255)  # 法师能量波：紫色，区别于普通穿透弹丸（青色）
    view.combat.projectiles.append(proj)
    # 音效 + 发射粒子
    sound_manager.play_ranged_attack()
    particle_system.emit_directional(
        player.center_x, player.center_y, 10,
        math.degrees(math.atan2(dy, dx)),
        (200, 150, 255), speed=160, life=0.25, size=4, spread=30,
    )


def _holy_shield(player, skill: dict) -> None:
    """圣盾庇护：启动 3 秒减伤护盾（take_damage 内按 damage_reduce 比例减伤）"""
    player.skill_active = float(skill.get("duration", 3.0))
    sound_manager.play_heal()  # 复用治疗音效（护盾开启的柔和音效）
    # 护盾开启粒子：金色光环围绕玩家
    particle_system.emit(player.center_x, player.center_y, 16,
                         (255, 215, 120), speed=60, life=0.5, size=4, gravity=0, spread=360)


def _shadow_strike(view, player, mouse_x: float, mouse_y: float,
                   damage: float, skill: dict, broadcast: bool) -> None:
    """影袭：瞬移到鼠标方向落点（≤250px 不穿墙）+ 落点范围伤害（150%）+ 1 秒眩晕"""
    dx = mouse_x - player.center_x
    dy = mouse_y - player.center_y
    dist = math.hypot(dx, dy)
    max_range = float(skill.get("range", 250))
    if dist == 0:
        return
    # 目标点：方向固定距离（不超过 250px；点击更近则取点击距离）
    tx = player.center_x + (dx / dist) * min(dist, max_range)
    ty = player.center_y + (dy / dist) * min(dist, max_range)
    # 不穿墙：沿方向从目标点回退到最近可行位置（每次回退 8px）
    step_x = (dx / dist) * 8.0
    step_y = (dy / dist) * 8.0
    while _collides_wall(view.wall_list, tx, ty, player):
        tx -= step_x
        ty -= step_y
        # 回退到玩家当前位置仍撞墙（理论不会发生）：放弃瞬移
        if math.hypot(tx - player.center_x, ty - player.center_y) <= 8.0:
            return
    px, py = player.center_x, player.center_y   # 瞬移起点（特效用）
    player.center_x = tx
    player.center_y = ty
    # ── 影袭特效：起点残影 + 路径拖尾 + 落点冲击 + 落地文字 ──
    # 起点残影：暗红幽灵弥散（慢速、短命）
    particle_system.emit(px, py, 14, (120, 40, 40), speed=55, life=0.45, size=5, spread=360)
    # 路径拖尾：沿位移方向喷射暗影粒子（飞掠残影）
    trail_angle = math.degrees(math.atan2(ty - py, tx - px))
    particle_system.emit_directional(px, py, 10, trail_angle, (200, 60, 60),
                                     speed=220, life=0.3, size=3, spread=18)
    # 落点冲击：亮红爆发 + 暖色扩散
    particle_system.emit(tx, ty, 22, (255, 90, 90), speed=170, life=0.4, size=4, spread=360)
    particle_system.emit(tx, ty, 12, (255, 190, 130), speed=120, life=0.3, size=3, spread=360)
    # 落地文字提示
    floating_texts.add(tx, ty + 30, "影袭!", (255, 120, 120), life=0.7, font_size=14, vy=60)
    # 落点范围伤害（半径 100）+ 眩晕 1 秒
    dmg = round(damage * float(skill.get("damage_mult", 1.5)))
    stun_lvl = max(1, int(skill.get("stun", 1.0)))
    hit_radius = 100.0
    for m in view.monsters:
        if not hasattr(m, "alive") or not m.alive:
            continue
        if math.hypot(m.center_x - tx, m.center_y - ty) <= hit_radius:
            actual = m.take_damage(dmg)
            if hasattr(m, "apply_debuff"):
                m.apply_debuff("stun", stun_lvl)
            floating_texts.add_damage(m.center_x, m.center_y + 25, actual)
            # 联机广播命中（主机裁决幽灵技能时；单机无 net_id 自动跳过）
            if broadcast and hasattr(view, "_broadcast_damage"):
                view._broadcast_damage(m, actual, hit=True, crit=False)
    sound_manager.play_attack()


def _collides_wall(wall_list, x: float, y: float, player) -> bool:
    """检测目标点是否与墙壁碰撞（按玩家尺寸的 AABB 判定，用于影袭不穿墙）"""
    half_w = getattr(player, "width", 32) / 2.0
    half_h = getattr(player, "height", 32) / 2.0
    for w in wall_list:
        # 墙壁精灵矩形范围
        wx0 = w.center_x - w.width / 2
        wx1 = w.center_x + w.width / 2
        wy0 = w.center_y - w.height / 2
        wy1 = w.center_y + w.height / 2
        # 玩家矩形与墙矩形 AABB 相交判定
        if (x + half_w > wx0 and x - half_w < wx1
                and y + half_h > wy0 and y - half_h < wy1):
            return True
    return False


def modify_attack_damage(player, damage: float) -> float:
    """角色被动攻击修正（combat 攻击入口统一调用，本地/联机幽灵均生效）

    应用顺序：
    1. 法师「法力亲和」：伤害 ×1.2
    2. 骑士「逆境反击」：血量 <30% 时 ×1.3
    3. 刺客「致命一击」：15% 概率 ×2（每次攻击掷一次，弹丸/挥砍整体判定）
    4. 狂暴药水（power_effect_timer 未归零）：伤害 × power_mult（默认 1.0 无加成）
    返回修正后的伤害值（浮点，调用方自行 round）。
    """
    passive = getattr(player, "character_def", {}).get("passive") or {}
    dmg = float(damage)
    # 法师法力亲和：攻击伤害 +20%
    mult = passive.get("damage_mult", 1.0)
    dmg *= mult
    # 骑士逆境反击：血量 <30% 时攻击 +30%
    low_mult = passive.get("low_hp_damage_mult")
    threshold = passive.get("low_hp_threshold", 0.0)
    if low_mult and threshold and getattr(player, "max_hp", 1) > 0:
        hp_ratio = getattr(player, "hp", 0) / player.max_hp
        if hp_ratio < threshold:
            dmg *= low_mult
    # 刺客致命一击：15% 概率 2 倍伤害
    crit = passive.get("crit_chance", 0.0)
    if crit > 0 and random.random() < crit:
        dmg *= float(passive.get("crit_mult", 2.0))
        # 暴击提示：刺客被动暴击触发时在玩家位置显示"暴击!"浮动文字
        floating_texts.add(player.center_x, player.center_y + 30, "暴击!",
                           (255, 200, 50), life=0.8, font_size=16, vy=70)
    # 狂暴药水：伤害倍率（power_effect_timer 归零时 power_mult 已复位为 1.0）
    dmg *= getattr(player, "power_mult", 1.0)
    return dmg