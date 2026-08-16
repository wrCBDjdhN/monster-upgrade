"""实体交互回调：怪物死亡、环境物攻击、宝箱交互、掉落物管理"""

import math
import random
import arcade
from config import (
    CACTUS_THORN_DAMAGE, WELL_HEAL, WELL_SPEED_MULT, WELL_SPEED_DURATION,
    ROCKET_PAD_DESTROY_LOOT_COUNT, ROCKET_PAD_DESTROY_NORMAL_CHANCE,
    ROCKET_PAD_DESTROY_NORMAL_LV_MIN, ROCKET_PAD_DESTROY_NORMAL_LV_MAX,
    ROCKET_PAD_DESTROY_ARTIFACT_LV_MIN, ROCKET_PAD_DESTROY_ARTIFACT_LV_MAX,
    EXP_KILL_BASE, EXP_BOSS_MULT, EXP_HARVEST, EXP_CHEST,
)
# 怪物元数据（武器颜色/掉落表键名/死亡粒子颜色）统一从 monster_defs.py 读取
from entities.monster_defs import MONSTER_METADATA
from game.loot import DropItem, roll_loot
from game.sound_manager import sound_manager
from game.effects import particle_system, floating_texts
from game.render_helpers import draw_drop_icon


def _award_exp(view, amount: int):
    """本端玩家当前角色增加经验（等级系统：各端本地结算）

    - 写库 add_exp 自动处理升级（含连升，pending_choices 逐级累加）；
    - 同步刷新 view._level_data 缓存（HUD/升级面板读取，避免每帧查库）。
    """
    gs = view.window.game_state
    if not getattr(gs, "player_id", None):
        return
    from db.database import add_exp
    cid = getattr(gs, "character_id", "initial") or "initial"
    view._level_data = add_exp(gs.player_id, cid, amount)


def _award_kill_exp(view, monster):
    """击杀怪物经验（各端本地结算，主机按 last_attacker_id 归属判定）

    - solo：恒发本端（本地玩家击杀，攻击者 id 默认 0）；
    - host：仅击杀者为本端玩家（last_attacker_id==0，主机本地玩家约定 net_player_id=0）
      时发放；客户端击杀由客户端经 DAMAGE_RESULT 目标血量归零感知发放（见 game_view）；
    - client：on_monster_death 不在客户端运行（怪物由快照驱动），不在此发放。
    - 经验值 = EXP_KILL_BASE ×（BOSS 则 ×EXP_BOSS_MULT）。
    """
    gs = view.window.game_state
    if getattr(gs, "net_mode", "solo") == "host":
        # 主机：仅本端玩家击杀发放（客户端攻击者 id 为 1~3，非 0 时跳过）
        if getattr(monster, "last_attacker_id", 0) != 0:
            return
    amount = EXP_KILL_BASE * (EXP_BOSS_MULT if getattr(monster, "is_boss", False) else 1)
    _award_exp(view, amount)


def _on_rocket_boss_defeated(view, boss, pad):
    """火箭发射台 BOSS 被击败回调：掉落丰厚战利品"""
    from game.loot import DropItem
    pad.on_boss_defeated()
    # 等级经验：火箭台 BOSS 击杀经验（与普通 BOSS 同规则 EXP_BOSS_MULT；
    # 该死亡回调不走 on_monster_death，需在此单独发放）
    _award_kill_exp(view, boss)
    # BOSS 掉落：武器 + 护甲 + 头盔 + 金币 + 矿石
    drops = []
    # 掉落武器
    if boss.weapon:
        weapon_item_id = boss.weapon.get("item_id", "")
        weapon_level = boss.weapon.get("level", 1)
        drop = DropItem(boss.center_x, boss.center_y, "weapon", weapon_item_id, 1, level=weapon_level)
        drops.append(drop)
    # 掉落护甲
    if boss.armor:
        armor_item_id = boss.armor.get("item_id", "")
        armor_level = boss.armor.get("level", 1)
        drop = DropItem(boss.center_x + 20, boss.center_y, "armor", armor_item_id, 1, level=armor_level)
        drops.append(drop)
    # 掉落头盔
    if boss.helmet:
        helmet_item_id = boss.helmet.get("item_id", "")
        helmet_level = boss.helmet.get("level", 1)
        drop = DropItem(boss.center_x - 20, boss.center_y, "helmet", helmet_item_id, 1, level=helmet_level)
        drops.append(drop)
    # 额外掉落金币和矿石（BOSS必掉）
    drops.append(DropItem(boss.center_x, boss.center_y + 20, "gold", "gold", 50))
    drops.append(DropItem(boss.center_x + 30, boss.center_y, "resource", "ore", 5))
    # 分散掉落物位置
    scatter_drops(drops, boss.center_x, boss.center_y)
    view.drops.extend(drops)
    floating_texts.add(pad.center_x, pad.center_y + 50,
                       "BOSS 已击败! 按7炸毁 | 按8启用撤离",
                       arcade.color.GREEN, life=3.0)


