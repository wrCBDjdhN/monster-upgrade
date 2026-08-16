"""输入处理：键盘和鼠标事件"""

import math
import time
import random
import arcade
from config import FRUIT_SPEED_MULT
from game.sound_manager import sound_manager
from game.effects import particle_system, floating_texts
from entities.effects_defs import DEBUFF_POOL
from net.protocol import MsgType  # 联机消息类型（客户端上报 ATTACK_EVENT 用）


def handle_key_press(view, key, modifiers):
    """处理键盘按下事件"""
    gs = view.window.game_state

    # 观战模式（主机撤离/死亡后）：禁操作，仅响应 V 键循环切换观战视角
    if getattr(view, "_spectating", False):
        if key == arcade.key.V:
            view._cycle_spectate_target()
        return

    # 控制器
    if view.controller:
        view.controller.on_key_press(key)

    # 宝箱交互键
    if key == arcade.key.E:
        view._chest_key_pressed = True

    # 角色技能键 (F)：释放当前角色的特殊技能（法师奥术爆发/骑士圣盾/刺客影袭）。
    # 单机/主机本地直接释放；联机客户端上报主机裁决 + 本地纯表现（与 ATTACK_EVENT 同构）。
    if key == arcade.key.F:
        _handle_skill_key(view)
        return

    # 背包界面键 (TAB)：有未消费的升级（pending_choices>0）时优先打开升级面板，
    # 无待选升级则打开背包（升级面板关闭后可随时 TAB 回来继续选择）
    if key == arcade.key.TAB:
        ld = getattr(view, "_level_data", None) or {}
        if ld.get("pending_choices", 0) > 0:
            from views.level_up_view import LevelUpView
            view.window.show_view(LevelUpView(view.window_ref, game_view=view))
            return
        from views.backpack_view import BackpackView
        view.window.show_view(BackpackView(view.window_ref, game_view=view))
        return

    # 火箭发射台菜单选择（7=炸毁，8=启用撤离）
    # 直接按7/8即可选择，无需先按E
    if key in (arcade.key.KEY_7, arcade.key.KEY_8):
        # 检查是否有打开的菜单
        if hasattr(view, '_rocket_pad_menu') and view._rocket_pad_menu is not None:
            choice = 1 if key == arcade.key.KEY_7 else 2
            from game.entity_callbacks import handle_rocket_pad_choice
            handle_rocket_pad_choice(view, choice)
            return
        # 没有菜单时，检查是否靠近 boss_defeated 状态的发射台
        for pad in getattr(view, 'rocket_pads', []):
            dist = math.hypot(pad.center_x - view.player.center_x,
                              pad.center_y - view.player.center_y)
            if dist < 60 and pad.state == "boss_defeated":
                choice = 1 if key == arcade.key.KEY_7 else 2
                from game.entity_callbacks import handle_rocket_pad_choice
                view._rocket_pad_menu = pad
                handle_rocket_pad_choice(view, choice)
                return

    # 药水快捷键 (1-3)
    if gs.player_id and key in (arcade.key.KEY_1, arcade.key.KEY_2, arcade.key.KEY_3):
        potion_index = key - arcade.key.KEY_1
        from db.database import get_potions, use_potion
        potions = get_potions(gs.player_id)
        if potion_index < len(potions):
            pot = potions[potion_index]
            # 联机客户端：药水使用请求交主机确认（HP 主机权威），本地不直接生效
            if gs.net_mode == "client" and gs.net_client is not None:
                gs.net_client.send((MsgType.POTION_USE, {
                    "player_id": getattr(gs, "net_player_id", 0),
                    "potion_id": pot["id"],
                    "potion_name": pot.get("name", ""),
                }))
                return
            effect_info = use_potion(gs.player_id, pot["id"])
            if effect_info:
                effect = effect_info["effect"]
                value = effect_info["value"]
                duration = effect_info["duration"]
                if effect == "heal":
                    view.player.apply_hot(value / max(1, duration), duration)
                    floating_texts.add(view.player.center_x, view.player.center_y,
                                      f"回复中 +{int(value)} HP", arcade.color.GREEN)
                elif effect == "speed":
                    # 用药水自身的 value 作为加速倍率（替代硬编码 1.5）
                    view.player.speed_mult = value * view.player.gear_speed_mult
                    view.player.speed_effect_timer = duration
                    floating_texts.add(view.player.center_x, view.player.center_y,
                                      "加速!", arcade.color.CYAN)
                elif effect == "fruit":
                    # 果实药水：回复生命 + 按 config 倍率加速
                    view.player.heal(value)
                    view.player.speed_mult = FRUIT_SPEED_MULT * view.player.gear_speed_mult
                    view.player.speed_effect_timer = duration
                    floating_texts.add(view.player.center_x, view.player.center_y,
                                      f"果实 +{value} HP 加速!", arcade.color.GREEN)


