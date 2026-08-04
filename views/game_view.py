"""GameView：整合地图/玩家/怪物/战斗/掉落/撤离/死亡"""

import math
import random
import copy
import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, PLAYER_HP,
    PLAYER_SIZE, PLAYER_COLOR, TILE_SIZE,
    ZOMBIE_SIZE, ZOMBIE_COLOR, ZOMBIE_ATTACK_DELAY,
    SKELETON_SIZE, SKELETON_COLOR, SKELETON_ATTACK_DELAY,
    EVAC_COLOR, EVAC_RADIUS,
    WELL_HEAL, WELL_SPEED_MULT, WELL_SPEED_DURATION,
    MONSTER_WEAPON_LEVEL_RANGE, MONSTER_GEAR_LEVEL_RANGE,
    BOSS_WEAPON_LEVEL_RANGE, BOSS_GEAR_LEVEL_RANGE,
)
from game.map_gen import generate_map
from game.player import Player, PlayerController
from game.monsters import (
    Zombie, Skeleton,
    MummyMelee, MummyRanged, Camel,
    BossZombie, BossSkeleton, BossMummy,
    Sniper, Assault, Bandit, RocketTroop, BossSpace,  # 航天基地怪物（修复：缺此映射时 space 主题刷怪全落回 Zombie）
)
from game.combat import CombatSystem
from game.batch_shapes import ShapeBatch
from game.loot import DropItem, roll_loot, try_pickup
from game.evac import EvacState, commit_run_to_warehouse, clear_run
from game.harvestable import HarvestableEntity, spawn_harvestables
from game.chest import Chest, spawn_chests
from db.database import get_weapons, get_gold, get_equipment
from game.sound_manager import sound_manager
from game.effects import particle_system, floating_texts

# 新模块导入
from game.monster_utils import lookup_weapon_range, assign_monster_armor, assign_monster_helmet, assign_monster_weapon
from game.entity_callbacks import (
    on_monster_death, handle_harvestable_combat, on_harvestable_destroyed,
    handle_chest_interaction, handle_well_interaction, spawn_chest_loot,
    get_drop_display_name, scatter_drops, sync_obstacles,
)
from game.respawn import respawn_harvestables, respawn_monsters
from game.rendering import render_game
from game.input_handler import (
    handle_key_press, handle_key_release, handle_mouse_motion,
    handle_mouse_press, handle_mouse_release,
)