def on_monster_death(view, monster):
    """怪物死亡回调"""
    # 死亡音效
    sound_manager.play_death()

    # 死亡粒子爆炸（死亡颜色从 MONSTER_METADATA 按怪物类名读取）
    cls_name = monster.__class__.__name__
    meta = MONSTER_METADATA.get(cls_name)
    color = meta["death_color"] if meta else (255, 255, 255)
    particle_system.emit(monster.center_x, monster.center_y, 20, color, speed=150, life=0.6, size=4, gravity=100)

    # 掉落物（掉落表键名同样取自 MONSTER_METADATA）
    loot = roll_loot(
        meta["loot_key"] if meta else "zombie",
        monster.center_x, monster.center_y,
    )

    # 掉落武器（如果怪物持有武器）：等级取分配时的等级，避免静默丢失
    if monster.weapon:
        weapon_item_id = monster.weapon.get("item_id", "")
        weapon_level = monster.weapon.get("level", 1)
        weapon_color = monster.weapon.get("color") or (meta["weapon_color"] if meta else (255, 255, 255))
        drop = DropItem(monster.center_x, monster.center_y, "weapon", weapon_item_id, 1, level=weapon_level)
        drop.color = weapon_color
        loot.append(drop)

    # 掉落护甲（如果怪物穿了护甲）：等级取分配时的等级，避免静默丢失
    if monster.armor:
        armor_item_id = monster.armor.get("item_id", "")
        armor_level = monster.armor.get("level", 1)
        armor_color = monster.armor.get("color", (150, 150, 150))
        drop = DropItem(monster.center_x, monster.center_y, "armor", armor_item_id, 1, level=armor_level)
        drop.color = armor_color
        loot.append(drop)

    # 掉落头盔（如果怪物戴了头盔）：等级取分配时的等级，避免静默丢失
    if monster.helmet:
        helmet_item_id = monster.helmet.get("item_id", "")
        helmet_level = monster.helmet.get("level", 1)
        helmet_color = monster.helmet.get("color", (139, 90, 43))
        drop = DropItem(monster.center_x, monster.center_y, "helmet", helmet_item_id, 1, level=helmet_level)
        drop.color = helmet_color
        loot.append(drop)

    # 分散掉落物位置，避免重叠
    scatter_drops(loot, monster.center_x, monster.center_y)
    view.drops.extend(loot)

    # 等级经验：击杀怪物经验（各端本地结算，归属判定见 _award_kill_exp）
    _award_kill_exp(view, monster)