def handle_key_release(view, key, modifiers):
    """处理键盘释放事件"""
    # 观战模式：禁操作（不向控制器转发）
    if getattr(view, "_spectating", False):
        return
    if view.controller:
        view.controller.on_key_release(key)
    if key == arcade.key.E:
        view._chest_key_pressed = False


def handle_mouse_motion(view, x, y, dx, dy):
    """处理鼠标移动事件"""
    view._mouse_x, view._mouse_y = x, y


def handle_mouse_press(view, x, y, button, modifiers):
    """处理鼠标按下事件"""
    # 观战模式：禁操作（不攻击、不交互）
    if getattr(view, "_spectating", False):
        return
    if button == arcade.MOUSE_BUTTON_LEFT:
        view._mouse_x, view._mouse_y = x, y
        view._left_mouse_held = True

        # 屏幕坐标转世界坐标（combat 函数需要世界坐标计算方向）
        cam = view.controller.camera.position
        world_x = x + cam[0] - view.window.width / 2
        world_y = y + cam[1] - view.window.height / 2

        gs = view.window.game_state
        kind = gs.current_weapon_kind
        weapon_range = getattr(gs, 'weapon_range', 40)
        view._last_attack_range = weapon_range
        view._attack_kind = kind

        # ── 联机客户端：攻击判定收敛主机 ──
        # 本地只保留表现效果（攻击闪白/斩击粒子/攻击范围可视化/音效），不跑本地命中判定
        # （melee_attack / ranged_attack / spawn_laser），改为上报 ATTACK_EVENT 给主机，
        # 由主机权威裁决命中并广播 DAMAGE_RESULT，客户端据此显示伤害。
        if gs.net_mode == "client" and gs.net_client is not None:
            # 本地攻速节流：客户端不再经 combat 冷却（判定已移交主机），用独立计时器
            # 模拟攻速，避免全自动武器/快速连点每帧重复上报刷爆网络
            if view._net_fire_cd > 0:
                return
            view._net_fire_cd = 1.0 / max(0.1, getattr(gs, 'weapon_speed', 1.0))
            # 攻击方向（弧度）：由屏幕坐标 → 世界坐标目标方向换算，主机据此重建攻击朝向
            angle = math.atan2(world_y - view.player.center_y, world_x - view.player.center_x)
            # 附带装备/武器附加 debuff 列表（元素为 (效果ID, 效果等级) 元组）：
            # 主机权威裁决命中时一并施加，使客户端装备特殊效果（中毒/燃烧/冰冻等）联网生效
            debuffs = getattr(view, "_attack_debuffs", [])
            gs.net_client.send((MsgType.ATTACK_EVENT, {
                "attacker_id": getattr(gs, "net_player_id", 0),  # 客户端联机玩家 id（大厅接入后由 gs.net_player_id 提供）
                "weapon": view._current_weapon_name(),
                "angle": angle,
                # x/y = 攻击瞬间玩家世界坐标：主机据此修正幽灵位置（20Hz 快照滞后），
                # 避免近战扇形/远程弹丸因幽灵位置滞后判定 miss（修复客户端攻击打不中怪物）
                "x": view.player.center_x,
                "y": view.player.center_y,
                "damage": getattr(gs, 'weapon_damage', 0),  # 实际伤害（升级武器以客户端为准，主机据此裁决，修复联机假伤害）
                "debuffs": list(debuffs),  # (效果ID, 效果等级) 元组列表
                "timestamp": time.time() * 1000.0,  # 时间戳（毫秒），供主机去重/延迟测量
            }))
            # 本地表现：攻击闪白 + 命中反馈由主机 DAMAGE_RESULT 驱动（不本地判定）
            view._player_attack_flash = 0.1
            view._attack_this_frame = True
            if kind == "melee":
                sound_manager.play_attack()
                # 攻击斩击粒子（本地表现，命中反馈由主机 DAMAGE_RESULT 驱动）
                dx = world_x - view.player.center_x
                dy = world_y - view.player.center_y
                dist = math.hypot(dx, dy)
                if dist > 0:
                    particle_system.emit_directional(
                        view.player.center_x, view.player.center_y,
                        8, math.degrees(math.atan2(dy, dx)),
                        (255, 255, 200), speed=120, life=0.2, size=3, spread=40
                    )
                # 记录攻击用于范围可视化
                view._attack_visual = (view.player.center_x, view.player.center_y,
                                       world_x, world_y, weapon_range, 0.2)
            else:
                sound_manager.play_ranged_attack()
                # 本地生成纯表现弹丸/激光（仅渲染运动，命中判定收敛主机由 DAMAGE_RESULT 驱动）：
                # 修复「客户端发射的远程子弹不可见」——之前只发 ATTACK_EVENT 不本地生成弹丸，
                # 弹丸只在主机渲染，客户端看不到自己的子弹。
                # 本地弹丸随 combat.update 推进/碰墙消失，但 check_monster_hits 在客户端被跳过，
                # 因此不产生本地命中判定（攻击判定仍在主机权威）。
                # 注意：ranged_attack/spawn_laser 内部检查 can_attack 冷却（按 attacker_id=0），
                # 客户端节流已由 _net_fire_cd 承担，此处先清除冷却键保证本地表现弹丸每次都生成
                # （避免视觉断续），本地纯表现弹丸不参与主机权威的命中判定。
                view.combat._cooldowns.pop(0, None)
                if getattr(gs, 'weapon_special', '') == "laser":
                    view.combat.spawn_laser(
                        view.player,
                        gs.weapon_damage,
                        world_x, world_y,
                        length=weapon_range,
                        width=24,
                        duration=3.0,
                    )
                else:
                    view.combat.ranged_attack(
                        view.player,
                        gs.weapon_damage,
                        getattr(gs, 'weapon_proj_speed', 400),
                        world_x, world_y,
                        getattr(gs, 'weapon_special', ''),
                        None,  # debuff_id：命中判定收敛主机，本地弹丸不携带（不判定）
                        getattr(gs, 'weapon_speed', 1.0),
                        debuffs=None,
                        # 散射/吸血：客户端本地纯表现弹丸，与主机裁决口径一致（视觉对齐）
                        lifesteal=getattr(gs, 'weapon_lifesteal', 0.0),
                        spread_count=getattr(gs, 'weapon_spread_count', 1),
                        spread_angle=getattr(gs, 'weapon_spread_angle', 0.0),
                    )
            return

        if not view.combat.can_attack():
            return

        if kind == "melee":
            hit = view.combat.melee_attack(
                view.player,
                [m for m in view.monsters if hasattr(m, 'alive') and m.alive],
                gs.weapon_damage,
                weapon_range,
                world_x, world_y,
                getattr(gs, 'weapon_speed', 1.0),
                # 吸血剑：近战命中按实际伤害比例回血（攻击者=本地玩家）
                lifesteal=getattr(gs, 'weapon_lifesteal', 0.0),
            )
            view._player_attack_flash = 0.1
            view._attack_this_frame = True
            sound_manager.play_attack()

            # 攻击斩击粒子
            dx = world_x - view.player.center_x
            dy = world_y - view.player.center_y
            dist = math.hypot(dx, dy)
            if dist > 0:
                angle = math.degrees(math.atan2(dy, dx))
                particle_system.emit_directional(
                    view.player.center_x, view.player.center_y,
                    8, angle, (255, 255, 200), speed=120, life=0.2, size=3, spread=40
                )

            # 记录攻击用于范围可视化
            view._attack_visual = (view.player.center_x, view.player.center_y,
                                   world_x, world_y, weapon_range, 0.2)

            # 命中怪物反馈
            if hit:
                sound_manager.play_monster_hit()
                for m, actual in hit:
                    floating_texts.add_damage(m.center_x, m.center_y + 25, actual)
                    # 施加武器/装备附加的攻击效果（元素为 (效果ID, 效果等级) 元组）
                    for eid, lvl in view._attack_debuffs:
                        if hasattr(m, 'apply_debuff'):
                            m.apply_debuff(eid, lvl)
        else:
            # 陨星炮（激光神器）
            if getattr(gs, 'weapon_special', '') == "laser":
                view.combat.spawn_laser(
                    view.player,
                    gs.weapon_damage,
                    world_x, world_y,
                    length=weapon_range,
                    width=24,
                    duration=3.0,
                )
            else:
                # 权杖：每颗子弹随机附带一种 debuff
                debuff_id = None
                if gs.current_weapon_id == "scepter":
                    debuff_id = random.choice(DEBUFF_POOL)
                view.combat.ranged_attack(
                    view.player,
                    gs.weapon_damage,
                    getattr(gs, 'weapon_proj_speed', 400),
                    world_x, world_y,
                    getattr(gs, 'weapon_special', ''),
                    debuff_id,
                    getattr(gs, 'weapon_speed', 1.0),
                    # 散射/吸血：单机命中判定在此弹丸上完成（check_monster_hits 统一结算）
                    lifesteal=getattr(gs, 'weapon_lifesteal', 0.0),
                    spread_count=getattr(gs, 'weapon_spread_count', 1),
                    spread_angle=getattr(gs, 'weapon_spread_angle', 0.0),
                )
            view._player_attack_flash = 0.1
            view._attack_this_frame = True
            sound_manager.play_ranged_attack()


