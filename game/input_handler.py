"""输入处理：键盘和鼠标事件"""

import random
import arcade
from config import FRUIT_SPEED_MULT
from game.sound_manager import sound_manager
from game.effects import particle_system, floating_texts
from entities.effects_defs import DEBUFF_POOL


def handle_key_press(view, key, modifiers):
    """处理键盘按下事件"""
    gs = view.window.game_state

    # 控制器
    if view.controller:
        view.controller.on_key_press(key)

    # 宝箱交互键
    if key == arcade.key.E:
        view._chest_key_pressed = True

    # 背包界面键 (TAB)
    if key == arcade.key.TAB:
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
            import math
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
    if view.controller:
        view.controller.on_key_release(key)
    if key == arcade.key.E:
        view._chest_key_pressed = False


def handle_mouse_motion(view, x, y, dx, dy):
    """处理鼠标移动事件"""
    view._mouse_x, view._mouse_y = x, y


def handle_mouse_press(view, x, y, button, modifiers):
    """处理鼠标按下事件"""
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

        if not view.combat.can_attack():
            return

        if kind == "melee":
            hit = view.combat.melee_attack(
                view.player,
                [m for m in view.monsters if hasattr(m, 'alive') and m.alive],
                gs.weapon_damage,
                weapon_range,
                world_x, world_y,
            )
            view._player_attack_flash = 0.1
            view._attack_this_frame = True
            sound_manager.play_attack()

            # 攻击斩击粒子
            import math
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
                )
            view._player_attack_flash = 0.1
            view._attack_this_frame = True
            sound_manager.play_ranged_attack()


def handle_mouse_release(view, x, y, button, modifiers):
    """鼠标释放时清除按住状态，停止全自动武器持续射击"""
    if button == arcade.MOUSE_BUTTON_LEFT:
        view._left_mouse_held = False


def screen_to_world(view, x, y):
    """屏幕坐标转世界坐标"""
    cam = view.controller.camera.position
    return x + cam.x - view.window.width / 2, y + cam.y - view.window.height / 2