def handle_harvestable_combat(view, dt):
    """处理玩家对环境物的攻击。

    - 近战：仅"攻击发生的那一帧"对范围内扇形内目标即时造成伤害
      （消费 _attack_this_frame 标志，避免每帧重复扣血）。
    - 弹丸命中：必须【每帧】检测，因为弹丸飞行会持续多个帧；
      若只检测攻击当帧，弹丸在后续帧飞过树木/矿石/石头时便不再判定，
      表现为"子弹穿过环境物、无法造成伤害"。
    """
    attack_happened = getattr(view, '_attack_this_frame', False)
    # 消费攻击标志（仅用于近战即时扣血，避免每帧重复扣血）
    view._attack_this_frame = False

    # 近战武器：攻击当帧对范围内扇形内的环境物即时造成伤害。
    if attack_happened and getattr(view, '_attack_kind', None) == "melee":
        px, py = view.player.center_x, view.player.center_y
        attack_range = getattr(view, '_last_attack_range', 40)
        # 屏幕坐标转世界坐标（_mouse_x/_mouse_y 是屏幕坐标，px/py 是世界坐标）
        cam = view.controller.camera.position
        world_mx = view._mouse_x + cam[0] - view.window.width / 2
        world_my = view._mouse_y + cam[1] - view.window.height / 2
        for i, h in enumerate(view.harvestables):
            if not h.alive:
                continue
            dist = math.hypot(h.center_x - px, h.center_y - py)
            if dist <= (attack_range + 30):
                # 扇形角度检查
                dx = h.center_x - px
                dy = h.center_y - py
                angle = math.degrees(math.atan2(dy, dx))
                mouse_angle = math.degrees(math.atan2(world_my - py, world_mx - px))
                diff = abs((angle - mouse_angle + 180) % 360 - 180)
                if diff <= 60:  # 在攻击扇形内
                    dmg = int(getattr(view.window.game_state, 'weapon_damage', 8))
                    h.take_damage(dmg)
                    # 联机主机：广播环境物单次受击（Bug2 修复：主机对资源的伤害同步到客户端）
                    _broadcast_env_damage = getattr(view, "_broadcast_env_damage", None)
                    if _broadcast_env_damage is not None:
                        _broadcast_env_damage(i, dmg)
                    floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                    particle_system.emit(h.center_x, h.center_y, 5, (150, 150, 150), speed=60, life=0.3, size=3)
                    # 仙人掌反伤：攻击者自身受到伤害（受防御减免）
                    if h.resource_type == "cactus":
                        view.player.take_damage(CACTUS_THORN_DAMAGE)
                        floating_texts.add_damage(view.player.center_x, view.player.center_y + 30, CACTUS_THORN_DAMAGE)
                    # 如果击杀，掉落资源
                    if not h.alive:
                        on_harvestable_destroyed(view, h)

    # 弹丸命中环境物（每帧检测，近战/远程通用，仅当弹丸真正碰撞到才造成伤害）
    for proj in list(view.combat.projectiles):
        for i, h in enumerate(view.harvestables):
            if h.alive and arcade.check_for_collision(proj, h):
                dmg = proj.damage
                h.take_damage(dmg)
                # 联机主机：广播环境物单次受击（Bug2 修复：主机对资源的伤害同步到客户端）
                _broadcast_env_damage = getattr(view, "_broadcast_env_damage", None)
                if _broadcast_env_damage is not None:
                    _broadcast_env_damage(i, dmg)
                floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                particle_system.emit(h.center_x, h.center_y, 5, (150, 150, 150), speed=60, life=0.3, size=3)
                # 仙人掌反伤：攻击者自身受到伤害（受防御减免）
                if h.resource_type == "cactus":
                    view.player.take_damage(CACTUS_THORN_DAMAGE)
                    floating_texts.add_damage(view.player.center_x, view.player.center_y + 30, CACTUS_THORN_DAMAGE)
                proj.remove_from_sprite_lists()
                if not h.alive:
                    # 采集经验按攻击者归属：客户端弹丸（owner_net_id!=0）不发给本端主机
                    on_harvestable_destroyed(view, h, award_exp=(proj.owner_net_id == 0))
                break

    # 激光命中环境物（陨星炮：路径上的矿石/树木/石头持续受到完整伤害）
    for beam in view.combat.lasers:
        for h, dmg in beam.hit_harvestables(view.harvestables):
            # 联机主机：广播环境物单次受击（Bug2 修复：主机对资源的伤害同步到客户端）
            _broadcast_env_damage = getattr(view, "_broadcast_env_damage", None)
            if _broadcast_env_damage is not None:
                _broadcast_env_damage(view.harvestables.index(h), dmg)
            floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
            particle_system.emit(h.center_x, h.center_y, 5, (150, 150, 150), speed=60, life=0.3, size=3)
            # 仙人掌反伤：攻击者自身受到伤害（受防御减免）
            if h.resource_type == "cactus":
                view.player.take_damage(CACTUS_THORN_DAMAGE)
                floating_texts.add_damage(view.player.center_x, view.player.center_y + 30, CACTUS_THORN_DAMAGE)
            if not h.alive:
                # 采集经验按攻击者归属：客户端激光（owner_net_id!=0）不发给本端主机
                on_harvestable_destroyed(view, h, award_exp=(beam.owner_net_id == 0))