def handle_mouse_release(view, x, y, button, modifiers):
    """鼠标释放时清除按住状态，停止全自动武器持续射击"""
    # 观战模式：禁操作
    if getattr(view, "_spectating", False):
        return
    if button == arcade.MOUSE_BUTTON_LEFT:
        view._left_mouse_held = False


def screen_to_world(view, x, y):
    """屏幕坐标转世界坐标"""
    cam = view.controller.camera.position
    return x + cam.x - view.window.width / 2, y + cam.y - view.window.height / 2


def _handle_skill_key(view):
    """角色技能释放（F 键）

    - 单机/主机本地：use_skill 直接释放（伤害/位移/护盾本地生效）
    - 联机客户端：上报 SKILL_USE（主机在对应幽灵上权威裁决命中/位移/护盾），
      本地仅纯表现（弹丸/瞬移/护盾视觉 + 本地冷却计时），与 ATTACK_EVENT 同构
    """
    from game.character_skills import use_skill, can_use_skill
    gs = view.window.game_state
    if not can_use_skill(view.player):
        return  # 冷却中/眩晕/无技能角色：不消耗
    # 鼠标世界坐标（技能方向/落点；与 handle_mouse_press 同口径换算）
    cam = view.controller.camera.position
    world_x = view._mouse_x + cam[0] - view.window.width / 2
    world_y = view._mouse_y + cam[1] - view.window.height / 2
    damage = getattr(gs, "weapon_damage", 0)
    if gs.net_mode == "client" and gs.net_client is not None:
        # 客户端：上报 SKILL_USE（主机权威裁决），本地仅纯表现
        gs.net_client.send((MsgType.SKILL_USE, {
            "player_id": getattr(gs, "net_player_id", 0),
            "x": view.player.center_x,
            "y": view.player.center_y,
            "mouse_x": world_x,
            "mouse_y": world_y,
            "damage": damage,
        }))
        use_skill(view, view.player, world_x, world_y, damage, broadcast=False)
        return
    # 单机/主机本地：直接释放（含命中判定与广播；solo 下 broadcast 无副作用自动跳过）
    use_skill(view, view.player, world_x, world_y, damage, broadcast=True)