class GameView(arcade.View):
    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self.map_data = None
        self.player = None
        self.controller = None
        self.combat = None
        self.evac = None
        self._frame = 0
        # 缓存（避免每帧查 DB）
        self._cached_equip = None
        self._cached_potions = None
        self._cached_db_gold = None
        self._cache_frame = -999
        self.monsters: list = []
        self.drops: list[DropItem] = []
        self.projectiles = None
        # 可破坏环境物
        self.harvestables: list = []
        # 资源随机刷新（避免被砍光后地图长期空荡）：每隔一段时间在空地补刷
        self._harvest_respawn_timer = 0.0
        self._harvest_respawn_interval = 6.0   # 每 6 秒尝试补刷一次
        self._harvest_respawn_batch = 3        # 每次最多补刷数量
        self._harvest_cap = 50                 # 地图上资源上限（含初始）
        # 野外怪物刷新（持续补充被击杀的怪物）
        self._monster_respawn_timer = 0.0
        self._monster_respawn_interval = 10.0  # 每 10 秒尝试补刷一次
        self._monster_respawn_batch = 2        # 每次最多补刷数量
        self._monster_cap = 20                 # 野外怪物上限（含初始）
        # 宝箱
        self.chests: list = []
        # 火箭发射台（仅 space 主题有内容，防御性初始化为空列表）
        self.rocket_pads: list = []
        # 发射台菜单选择状态（None=未开启；pad=已开启 1/2 菜单）
        self._rocket_pad_menu = None
        # 精灵列表
        self.wall_list = arcade.SpriteList()
        self.evac_sprites = arcade.SpriteList()
        # 死亡/撤离消息
        self._message = ""
        self._message_timer = 0.0
        # 背包已满提示节流计时器（避免每帧刷屏）
        self._backpack_full_cd = 0.0
        # 特效状态
        self._player_hit_flash = 0.0    # 玩家受击屏幕闪红
        self._player_attack_flash = 0.0 # 玩家攻击闪白
        self._attack_visual = None      # (px, py, mx, my, range, timer) 攻击范围可视化
        # 鼠标位置缓存
        self._mouse_x = 0.0
        self._mouse_y = 0.0
        # 武器名称缓存
        self._cached_weapon_name = "拳头"
        self._cached_weapon_id = None
        # HUD 文本对象缓存：用持久 arcade.Text，仅在字符串变化时重绘纹理，
        # 避免每帧 draw_text 重建纹理导致的卡顿
        self._hud_texts: dict = {}
        # 撤离圈单位圆环多边形缓存（形状固定，仅平移）
        self._evac_unit_ring = None
        # 宝箱按键状态
        self._chest_key_pressed = False
        # 水井是否已首次开启（首次开箱掉落，之后回血加速）
        self._well_opened = False
        # 最近一次攻击距离
        self._last_attack_range = 40
        # 本帧是否发生攻击
        self._attack_this_frame = False
        # 本次攻击的武器类型（melee/ranged），用于区分近战即时伤害与远程弹丸伤害
        self._attack_kind = None
        # 鼠标左键按住状态（用于全自动武器持续射击）
        self._left_mouse_held = False
        # 世界坐标标签列表（在HUD阶段绘制）：[(world_x, world_y, text, color, font_size)]
        self._world_labels: list = []
        # 怪物精灵列表缓存（弹丸碰撞检测用，每帧在 on_update 中构建）
        self._monster_sprite_list = arcade.SpriteList()
        # 行动时间倒计时（space=8min, forest/desert=5min）
        self._action_time_remaining = None  # None=无限，float=剩余秒数

    def setup(self):
        gs = self.window.game_state
        seed = gs.current_map_seed
        # 按所选地图主题生成（forest 森林 / desert 沙漠荒地）
        theme = getattr(gs, "map_theme", "forest")
        self.map_data = generate_map(seed, theme=theme)

        # 行动时间（按主题设定）
        from config import ACTION_TIME_SPACE, ACTION_TIME_FOREST, ACTION_TIME_DESERT
        theme = getattr(gs, "map_theme", "forest")
        if theme == "space":
            self._action_time_remaining = ACTION_TIME_SPACE
        elif theme == "desert":
            self._action_time_remaining = ACTION_TIME_DESERT
        else:
            self._action_time_remaining = ACTION_TIME_FOREST
        gs.action_time_remaining = self._action_time_remaining  # 同步到 GameState 供 HUD 读取

        # 玩家
        sx, sy = self.map_data["rooms"][0].center if self.map_data["rooms"] else (400, 400)
        self.player = Player(center_x=sx, center_y=sy)

        # 加载装备属性
        equip = {}
        if gs.player_id:
            from db.database import get_equipment, get_backpack_capacity
            from entities.effects_defs import (
                EFFECTS as _EFFECTS_DEFS, parse_effect_item, effect_params,
            )
            equip = get_equipment(gs.player_id)
            total_def = 0
            for slot_name in ("helmet", "armor", "backpack"):
                if slot_name in equip:
                    eq = equip[slot_name]
                    total_def += eq.get("defense", 0)
                    if slot_name == "helmet":
                        gs.equipped_helmet_id = eq["item_id"]
                    elif slot_name == "armor":
                        gs.equipped_armor_id = eq["item_id"]
                    # 处理装备被动效果（max_hp / regen / speed）
                    # 修复：效果元素为 "id:level" 格式，需先 parse_effect_item 解析出效果id与等级，
                    # 再用 effect_params 取分级后的数值（与武器效果处理逻辑保持一致），否则效果不生效
                    for e in eq.get("effects", []):
                        eid, elvl = parse_effect_item(e)
                        edata = _EFFECTS_DEFS.get(eid, {})
                        if edata.get("type") == "passive":
                            pdata = effect_params(eid, elvl)
                            if eid == "max_hp":
                                self.player.max_hp += pdata.get("value", 25)
                                self.player.hp += pdata.get("value", 25)
                            elif eid == "regen":
                                self.player.regen_per_sec += pdata.get("value", 1)
                            elif eid == "speed":
                                self.player.gear_speed_mult *= (1.0 + pdata.get("value", 0.30))
            self.player.defense = total_def
            self.player.backpack_capacity = get_backpack_capacity(gs.player_id)
            gs.backpack_capacity = self.player.backpack_capacity  # 同步到 GameState 供其他 View 使用

        # 墙壁精灵（参与碰撞）
        self.wall_list = arcade.SpriteList()
        for wx, wy, ww, wh in self.map_data["walls"]:
            if ww > 0 and wh > 0:
                wall = arcade.SpriteSolidColor(ww, wh, color=(60, 60, 60))
                wall.center_x = wx + ww // 2
                wall.center_y = wy + wh // 2
                self.wall_list.append(wall)

        # 完整障碍物列表（墙壁 + 环境物 + 宝箱）用于物理碰撞
        self.obstacle_list = arcade.SpriteList()
        self.obstacle_list.extend(self.wall_list)

        # 可破坏环境物（树/矿石/石头）
        self.harvestables = []
        for hx, hy, htype in self.map_data.get("harvestables", []):
            h = HarvestableEntity(hx, hy, htype)
            self.harvestables.append(h)
            self.obstacle_list.append(h)

        # 野外金币（地图上随机分布的拾取物，永不消失）
        for gx, gy in self.map_data.get("wild_coins", []):
            coin = DropItem(gx, gy, "gold", "gold", 1)
            coin._lifetime = None  # 地图初始金币永不消失
            self.drops.append(coin)

        # 房间内资源点（每个房间 3~5 个，永不消失）
        for rx, ry, rtype in self.map_data.get("resources", []):
            res = DropItem(rx, ry, "resource", rtype, 1)
            res._lifetime = None  # 地图初始资源永不消失
            self.drops.append(res)

        # 宝箱（每个房间1个）
        self.chests = []
        for cx, cy in self.map_data.get("chest_positions", []):
            chest = Chest(cx, cy)
            self.chests.append(chest)
            self.obstacle_list.append(chest)

        # 火箭发射台（仅 space 主题）
        self.rocket_pads = []
        if theme == "space":
            from game.rocket_pad import RocketPad
            for rpx, rpy in self.map_data.get("rocket_pads", []):
                rp = RocketPad(rpx, rpy)
                self.rocket_pads.append(rp)

        # 怪物
        walls_for_collision = self.map_data["walls"]

        # 怪物类型字符串 → 类映射（与 game/respawn.py 的类型池保持一致）
        _MONSTER_CLASSES = {
            "zombie": Zombie,
            "skeleton": Skeleton,
            "mummy_melee": MummyMelee,
            "mummy_ranged": MummyRanged,
            "camel": Camel,
            # 航天基地怪物（修复：缺失时 spawn_points/wild_spawns 的 space 类型全落回 Zombie）
            "sniper": Sniper,
            "assault": Assault,
            "bandit": Bandit,
            "rocket_troop": RocketTroop,
        }
        _BOSS_CLASSES = {
            "boss_zombie": BossZombie,
            "boss_skeleton": BossSkeleton,
            "boss_mummy": BossMummy,
            "boss_space": BossSpace,  # 修复：缺失时 space 主题的 BOSS 建筑永远空置
        }

        # 房间内怪物
        for mx, my, mtype in self.map_data["spawn_points"]:
            cls = _MONSTER_CLASSES.get(mtype, Zombie)
            m = cls(center_x=mx, center_y=my)
            m.set_on_death(self._on_monster_death)
            # 随机穿戴护甲、头盔和武器（普通怪装备等级 Lv1-10；木乃伊系怪物可携带木乃伊武器）
            is_desert = mtype in ("mummy_melee", "mummy_ranged", "camel")
            assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), is_desert=is_desert)
            assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), is_desert=is_desert)
            assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), is_desert=is_desert)
            m._walls = walls_for_collision
            self.monsters.append(m)

        # 野外怪物
        for wx, my, mtype in self.map_data.get("wild_spawns", []):
            cls = _MONSTER_CLASSES.get(mtype, Zombie)
            m = cls(center_x=wx, center_y=my)
            m.set_on_death(self._on_monster_death)
            m._walls = walls_for_collision
            # 随机穿戴护甲、头盔和武器（普通怪装备等级 Lv1-10；木乃伊系怪物可携带木乃伊武器）
            is_desert = mtype in ("mummy_melee", "mummy_ranged", "camel")
            assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), is_desert=is_desert)
            assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), is_desert=is_desert)
            assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), is_desert=is_desert)
            self.monsters.append(m)

        # BOSS（每局仅 1 个，位于金字塔/角落建筑内，不参与野外刷新）
        boss_spawn = self.map_data.get("boss_spawn")
        boss_type = self.map_data.get("boss_type")
        if boss_spawn and boss_type and boss_type in _BOSS_CLASSES:
            boss = _BOSS_CLASSES[boss_type](center_x=boss_spawn[0], center_y=boss_spawn[1])
            boss.set_on_death(self._on_monster_death)
            boss._walls = walls_for_collision
            # BOSS 穿戴护甲、头盔和武器（BOSS 装备等级 Lv20-30；BOSS木乃伊可携带木乃伊武器）
            is_desert = boss_type == "boss_mummy"
            assign_monster_armor(boss, level=random.randint(*BOSS_GEAR_LEVEL_RANGE), is_desert=is_desert)
            assign_monster_helmet(boss, level=random.randint(*BOSS_GEAR_LEVEL_RANGE), is_desert=is_desert)
            assign_monster_weapon(boss, level=random.randint(*BOSS_WEAPON_LEVEL_RANGE), is_desert=is_desert)
            self.monsters.append(boss)

        # 水井守卫（沙漠主题固定 3 个木乃伊近战）
        for gx, gy, gtype in self.map_data.get("water_well_guards", []):
            cls = _MONSTER_CLASSES.get(gtype, MummyMelee)
            guard = cls(center_x=gx, center_y=gy)
            guard.set_on_death(self._on_monster_death)
            guard._walls = walls_for_collision
            # 水井守卫为木乃伊系，穿戴护甲、头盔和武器（普通怪等级 Lv1-10，可携带木乃伊武器）
            assign_monster_armor(guard, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), is_desert=True)
            assign_monster_helmet(guard, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), is_desert=True)
            assign_monster_weapon(guard, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), is_desert=True)
            self.monsters.append(guard)

        # 弹丸列表（骷髅的）
        self.skeleton_projectiles = arcade.SpriteList()

        # 玩家当前武器（根据仓库选择）
        equipped_id = getattr(gs, 'equipped_weapon_id', None)
        weapons = get_weapons(gs.player_id) if gs.player_id else []
        w = None
        if equipped_id:
            for wp in weapons:
                if wp["id"] == equipped_id:
                    w = wp
                    break
        if w:
            gs.current_weapon_kind = w["kind"]
            gs.current_weapon_id = w["id"]
            gs.current_weapon_item_id = w["item_id"]  # 存储item_id用于渲染
            gs.weapon_damage = w["damage"]
            gs.weapon_speed = w["attack_speed"]
            # 从武器定义获取攻击距离（按中文名匹配）
            gs.weapon_range = lookup_weapon_range(w["kind"], w["name"])
            # 远程武器：设置弹丸速度和特殊属性
            if w["kind"] == "ranged":
                from entities.weapon_defs import RANGED_WEAPONS
                for wdef in RANGED_WEAPONS.values():
                    if wdef["item_id"] == w["item_id"]:
                        gs.weapon_proj_speed = wdef.get("projectile_speed", 400)
                        gs.weapon_special = wdef.get("special", "")
                        gs.weapon_auto_fire = wdef.get("auto_fire", False)
                        break
                else:
                    gs.weapon_proj_speed = 400
                    gs.weapon_special = ""
                    gs.weapon_auto_fire = False
            else:
                gs.weapon_proj_speed = 0
                gs.weapon_special = ""
                gs.weapon_auto_fire = False
        else:
            gs.current_weapon_kind = "melee"
            gs.current_weapon_id = None
            gs.current_weapon_item_id = None
            gs.weapon_damage = 8
            gs.weapon_speed = 0.5
            gs.weapon_range = 40
            gs.weapon_proj_speed = 0
            gs.weapon_special = ""
            gs.weapon_auto_fire = False

        # 战斗系统 - CombatSystem 只需要 wall_list 用于弹丸碰撞检测
        self.combat = CombatSystem(self.wall_list)

        # 撤离状态
        self.evac = EvacState()

        # 撤离圈精灵
        self.evac_sprites.clear()
        self.evac_sprites = arcade.SpriteList()
        for s in self.map_data["evac_points"]:
            sprite = arcade.SpriteSolidColor(20, 20, color=EVAC_COLOR)
            sprite.center_x = s[0]
            sprite.center_y = s[1]
            self.evac_sprites.append(sprite)

        # 重置携带物
        gs.run_carried = {}

        # 摄像机控制器
        physics = arcade.PhysicsEngineSimple(self.player, self.obstacle_list)
        self.controller = PlayerController(self.player, physics)
        self.controller.camera.position = (self.player.center_x, self.player.center_y)

        # 攻击 debuff 列表（从武器和装备的 buff 中收集，元素为 (效果ID, 效果等级) 元组）
        self._attack_debuffs = []
        from entities.effects_defs import EFFECTS, effects_label, parse_effect_item, effect_params
        # 武器附加效果（effects 元素为 "id:level" 或纯 id，需查 EFFECTS 字典取详情）
        weapon_effects = []
        if w and w.get("effects"):
            for e in w["effects"]:
                eid, elvl = parse_effect_item(e)
                edata = EFFECTS.get(eid, {})
                etype = edata.get("type")
                if etype == "passive":
                    pdata = effect_params(eid, elvl)
                    weapon_effects.append({"id": eid, "level": elvl, **pdata})
                    if eid == "regen":
                        self.player.regen_per_sec += pdata["value"]
                    elif eid == "speed":
                        self.player.gear_speed_mult *= (1.0 + pdata["value"])
                    elif eid == "max_hp":
                        self.player.max_hp += pdata.get("value", 25)
                        self.player.hp += pdata.get("value", 25)
                elif etype == "debuff":
                    self._attack_debuffs.append((eid, elvl))
        # 装备附加效果（装备也可能带 debuff，攻击时一并施加）
        for slot_name in ("helmet", "armor", "backpack"):
            if slot_name in equip:
                for e in equip[slot_name].get("effects", []):
                    eid, elvl = parse_effect_item(e)
                    edata = EFFECTS.get(eid, {})
                    if edata.get("type") == "debuff" and (eid, elvl) not in self._attack_debuffs:
                        self._attack_debuffs.append((eid, elvl))
        # 武器顶层 debuff 字段（如诅咒弯刀 debuff="poison"，命中即施毒，等级按1）
        if w and w.get("debuff") and w["debuff"] not in [d[0] for d in self._attack_debuffs]:
            self._attack_debuffs.append((w["debuff"], 1))
        self.player.speed_mult = self.player.gear_speed_mult
        # 缓存武器效果名，供 HUD 显示（效果等级>1 时传入 "id:level" 带等级显示）
        self._cached_weapon_effects = effects_label(
            [f"{e['id']}:{e['level']}" if e.get("level", 1) > 1 else e["id"] for e in weapon_effects]
        )

        # 重置携带物
        gs.run_carried = {}

    def _on_monster_death(self, monster):
        """怪物死亡回调 - 委托给 entity_callbacks"""
        on_monster_death(self, monster)

    def _handle_harvestable_combat(self, dt):
        """处理玩家对环境物的攻击 - 委托给 entity_callbacks"""
        handle_harvestable_combat(self, dt)

    def _on_harvestable_destroyed(self, harvestable):
        """环境物被摧毁 - 委托给 entity_callbacks"""
        on_harvestable_destroyed(self, harvestable)

    def _handle_chest_interaction(self):
        """宝箱交互 - 委托给 entity_callbacks"""
        handle_chest_interaction(self)

    def _handle_well_interaction(self):
        """水井交互 - 委托给 entity_callbacks"""
        handle_well_interaction(self)

    def _handle_rocket_pad_interaction(self):
        """火箭发射台交互：委托给 entity_callbacks"""
        from game.entity_callbacks import handle_rocket_pad_interaction
        handle_rocket_pad_interaction(self)

    def _scatter_drops(self, drops, center_x, center_y, radius=30):
        """分散掉落物 - 委托给 entity_callbacks"""
        scatter_drops(drops, center_x, center_y, radius)

    def _sync_obstacles(self):
        """同步障碍物 - 委托给 entity_callbacks"""
        sync_obstacles(self)

    def _respawn_harvestables(self, dt):
        """资源刷新 - 委托给 respawn"""
        respawn_harvestables(self, dt)

    def _respawn_monsters(self, dt):
        """怪物刷新 - 委托给 respawn"""
        respawn_monsters(self, dt)

    def on_show_view(self):
        self.window.background_color = (20, 25, 20)

    def _hud_text(self, key, text, x, y, color, size=12, anchor_x="left",
                  anchor_y="baseline", bold=False):
        """复用持久 arcade.Text 对象：仅当字符串变化时才重绘纹理，
        避免每帧 draw_text 重建纹理造成卡顿。"""
        t = self._hud_texts.get(key)
        if t is None or t._hud_key_size != size or t._hud_key_color != color \
                or t._hud_key_bold != bold:
            t = arcade.Text(text, x, y, color, size,
                            anchor_x=anchor_x, anchor_y=anchor_y, bold=bold)
            t._hud_key_size = size
            t._hud_key_color = color
            t._hud_key_bold = bold
            self._hud_texts[key] = t
        if t.value != text:
            t.value = text
        t.position = (x, y)
        t.draw()

    def _in_view(self, x, y, margin=80):
        """视口裁剪：仅绘制相机可见范围内的物体，减少每帧图元提交数。
        物体中心在可见矩形(含边距)外则跳过——屏幕外本就不可见。"""
        cam = self.controller.camera.position
        hw, hh = WINDOW_WIDTH / 2, WINDOW_HEIGHT / 2
        return (cam[0] - hw - margin <= x <= cam[0] + hw + margin and
                cam[1] - hh - margin <= y <= cam[1] + hh + margin)

    def on_draw(self):
        """渲染 - 委托给 rendering.render_game"""
        render_game(self)

    def _apply_free_equip(self, d):
        """无背包拾取空槽位武器/装备/背包时，立即在局内生效（穿戴 / 加防御 / 获得容量）

        由 game/loot.py 的 try_pickup 在免费装备成功时回调触发；
        用于同步 GameState 与玩家实体的即时状态，避免仅入包而不生效。
        """
        gs = self.window.game_state
        if d.item_type == "weapon":
            # 同步武器槽位与战斗属性（伤害/攻速/距离/远程特效），参数与 setup() 加载仓库武器一致
            from entities.weapon_defs import ALL_WEAPONS
            wdef = ALL_WEAPONS.get(d.item_id)
            if not wdef:
                return
            gs.equipped_weapon_id = d.item_id
            gs.current_weapon_kind = wdef.get("kind", "melee")
            gs.current_weapon_id = d.item_id
            gs.current_weapon_item_id = d.item_id   # 供渲染视觉
            gs.weapon_damage = wdef.get("damage", 8)
            gs.weapon_speed = wdef.get("attack_speed", 1.0)
            gs.weapon_range = wdef.get("range", 40)
            if gs.current_weapon_kind == "ranged":
                gs.weapon_proj_speed = wdef.get("projectile_speed", 400)
                gs.weapon_special = wdef.get("special", "")
                gs.weapon_auto_fire = wdef.get("auto_fire", False)
            else:
                gs.weapon_proj_speed = 0
                gs.weapon_special = ""
                gs.weapon_auto_fire = False
            # 刷新武器名缓存，避免渲染层因 current_weapon_id 匹配不到数据库行而显示"拳头"
            self._cached_weapon_id = d.item_id
            self._cached_weapon_name = wdef.get("name", "武器")
        elif d.item_type in ("helmet", "armor"):
            from entities.equipment_defs import HELMETS, ARMORS
            defs = HELMETS if d.item_type == "helmet" else ARMORS
            edef = defs.get(d.item_id, {})
            if d.item_type == "helmet":
                gs.equipped_helmet_id = d.item_id
            else:
                gs.equipped_armor_id = d.item_id
            # 防御即时生效（与 setup() 累加装备基础防御的语义一致）
            self.player.defense += edef.get("defense", 0)
        elif d.item_type == "backpack":
            from entities.equipment_defs import BACKPACKS
            bdef = BACKPACKS.get(d.item_id, {})
            # 获得容器即时生效：容量按背包定义设置，并同步到 GameState 供其他 View 使用
            self.player.backpack_capacity = bdef.get("capacity", 0)
            gs.backpack_capacity = self.player.backpack_capacity

    def on_update(self, delta_time):
        self._frame += 1

        gs = self.window.game_state
        dt = min(delta_time, 0.05)

        # 行动时间倒计时
        if self._action_time_remaining is not None and self._action_time_remaining > 0:
            self._action_time_remaining -= dt
            gs.action_time_remaining = self._action_time_remaining  # 同步到 GameState
            if self._action_time_remaining <= 0:
                self._action_time_remaining = 0
                gs.action_time_remaining = 0
                self._fail_run("行动超时！未能在规定时间内撤离")
                return

        if not self.player.alive:
            self._fail_run("你已阵亡！")
            return

        # 相机始终跟随玩家并居中（position 即视口中心）
        self.controller.camera.position = (
            self.player.center_x, self.player.center_y)

        # 特效计时器衰减
        if self._player_hit_flash > 0:
            self._player_hit_flash = max(0, self._player_hit_flash - dt)
        if self._player_attack_flash > 0:
            self._player_attack_flash = max(0, self._player_attack_flash - dt)
        if self._attack_visual:
            px, py, mx, my, rng, timer = self._attack_visual
            timer -= dt
            if timer <= 0:
                self._attack_visual = None
            else:
                self._attack_visual = (px, py, mx, my, rng, timer)

        # 粒子和漂浮文字更新
        particle_system.update(dt)
        floating_texts.update(dt)

        # 记录受击前 HP
        hp_before = self.player.hp

        # 药水效果计时器
        if hasattr(self.player, 'speed_effect_timer') and self.player.speed_effect_timer > 0:
            self.player.speed_effect_timer -= dt
            if self.player.speed_effect_timer <= 0:
                # 药水效果结束，回到装备带来的基础移速倍率
                self.player.speed_mult = self.player.gear_speed_mult

        # 持续回复效果（HoT）
        if hasattr(self.player, 'heal_duration') and self.player.heal_duration > 0:
            self.player.heal_duration -= dt
            if self.player.heal_per_sec > 0:
                heal_amount = self.player.heal_per_sec * dt
                old_hp = self.player.hp
                self.player.heal(int(heal_amount) if heal_amount >= 1 else 0)
                # 实际上逐帧回复（round 到 2 位小数，避免浮点累加出现极长小数点）
                self.player.hp = min(self.player.max_hp, round(self.player.hp + heal_amount, 2))
            if self.player.heal_duration <= 0:
                self.player.heal_per_sec = 0.0

        # 装备持续回血效果（自然回复：每秒回复固定值）
        if getattr(self.player, 'regen_per_sec', 0) > 0:
            # round 到 2 位小数：dt 为帧间隔（如 1/60），逐帧累加会产生浮点长小数
            self.player.hp = min(self.player.max_hp, round(self.player.hp + self.player.regen_per_sec * dt, 2))

        # 同步障碍物列表（确保已摧毁/已打开的物体不再阻挡移动）
        self._sync_obstacles()

        # 玩家移动
        self.controller.update(dt)

        # 环境物受击闪烁计时衰减（否则被攻击后会一直显示白圈）
        for h in self.harvestables:
            if h.alive:
                h.update(dt)

        # 资源随机刷新（被砍光后补刷，保持地图有可采集资源）
        self._respawn_harvestables(dt)

        # 野外怪物刷新（被击杀后补刷，保持地图有怪物）
        self._respawn_monsters(dt)

        # 怪物 AI
        # 近战怪 try_attack 返回 bool（是否命中，伤害已直接结算）；远程怪返回弹丸对象
        for m in self.monsters:
            if hasattr(m, 'alive') and m.alive:
                m.update(self.player.center_x, self.player.center_y, dt)
                proj = m.try_attack(self.player)
                # 仅当返回真实弹丸精灵时才加入弹丸列表（防御近战怪返回 True 被误加入导致崩溃）
                if isinstance(proj, arcade.Sprite):
                    self.skeleton_projectiles.append(proj)

        # 骷髅弹丸更新 + 命中检测
        for p in list(self.skeleton_projectiles):
            p.update(dt)
            if p.expired:
                p.remove_from_sprite_lists()
                continue
            # 碰墙消亡
            if arcade.check_for_collision_with_list(p, self.wall_list):
                p.remove_from_sprite_lists()
                continue
            if arcade.check_for_collision(p, self.player):
                self.player.take_damage(p.damage)
                # 弹丸附带 debuff（木乃伊毒弹/BOSS 冰冻弹等）施加到玩家
                if getattr(p, "debuff_id", None):
                    self.player.apply_debuff(p.debuff_id)
                p.remove_from_sprite_lists()

        # 检测玩家是否受伤，触发屏幕闪红
        if self.player.hp < hp_before:
            self._player_hit_flash = 0.3
            sound_manager.play_hurt()
            # round 伤害值：hp_before/hp 可能因 regen 回复带小数，二进制相减会产生长小数（如 9.879999999999995）
            dmg_shown = round(hp_before - self.player.hp, 1)
            floating_texts.add_damage(self.player.center_x, self.player.center_y + 20, dmg_shown)
            particle_system.emit(self.player.center_x, self.player.center_y, 8, (255, 80, 80), speed=80, life=0.3, size=4)

        # 战斗系统
        self.combat.update(dt)
        # 屏幕鼠标坐标转世界坐标（激光和全自动武器需要世界坐标计算方向）
        cam = self.controller.camera.position
        world_mx = self._mouse_x + cam[0] - self.window.width / 2
        world_my = self._mouse_y + cam[1] - self.window.height / 2
        # 激光束更新（陨星炮神器：实时跟随鼠标方向）
        self.combat.update_lasers(dt, world_mx, world_my)
        # 弹丸命中怪物（复用精灵列表，避免每帧新建）
        self._monster_sprite_list.clear()
        for m in self.monsters:
            if hasattr(m, 'alive') and m.alive:
                self._monster_sprite_list.append(m)
        hit_list = self.combat.check_monster_hits(self._monster_sprite_list)
        # 激光命中检测（陨星炮：对路径上的怪物持续造成伤害）
        hit_list.extend(self.combat.check_laser_hits(self._monster_sprite_list))
        # 远程弹丸/激光命中反馈：显示实际伤害
        for m, actual in hit_list:
            sound_manager.play_monster_hit()
            floating_texts.add_damage(m.center_x, m.center_y + 25, actual)
            # 施加武器/装备附加的攻击效果（中毒/燃烧/冰冻/减速/眩晕，含效果等级）
            for eid, lvl in self._attack_debuffs:
                if hasattr(m, 'apply_debuff'):
                    m.apply_debuff(eid, lvl)

        # 全自动武器：按住鼠标左键时持续射击（仅auto_fire标记的武器可连发）
        if (self._left_mouse_held
                and getattr(gs, 'weapon_auto_fire', False)
                and self.combat.can_attack()):
            self.combat.ranged_attack(
                self.player,
                gs.weapon_damage,
                getattr(gs, 'weapon_proj_speed', 400),
                world_mx, world_my,
                getattr(gs, 'weapon_special', ''),
            )
            self._player_attack_flash = 0.1
            self._attack_this_frame = True
            self._attack_kind = "ranged"
            self._last_attack_range = getattr(gs, 'weapon_range', 250)
            sound_manager.play_ranged_attack()

        # 玩家近战/远程攻击也命中环境物
        self._handle_harvestable_combat(dt)

        # 宝箱交互（靠近按E）
        self._handle_chest_interaction()
        # 水井交互（靠近按E，首次开箱/之后回血加速）
        self._handle_well_interaction()
        # 火箭发射台交互（靠近按E）
        self._handle_rocket_pad_interaction()

        # 火箭发射台状态机更新
        for pad in self.rocket_pads:
            pad.update(dt)
            # 撤离倒计时归零 → 检查玩家是否在范围内
            if pad.state == "evac_success":
                # 检查玩家是否在发射台范围内
                dist = ((self.player.center_x - pad.center_x) ** 2 +
                        (self.player.center_y - pad.center_y) ** 2) ** 0.5
                if dist < 80:  # 玩家在范围内，撤离成功
                    # 修复：删除局部导入（顶部已导入），否则会让 commit_run_to_warehouse/clear_run
                    # 成为 on_update 的局部变量，未走此分支时第 814 行报 UnboundLocalError
                    commit_run_to_warehouse(gs.player_id, gs.run_carried)
                    # 保存携带物品用于显示收益
                    carried_copy = dict(gs.run_carried) if hasattr(gs, 'run_carried') else {}
                    clear_run(gs.run_carried)
                    # 增强提示：大字+粒子效果
                    floating_texts.add(self.player.center_x, self.player.center_y + 80,
                                       "撤离成功！战利品已存入仓库",
                                       arcade.color.GREEN, life=3.0, font_size=22)
                    particle_system.emit(self.player.center_x, self.player.center_y, 30,
                                        (255, 215, 0), speed=100, life=1.0, size=5)
                    sound_manager.play_level_up()
                    # 跳转到撤离结果页面
                    from views.evac_result_view import EvacResultView
                    self.window.show_view(EvacResultView(self.window_ref, success=True, run_carried=carried_copy))
                    return
                else:  # 玩家不在范围内，撤离失败
                    pad.state = "destroyed"  # 标记发射台已失效
                    # 修复：不再在地图内弹出失败文字，统一使用撤离结果页面提示
                    particle_system.emit(self.player.center_x, self.player.center_y, 20,
                                        (255, 50, 50), speed=80, life=0.8, size=4)
                    sound_manager.play_hurt()
                    # 跳转到撤离结果页面
                    from views.evac_result_view import EvacResultView
                    self.window.show_view(EvacResultView(self.window_ref, success=False))
                    return

        # 掉落物更新
        for d in self.drops[:]:
            d.update(dt)
            if d.expired:
                self.drops.remove(d)

        # 按E拾取（带效果）
        old_carried = copy.deepcopy(gs.run_carried) if hasattr(gs, 'run_carried') else {}
        picked, skipped_full, skipped_no_bag = (False, False, False)
        if getattr(self, '_chest_key_pressed', False):
            picked, skipped_full, skipped_no_bag = try_pickup(
                self.player, self.drops, gs.run_carried,
                equipped_weapon_id=gs.equipped_weapon_id,
                equipped_helmet_id=gs.equipped_helmet_id,
                equipped_armor_id=gs.equipped_armor_id,
                on_free_equip=self._apply_free_equip
            )

        # 背包已满 / 未携带背包 提醒（节流，避免每帧刷屏）
        if self._backpack_full_cd > 0:
            self._backpack_full_cd = max(0.0, self._backpack_full_cd - delta_time)
        if (skipped_full or skipped_no_bag) and self._backpack_full_cd <= 0:
            if skipped_no_bag:
                self._message = "未携带背包，无法拾取武器/装备/资源！"
                tip = "未携带背包!"
            else:
                self._message = "背包已满，无法拾取更多物品！"
                tip = "背包已满!"
            self._message_timer = 2.0
            self._backpack_full_cd = 3.0  # 冷却：至少 3 秒后再提示
            # 在玩家头顶显示漂浮文字，双重提示
            floating_texts.add(
                self.player.center_x, self.player.center_y + 40,
                tip, arcade.color.RED, life=1.5, font_size=14)

        # 拾取反馈效果
        if hasattr(gs, 'run_carried'):
            # 金币拾取
            old_gold = old_carried.get("gold", 0)
            new_gold = gs.run_carried.get("gold", 0)
            if new_gold > old_gold:
                diff = new_gold - old_gold
                sound_manager.play_gold_pickup()
                floating_texts.add_gold(self.player.center_x, self.player.center_y, diff)
                particle_system.emit(self.player.center_x, self.player.center_y, 10, (255, 215, 0), speed=60, life=0.5, size=3)

            # 资源拾取
            old_res = old_carried.get("resource", {})
            new_res = gs.run_carried.get("resource", {})
            for item_id, qty in new_res.items():
                old_qty = old_res.get(item_id, 0)
                if qty > old_qty:
                    from entities.resource_defs import RESOURCES
                    name = RESOURCES.get(item_id, {}).get("name", item_id)
                    sound_manager.play_resource_pickup(item_id)
                    floating_texts.add_resource(self.player.center_x, self.player.center_y, name, qty - old_qty)
                    particle_system.emit(self.player.center_x, self.player.center_y, 6, (180, 220, 180), speed=50, life=0.4, size=2)

            # 武器拾取
            old_weapon = old_carried.get("weapon", {})
            new_weapon = gs.run_carried.get("weapon", {})
            for (item_id, _level), qty in new_weapon.items():
                old_qty = old_weapon.get((item_id, _level), 0)
                if qty > old_qty:
                    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
                    all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
                    name = all_weapons.get(item_id, {}).get("name", item_id)
                    sound_manager.play_weapon_pickup()
                    floating_texts.add_weapon(self.player.center_x, self.player.center_y, name)
                    particle_system.emit(self.player.center_x, self.player.center_y, 12, (100, 200, 255), speed=80, life=0.5, size=3)

            # 装备拾取（头盔/护甲）
            for slot in ("helmet", "armor"):
                old_eq = old_carried.get(slot, {})
                new_eq = gs.run_carried.get(slot, {})
                for (item_id, _level), qty in new_eq.items():
                    old_qty = old_eq.get((item_id, _level), 0)
                    if qty > old_qty:
                        from entities.equipment_defs import HELMETS, ARMORS
                        defs = HELMETS if slot == "helmet" else ARMORS
                        name = defs.get(item_id, {}).get("name", item_id)
                        sound_manager.play_weapon_pickup()
                        floating_texts.add_weapon(self.player.center_x, self.player.center_y, name)
                        particle_system.emit(self.player.center_x, self.player.center_y, 10, (200, 200, 150), speed=60, life=0.5, size=3)

            # 背包拾取
            old_bp = old_carried.get("backpack", {})
            new_bp = gs.run_carried.get("backpack", {})
            for (item_id, _level), qty in new_bp.items():
                old_qty = old_bp.get((item_id, _level), 0)
                if qty > old_qty:
                    from entities.equipment_defs import BACKPACKS
                    name = BACKPACKS.get(item_id, {}).get("name", item_id)
                    sound_manager.play_weapon_pickup()
                    floating_texts.add_weapon(self.player.center_x, self.player.center_y, name)
                    particle_system.emit(self.player.center_x, self.player.center_y, 10, (200, 200, 150), speed=60, life=0.5, size=3)

        # 消息衰减
        if self._message_timer > 0:
            self._message_timer = max(0, self._message_timer - delta_time)

        # 撤离读条更新
        evac_result = self.evac.update(self.player, self.map_data["evac_points"], delta_time)

        # 检测撤离完成
        if evac_result == "evacuated":
            # 撤离成功：提交战利品到仓库
            commit_run_to_warehouse(gs.player_id, gs.run_carried)
            # 保存携带物品用于显示收益（先复制再清空）
            carried_copy = dict(gs.run_carried) if hasattr(gs, 'run_carried') else {}
            clear_run(gs.run_carried)
            # 增强提示：大字+粒子效果
            floating_texts.add(self.player.center_x, self.player.center_y + 80,
                               "撤离成功！战利品已存入仓库",
                               arcade.color.GREEN, life=3.0, font_size=22)
            particle_system.emit(self.player.center_x, self.player.center_y, 30,
                                (255, 215, 0), speed=100, life=1.0, size=5)
            sound_manager.play_level_up()  # 使用升级音效作为成功音效
            # 修复：普通撤离点撤离成功改为跳转撤离结果页面（与火箭发射台撤离保持一致），不再直接返回大厅
            from views.evac_result_view import EvacResultView
            self.window.show_view(EvacResultView(self.window_ref, success=True, run_carried=carried_copy))
            return

        # 宝箱按键标志重置（在 handle_chest_interaction 中消费后重置）
        if self._chest_key_pressed and not picked:
            pass  # 保持状态直到交互完成

    def _fail_run(self, reason: str):
        """行动失败统一处理：超时/死亡 → 清空携带物 → 显示失败页面"""
        gs = self.window.game_state
        # 清空本次携带数据
        if hasattr(gs, 'run_carried'):
            gs.run_carried = {}
        # 修复：不再在地图内弹出失败文字，统一使用撤离结果页面提示
        # 增强提示：粒子效果
        particle_system.emit(self.player.center_x, self.player.center_y, 20,
                            (255, 50, 50), speed=80, life=0.8, size=4)
        sound_manager.play_hurt()  # 使用受伤音效作为失败音效
        # 跳转到撤离结果页面
        from views.evac_result_view import EvacResultView
        self.window.show_view(EvacResultView(self.window_ref, success=False))

    def on_key_press(self, key, modifiers):
        handle_key_press(self, key, modifiers)

    def on_key_release(self, key, modifiers):
        handle_key_release(self, key, modifiers)

    def on_mouse_motion(self, x, y, dx, dy):
        handle_mouse_motion(self, x, y, dx, dy)

    def on_mouse_press(self, x, y, button, modifiers):
        handle_mouse_press(self, x, y, button, modifiers)

    def on_mouse_release(self, x, y, button, modifiers):
        handle_mouse_release(self, x, y, button, modifiers)