def on_harvestable_destroyed(view, harvestable, award_exp: bool = True):
    """环境物被摧毁，掉落资源

    award_exp：本端玩家自己的采集才发经验。联机主机裁决客户端攻击
    （_resolve_attack_event / 客户端弹丸激光）时须传 False，避免主机误发。
    """
    sound_manager.play_pickup()
    # 等级经验：采集资源经验（各端本地结算：仅本端玩家采集发放；
    # 客户端不裁决环境物伤害，on_monster_death 类回调不在客户端运行）
    if award_exp and getattr(view.window.game_state, "net_mode", "solo") != "client":
        _award_exp(view, EXP_HARVEST)
    # 仙人掌：掉落果实药水（potion 类型，拾取后进入药水栏）
    if harvestable.resource_type == "cactus":
        drop = DropItem(harvestable.center_x, harvestable.center_y, "potion", "fruit_potion", 1)
        view.drops.append(drop)
        particle_system.emit(harvestable.center_x, harvestable.center_y, 15, (100, 200, 100), speed=100, life=0.5, size=4)
        if harvestable in view.obstacle_list:
            view.obstacle_list.remove(harvestable)
        return
    drops_info = harvestable.get_drops()
    for res_type, qty in drops_info:
        drop = DropItem(harvestable.center_x, harvestable.center_y, "resource", res_type, qty)
        view.drops.append(drop)
    particle_system.emit(harvestable.center_x, harvestable.center_y, 15, (100, 200, 100), speed=100, life=0.5, size=4)
    # 从障碍物列表移除（不再阻挡移动）
    if harvestable in view.obstacle_list:
        view.obstacle_list.remove(harvestable)


def handle_chest_interaction(view):
    """宝箱交互：靠近按E打开"""
    if not view._chest_key_pressed:
        return
    for chest in view.chests:
        if chest.opened:
            continue
        dist = math.hypot(chest.center_x - view.player.center_x, chest.center_y - view.player.center_y)
        if dist < 40:
            loot = chest.open_chest()
            spawn_chest_loot(view, chest, loot)
            # 等级经验：开宝箱经验（本回调仅在 solo/host 运行——客户端开箱由主机裁决广播）
            _award_exp(view, EXP_CHEST)
            view._chest_key_pressed = False
            break


def handle_well_interaction(view):
    """水井交互：靠近按E → 首次打开（同宝箱掉落），之后回复生命+移速加速"""
    if not view._chest_key_pressed:
        return
    well = view.map_data.get("water_well")
    if not well:
        return
    # 未靠近水井则忽略
    dist = math.hypot(well[0] - view.player.center_x, well[1] - view.player.center_y)
    if dist >= 40:
        return
    view._chest_key_pressed = False
    # 首次开启：复用宝箱开箱逻辑生成掉落
    if not getattr(view, "_well_opened", False):
        view._well_opened = True
        from game.chest import Chest
        tmp_chest = Chest(well[0], well[1])
        loot = tmp_chest.open_chest()
        spawn_chest_loot(view, tmp_chest, loot)
    else:
        # 已开启：回复生命 + 移速加速（数值来自 config）
        view.player.heal(WELL_HEAL)
        view.player.speed_mult = WELL_SPEED_MULT * view.player.gear_speed_mult
        view.player.speed_effect_timer = WELL_SPEED_DURATION
        floating_texts.add(view.player.center_x, view.player.center_y + 30,
                           f"水井 +{WELL_HEAL} HP 加速!", arcade.color.GREEN)
        particle_system.emit(well[0], well[1], 12, (100, 200, 255), speed=60, life=0.5, size=4)


def handle_rocket_pad_interaction(view):
    """火箭发射台交互：靠近按 E 激活/按 7/8 直接选择"""
    # 7/8 直接选择（无需先按E）
    if hasattr(view, '_rocket_pad_menu') and view._rocket_pad_menu is not None:
        return  # 已有菜单打开，等 input_handler 处理
    for pad in getattr(view, 'rocket_pads', []):
        dist = math.hypot(pad.center_x - view.player.center_x,
                          pad.center_y - view.player.center_y)
        if dist >= 60:
            continue
        if pad.state == "idle":
            # 按E激活
            if view._chest_key_pressed:
                view._chest_key_pressed = False
                if pad.activate():
                    from game.monsters import BossSpace
                    from game.monster_utils import assign_monster_weapon, assign_monster_armor, assign_monster_helmet
                    boss = BossSpace(center_x=pad.center_x, center_y=pad.center_y + 60)
                    # 为BOSS分配武器和装备
                    assign_monster_weapon(boss, level=15, is_space=True)
                    assign_monster_armor(boss, level=15, is_space=True)
                    assign_monster_helmet(boss, level=15, is_space=True)
                    boss.set_on_death(lambda b, p=pad: _on_rocket_boss_defeated(view, b, p))
                    view.monsters.append(boss)
                    pad.set_boss(boss)
                    sound_manager.play_rocket_launch()
                    floating_texts.add(pad.center_x, pad.center_y + 50,
                                       "BOSS 即将出现!", arcade.color.RED, life=2.0)
        elif pad.state == "boss_defeated":
            # 标记可选择状态，让 input_handler 处理 7/8
            view._rocket_pad_menu = pad


