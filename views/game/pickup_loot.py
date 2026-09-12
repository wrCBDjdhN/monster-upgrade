"""PickupLootManager：拾取/战利品交互逻辑（从 GameView 抽取）

将 views/game_view.py 中与拾取、战利品、环境交互相关的逻辑集中到独立管理器，
GameView 通过持有本管理器实例（self.pickup_loot）调用，降低 God-class 复杂度。

管理器通过 self.gv 引用 GameView，所有对 GameView 属性的访问统一走 self.gv.*。
"""

import math
import arcade
from game.loot import DropItem, try_pickup
from game.evac import commit_run_to_warehouse, clear_run
from game.effects import particle_system, floating_texts
from game.sound_manager import sound_manager
from game.entity_callbacks import (
    handle_harvestable_combat, on_harvestable_destroyed,
    handle_chest_interaction, handle_well_interaction,
    spawn_chest_loot, scatter_drops, sync_obstacles, _award_exp,
)
from config import EXP_EVAC, LIFESTEAL_DEFAULT, SPREAD_COUNT_DEFAULT, SPREAD_ANGLE_DEFAULT, ROCKET_PAD_INTERACT_RANGE, CHEST_WELL_INTERACT_RANGE
from net.protocol import MsgType


class PickupLootManager:
    """拾取/战利品交互管理器：封装 GameView 中拾取与交互相关方法

    构造时接收 GameView 实例并保存为 self.gv，方法内部通过 self.gv 访问
    玩家/掉落/宝箱/环境物/火箭发射台等 GameView 运行时状态。
    """

    def __init__(self, game_view):
        """保存 GameView 引用，供各方法访问游戏运行时状态"""
        self.gv = game_view

    def _record_player_pickup(self, player_id: int, drop: DropItem) -> None:
        """主机记录某玩家拾取的掉落物到 _players_run_carried（撤离结算清单数据源，Todo 19 消费）

        run_carried 结构口径与 GameState.run_carried 完全一致：
        - gold: 直接累加 quantity；
        - resource/potion: {item_id: qty}；
        - weapon/helmet/armor/backpack: {(item_id, level): qty}（同名不同等级并存）。
        主机在拾取仲裁成功时累加；该玩家撤离时据此下发 EVAC_RESULT 清单（各端写各自本地库）。
        """
        carried = self.gv._players_run_carried.setdefault(player_id, {})
        if drop.item_type == "gold":
            carried.setdefault("gold", 0)
            carried["gold"] += drop.quantity
        elif drop.item_type in ("weapon", "helmet", "armor", "backpack"):
            carried.setdefault(drop.item_type, {})
            key = (drop.item_id, drop.level)
            carried[drop.item_type][key] = carried[drop.item_type].get(key, 0) + drop.quantity
        else:  # resource / potion：以 item_id 为键
            carried.setdefault(drop.item_type, {})
            key = drop.item_id
            carried[drop.item_type][key] = carried[drop.item_type].get(key, 0) + drop.quantity

    def _handle_harvestable_combat(self, dt: float) -> None:
        """处理玩家对环境物的攻击 - 委托给 entity_callbacks"""
        handle_harvestable_combat(self.gv, dt)

    def _handle_chest_interaction(self) -> None:
        """宝箱交互 - 委托给 entity_callbacks"""
        handle_chest_interaction(self.gv)

    def _handle_well_interaction(self) -> None:
        """水井交互 - 委托给 entity_callbacks"""
        handle_well_interaction(self.gv)

    def _handle_rocket_pad_interaction(self) -> None:
        """火箭发射台交互：委托给 entity_callbacks"""
        from game.entity_callbacks import handle_rocket_pad_interaction
        handle_rocket_pad_interaction(self.gv)

    def _send_client_interaction_request(self, gs: "GameState") -> None:
        """客户端发送交互请求给主机（宝箱/水井/火箭发射台）

        按E时检测附近可交互物，发送 INTERACTION_REQUEST 给主机裁决。
        主机处理后通过 MAP_CHANGE 广播结果，客户端镜像状态。
        """
        if not self.gv._chest_key_pressed or self.gv._spectating:
            return
        if gs.net_client is None:
            print("[Client] net_client is None, skip interaction")
            return

        player_x = self.gv.player.center_x
        player_y = self.gv.player.center_y
        interaction_type = None

        print(f"[Client] 检测交互: chests={len(self.gv.chests)}, well={self.gv.map_data.get('water_well')}, pads={len(self.gv.rocket_pads)}")

        # 检测宝箱
        for i, chest in enumerate(self.gv.chests):
            if chest.opened:
                continue
            dist = math.hypot(chest.center_x - player_x, chest.center_y - player_y)
            print(f"[Client] 宝箱 {i}: pos=({chest.center_x},{chest.center_y}), opened={chest.opened}, dist={dist:.1f}")
            if dist < CHEST_WELL_INTERACT_RANGE:
                interaction_type = "chest"
                break

        # 检测水井
        if interaction_type is None:
            well = self.gv.map_data.get("water_well")
            if well:
                dist = math.hypot(well[0] - player_x, well[1] - player_y)
                print(f"[Client] 水井: pos=({well[0]},{well[1]}), dist={dist:.1f}")
                if dist < CHEST_WELL_INTERACT_RANGE:
                    interaction_type = "well"

        # 检测火箭发射台
        if interaction_type is None:
            for i, pad in enumerate(self.gv.rocket_pads):
                dist = math.hypot(pad.center_x - player_x, pad.center_y - player_y)
                print(f"[Client] 火箭台 {i}: pos=({pad.center_x},{pad.center_y}), state={pad.state}, dist={dist:.1f}")
                if dist < ROCKET_PAD_INTERACT_RANGE and pad.state in ("idle", "boss_defeated"):
                    interaction_type = "rocket_pad"
                    break

        print(f"[Client] 交互类型: {interaction_type}")

        # 发送交互请求
        if interaction_type:
            gs.net_client.send((MsgType.INTERACTION_REQUEST, {
                "player_id": getattr(gs, "net_player_id", 0),
                "interaction_type": interaction_type,
                "x": player_x,
                "y": player_y,
            }))
            self.gv._chest_key_pressed = False  # 消费按键
            print(f"[Client] 发送交互请求: {interaction_type}")

    def _apply_free_equip(self, d: dict) -> None:
        """无背包拾取空槽位武器/装备/背包时，立即在局内生效（穿戴 / 加防御 / 获得容量）

        由 game/loot.py 的 try_pickup 在免费装备成功时回调触发；
        用于同步 GameState 与玩家实体的即时状态，避免仅入包而不生效。
        """
        gs = self.gv.window.game_state
        # 记录局内免费拾取的物品 ID，撤离时仅将这些物品入库（避免带入仓库的装备重复入库）
        gs.free_equipped_item_ids.add(d.item_id)
        if d.item_type == "weapon":
            # 同步武器槽位与战斗属性（伤害/攻速/距离/远程特效），参数与 setup() 加载仓库武器一致
            from entities.weapon_defs import ALL_WEAPONS
            wdef = ALL_WEAPONS.get(d.item_id)
            if not wdef:
                return
            # 修复：不写 gs.equipped_weapon_id —— 该字段语义是 weapons 表 DB row id（int），
            # 局内免费拾取的武器尚无 DB 记录（撤离时才 create_weapon 入库）。
            # 旧版误写 item_id 字符串会导致：撤离后 setup() 匹配不到 DB 武器（变拳头），
            # 且 try_pickup 因该字段非 None 判定"已装备武器"→ 无背包时无法免费拾取武器。
            # 局内"是否已装备武器"改由 current_weapon_item_id 承担（try_pickup 调用处已改）。
            gs.current_weapon_kind = wdef.get("kind", "melee")
            gs.current_weapon_id = d.item_id
            gs.current_weapon_item_id = d.item_id   # 供渲染视觉
            gs.weapon_damage = wdef.get("damage", 8)
            gs.weapon_speed = wdef.get("attack_speed", 1.0)
            gs.weapon_range = wdef.get("range", 40)
            # 等级系统：拾取新武器会重置 weapon_damage/weapon_speed，这里补回角色永久加成
            # （加成在 setup() 缓存到 gs.level_bonus_damage/level_bonus_atk_speed，与 setup 加载武器同口径）
            gs.weapon_damage += getattr(gs, "level_bonus_damage", 0)
            gs.weapon_speed += getattr(gs, "level_bonus_atk_speed", 0)
            if gs.current_weapon_kind == "ranged":
                gs.weapon_proj_speed = wdef.get("projectile_speed", 400)
                gs.weapon_special = wdef.get("special", "")
                gs.weapon_auto_fire = wdef.get("auto_fire", False)
            else:
                gs.weapon_proj_speed = 0
                gs.weapon_special = ""
                gs.weapon_auto_fire = False
            # 武器扩展机制：吸血/散射/光环（与 setup() 加载仓库武器一致）
            gs.weapon_lifesteal = wdef.get("lifesteal", LIFESTEAL_DEFAULT)
            gs.weapon_spread_count = wdef.get("spread_count", SPREAD_COUNT_DEFAULT)
            gs.weapon_spread_angle = wdef.get("spread_angle", SPREAD_ANGLE_DEFAULT)
            gs.weapon_aura_slow = wdef.get("aura_slow", False)
            gs.weapon_aura_radius = wdef.get("aura_radius", 0)
            # 刷新武器名缓存，避免渲染层因 current_weapon_id 匹配不到数据库行而显示"拳头"
            self.gv._cached_weapon_id = d.item_id
            self.gv._cached_weapon_name = wdef.get("name", "武器")
        elif d.item_type in ("helmet", "armor"):
            from entities.equipment_defs import HELMETS, ARMORS
            from entities.effects_defs import roll_effects_for_slot, parse_effect_item, effect_params, EFFECTS as _EFFECTS_DEFS
            defs = HELMETS if d.item_type == "helmet" else ARMORS
            edef = defs.get(d.item_id, {})
            if d.item_type == "helmet":
                gs.equipped_helmet_id = d.item_id
            else:
                gs.equipped_armor_id = d.item_id
            # 防御即时生效（与 setup() 累加装备基础防御的语义一致）
            self.gv.player.defense += edef.get("defense", 0)
            # 修复：生成装备被动效果并应用到玩家（与 setup() 一致）
            # 地面掉落无 effects 属性，需按等级动态生成；被动效果含 max_hp/regen/speed/damage/lifesteal/thorns/crit_chance
            effects = roll_effects_for_slot(getattr(d, "level", 1) or 1, d.item_type)
            for e in effects:
                eid, elvl = parse_effect_item(e)
                edata = _EFFECTS_DEFS.get(eid, {})
                if edata.get("type") == "passive":
                    pdata = effect_params(eid, elvl)
                    if eid == "max_hp":
                        self.gv.player.max_hp += int(pdata.get("value", 25))
                        self.gv.player.hp += int(pdata.get("value", 25))
                    elif eid == "regen":
                        self.gv.player.regen_per_sec += pdata.get("value", 1)
                    elif eid == "speed":
                        self.gv.player.gear_speed_mult *= (1.0 + pdata.get("value", 0.30))
                    elif eid == "defense":
                        self.gv.player.defense += int(pdata.get("value", 3))
                    elif eid == "damage":
                        current = getattr(self.gv.player, "equip_damage_mult", 1.0)
                        self.gv.player.equip_damage_mult = current * (1.0 + pdata.get("value", 0.08))
                    elif eid == "lifesteal":
                        current = getattr(self.gv.player, "equip_lifesteal", 0.0)
                        self.gv.player.equip_lifesteal = current + pdata.get("value", 0.03)
                    elif eid == "thorns":
                        current = getattr(self.gv.player, "equip_thorns", 0.0)
                        self.gv.player.equip_thorns = current + pdata.get("value", 0.10)
                    elif eid == "crit_chance":
                        current = getattr(self.gv.player, "crit_chance", 0.0)
                        self.gv.player.crit_chance = current + pdata.get("value", 0.05)
            # 同步更新 HUD 缓存，使左侧装备栏即时显示新拾取的装备（含 effects 列表）
            if self.gv._cached_equip is None:
                self.gv._cached_equip = {}
            self.gv._cached_equip[d.item_type] = {
                "id": None, "item_id": d.item_id,
                "name": edef.get("name", d.item_type),
                "defense": edef.get("defense", 0),
                "capacity": 0, "level": getattr(d, "level", 1) or 1, "effects": effects,
            }
        elif d.item_type == "backpack":
            from entities.equipment_defs import BACKPACKS
            bdef = BACKPACKS.get(d.item_id, {})
            # 获得容器即时生效：容量按背包定义设置，并同步到 GameState 供其他 View 使用
            self.gv.player.backpack_capacity = bdef.get("capacity", 0)
            gs.backpack_capacity = self.gv.player.backpack_capacity
            # 记录当前装备的背包 item_id，供背包视图装备栏显示/丢弃
            gs.equipped_backpack_id = d.item_id
            # 同步更新 HUD 缓存，使左侧装备栏即时显示新拾取的背包
            if self.gv._cached_equip is None:
                self.gv._cached_equip = {}
            self.gv._cached_equip["backpack"] = {
                "id": None, "item_id": d.item_id,
                "name": bdef.get("name", "背包"),
                "defense": 0,
                "capacity": bdef.get("capacity", 0),
                "level": 1, "effects": [],
            }

    def _add_equipped_to_carried(self, gs: "GameState") -> None:
        """撤离前将装备栏中**局内免费拾取**的物品加入 run_carried，以便 commit_run_to_warehouse 入库

        只有通过 _apply_free_equip() 记录到 free_equipped_item_ids 中的物品才会入库，
        避免从仓库带入的装备撤离后重复入库。
        """
        carried = gs.run_carried
        free_ids = gs.free_equipped_item_ids  # 局内免费拾取的物品 ID 集合

        # 武器：从 current_weapon_item_id 获取 item_id，仅免费拾取的才入库
        weapon_item_id = getattr(gs, 'current_weapon_item_id', None)
        if weapon_item_id and weapon_item_id in free_ids:
            carried.setdefault("weapon", {})
            key = (weapon_item_id, 1)  # 免费装备的武器等级默认为1
            carried["weapon"][key] = carried["weapon"].get(key, 0) + 1

        # 头盔：仅免费拾取的才入库
        helmet_id = getattr(gs, 'equipped_helmet_id', None)
        if helmet_id and helmet_id in free_ids:
            carried.setdefault("helmet", {})
            key = (helmet_id, 1)
            carried["helmet"][key] = carried["helmet"].get(key, 0) + 1

        # 护甲：仅免费拾取的才入库
        armor_id = getattr(gs, 'equipped_armor_id', None)
        if armor_id and armor_id in free_ids:
            carried.setdefault("armor", {})
            key = (armor_id, 1)
            carried["armor"][key] = carried["armor"].get(key, 0) + 1

        # 背包：仅免费拾取的才入库
        backpack_id = getattr(gs, 'equipped_backpack_id', None)
        if backpack_id and backpack_id in free_ids:
            carried.setdefault("backpack", {})
            key = (backpack_id, 1)
            carried["backpack"][key] = carried["backpack"].get(key, 0) + 1