def handle_rocket_pad_choice(view, choice: int):
    """处理火箭发射台菜单选择（7=炸毁，8=启用撤离）"""
    pad = getattr(view, '_rocket_pad_menu', None)
    if pad is None:
        return
    view._rocket_pad_menu = None
    if choice == 1:  # 炸毁
        reward = pad.destroy()
        if reward:
            from game.loot import DropItem
            # 金币掉落（随机范围）
            view.drops.append(DropItem(pad.center_x, pad.center_y, "gold", "gold", reward["gold"]))
            # 随机资源掉落（总量 20-50，类型随机拆成 1~3 份）
            view.drops.extend(_generate_pad_resource_drops(pad.center_x + 20, pad.center_y,
                                                           reward["resources"]))
            # 炸毁必定掉落：2 件武器/装备（每件 70% Lv20-50 普通 / 30% Lv10-20 神器）
            drops = _generate_pad_destroy_loot(pad.center_x, pad.center_y)
            view.drops.extend(drops)
            sound_manager.play_explosion()
            floating_texts.add(pad.center_x, pad.center_y + 50,
                               f"炸毁! +{reward['gold']}金币 +{reward['resources']}资源",
                               arcade.color.YELLOW, life=2.0)
    elif choice == 2:  # 启用撤离
        pad.start_evacuation()
        sound_manager.play_rocket_launch()
        floating_texts.add(pad.center_x, pad.center_y + 50,
                           "发射台已启用! 30秒内撤离",
                           arcade.color.GREEN, life=3.0)


def _generate_pad_resource_drops(cx: float, cy: float, total: int) -> list:
    """生成火箭发射台炸毁的资源掉落：总量拆成 1~3 份，类型随机（木材/石材/矿石）"""
    from entities.resource_defs import RESOURCES
    res_types = list(RESOURCES.keys())
    drops = []
    # 随机拆成 1~3 份（每份至少 1 个，保证总和 = total）
    parts = random.randint(1, min(3, total))
    remaining = total
    for i in range(parts):
        if i == parts - 1:
            qty = remaining
        else:
            qty = random.randint(1, remaining - (parts - i - 1))
        remaining -= qty
        res_type = random.choice(res_types)
        # 每份错开位置，避免掉落物重叠
        drops.append(DropItem(cx + 20 + 16 * i, cy, "resource", res_type, qty))
    return drops


def _generate_pad_destroy_loot(cx: float, cy: float) -> list:
    """生成火箭发射台炸毁掉落物：2 件武器/装备，每件独立 70% Lv20-50 普通 / 30% Lv10-20 神器"""
    drops = []
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
    from entities.equipment_defs import HELMETS, ARMORS
    # 武器池（排除空手拳套）与神器池
    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
    weapon_ids = [k for k in all_weapons if k != "fist"]
    artifact_weapons = [k for k, v in all_weapons.items() if v.get("artifact")]
    artifact_helmets = [k for k, v in HELMETS.items() if v.get("artifact")]
    artifact_armors = [k for k, v in ARMORS.items() if v.get("artifact")]
    all_artifacts = artifact_weapons + artifact_helmets + artifact_armors

    for i in range(ROCKET_PAD_DESTROY_LOOT_COUNT):
        # 每件独立掷骰：70% 普通武器/装备（Lv20-50），30% 神器（Lv10-20）
        if random.random() < ROCKET_PAD_DESTROY_NORMAL_CHANCE:
            level = random.randint(ROCKET_PAD_DESTROY_NORMAL_LV_MIN,
                                   ROCKET_PAD_DESTROY_NORMAL_LV_MAX)
            if random.random() < 0.5:
                # 武器
                wid = random.choice(weapon_ids)
                drops.append(DropItem(cx + 30 * i, cy + 20, "weapon", wid, 1, level=level))
            else:
                # 装备（头盔/护甲）
                slot = random.choice(["helmet", "armor"])
                defs = HELMETS if slot == "helmet" else ARMORS
                item_id = random.choice(list(defs.keys()))
                drops.append(DropItem(cx + 30 * i, cy + 20, slot, item_id, 1, level=level))
        else:
            if not all_artifacts:
                continue
            level = random.randint(ROCKET_PAD_DESTROY_ARTIFACT_LV_MIN,
                                   ROCKET_PAD_DESTROY_ARTIFACT_LV_MAX)
            item_id = random.choice(all_artifacts)
            if item_id in artifact_weapons:
                drops.append(DropItem(cx + 30 * i, cy + 20, "weapon", item_id, 1, level=level))
            elif item_id in artifact_helmets:
                drops.append(DropItem(cx + 30 * i, cy + 20, "helmet", item_id, 1, level=level))
            else:
                drops.append(DropItem(cx + 30 * i, cy + 20, "armor", item_id, 1, level=level))
    return drops


def spawn_chest_loot(view, chest, loot):
    """宝箱掉落物生成"""
    chest_drops = []
    # 资源（格式: [(type, qty), ...]）
    for res_type, qty in loot.get("resources", []):
        drop = DropItem(chest.center_x, chest.center_y, "resource", res_type, qty)
        chest_drops.append(drop)
    # 武器（格式: [{"item_id": ..., "level": ...}, ...]，宝箱/水井可掉落任意武器含神器）
    for w in loot.get("weapons", []):
        drop = DropItem(chest.center_x, chest.center_y, "weapon", w["item_id"], 1,
                        level=w.get("level", 1))
        chest_drops.append(drop)
    # 装备（格式: [(item_id, slot, level), ...]）
    for entry in loot.get("equipment", []):
        item_id, slot, level = entry if len(entry) == 3 else (*entry, 1)
        drop = DropItem(chest.center_x, chest.center_y, slot, item_id, 1, level=level)
        chest_drops.append(drop)
    # 金币
    if loot.get("gold", 0) > 0:
        drop = DropItem(chest.center_x, chest.center_y, "gold", "gold", loot["gold"])
        chest_drops.append(drop)
    # 分散掉落物位置，避免重叠
    scatter_drops(chest_drops, chest.center_x, chest.center_y)
    view.drops.extend(chest_drops)


def get_drop_display_name(drop) -> str:
    """根据掉落物类型和ID获取显示名称"""
    item_type = drop.item_type
    item_id = drop.item_id
    if item_type == "gold":
        return "金币"
    elif item_type == "resource":
        from entities.resource_defs import RESOURCES
        return RESOURCES.get(item_id, {}).get("name", item_id)
    elif item_type == "weapon":
        from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
        all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
        return all_weapons.get(item_id, {}).get("name", item_id)
    elif item_type == "helmet":
        from entities.equipment_defs import HELMETS
        return HELMETS.get(item_id, {}).get("name", item_id)
    elif item_type == "armor":
        from entities.equipment_defs import ARMORS
        return ARMORS.get(item_id, {}).get("name", item_id)
    elif item_type == "backpack":
        from entities.equipment_defs import BACKPACKS
        return BACKPACKS.get(item_id, {}).get("name", item_id)
    elif item_type == "potion":
        # 仙人掌果实等掉落药水显示定义名称
        from entities.equipment_defs import POTIONS
        return POTIONS.get(item_id, {}).get("name", item_id)
    return item_id


def scatter_drops(drops: list, center_x: float, center_y: float, radius: float = 30):
    """将掉落物以圆形分散，避免重叠。每个物品围绕中心点均匀分布。"""
    if not drops:
        return
    if len(drops) == 1:
        # 单个物品不需要偏移
        return
    for i, drop in enumerate(drops):
        # 均匀分布在圆周上
        angle = (2 * math.pi * i) / len(drops)
        # 随机化半径，避免过于规律
        r = radius * (0.6 + 0.4 * (i % 3) / 2)
        drop.center_x = center_x + math.cos(angle) * r
        drop.center_y = center_y + math.sin(angle) * r


def sync_obstacles(view):
    """每帧同步障碍物列表：
    只保留墙壁 + 存活的环境物 + 未打开的宝箱。
    """
    # 原地清空并重填，物理引擎引用 self.obstacle_list 不变
    view.obstacle_list.clear()
    view.obstacle_list.extend(view.wall_list)
    for h in view.harvestables:
        if h.alive:
            view.obstacle_list.append(h)
    for chest in view.chests:
        if not chest.opened:
            view.obstacle_list.append(chest)

    # 注意：RocketPad 不是 arcade.Sprite，加入 SpriteList（obstacle_list）会触发
    # PhysicsEngine 崩溃（SpriteList.append 要求 Sprite），故发射台不参与物理碰撞，
    # 仅作为交互/装饰物（状态机与绘制由 game_view 驱动）
