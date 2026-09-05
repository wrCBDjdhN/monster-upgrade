"""GameView：整合地图/玩家/怪物/战斗/掉落/撤离/死亡"""

import math
import random
import copy
import re  # 怪物类名 → snake_case 刷怪键转换
import time  # 联机 ATTACK_EVENT 时间戳（毫秒）
import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, PLAYER_HP,
    PLAYER_SIZE, PLAYER_COLOR, TILE_SIZE, PLAYER_SPEED,
    EVAC_COLOR, EVAC_RADIUS,
    WELL_HEAL, WELL_SPEED_MULT, WELL_SPEED_DURATION,
    CACTUS_THORN_DAMAGE,  # 仙人掌反伤：客户端近战攻击环境物命中时作用于攻击者（幽灵）
    MONSTER_WEAPON_LEVEL_RANGE, MONSTER_GEAR_LEVEL_RANGE,
    BOSS_WEAPON_LEVEL_RANGE, BOSS_GEAR_LEVEL_RANGE,
    NET_SNAPSHOT_HZ,  # 状态快照广播频率（怪物快照 20Hz）
    NET_HEARTBEAT_SEC,  # 心跳间隔（客户端 RTT 测量与保活）
    NET_ACTION_TIME_BCAST_SEC,  # 行动时间广播间隔（主机每秒广播剩余行动时间）
    PROJECTILE_SIZE,  # 标准弹丸边长（客户端远端弹丸纯表现层渲染尺寸）
    DROP_PICKUP_RADIUS,  # 掉落物拾取半径（主机拾取仲裁距离阈值，Todo 18）
    LIFESTEAL_DEFAULT, SPREAD_COUNT_DEFAULT, SPREAD_ANGLE_DEFAULT,  # 武器扩展机制默认值（吸血/散射）
    AURA_SLOW_TICK, AURA_SLOW_LEVEL,  # 攻速光环：减速结算周期与效果等级
    # 等级系统：击杀/撤离经验常量（客户端击杀结算用）
    EXP_KILL_BASE, EXP_BOSS_MULT, EXP_EVAC,
)
from net.protocol import MsgType  # 联机消息类型枚举（MONSTER_SNAPSHOT 等）
from game.map_gen import generate_map
from game.player import Player, PlayerController
from game.monsters import Zombie, MummyMelee  # 刷怪映射兜底（未知类型回退用）
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
    _award_exp,  # 等级经验发放（客户端击杀/撤离经验共用，各端本地结算）
)
from game.respawn import respawn_harvestables, respawn_monsters
from game.rendering import render_game
from game.input_handler import (
    handle_key_press, handle_key_release, handle_mouse_motion,
    handle_mouse_press, handle_mouse_release,
)

# 怪物类映射自动生成：数据源 = MONSTER_CONFIGS（配置键即类名），刷怪键 = 类名转 snake_case，
# is_boss 分流到 BOSS 表。新增怪物只需同步 monster_defs.py 与 monsters.py，无需再改本文件。
from entities.monster_defs import MONSTER_CONFIGS
import game.monsters as monsters


def _spawn_key(class_name: str) -> str:
    """怪物类名 → 刷怪类型键（CamelCase 转 snake_case，如 BossZombie → boss_zombie）"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()


class _RemoteLaser:
    """客户端远端激光（纯表现层）：由主机 PROJECTILE_SNAPSHOT 的 lasers 列表维护

    - 轻量数据容器：仅存渲染所需字段（起点/角度/长度/宽度/剩余时长），
      快照到达即覆盖，快照缺失即删除（主机激光已消失）；
    - 不参与碰撞/伤害判定（激光命中收敛主机，客户端只按主机权威位置渲染）。
    """

    def __init__(self, proj_id: int):
        self.proj_id = proj_id
        self.x = 0.0
        self.y = 0.0
        self.angle = 0.0
        self.length = 600.0
        self.width = 24.0
        self.duration = 3.0

    def draw(self):
        """绘制远端激光（与 combat.LaserBeam 三层画法一致：外圈光晕 + 主体 + 中心亮核）"""
        if self.duration <= 0:
            return
        ex = self.x + math.cos(self.angle) * self.length
        ey = self.y + math.sin(self.angle) * self.length
        arcade.draw_line(self.x, self.y, ex, ey, (255, 100, 255, 60), self.width + 10)
        arcade.draw_line(self.x, self.y, ex, ey, (255, 255, 255), self.width)
        arcade.draw_line(self.x, self.y, ex, ey, (255, 180, 255), max(3, self.width // 3))


_MONSTER_CLASSES: dict[str, type] = {
    _spawn_key(n): getattr(monsters, n) for n in MONSTER_CONFIGS
    if hasattr(monsters, n) and not MONSTER_CONFIGS[n].get("is_boss")
}
_BOSS_CLASSES: dict[str, type] = {
    _spawn_key(n): getattr(monsters, n) for n in MONSTER_CONFIGS
    if hasattr(monsters, n) and MONSTER_CONFIGS[n].get("is_boss")
}


def _lookup_weapon_by_name(name: str) -> dict:
    """按武器中文名查武器定义（主机裁决客户端 ATTACK_EVENT 用）

    ALL_WEAPONS 按 item_id 键控，中文名需遍历匹配；未知/空名回退拳头（与单人起始一致）。
    客户端上报的是武器名，主机以本地武器表为权威数值来源（升级等差异后续任务再同步）。
    """
    from entities.weapon_defs import ALL_WEAPONS
    for wdef in ALL_WEAPONS.values():
        if wdef.get("name") == name:
            return wdef
    return ALL_WEAPONS.get("fist", {})


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
        # 远端玩家幽灵容器（联机客户端用）：后续任务由主机玩家快照填充，客户端仅渲染
        self.remote_players: dict = {}
        # 联机主机：怪物网络 id 分配表（net_id -> 怪物对象），net_id 在怪物存活期不变，
        # 客户端据此对快照做增/改/删同步
        self.net_id_to_monster: dict = {}
        # 联机主机：怪物网络 id 单调递增计数器（从 1 开始，不回收已死亡 id）
        self._next_monster_net_id = 1
        # 联机主机：怪物快照广播计时器（累计 dt，每 1/NET_SNAPSHOT_HZ 秒广播一次全量快照）
        self._snapshot_timer = 0.0
        # 远端怪物容器（联机客户端用）：net_id -> 怪物对象，由主机 MONSTER_SNAPSHOT 增删改维护；
        # 只读渲染用，绝不加入 self.monsters（不进任何本地 AI/伤害判定路径）
        self.remote_monsters: dict = {}
        # 远端怪物弹丸容器（联机客户端用）：arcade.SpriteList，由主机 PROJECTILE_SNAPSHOT 增删改维护；
        # 纯表现层渲染（不参与任何碰撞/伤害判定），solo 模式始终为空列表（SpriteList.draw 空绘制安全）
        self.remote_projectiles: arcade.SpriteList = arcade.SpriteList()
        # 联机客户端：弹丸 proj_id -> 远端弹丸精灵 索引表（O(1) 增/改/删，避免逐项线性扫描）
        self._remote_proj_map: dict[int, arcade.Sprite] = {}
        # 联机客户端：远端激光表现对象 proj_id -> _RemoteLaser（由主机 PROJECTILE_SNAPSHOT
        # 的 lasers 列表增删改维护；纯表现层渲染，命中判定收敛主机）
        self.remote_lasers: dict[int, _RemoteLaser] = {}
        # 联机主机：已发送 FULL_STATE 的玩家 id 集合（晚期加入全量同步，仅首次发送）
        self._full_state_sent: set = set()
        # 联机客户端：FULL_STATE 初始化快照（掉落/宝箱/环境物，供渲染任务使用，todo 18/20 收口）
        self._late_drops: list = []
        self._late_chests: list = []
        self._late_env: list = []
        # 联机主机：掉落物网络 id 单调递增计数器（从 1 开始，不回收；掉落物生成只在主机，
        # 分配后随 MAP_CHANGE drop_spawn 广播，客户端据此建本地视觉掉落物，Todo 18）
        self._next_drop_net_id = 1
        # 联机主机：每玩家 run_carried 记录（player_id -> dict，结构与 GameState.run_carried 一致），
        # 拾取仲裁成功时由 _record_player_pickup 累加，供 todo 19 撤离结算清单使用
        self._players_run_carried: dict = {}
        # 联机客户端：本次按 E 拾取前的 run_carried 快照（乐观拾取被主机拒绝时回滚，Todo 18）
        self._pickup_snapshot: dict | None = None
        # 联机客户端：本批拾取请求的待确认计数（全部确认后才清空快照，
        # 避免按住 E 期间逐帧覆盖快照导致主机拒绝后回滚失效）
        self._pending_pickup_count = 0
        # 联机主机：观战模式标志（主机撤离/死亡后房间保留、后台继续模拟，禁操作禁结算）。
        # 客户端各端状态由 _player_status 跟踪，全员结束后广播 ROOM_ENDED(all_finished) 回房等待
        self._spectating = False
        self._spectate_target_id: int | None = None   # 观战跟随目标玩家 id（None=跟随自己玩家）
        # 退出观战按钮（观战模式下屏幕右下角显示，点击返回大厅等待下一局）
        self._exit_spectate_rect = arcade.XYWH(WINDOW_WIDTH - 120, 80, 180, 40)
        self._exit_spectate_hover = False
        # 各玩家本局结束状态：alive/evac/dead/left（主机观战期间全员结束判定用，host=0 固定）
        self._player_status: dict[int, str] = {}
        # 联机客户端：撤离请求已发送标记（读条完成只发一次 EVAC_REQUEST，B12 单次闸门）
        self._evac_request_sent = False
        # 联机主机：弹丸网络 id 单调递增计数器（从 1 开始，不回收已消亡 id，见 _serialize_projectiles）
        self._next_proj_id = 1
        # 联机主机：运行期地图改动已广播跟踪（Todo 20，D2）——宝箱/环境物按列表序号广播一次，
        # 水井/火箭台状态变化才重播（撤离倒计时按整数秒节流），避免对相同状态重复广播
        self._broadcasted_chests: set = set()      # 已广播「已开启」的宝箱序号
        self._broadcasted_env: set = set()         # 已广播「已摧毁」的环境物序号
        self._well_broadcasted = False             # 水井首次开启是否已广播
        self._broadcasted_pads: dict = {}          # 火箭台序号 -> 上次广播的状态键（state 或 (state,countdown)）
        self.drops: list[DropItem] = []
        # 新手教程：游戏内引导横幅文字缓存（TextCache，避免每帧重建纹理）
        from views.text_cache import TextCache
        self._tut_tc = TextCache()
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
        # 键位绑定：{动作名: [arcade.key 属性名, ...]}，从 db settings 加载（默认 config.KEY_BINDINGS）。
        # PlayerController（移动）与 input_handler（E/F/TAB/1-3/7/8/V/M/ESC）共用；
        # 设置界面修改后写 db 并刷新本属性（见 views/settings_view.py）
        self.key_bindings = None
        self._load_key_bindings()
        # 小地图显示模式：False=周围视野（±MINIMAP_VIEW_RADIUS），True=全图（M 键切换）
        self._minimap_full = False
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
        # BOSS 战系统
        self.active_boss = None  # 当前激活的 BOSS 实例（进入 BOSS 房间/火箭台激活时设置）
        self._boss_door_sprite = None  # BOSS 房间门洞遮挡精灵（BOSS 存活时显示，死亡后移除）
        self._boss_room_locked = False  # BOSS 房间是否已锁定（玩家无法离开）
        # BOSS 介绍向导弹窗（进入 BOSS 房间时首次弹出，展示 4 页说明）
        self._boss_intro_active = False  # BOSS 介绍弹窗是否激活
        self._boss_intro_pages = []       # BOSS 介绍向导页列表
        self._boss_intro_idx = 0          # 当前显示的页码
        self._boss_intro_next_hover = False  # 下一步按钮悬停状态
        self._boss_intro_next_rect = None    # 下一步按钮矩形
        self._boss_intro_shown = False   # 本次运行是否已展示过 BOSS 介绍（避免重复弹出）
        # 联机客户端本地攻速节流计时器（客户端不再经 combat 冷却，判定已移交主机；
        # 用独立计时器模拟攻速，避免上报 ATTACK_EVENT 的频率超过武器攻速）
        self._net_fire_cd = 0.0
        # 联机客户端：本人实体快照上报计时器（20Hz 与主机快照同节拍，Todo 23）
        self._client_snap_timer = 0.0
        # 联机客户端：心跳/RTT 测量状态（每 NET_HEARTBEAT_SEC 发一次，测往返延迟）
        self._heartbeat_timer = 0.0
        self._heartbeat_seq = 0          # 心跳递增序号
        self._hb_sent_at = 0.0           # 最近一次心跳发送时刻（收到回显时算 RTT）
        self._net_rtt_ms = 0.0           # 最近一次心跳往返延迟（毫秒，状态条 E3 显示）
        # 联机主机：行动时间广播计时器（每 NET_ACTION_TIME_BCAST_SEC 广播剩余行动时间，
        # 客户端 HUD 显示以广播值为准——修复客户端行动时间卡死不动）
        self._action_time_bcast_timer = 0.0
        # 联机：房间结束已广播/已处理标记（主机撤离/死亡/超时广播 ROOM_ENDED 防重复；
        # 客户端收到后回大厅，避免后续帧再次触发同一路径）
        self._room_ended_handled = False
        # 世界坐标标签列表（在HUD阶段绘制）：[(world_x, world_y, text, color, font_size)]
        self._world_labels: list = []
        # 怪物精灵列表缓存（弹丸碰撞检测用，每帧在 on_update 中构建）
        self._monster_sprite_list = arcade.SpriteList()
        # 行动时间倒计时（space=8min, forest/desert=5min）
        self._action_time_remaining = None  # None=无限，float=剩余秒数
        # 角色等级数据缓存（等级/经验/待选升级/永久加成）：setup 读取、经验发放时刷新，
        # HUD 与升级面板（level_up_view）共用，避免每帧查库
        self._level_data = None

    def _load_key_bindings(self):
        """从 db settings 加载键位绑定（默认 config.KEY_BINDINGS），失败时回退默认

        settings 表由 init_db() 创建（start_view 引导时已执行）；此处防御性兜底：
        表不存在/读取异常时用默认绑定，保证任何时候进入游戏都不会因绑定缺失崩溃。
        设置界面（settings_view）修改绑定后写 db 并调用本方法刷新。
        """
        from config import KEY_BINDINGS
        try:
            from db.database import get_key_bindings
            self.key_bindings = get_key_bindings()
        except Exception:
            # 数据库尚未初始化或读取失败：回退默认绑定
            self.key_bindings = dict(KEY_BINDINGS)

    def setup(self):
        gs = self.window.game_state

        # 清空上一局残留的漂浮文字（撤离成功提示等）
        from game.effects import floating_texts
        floating_texts.texts.clear()

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
        # 联机模式下用 ROOM_START 下发/主机算好的权威出生点（gs.net_spawn）；
        # 单机回退到首个房间中心（原逻辑不变）。
        if gs.net_spawn is not None:
            sx, sy = gs.net_spawn
        else:
            sx, sy = self.map_data["rooms"][0].center if self.map_data["rooms"] else (400, 400)
        self.player = Player(center_x=sx, center_y=sy,
                             character_id=getattr(gs, "character_id", "initial"))

        # 加载装备属性
        equip = {}
        if gs.player_id:
            from db.database import get_equipment, get_backpack_capacity
            from entities.effects_defs import (
                EFFECTS as _EFFECTS_DEFS, parse_effect_item, effect_params,
            )
            equip = get_equipment(gs.player_id)
            # 修复：先清空 GameState 装备槽位，再按数据库权威值重载。
            # 此前仅在 DB 有装备时才覆盖 equipped_*_id，仓库中卸下装备后残留的
            # 旧值会导致背包栏显示已装备但属性（防御/容量）未生效。
            gs.equipped_helmet_id = None
            gs.equipped_armor_id = None
            gs.equipped_backpack_id = None
            total_def = 0
            for slot_name in ("helmet", "armor", "backpack"):
                if slot_name in equip:
                    eq = equip[slot_name]
                    total_def += eq.get("defense", 0)
                    if slot_name == "helmet":
                        gs.equipped_helmet_id = eq["item_id"]
                    elif slot_name == "armor":
                        gs.equipped_armor_id = eq["item_id"]
                    elif slot_name == "backpack":
                        # 装备栏需要显示当前装备的背包，故同步记录 item_id
                        gs.equipped_backpack_id = eq["item_id"]
                    # 处理装备被动效果（max_hp / regen / speed / defense / damage / lifesteal / thorns / crit_chance）
                    # 修复：效果元素为 "id:level" 格式，需先 parse_effect_item 解析出效果id与等级，
                    # 再用 effect_params 取分级后的数值（与武器效果处理逻辑保持一致），否则效果不生效
                    for e in eq.get("effects", []):
                        eid, elvl = parse_effect_item(e)
                        edata = _EFFECTS_DEFS.get(eid, {})
                        if edata.get("type") == "passive":
                            pdata = effect_params(eid, elvl)
                            if eid == "max_hp":
                                self.player.max_hp += int(pdata.get("value", 25))
                                self.player.hp += int(pdata.get("value", 25))
                            elif eid == "regen":
                                self.player.regen_per_sec += pdata.get("value", 1)
                            elif eid == "speed":
                                self.player.gear_speed_mult *= (1.0 + pdata.get("value", 0.30))
                            elif eid == "defense":
                                total_def += int(pdata.get("value", 3))
                            elif eid == "damage":
                                # 伤害加成：按百分比增加武器伤害（存储倍率供战斗读取）
                                current = getattr(self.player, "equip_damage_mult", 1.0)
                                self.player.equip_damage_mult = current * (1.0 + pdata.get("value", 0.08))
                            elif eid == "lifesteal":
                                current = getattr(self.player, "equip_lifesteal", 0.0)
                                self.player.equip_lifesteal = current + pdata.get("value", 0.03)
                            elif eid == "thorns":
                                current = getattr(self.player, "equip_thorns", 0.0)
                                self.player.equip_thorns = current + pdata.get("value", 0.10)
                            elif eid == "crit_chance":
                                current = getattr(self.player, "crit_chance", 0.0)
                                self.player.crit_chance = current + pdata.get("value", 0.05)
            # 总防御 = 角色基础防御（骑士 +10）+ 装备防御；原代码直接覆盖会丢失角色基础防御
            self.player.defense = self.player.base_defense + total_def
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

        # 地图初始掉落（野外金币 + 房间资源）：掉落一律由主机生成并随快照同步（B1/B13），客户端跳过，
        # 避免本地掉落列表与主机快照冲突
        # TODO(联机): 怪物/掉落/宝箱由主机快照驱动
        if gs.net_mode != "client":
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

        # 宝箱（每个房间1个；按主题区分掉落池/等级，用户需求）
        self.chests = []
        for cx, cy in self.map_data.get("chest_positions", []):
            chest = Chest(cx, cy, theme=theme)
            self.chests.append(chest)
            self.obstacle_list.append(chest)

        # 火箭发射台（仅 space 主题）
        self.rocket_pads = []
        if theme == "space":
            from game.rocket_pad import RocketPad
            for rpx, rpy in self.map_data.get("rocket_pads", []):
                rp = RocketPad(rpx, rpy)
                self.rocket_pads.append(rp)

        # 全部怪物生成（房间内/野外/BOSS/水井守卫）：怪物只在主机生成并随快照下发（B1/B3），
        # 装备分配随机数由主机权威；客户端怪物由快照渲染
        # TODO(联机): 怪物/掉落/宝箱由主机快照驱动
        if gs.net_mode != "client":
            # 怪物
            walls_for_collision = self.map_data["walls"]

            # 房间内怪物
            for mx, my, mtype in self.map_data["spawn_points"]:
                cls = _MONSTER_CLASSES.get(mtype, Zombie)
                m = cls(center_x=mx, center_y=my)
                m.set_on_death(self._on_monster_death)
                # 随机穿戴护甲、头盔和武器（按地图主题分策略，见 config.MONSTER_THEME_EQUIP）
                assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)
                m._walls = walls_for_collision
                self.monsters.append(m)

            # 野外怪物
            for wx, my, mtype in self.map_data.get("wild_spawns", []):
                cls = _MONSTER_CLASSES.get(mtype, Zombie)
                m = cls(center_x=wx, center_y=my)
                m.set_on_death(self._on_monster_death)
                m._walls = walls_for_collision
                # 随机穿戴护甲、头盔和武器（按地图主题分策略）
                assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)
                self.monsters.append(m)

            # BOSS（每局仅 1 个，位于金字塔/角落建筑内，不参与野外刷新）
            boss_spawn = self.map_data.get("boss_spawn")
            boss_type = self.map_data.get("boss_type")
            if boss_spawn and boss_type and boss_type in _BOSS_CLASSES:
                boss = _BOSS_CLASSES[boss_type](center_x=boss_spawn[0], center_y=boss_spawn[1])
                boss.set_on_death(self._on_monster_death)
                boss._walls = walls_for_collision
                # 设置BOSS主题（供召唤小怪时分配装备）
                boss._theme = theme
                # 注入召唤回调：BOSS 召唤小怪时直接加入游戏怪物列表
                def _on_boss_summon(boss_ref, summoned_list, _view=self):
                    for sm in summoned_list:
                        sm.set_on_death(_view._on_monster_death)
                        sm._walls = _view.map_data.get("walls", [])
                        _view.monsters.append(sm)
                boss._summon_callback = _on_boss_summon
                # BOSS 穿戴护甲、头盔和武器（BOSS 装备等级 Lv20-30，按主题分策略）
                assign_monster_armor(boss, level=random.randint(*BOSS_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_helmet(boss, level=random.randint(*BOSS_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_weapon(boss, level=random.randint(*BOSS_WEAPON_LEVEL_RANGE), theme=theme)
                self.monsters.append(boss)
                self.active_boss = boss  # 激活屏幕顶部 BOSS 血条

            # 水井守卫（沙漠主题固定 3 个木乃伊近战）
            for gx, gy, gtype in self.map_data.get("water_well_guards", []):
                cls = _MONSTER_CLASSES.get(gtype, MummyMelee)
                guard = cls(center_x=gx, center_y=gy)
                guard.set_on_death(self._on_monster_death)
                guard._walls = walls_for_collision
                # 水井守卫穿戴护甲、头盔和武器（按主题分策略）
                assign_monster_armor(guard, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_helmet(guard, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
                assign_monster_weapon(guard, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)
                self.monsters.append(guard)

        # 联机主机：为全部初始怪物分配单调网络 id（仅 host 模式且已注入 net_server 时执行；
        # 运行期补刷（respawn）的新怪物在序列化时惰性分配，见 _serialize_monsters；
        # net_id 存储在 m.net_id 上，怪物存活期保持不变，客户端据此做增删改同步）
        if gs.net_mode == "host" and gs.net_server is not None:
            self._next_monster_net_id = 1
            self.net_id_to_monster = {}
            for m in self.monsters:
                m.net_id = self._next_monster_net_id
                self.net_id_to_monster[m.net_id] = m
                self._next_monster_net_id += 1
            # 弹丸网络 id 单调计数器同步复位（新一局从 1 开始，弹丸与怪物共用「不回收 id」策略）
            self._next_proj_id = 1
            # 玩家本局结束状态表初始化（观战全员结束判定用）：host=0 固定 alive 起步，
            # 客户端按名册登记 alive（断线/撤离/死亡由运行期事件更新为 left/evac/dead）
            self._player_status = {0: "alive"}
            for pid in (gs.net_roster or {}):
                if pid != 0:
                    self._player_status[pid] = "alive"
            # 观战模式复位（新一局非观战起步）
            self._spectating = False
            self._spectate_target_id = None

        # 弹丸列表（骷髅的）
        self.skeleton_projectiles = arcade.SpriteList()
        # 远端怪物弹丸容器（客户端）：新一局清空，避免残留上一局快照产生的弹丸
        self.remote_projectiles = arcade.SpriteList()
        self._remote_proj_map = {}
        # 远端激光表现对象（客户端）：新一局清空，避免残留上一局快照产生的激光
        self.remote_lasers = {}

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
            # 武器扩展机制：吸血/散射/光环（从武器定义读取，供攻击与光环结算使用）
            # 修复：DB 武器记录不含扩展字段（仅 id/item_id/kind/name/damage/attack_speed/level/effects），
            # 此前从 w（DB 记录）取值恒为默认值 → 三连散射炮只发射 1 发/吸血剑不吸血/冰霜领域光环不生效。
            # 改为按 item_id 从 ALL_WEAPONS 武器定义读取，与拾取武器路径（本文件 _equip_pickup 附近）口径一致。
            from entities.weapon_defs import ALL_WEAPONS
            wdef_full = ALL_WEAPONS.get(w["item_id"], {})
            gs.weapon_lifesteal = wdef_full.get("lifesteal", LIFESTEAL_DEFAULT)
            gs.weapon_spread_count = wdef_full.get("spread_count", SPREAD_COUNT_DEFAULT)
            gs.weapon_spread_angle = wdef_full.get("spread_angle", SPREAD_ANGLE_DEFAULT)
            gs.weapon_aura_slow = wdef_full.get("aura_slow", False)
            gs.weapon_aura_radius = wdef_full.get("aura_radius", 0)
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
            gs.weapon_lifesteal = LIFESTEAL_DEFAULT
            gs.weapon_spread_count = SPREAD_COUNT_DEFAULT
            gs.weapon_spread_angle = SPREAD_ANGLE_DEFAULT
            gs.weapon_aura_slow = False
            gs.weapon_aura_radius = 0

        # ── 角色等级系统：应用永久加成（等级绑定职业，各端本地结算）──
        # 从 character_levels 表读取等级/经验/永久加成并缓存到 _level_data（HUD/升级面板共用）；
        # 血量/防御/移速叠加到玩家实体，伤害/攻速叠加到武器数值。
        # 伤害/攻速加成同时缓存到 gs.level_bonus_*，供 _apply_free_equip 拾取武器时叠加
        # （拾取新武器会重置 weapon_damage/weapon_speed，需在此补回等级加成，否则加成丢失）。
        self._level_data = None
        if gs.player_id:
            from db.database import get_character_levels
            ld = get_character_levels(gs.player_id, getattr(gs, "character_id", "initial"))
            self._level_data = ld
            # 血量/防御：叠加到玩家实体（防御在装备计算之后叠加，避免被基础防御+装备覆盖）
            self.player.max_hp += int(ld["bonus_hp"])
            self.player.hp += int(ld["bonus_hp"])
            self.player.defense += ld["bonus_defense"]
            # 移速：bonus_speed 单位像素/帧，折算为倍率叠加到角色基础移速倍率
            # （实际移速 = PLAYER_SPEED × effective_speed_mult，effective = char_speed_mult × 药水 × debuff）
            self.player.char_speed_mult += ld["bonus_speed"] / PLAYER_SPEED
            # 伤害/攻速：叠加到武器数值，并缓存供 _apply_free_equip 拾取武器时叠加
            gs.level_bonus_damage = ld["bonus_damage"]
            gs.level_bonus_atk_speed = ld["bonus_atk_speed"]
            gs.weapon_damage += ld["bonus_damage"]
            gs.weapon_speed += ld["bonus_atk_speed"]

        # 战斗系统 - CombatSystem 只需要 wall_list 用于弹丸碰撞检测
        self.combat = CombatSystem(self.wall_list)
        # 攻速光环结算计时器（冰霜领域神器：每 AURA_SLOW_TICK 秒对周围怪物施加减速）
        self._aura_tick = 0.0

        # 撤离状态
        self.evac = EvacState()
        # 联机客户端：撤离请求闸门重置（进入新一局后可再次请求撤离，B12）
        self._evac_request_sent = False
        # 联机主机：运行期地图改动已广播跟踪重置（新一局地图对象全新，旧广播记录作废，Todo 20）
        self._broadcasted_chests = set()
        self._broadcasted_env = set()
        self._well_broadcasted = False
        self._broadcasted_pads = {}
        # 水井首次开启状态重置（与 _well_broadcasted 对称；同实例跨局复用不残留）
        self._well_opened = False

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
        # 重置本局药水槽（run_potions：不占容量、上限 RUN_POTION_SLOTS）
        gs.run_potions = {}
        # 重置局内免费拾取记录：free_equipped_item_ids 是会话级集合，若不随新一局清空，
        # 上一局免费拾取的 item_id 会残留，导致撤离时把「从仓库带入的同名装备」误判为
        # 局内拾取而重复入库（修复：撤离复制仓库装备 bug）
        gs.free_equipped_item_ids = set()

        # 摄像机控制器
        physics = arcade.PhysicsEngineSimple(self.player, self.obstacle_list)
        # 传入键位绑定：PlayerController 移动键查询绑定（设置界面重绑后同步生效）
        self.controller = PlayerController(self.player, physics, key_bindings=self.key_bindings)
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
                        self.player.max_hp += int(pdata.get("value", 25))
                        self.player.hp += int(pdata.get("value", 25))
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
        # 新一局开始：重置药水 buff 状态（护盾/狂暴/持续回复不跨局残留）
        self.player.shield = 0.0
        self.player.shield_effect_timer = 0.0
        self.player.power_mult = 1.0
        self.player.power_effect_timer = 0.0
        self.player.speed_effect_timer = 0.0
        self.player.heal_duration = 0.0
        self.player.heal_per_sec = 0.0
        # 缓存武器效果名，供 HUD 显示（效果等级>1 时传入 "id:level" 带等级显示）
        self._cached_weapon_effects = effects_label(
            [f"{e['id']}:{e['level']}" if e.get("level", 1) > 1 else e["id"] for e in weapon_effects]
        )

        # 重置携带物
        gs.run_carried = {}

    def _current_weapon_name(self) -> str:
        """当前武器中文名（联机 ATTACK_EVENT 载荷 weapon 字段用）

        优先按 current_weapon_item_id 查武器定义（免渲染 HUD 缓存刷新时序问题），
        查不到时回退 HUD 缓存名（__init__ 默认"拳头"，rendering 每帧同步）。
        """
        gs = self.window.game_state
        from entities.weapon_defs import ALL_WEAPONS
        if gs.current_weapon_item_id and gs.current_weapon_item_id in ALL_WEAPONS:
            return ALL_WEAPONS[gs.current_weapon_item_id].get("name", self._cached_weapon_name)
        return self._cached_weapon_name

    def _broadcast_damage(self, monster, damage: float, hit: bool = True,
                          crit: bool = False, debuffs=None) -> None:
        """主机广播单条 DAMAGE_RESULT（攻击判定收敛主机：命中→广播，各端同步血量显示）

        target_id 取怪物 net_id（Todo 10 分配；未分配 net_id 的怪物无法被客户端定位，跳过）。
        debuffs：命中施加的附加效果列表 [(效果ID, 等级), ...]（修复客户端特殊效果不全生效：
        之前只广播单个无等级 debuff，客户端只能施加 1 级且多效果丢失）。
        """
        net_id = getattr(monster, "net_id", None)
        if net_id is None:
            return  # 无 net_id（solo 模式怪物无网络 id），不广播
        gs = self.window.game_state
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.broadcast(MsgType.DAMAGE_RESULT, {
                "target_id": net_id,
                "damage": damage,
                "hit": hit,
                "crit": crit,
                "debuffs": [list(d) if isinstance(d, tuple) else d for d in (debuffs or [])],
            })

    def _resolve_attack_event(self, sender_id: int, payload: dict) -> None:
        """主机权威裁决客户端的攻击动作（ATTACK_EVENT）：命中判定收敛主机

        - 攻击者用客户端幽灵（remote_players，懒创建于地图出生点）；若该 id 是主机本地
          玩家（gs.net_player_id，大厅接入后由 todo 21 设置）则直接用 self.player；
        - 按武器名查本地武器表取数值（kind/damage/range/attack_speed/special/debuff），
          近战即时判定并广播 DAMAGE_RESULT；远程生成弹丸/激光（幽灵位置为发射原点），
          命中由 on_update 的 check_monster_hits/check_laser_hits 统一判定并广播；
        - 幽灵缺失时按出生点懒创建最小幽灵（完整玩家实体同步是 todo 23，此处保持最小实现）。
        """
        gs = self.window.game_state
        attacker_id = payload.get("attacker_id", sender_id)
        weapon_name = payload.get("weapon", "")
        angle = float(payload.get("angle", 0.0))
        # 攻击者实体：防御性支持主机本地玩家（正常路径主机本地攻击不经此入口）
        if getattr(gs, "net_player_id", None) is not None and attacker_id == gs.net_player_id:
            attacker = self.player
        else:
            ghost = self._ensure_ghost(attacker_id)
            attacker = ghost
        # 用客户端上报的攻击瞬间世界坐标修正幽灵位置：幽灵位置由 20Hz 快照更新存在
        # 滞后，近战扇形/远程弹丸发射点按滞后位置裁决会导致 miss（修复客户端攻击
        # 打不中怪物/资源）。仅修正幽灵（self.player 是主机本地实体，位置本就准确）。
        report_x = payload.get("x")
        report_y = payload.get("y")
        if report_x is not None and report_y is not None and attacker is not self.player:
            attacker.center_x = float(report_x)
            attacker.center_y = float(report_y)
        # 按武器名查定义（主机权威数值；未知武器回退拳头）
        wdef = _lookup_weapon_by_name(weapon_name)
        kind = wdef.get("kind", "melee")
        # 伤害优先采用客户端上报的实际伤害（修复联机假伤害 8/1）：主机模板 damage 是
        # 基础值，客户端武器可能已升级（gs.weapon_damage 更高），直接按模板裁决会让
        # 升级武器在联机时伤害退回基础值；客户端是本人武器的权威源，与幽灵 HP 采纳同口径。
        damage = float(payload.get("damage") or wdef.get("damage", 8))
        speed = wdef.get("attack_speed", 1.0)
        wrange = wdef.get("range", 40)
        # 攻击目标点：由方向角（弧度）换算（combat 用世界坐标目标点计算朝向）
        tx = attacker.center_x + math.cos(angle) * 100.0
        ty = attacker.center_y + math.sin(angle) * 100.0
        # 客户端上报的装备/武器附加 debuff 列表（元素为 (效果ID, 效果等级) 元组）：
        # 客户端 _attack_debuffs 来自头盔/护甲/背包 effects 与武器 debuff，命中时一并施加
        # （修复「客户端装备特殊效果没起作用」——之前主机只按武器名施加 wdef 自带 debuff）
        raw_debuffs = payload.get("debuffs") or []
        debuffs = []
        for d in raw_debuffs:
            # 兼容 (id, lvl) 元组/列表两种载荷形态（json 序列化后元组变列表）
            if isinstance(d, (list, tuple)) and len(d) >= 2:
                debuffs.append((d[0], int(d[1])))
        # 客户端上报的装备暴击率和吸血（幽灵无此属性，需从客户端同步）：
        # 修复「客户端暴击/装备吸血不生效」——幽灵是最小实体，不继承装备 passive 效果
        if attacker is not self.player:
            attacker.crit_chance = float(payload.get("crit_chance", 0.0))
            attacker.equip_lifesteal = float(payload.get("equip_lifesteal", 0.0))
        # 存活怪物列表（主机权威怪物，客户端攻击由主机裁决命中）
        monsters = [m for m in self.monsters if hasattr(m, "alive") and m.alive]
        if kind == "melee":
            # 近战即时判定（按攻击者 id 独立冷却），逐条广播命中结果
            # 汇总武器自带 debuff + 客户端装备附加 debuff，一并传入 melee_attack 施加
            wdebuff = wdef.get("debuff")
            combined_debuffs = []
            if wdebuff:
                combined_debuffs.append((wdebuff, 1))
            combined_debuffs.extend(debuffs)
            hit = self.combat.melee_attack(
                attacker, monsters, damage, wrange, tx, ty, speed,
                attacker_id=attacker_id,
                lifesteal=wdef.get("lifesteal", LIFESTEAL_DEFAULT),
                debuffs=combined_debuffs,
            )
            for m, actual in hit:
                self._broadcast_damage(m, actual, hit=True, crit=False, debuffs=combined_debuffs)
            # 近战命中环境物（修复「客户端近战打资源不同步」）：客户端近战攻击收敛主机后，
            # 环境物受击/摧毁/掉落也必须在主机裁决并广播（客户端本地不裁决环境物伤害）。
            # 判定口径与 handle_harvestable_combat 完全一致（范围 wrange+30、扇形 60°）
            for i, h in enumerate(self.harvestables):
                if not h.alive:
                    continue
                dist = math.hypot(h.center_x - attacker.center_x,
                                  h.center_y - attacker.center_y)
                if dist <= (wrange + 30):
                    # 扇形角度检查：与本地路径一致（鼠标方向 = 攻击角 angle，弧度转度）
                    dx = h.center_x - attacker.center_x
                    dy = h.center_y - attacker.center_y
                    ang = math.degrees(math.atan2(dy, dx))
                    mouse_angle = math.degrees(angle)
                    diff = abs((ang - mouse_angle + 180) % 360 - 180)
                    if diff <= 60:  # 在攻击扇形内
                        dmg = int(damage)
                        h.take_damage(dmg)
                        # 主机广播环境物单次受击（Bug2 修复：客户端近战打资源时
                        # 客户端也能看到受击反馈，否则只有最终 env_destroyed 才同步）
                        self._broadcast_env_damage(i, dmg)
                        floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                        particle_system.emit(h.center_x, h.center_y, 5,
                                             (150, 150, 150), speed=60, life=0.3, size=3)
                        # 仙人掌反伤：攻击者（客户端幽灵）受到反弹伤害（受防御减免）
                        if h.resource_type == "cactus":
                            # 修复：用 take_damage 返回的实际伤害显示（护盾/防御减免后真实扣血量）
                            actual = attacker.take_damage(CACTUS_THORN_DAMAGE)
                            floating_texts.add_damage(attacker.center_x,
                                                     attacker.center_y + 30,
                                                     actual)
                        # 环境物被击杀：掉落 + 移出障碍（env_destroyed/drop_spawn
                        # 由 _broadcast_map_changes 与 drop_spawn 广播统一同步到客户端）
                        if not h.alive:
                            # 客户端近战裁决路径：采集经验归属客户端，不发给本端主机
                            on_harvestable_destroyed(self, h, award_exp=False)
        elif wdef.get("special") == "laser":
            # 陨星炮：主机生成激光（起点=幽灵位置），命中由 check_laser_hits 统一广播；
            # 客户端装备附加 debuff 挂到激光上，命中即施加
            self.combat.spawn_laser(
                attacker, damage, tx, ty,
                length=wrange, width=24, duration=3.0,
                attacker_id=attacker_id,
                debuffs=debuffs,
            )
        else:
            # 远程：生成弹丸（发射原点=幽灵位置），命中由 check_monster_hits 统一判定广播；
            # debuff 按武器定义生成（权杖随机 debuff 与单人本地路径保持一致），
            # 客户端装备附加 debuff 一并挂到弹丸上（命中时全部施加）
            debuff_id = None
            if wdef.get("random_debuff"):
                from entities.effects_defs import DEBUFF_POOL
                debuff_id = random.choice(DEBUFF_POOL)
            elif wdef.get("debuff"):
                debuff_id = wdef["debuff"]
            self.combat.ranged_attack(
                attacker, damage,
                wdef.get("projectile_speed", 400), tx, ty,
                wdef.get("special", ""), debuff_id, speed,
                attacker_id=attacker_id,
                debuffs=debuffs,
                # 散射/吸血（主机裁决弹丸命中时生效）：散射按武器定义生成多发弹丸
                lifesteal=wdef.get("lifesteal", LIFESTEAL_DEFAULT),
                spread_count=wdef.get("spread_count", SPREAD_COUNT_DEFAULT),
                spread_angle=wdef.get("spread_angle", SPREAD_ANGLE_DEFAULT),
            )

    def _resolve_skill_use(self, sender_id: int, payload: dict) -> None:
        """主机权威裁决客户端的技能释放（SKILL_USE）：技能效果收敛主机

        - 施放者用客户端幽灵（remote_players，懒创建于地图出生点，角色 id 来自
          ROOM_START 名册）；若该 id 是主机本地玩家则直接用 self.player；
        - 用客户端上报的施放瞬间世界坐标修正幽灵位置（与 ATTACK_EVENT 同口径，
          20Hz 快照存在滞后，技能弹丸发射点/影袭落点按滞后位置裁决会 miss）；
        - use_skill(broadcast=True)：奥术爆发弹丸经 PROJECTILE_SNAPSHOT 同步、
          影袭落点命中经 _broadcast_damage 广播、圣盾减伤在幽灵 take_damage 结算，
          全部与客户端本地纯表现（input_handler F 键）保持一致。
        """
        gs = self.window.game_state
        caster_id = payload.get("player_id", sender_id)
        # 施放者实体：防御性支持主机本地玩家（正常路径主机本地技能不经此入口）
        if getattr(gs, "net_player_id", None) is not None and caster_id == gs.net_player_id:
            caster = self.player
        else:
            caster = self._ensure_ghost(caster_id)
        # 用客户端上报的施放瞬间世界坐标修正幽灵位置（仅幽灵；主机本地玩家位置本就准确）
        report_x = payload.get("x")
        report_y = payload.get("y")
        if report_x is not None and report_y is not None and caster is not self.player:
            caster.center_x = float(report_x)
            caster.center_y = float(report_y)
        # 技能伤害基数 = 客户端上报的当前武器伤害（与 ATTACK_EVENT 采纳客户端伤害同口径）
        damage = float(payload.get("damage") or 0)
        from game.character_skills import use_skill
        use_skill(self, caster,
                  float(payload.get("mouse_x") or 0),
                  float(payload.get("mouse_y") or 0),
                  damage, broadcast=True)

    def _apply_damage_result(self, payload: dict) -> None:
        """客户端应用主机下发的 DAMAGE_RESULT：远端怪物按 net_id 扣血并显示命中反馈

        - 目标缺失（怪物已被击杀/快照尚未到达）时静默忽略；
        - 主机权威血量以 MONSTER_SNAPSHOT 为准，此处扣血仅做即时显示，下一帧快照校准。
        """
        target_id = payload.get("target_id")
        if target_id is None:
            return
        rm = self.remote_monsters.get(target_id)
        if rm is None:
            return  # 目标缺失（远端怪物已删除/快照未达）：忽略
        damage = payload.get("damage", 0)
        if payload.get("hit"):
            # 扣血（不小于 0）+ 命中音效 + 漂浮伤害文字 + 受击闪白
            rm.hp = max(0.0, rm.hp - damage)
            sound_manager.play_monster_hit()
            floating_texts.add_damage(rm.center_x, rm.center_y + 25, damage)
            if hasattr(rm, "_hit_flash"):
                rm._hit_flash = 0.15
            # 等级系统：客户端击杀经验（各端本地结算）
            # 主机在 on_monster_death 按 last_attacker_id 归属发放；客户端无归属广播，
            # 简化：DAMAGE_RESULT 使远端怪物血量归零即视为本端参与击杀（host 本地玩家击杀
            # 也走本路径，net_mode 非 client 时跳过避免与 on_monster_death 重复发放）。
            if (rm.hp <= 0 and not getattr(rm, "_exp_awarded", False)
                    and getattr(self.window.game_state, "net_mode", "solo") == "client"):
                rm._exp_awarded = True  # 防重复：同一怪物只发一次击杀经验
                amount = EXP_KILL_BASE * (EXP_BOSS_MULT if getattr(rm, "is_boss", False) else 1)
                _award_exp(self, amount)
        # 附加 debuff 列表（表现层记录，快照会校准覆盖）：逐条施加，含效果等级
        # （修复客户端特殊效果不全生效：旧版只广播单个无等级 debuff，多效果丢失）
        debuffs = payload.get("debuffs") or []
        if not debuffs and payload.get("debuff"):
            debuffs = [[payload.get("debuff"), 1]]  # 兼容旧单字段载荷
        for entry in debuffs:
            if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                eid = entry[0]
                lvl = int(entry[1]) if len(entry) >= 2 and entry[1] else 1
            else:
                eid, lvl = entry, 1
            if eid and hasattr(rm, "apply_debuff"):
                try:
                    rm.apply_debuff(eid, lvl)
                except Exception:
                    pass  # 未知效果：忽略，快照校准

    def _ensure_ghost(self, player_id: int) -> Player:
        """主机/客户端按需创建远端玩家幽灵（懒创建），并注册主机侧受击广播钩子

        - 幽灵 = Player 实例，持全量 HP（主机权威）；完整实体同步是 todo 23，
          此处按地图出生点生成最小幽灵（攻击/受击坐标足够）；
        - 主机模式：幽灵注册 on_take_damage 钩子 → 扣血即广播 PLAYER_HURT
          （HP 主机权威，见 player.py on_take_damage 注释）；
        - 客户端模式：只创建幽灵供 HP 显示（钩子恒 None，不广播）。
        """
        ghost = self.remote_players.get(player_id)
        if ghost is not None:
            return ghost
        gs = self.window.game_state
        # 幽灵出生点：优先取 ROOM_START 下发的全房出生点（gs.net_spawns 含主机 slot=0），
        # 未知玩家（晚期加入等）回退到首个房间中心
        spawn = (gs.net_spawns or {}).get(player_id)
        if spawn is not None:
            sx, sy = spawn
        else:
            sx, sy = (self.map_data["rooms"][0].center
                      if self.map_data.get("rooms") else (400, 400))
        # 幽灵名册：玩家名 + 角色 id（名册驱动，供渲染头顶名称、状态条与技能裁决用）
        roster_entry = (gs.net_roster or {}).get(player_id)
        ghost = Player(
            center_x=sx, center_y=sy,
            character_id=roster_entry.get("character_id", "initial") if roster_entry else "initial",
        )
        ghost.net_name = roster_entry.get("name", f"玩家{player_id}") if roster_entry else f"玩家{player_id}"
        # 幽灵联机身份：技能弹丸归属（_arcane_blast 取 owner_net_id=net_player_id，
        # 客户端快照跳过 owner_id==my_id 去重，见 _apply_projectile_snapshot 注释）
        ghost.net_player_id = player_id
        # 幽灵朝向（Entity 同步 todo 23 快照字段，默认朝右）
        ghost.facing = 0.0
        # 主机权威：幽灵受击（怪物近战/弹丸）→ 广播 PLAYER_HURT（各端扣血显示）。
        # debuff 从幽灵 _pending_debuff 读取：怪物攻击路径先设置再 take_damage，
        # 钩子广播时随伤害一并下发（修复客户端玩家被怪物攻击时特殊效果未生效）
        if gs.net_mode == "host":
            ghost.on_take_damage = lambda actual, pid=player_id: self._broadcast_player_hurt(
                pid, actual,
                getattr(ghost, "_pending_debuff", None),
                getattr(ghost, "_pending_debuff_level", 1),
                getattr(ghost, "_pending_debuff_effects", None),
            )
        self.remote_players[player_id] = ghost
        print(f"[GameView] 远端玩家幽灵懒创建: player_id={player_id} @({sx:.0f},{sy:.0f})")
        return ghost

    def _broadcast_player_hurt(self, player_id: int, damage: float,
                               debuff=None, debuff_level: int = 1,
                               debuffs: list = None) -> None:
        """主机广播 PLAYER_HURT：玩家受伤（HP 主机权威），各端据此本地扣血 + 受击反馈

        debuff/debuff_level：怪物攻击（近战/弹丸）附带的附加效果与等级，随广播下发
        客户端，修复「客户端玩家被怪物攻击时特殊效果未生效」（旧版钩子恒传 None）。
        debuffs：全部效果列表 [(效果ID, 效果等级), ...]（联机多效果同步用）
        """
        gs = self.window.game_state
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.broadcast(MsgType.PLAYER_HURT, {
                "player_id": player_id,
                "damage": damage,
                "debuff": debuff,
                "debuff_level": debuff_level,
                "debuffs": debuffs or [],
            })

    def _serialize_players(self) -> list:
        """序列化全部玩家为 PLAYER_SNAPSHOT 的 players 列表（主机权威）

        - 主机本地玩家 = gs.net_player_id（未接入大厅时为 0 约定值）；
        - 客户端幽灵 = remote_players（HP 权威值，位置暂为出生点，todo 23 补实体同步）；
        - 载荷键名严格遵循 net/protocol.py MESSAGE_SCHEMAS["PLAYER_SNAPSHOT"]。
        """
        gs = self.window.game_state
        host_id = getattr(gs, "net_player_id", None)
        if host_id is None:
            host_id = 0  # 大厅未接入（todo 21）前的约定：主机固定玩家 id=0
        players = [{
            "player_id": host_id,
            "x": self.player.center_x, "y": self.player.center_y,
            "hp": self.player.hp, "max_hp": self.player.max_hp,
            "weapon": self._current_weapon_name(),
            "facing": getattr(self.player, "facing", 0.0),
            # 修复(BUG1)：主机撤离/死亡进入观战后广播 alive=False → 客户端侧主机幽灵
            # HP 归零、渲染消失，不再作为观战跟随目标（否则客户端观战跟随静止在
            # 撤离点的主机幽灵，视角卡死无法移动）
            "alive": self.player.alive and not self._spectating,
        }]
        for pid, ghost in self.remote_players.items():
            players.append({
                "player_id": pid,
                "x": ghost.center_x, "y": ghost.center_y,
                "hp": getattr(ghost, "hp", 0), "max_hp": getattr(ghost, "max_hp", 0),
                "weapon": None, "facing": 0.0,
                "alive": getattr(ghost, "alive", True),
            })
        return players

    def _apply_player_hurt(self, payload: dict) -> None:
        """客户端应用主机 PLAYER_HURT：本地扣血 + 受击反馈；远端玩家幽灵同步 HP

        - player_id == 本地玩家 id → 本地扣血条 + 屏幕红闪 + 音效（本地触发，
          不依赖额外网络往返）；HP 权威值以 PLAYER_SNAPSHOT 校准为准；
        - player_id 为他人 → 对应幽灵 hp 扣减（显示用，快照校准）。
        """
        gs = self.window.game_state
        player_id = payload.get("player_id")
        damage = payload.get("damage", 0)
        if player_id is None:
            return
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id == my_id:
            # 本地玩家受伤：扣血 + 红闪 + 音效（受击反馈本地即时触发）
            self.player.hp = max(0, round(self.player.hp - damage, 2))
            self._player_hit_flash = 0.3
            sound_manager.play_hurt()
            floating_texts.add_damage(self.player.center_x, self.player.center_y + 20, damage)
            debuff = payload.get("debuff")
            debuff_level = payload.get("debuff_level") or 1  # 附带效果等级（默认 1 级）
            if debuff and hasattr(self.player, "apply_debuff"):
                try:
                    self.player.apply_debuff(debuff, debuff_level)
                except Exception:
                    pass  # 未知效果：忽略，快照校准
        else:
            # 远端玩家（幽灵）受伤：同步 HP 显示（扣血，快照校准）
            ghost = self.remote_players.get(player_id)
            if ghost is not None:
                ghost.hp = max(0, round(getattr(ghost, "hp", 0) - damage, 2))

    def _apply_player_snapshot(self, payload) -> None:
        """客户端应用主机 PLAYER_SNAPSHOT：校准本地与幽灵 HP（防漂移）"""
        gs = self.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        for entry in payload.get("players", []):
            pid = entry.get("player_id")
            hp = entry.get("hp", 0)
            max_hp = entry.get("max_hp", 0)
            if my_id is not None and pid == my_id:
                # 观战期跳过自身 HP 校准：主机快照中已撤离玩家幽灵 alive=False → hp=0，
                # 若仍校准会把本地 player.hp 置 0（触发 Player.alive=False 派生副作用），
                # 观战渲染已由 _spectating 守卫隐藏本体，HP 值无需再校准（修复观战崩溃）
                if self._spectating:
                    continue
                # 本地玩家：权威 HP 校准（含 max_hp；round 避免浮点长小数）
                self.player.max_hp = max(1, int(max_hp or self.player.max_hp))
                self.player.hp = min(self.player.max_hp, round(hp, 2))
            else:
                ghost = self.remote_players.get(pid)
                if ghost is None:
                    ghost = self._ensure_ghost(pid)
                ghost.max_hp = max(1, int(max_hp or getattr(ghost, "max_hp", 0)))
                ghost.hp = min(ghost.max_hp, round(hp, 2))
                # 远端实体同步：主机快照为权威位置/朝向/存活（todo 23 玩家实体同步）
                ghost.center_x = entry.get("x", ghost.center_x)
                ghost.center_y = entry.get("y", ghost.center_y)
                ghost.facing = entry.get("facing", getattr(ghost, "facing", 0.0))
                if not entry.get("alive", True):
                    ghost.hp = 0  # 存活标记为假：HP 归零（Player.alive 由 hp>0 派生）

    def _send_player_snapshot(self) -> None:
        """客户端 20Hz 上报本人实体为 PLAYER_SNAPSHOT（单播，主机据此更新本端幽灵）

        - 客户端是本人位置/朝向/HP 的权威源：主机快照（_serialize_players）以
          ghost 转发给其他端，实现全房实体同步（todo 23 玩家实体同步）；
        - facing 用鼠标世界坐标计算（玩家本地无独立 facing 字段）。
        """
        gs = self.window.game_state
        if gs.net_client is None or gs.net_player_id is None:
            return
        my_id = gs.net_player_id
        # 鼠标世界坐标（沿用 on_mouse_motion 里的计算；坐标系为逻辑分辨率，
        # 窗口最大化/全屏时由 main.GameWindow 统一换算，见 input_handler 注释）
        cam = self.controller.camera.position if self.controller else (0, 0)
        world_mx = self._mouse_x + cam[0] - WINDOW_WIDTH / 2
        world_my = self._mouse_y + cam[1] - WINDOW_HEIGHT / 2
        import math
        facing = math.atan2(world_my - self.player.center_y,
                            world_mx - self.player.center_x)
        gs.net_client.send((MsgType.PLAYER_SNAPSHOT, {
            "players": [{
                "player_id": my_id,
                "x": self.player.center_x, "y": self.player.center_y,
                "hp": self.player.hp, "max_hp": self.player.max_hp,
                "weapon": self._current_weapon_name(),
                "facing": facing,
                # 观战时已撤离/阵亡：上报 alive=False → 主机侧幽灵 HP 归零、渲染消失，
                # 不会被主机观战跟随（修复：撤离幽灵停在撤离点不再移动，跟随即视角卡死）
                "alive": self.player.alive and not getattr(self, "_spectating", False),
            }],
        }))

    def _apply_client_snapshot(self, sender_id: int, payload: dict) -> None:
        """主机应用客户端上报的 PLAYER_SNAPSHOT：更新该玩家幽灵的位置/朝向/存活

        - 只采纳 sender_id 本人条目（防伪造他人坐标）；幽灵 HP/max_hp 采纳客户端上报值：
          客户端本地应用了装备被动效果（max_hp/regen 加成），是本人 HP/max_hp 的权威源，
          若不采纳，主机 _serialize_players 会把无加成的默认 max_hp 广播回客户端，
          客户端 _apply_player_snapshot 校准后会把装备 max_hp 加成覆盖掉（B1 装备效果不全生效）；
        - 阵亡时 HP 归零（主机随后 PLAYER_DEATH 单播通知）；
        - 幽灵经 _ensure_ghost 懒创建（客户端幽灵在主机侧必然已存在）。
        """
        gs = self.window.game_state
        ghost = self._ensure_ghost(sender_id)
        for entry in payload.get("players", []):
            if entry.get("player_id") != sender_id:
                continue  # 只同步本人上报条目
            ghost.center_x = entry.get("x", ghost.center_x)
            ghost.center_y = entry.get("y", ghost.center_y)
            ghost.facing = entry.get("facing", getattr(ghost, "facing", 0.0))
            # 采纳客户端上报的 HP/max_hp（含装备被动加成），保持幽灵与客户端本人一致，
            # 避免快照校准覆盖装备效果（详见函数 docstring）
            ghost.max_hp = max(1, int(entry.get("max_hp") or ghost.max_hp))
            ghost.hp = min(ghost.max_hp, round(entry.get("hp", ghost.hp), 2))
            if not entry.get("alive", True):
                ghost.hp = 0  # 客户端阵亡：HP 归零（主机随后 PLAYER_DEATH 单播通知）

    def _broadcast_room_ended(self, reason: str, close_room: bool = False) -> None:
        """主机广播 ROOM_ENDED 并收口本局房间生命周期（重构：房间生命周期与单局解耦）

        - reason：room_closed（点「关闭房间」按钮）/ all_finished（全员结束，回房等待再次开局）
          / host_evac / host_evac_fail / host_fail（旧路径保留兼容）；
        - close_room=True（reason=room_closed）：广播后停止 net_server 并清理联机字段，
          主机回 StartView（房间解散）。否则（close_room=False）房间保留：服务器继续监听、
          端口不释放，主机回 LobbyView host_wait 等待玩家再次准备开局。
        - 只在主机且尚未广播过时发送（_room_ended_handled 单次闸门，防止撤离+死亡交织重复）；
        - 广播已入队（call_soon_threadsafe），stop() join 期间事件循环会完成发送。
        """
        if self._room_ended_handled:
            return
        self._room_ended_handled = True
        gs = self.window.game_state
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.broadcast(MsgType.ROOM_ENDED, {"reason": reason})
            print(f"[GameView] 主机广播房间结束: {reason} close_room={close_room}")
            if close_room:
                # 关闭房间：停止服务器释放端口，保证下一次建房可重新监听
                gs.net_server.stop()
                gs.net_server = None
                gs.net_mode = "solo"
                from views.start_view import StartView
                self.window.show_view(StartView(self.window_ref))
            else:
                # 本局结束但房间保留：主机回 LobbyView host_wait 等待再次开局（服务器不停止）
                self._back_to_lobby("本局结束，等待再次开局")

    def _back_to_lobby(self, notice: str = "") -> None:
        """本局结束回房等待（房间保留）：新建 LobbyView 并复用现有 server/client 连接

        - host：mode=host_wait，复用 gs.net_server（服务器线程继续监听，端口不释放）；
        - client：mode=client_wait，复用 gs.net_client（连接保留，不重新握手）；
        - 复用规则：_handshake_sent 置 True（client_wait 不再补发 HELLO+JOIN），
          服务器/客户端对象直接从 LobbyView.server/client 属性接管。
        """
        gs = self.window.game_state
        from views.lobby_view import LobbyView
        lobby = LobbyView(self.window_ref)
        if gs.net_mode == "host" and gs.net_server is not None:
            lobby.mode = "host_wait"
            lobby.server = gs.net_server
            lobby.bridge = getattr(gs.net_server, "bridge", None)
            lobby._players_info = gs.net_server.player_info()
            lobby._status = notice or "本局结束，等待再次开局"
            lobby._notice = notice
        elif gs.net_mode == "client" and gs.net_client is not None:
            lobby.mode = "client_wait"
            lobby.client = gs.net_client
            lobby._handshake_sent = True  # 连接已建立：回房不重复握手
            lobby._status = notice or "已回到房间，等待下一局"
            lobby._notice = notice
        gs.run_carried = {}  # 本局结束：携带物已结算/清空
        gs.run_potions = {}  # 本局药水槽一并清空（未撤离不结算）
        self.window.show_view(lobby)

    def _apply_room_ended(self, payload: dict) -> None:
        """客户端应用主机 ROOM_ENDED：按 reason 收口房间生命周期

        - room_closed（主机关闭房间）：房间解散 → 停止客户端连接回 StartView；
        - 其他（all_finished 全员结束等）：本局结束但房间保留 → 客户端不断开连接，
          回 LobbyView client_wait 等待主机再次开局（复用 gs.net_client）。
        - 未撤离即失败语义由主机侧统一收口；_room_ended_handled 防止重复处理。
        """
        if self._room_ended_handled:
            return
        self._room_ended_handled = True
        gs = self.window.game_state
        reason = payload.get("reason", "host_end")
        if reason == "room_closed":
            # 房间解散：停止客户端连接回主菜单（与 _leave 同口径，保留 net_room_id 供展示）
            if gs.net_client is not None:
                gs.net_client.stop()
            gs.net_client = None
            gs.net_mode = "solo"
            gs.run_carried = {}  # 房间结束未撤离：本次携带物不结算清除
            gs.run_potions = {}  # 本局药水槽一并清空
            from views.start_view import StartView
            self.window.show_view(StartView(self.window_ref))
            return
        # 本局结束但房间保留：回房等待（连接保持，等待主机再次开局）
        gs.run_carried = {}
        gs.run_potions = {}  # 本局药水槽一并清空
        self._back_to_lobby(f"本局结束：{reason}")

    def _handle_potion_use(self, sender_id: int, payload: dict) -> None:
        """主机确认客户端药水使用请求：查库存 → 应用到对应玩家/幽灵 → 广播 POTION_ACK

        - potion_id 支持两种来源：
          * "run:item_id"：本局药水槽（不占背包容量），从 equipment_defs.POTIONS 取效果，
            无 DB 扣减；主机校验该玩家 run 药水库存并扣减；
          * 普通药水 id：查库验证 → use_potion 扣减。
        - 效果统一经 Player.apply_potion_effect（含瞬回修复/护盾/狂暴），主机权威：
          各端 HP 经 PLAYER_SNAPSHOT 校准；治疗数字随 POTION_ACK 广播，全端可见。
        - ACK 携带 effect/value/duration：客户端对本人本地即时生效（buff 不随快照同步）。
        """
        gs = self.window.game_state
        potion_id = payload.get("potion_id", "")
        from entities.equipment_defs import POTIONS
        # 本局药水槽药水（run: 前缀，仅字符串）：效果来自 POTIONS 定义，扣减对应库存
        if isinstance(potion_id, str) and potion_id.startswith("run:"):
            item_id = potion_id[4:]
            pdef = POTIONS.get(item_id)
            if pdef is None:
                self._reject_potion(sender_id, potion_id)
                return
            effect = pdef.get("effect", "heal")
            value = pdef.get("value", 0)
            duration = pdef.get("duration", 0)
            # 校验并扣减该玩家的 run 药水库存：
            # - 本人（主机）：gs.run_potions；
            # - 客户端：_players_run_carried[sender_id]["potion"]（主机记录的拾取清单）。
            if sender_id == getattr(gs, "net_player_id", None):
                run_potions = getattr(gs, "run_potions", None) or {}
                if run_potions.get(item_id, 0) <= 0:
                    self._reject_potion(sender_id, potion_id)
                    return
                run_potions[item_id] -= 1
                if run_potions[item_id] <= 0:
                    del run_potions[item_id]
            else:
                carried = self._players_run_carried.setdefault(sender_id, {})
                potion_slot = carried.setdefault("potion", {})
                if potion_slot.get(item_id, 0) <= 0:
                    self._reject_potion(sender_id, potion_id)
                    return
                potion_slot[item_id] -= 1
                if potion_slot[item_id] <= 0:
                    del potion_slot[item_id]
        else:
            # 仓库药水：查库验证 → use_potion 扣减
            # 修复：sender_id 是网络 player_id（主机=0，客户端=1/2/3），
            # DB 查询需要 DB player_id（数据库自增 ID）。
            # 主机用 gs.player_id；客户端通过玩家名反查 DB ID。
            from db.database import get_potions, use_potion
            db_pid = sender_id
            if sender_id == getattr(gs, "net_player_id", None):
                db_pid = gs.player_id
            elif gs.net_server is not None:
                for pid, pname, _slot in gs.net_server.player_info():
                    if pid == sender_id:
                        from db.database import get_or_create_player
                        db_pid = get_or_create_player(pname)
                        break
            potions = get_potions(db_pid)
            pot = next((p for p in potions if p["id"] == potion_id), None)
            if pot is None:
                self._reject_potion(sender_id, potion_id)
                return
            effect_info = use_potion(db_pid, potion_id)
            if not effect_info:
                self._reject_potion(sender_id, potion_id)
                return
            effect = effect_info["effect"]
            value = effect_info.get("value", 0)
            duration = effect_info.get("duration", 0)
        # 目标玩家实体：主机本地玩家或客户端幽灵
        target = self.player if getattr(gs, "net_player_id", None) == sender_id \
            else self._ensure_ghost(sender_id)
        # 统一应用药水效果（heal 无 duration 时瞬回，见 Player.apply_potion_effect）
        heal_amount = target.apply_potion_effect(effect, value, duration)
        # 广播确认（含效果信息，客户端本地即时生效 + 全端可见治疗数字）
        gs.net_server.broadcast(MsgType.POTION_ACK, {
            "player_id": sender_id, "potion_id": potion_id,
            "accepted": True, "heal_amount": heal_amount,
            "effect": effect, "value": value, "duration": duration,
        })

    def _reject_potion(self, sender_id: int, potion_id: str) -> None:
        """广播药水使用被拒（库存不足/无效药水）：客户端保持原状"""
        gs = self.window.game_state
        gs.net_server.broadcast(MsgType.POTION_ACK, {
            "player_id": sender_id, "potion_id": potion_id,
            "accepted": False, "heal_amount": 0.0,
        })

    def _apply_potion_ack(self, payload: dict) -> None:
        """客户端应用主机 POTION_ACK：本地即时生效 + 治疗数字漂浮文字

        - 本人：按 ACK 的 effect/value/duration 本地应用（speed/shield/power buff
          不随快照同步，须本地即时生效；heal 瞬回/持续），HP 权威值以
          PLAYER_SNAPSHOT 校准为准；
        - 他人（幽灵）：效果已在主机侧应用到幽灵实体，仅显示治疗数字；
        - accepted=False（库存不足/无效）时仅提示，不做本地扣减。
        """
        gs = self.window.game_state
        player_id = payload.get("player_id")
        heal_amount = payload.get("heal_amount", 0)
        accepted = payload.get("accepted", False)
        my_id = getattr(gs, "net_player_id", None)
        if not accepted:
            floating_texts.add(self.player.center_x, self.player.center_y + 20,
                              "药水无效", arcade.color.RED)
            return
        # 本人：本地即时应用效果（与主机对本人实体应用同口径）
        if my_id is not None and player_id == my_id:
            effect = payload.get("effect", "heal")
            value = payload.get("value", 0)
            duration = payload.get("duration", 0)
            heal_amount = self.player.apply_potion_effect(effect, value, duration)
            # 本局药水（run: 前缀）：本地同步扣减 run_potions（主机权威扣减后广播，
            # 客户端据此保持本地槽位与主机一致；仓库药水已由主机 use_potion 扣减，无需处理）
            potion_id = payload.get("potion_id", "")
            if isinstance(potion_id, str) and potion_id.startswith("run:"):
                item_id = potion_id[4:]
                run_potions = getattr(gs, "run_potions", None)
                if run_potions and run_potions.get(item_id, 0) > 0:
                    run_potions[item_id] -= 1
                    if run_potions[item_id] <= 0:
                        del run_potions[item_id]
            # 浮动文字：按效果显示对应文案
            if heal_amount > 0:
                floating_texts.add(self.player.center_x, self.player.center_y,
                                   f"+{int(heal_amount)} HP", arcade.color.GREEN)
            elif effect == "speed":
                floating_texts.add(self.player.center_x, self.player.center_y,
                                   "加速!", arcade.color.CYAN)
            elif effect == "shield":
                floating_texts.add(self.player.center_x, self.player.center_y,
                                   f"护盾 +{int(value)}", (120, 160, 255))
            elif effect == "power":
                floating_texts.add(self.player.center_x, self.player.center_y,
                                   f"狂暴! +{int((value - 1) * 100)}% 伤害", (255, 120, 40))
            elif effect == "fruit":
                floating_texts.add(self.player.center_x, self.player.center_y,
                                   "果实加速!", arcade.color.GREEN)
        else:
            ghost = self.remote_players.get(player_id)
            if ghost is not None and heal_amount > 0:
                floating_texts.add(ghost.center_x, ghost.center_y,
                                   f"+{int(heal_amount)} HP", arcade.color.GREEN)

    def _record_player_pickup(self, player_id: int, drop: DropItem) -> None:
        """主机记录某玩家拾取的掉落物到 _players_run_carried（撤离结算清单数据源，Todo 19 消费）

        run_carried 结构口径与 GameState.run_carried 完全一致：
        - gold: 直接累加 quantity；
        - resource/potion: {item_id: qty}；
        - weapon/helmet/armor/backpack: {(item_id, level): qty}（同名不同等级并存）。
        主机在拾取仲裁成功时累加；该玩家撤离时据此下发 EVAC_RESULT 清单（各端写各自本地库）。
        """
        carried = self._players_run_carried.setdefault(player_id, {})
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

    def _handle_pickup_request(self, sender_id: int, payload: dict) -> None:
        """主机仲裁客户端拾取请求：先到先得 + 距离校验，广播 PICKUP_RESULT（Todo 18）

        仲裁规则（对应协议 PICKUP_REQUEST/RESULT 设计）：
        - 按掉落物网络 id 在主机掉落列表中查找（掉落在主机权威生成，客户端视觉为副本）；
        - 距离校验：用该玩家幽灵位置（远程玩家实体）与掉落物距离 < DROP_PICKUP_RADIUS；
        - 先到先得：匹配成功即从主机掉落列表移除该掉落物并广播 accepted=True，
          后续同 id 请求必然失败（already_taken）——双客户端抢同一掉落物只有一人获得。
        """
        gs = self.window.game_state
        if gs.net_server is None:
            return  # 非主机：理论不可达（inbound 仅主机消费），防御性返回
        net_id = payload.get("item_id")
        if not net_id:
            return  # 非法请求：无掉落物网络 id
        # 在主机权威掉落列表中查找目标掉落物
        target = next((d for d in self.drops if d.net_id == net_id), None)
        if target is None:
            # 已不存在：被其他玩家先拾取/已过期/从未生成 → 拒绝
            gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
                "player_id": sender_id, "item_id": net_id,
                "accepted": False, "reason": "already_taken",
            })
            return
        # 距离校验：优先用客户端上报的拾取瞬间坐标（与客户端本地 try_pickup 判定口径一致，
        # 避免幽灵位置 20Hz 快照滞后导致正常范围内的拾取被误判为 too_far）；
        # 未携带坐标（旧协议/异常）时回退幽灵位置兜底
        req_x, req_y = payload.get("x"), payload.get("y")
        if req_x is not None and req_y is not None:
            dist = math.hypot(req_x - target.center_x,
                              req_y - target.center_y)
        else:
            ghost = self._ensure_ghost(sender_id)
            dist = math.hypot(ghost.center_x - target.center_x,
                              ghost.center_y - target.center_y)
        if dist >= DROP_PICKUP_RADIUS:
            # 拒绝：物品仍在主机掉落列表，广播掉落物详情供客户端恢复视觉（乐观拾取回滚）
            gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
                "player_id": sender_id, "item_id": net_id,
                "accepted": False, "reason": "too_far",
                "drop": {
                    "item_type": target.item_type, "item_id": target.item_id,
                    "x": target.center_x, "y": target.center_y,
                    "quantity": target.quantity, "level": target.level,
                },
            })
            return
        # 先到先得：从主机掉落列表移除（后续请求必失败）并记录该玩家携带物
        self.drops.remove(target)
        self._record_player_pickup(sender_id, target)
        gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
            "player_id": sender_id, "item_id": net_id,
            "accepted": True, "reason": None,
        })

    def _handle_evac_request(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端撤离请求（B12）：下发主机权威携带物清单，广播 EVAC_RESULT

        - 数据源 = _players_run_carried[sender_id]（主机拾取仲裁成功时累加，口径与
          GameState.run_carried 一致，包含 tuple 键的装备类条目）；
        - 广播 EVAC_RESULT 含 "player_id" + "run_carried"，各端（含请求客户端）据此
          调用 commit_run_to_warehouse 写各自本地库（每端写自己的库）；
        - run_carried 的 tuple 键先经 _serialize_evac_carried 转为 "id|level" 字符串
          （json 序列化安全），客户端 _apply_evac_result 内 _deserialize_evac_carried 还原。
        """
        gs = self.window.game_state
        if gs.net_server is None:
            return  # 非主机：理论不可达（inbound 仅主机消费），防御性返回
        carried = self._players_run_carried.get(sender_id, {})
        gs.net_server.broadcast(MsgType.EVAC_RESULT, {
            "player_id": sender_id,
            "run_carried": self._serialize_evac_carried(carried),
        })
        # 记录该玩家本局已撤离（观战期间全员结束判定用；客户端撤离后回房等待）
        self._player_status[sender_id] = "evac"
        # 修复：设置幽灵 HP=0 + alive=False，使小地图不再绘制该玩家点
        # （与 _handle_player_abandon 对齐，此前遗漏导致撤离后小地图幽灵残留）
        ghost = self.remote_players.get(sender_id)
        if ghost is not None:
            ghost.hp = 0
            ghost.alive = False
        print(f"[GameView] 主机响应玩家 {sender_id} 撤离请求：广播 EVAC_RESULT 结算清单")

    def _serialize_evac_carried(self, carried: dict) -> dict:
        """将携带物清单的 tuple 键转为 JSON 安全字符串键（EVAC_RESULT 载荷）

        run_carried 中 weapon/helmet/armor/backpack 的键是 (item_id, level) tuple，
        json.dumps 无法直接序列化 tuple 键；统一转为 "item_id|level" 字符串；
        gold 与 resource/potion 的键本就是 str/int，原样保留。
        """
        out: dict = {}
        for item_type, value in carried.items():
            if item_type == "gold":
                out[item_type] = value
            elif isinstance(value, dict):
                # 装备/资源/药水：tuple 键 → "id|level"；str 键原样保留
                out[item_type] = {
                    f"{k[0]}|{k[1]}" if isinstance(k, tuple) else k: v
                    for k, v in value.items()
                }
            else:
                out[item_type] = value
        return out

    def _deserialize_evac_carried(self, carried: dict) -> dict:
        """将 EVAC_RESULT 载荷还原为 commit_run_to_warehouse 入参口径（tuple 键还原）"""
        out: dict = {}
        for item_type, value in carried.items():
            if item_type == "gold" or not isinstance(value, dict):
                out[item_type] = value
                continue
            restored: dict = {}
            for key, qty in value.items():
                if isinstance(key, str) and "|" in key:
                    item_id, _, level = key.partition("|")
                    restored[(item_id, int(level))] = qty
                else:
                    restored[key] = qty
            out[item_type] = restored
        return out

    def _apply_map_change(self, payload: dict) -> None:
        """客户端应用主机 MAP_CHANGE：运行期地图改动（掉落生成/宝箱/环境物/水井/火箭台）

        - drop_spawn（Todo 18）：掉落物生成广播 → 本地创建视觉掉落物（带主机 net_id）；
        - chest_opened / env_destroyed / well_used / rocket_pad（Todo 20，D2）：
          obj_id = 列表序号（与 FULL_STATE 的 chests/env_objects id 同口径，客户端确定性
          重建的地图对象顺序与主机一致），直接把本地对象与主机权威状态对齐——
          宝箱标记已开（渲染/碰撞跳过）、环境物标记已摧毁、水井首次开启、
          火箭台镜像状态机与撤离倒计时（客户端不本地推进，纯表现层）。
        """
        change_type = payload.get("change_type")
        if change_type == "drop_spawn":
            for entry in payload.get("extra", {}).get("drops", []):
                net_id = entry.get("net_id")
                if not net_id:
                    continue  # 非法条目：无网络 id
                if any(d.net_id == net_id for d in self.drops):
                    continue  # 已存在（重复广播）：跳过
                drop = DropItem(
                    entry.get("x", 0.0), entry.get("y", 0.0),
                    entry.get("item_type", "gold"), entry.get("item_id", "gold"),
                    quantity=entry.get("quantity", 1), level=entry.get("level", 1),
                    net_id=net_id,
                )
                self.drops.append(drop)
            return
        # 运行期地图对象状态对齐（坐标对象均已在 setup 确定性重建，按序号直接对齐）
        obj_id = payload.get("obj_id")
        if change_type == "chest_opened":
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.chests):
                self.chests[obj_id].opened = True
        elif change_type == "env_destroyed":
            # 注意：alive 是只读 property（hp > 0），不能直接赋值；
            # 置 hp=0 + 移出障碍物，与主机 on_harvestable_destroyed 一致
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.harvestables):
                h = self.harvestables[obj_id]
                h.hp = 0
                if h in self.obstacle_list:
                    self.obstacle_list.remove(h)
        elif change_type == "env_damage":
            # 主机对资源造成的逐次伤害（Bug2 修复）：本地血条扣减 + 伤害数字 + 粒子，
            # 与 _client_visual_collisions 同口径 clamp 最低 1（避免本地提前"死亡"，
            # 最终归零以 env_destroyed 广播为准）
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.harvestables):
                h = self.harvestables[obj_id]
                if h.alive:
                    dmg = payload.get("state", {}).get("damage", 0)
                    h.hp = max(1, h.hp - dmg)
                    floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                    particle_system.emit(h.center_x, h.center_y, 5,
                                         (150, 150, 150), speed=60, life=0.3, size=3)
        elif change_type == "env_spawn":
            # 主机运行期补刷的环境物（修复「二次刷新资源客户端不可见」）：
            # 主机 respawn_harvestables 追加在列表末尾，客户端同样**追加**以保持
            # 两端列表长度/序号同步（obstacle_list 由 _sync_obstacles 每帧重建自动纳入）
            if isinstance(obj_id, int) and obj_id == len(self.harvestables):
                st = payload.get("state", {})
                h = HarvestableEntity(
                    st.get("x", 0.0), st.get("y", 0.0),
                    st.get("resource_type", "tree"),
                )
                self.harvestables.append(h)
        elif change_type == "well_used":
            # 水井首次开启：镜像 _well_opened（与主机交互/提示口径一致）
            self._well_opened = True
        elif change_type == "rocket_pad":
            # 火箭台：镜像状态机与撤离倒计时（客户端渲染读取 pad.state/_countdown_timer）
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.rocket_pads):
                pad = self.rocket_pads[obj_id]
                state = payload.get("state", {})
                if "pad_state" in state:
                    pad.state = state["pad_state"]
                if "countdown" in state:
                    pad._countdown_timer = float(state["countdown"])

    def _broadcast_map_changes(self) -> None:
        """联机主机：检测并广播运行期地图改动（宝箱开启/环境物摧毁/水井/火箭台状态）

        - 变更按列表序号 obj_id 广播（与 FULL_STATE 的 chests/env_objects id 同口径，
          客户端确定性重建的地图对象顺序一致，按序号直接对齐）；
        - 已广播跟踪（_broadcasted_chests/_broadcasted_env/_well_broadcasted/
          _broadcasted_pads）：只在状态首次变化时广播，避免对相同状态重复刷屏；
        - 火箭台撤离倒计时按整数秒重播（客户端纯表现层镜像倒计时，不本地推进）。
        """
        gs = self.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        # 宝箱：首次被打开 → 广播 opened（客户端据此跳过渲染/碰撞）
        for i, chest in enumerate(self.chests):
            if chest.opened and i not in self._broadcasted_chests:
                self._broadcasted_chests.add(i)
                gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                    "obj_id": i, "change_type": "chest_opened",
                    "state": {"opened": True}, "extra": {},
                })
        # 环境物：首次被摧毁 → 广播 alive=False（客户端渲染/碰撞跳过）
        for i, h in enumerate(self.harvestables):
            if not h.alive and i not in self._broadcasted_env:
                self._broadcasted_env.add(i)
                gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                    "obj_id": i, "change_type": "env_destroyed",
                    "state": {"alive": False}, "extra": {},
                })
        # 水井：首次开启 → 广播一次（客户端镜像 _well_opened，提示口径一致）
        if self._well_opened and not self._well_broadcasted:
            self._well_broadcasted = True
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": 0, "change_type": "well_used",
                "state": {"opened": True}, "extra": {},
            })
        # 火箭台：状态变化（撤离中按整数秒计数）→ 广播状态机镜像
        for i, pad in enumerate(self.rocket_pads):
            pad_state = getattr(pad, "state", None)
            if pad_state is None:
                continue
            if pad_state == "evacuating":
                key = ("evacuating", int(pad.get_countdown()))
            else:
                key = (pad_state,)
            if self._broadcasted_pads.get(i) != key:
                self._broadcasted_pads[i] = key
                gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                    "obj_id": i, "change_type": "rocket_pad",
                    "state": {"pad_state": pad_state,
                              "countdown": pad.get_countdown()},
                    "extra": {},
                })

    def _broadcast_env_damage(self, obj_id: int, damage: float) -> None:
        """联机主机：广播环境物单次受击（Bug2 修复：主机对资源的伤害同步到客户端）

        - 主机本地近战/弹丸/激光命中环境物时逐次广播（handle_harvestable_combat 与
          _resolve_attack_event 近战环境物路径均调用），客户端据此本地扣血条 +
          显示伤害数字/粒子，不再只有最终 env_destroyed 才同步；
        - obj_id = harvestables 列表序号（与 env_destroyed 同口径，客户端确定性重建
          的地图对象顺序一致）；solo/客户端模式不广播（solo 无网）。
        """
        gs = self.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        gs.net_server.broadcast(MsgType.MAP_CHANGE, {
            "obj_id": obj_id, "change_type": "env_damage",
            "state": {"damage": damage}, "extra": {},
        })

    def _broadcast_env_spawn(self, obj_id: int, x: float, y: float, resource_type: str) -> None:
        """联机主机：广播运行期补刷的环境物（修复「二次刷新资源客户端不可见」）

        - 主机 respawn_harvestables 补刷新资源时调用（game/respawn.py），客户端据此
          在本地 harvestables 列表**追加**新对象（obj_id = 追加位置，与 env_destroyed/
          env_damage 同口径，两端列表长度同步增长，序号保持一致）；
        - 客户端不再本地生成补刷资源（respawn 只在主机跑），完全由本广播驱动；
        - solo/客户端模式不广播（solo 无网；客户端不跑 respawn）。
        """
        gs = self.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        gs.net_server.broadcast(MsgType.MAP_CHANGE, {
            "obj_id": obj_id, "change_type": "env_spawn",
            "state": {"x": x, "y": y, "resource_type": resource_type}, "extra": {},
        })

    def _apply_pickup_result(self, payload: dict) -> None:
        """客户端应用主机 PICKUP_RESULT：成功保持乐观状态 / 被拒回滚 run_carried

        - accepted=True（本人/他人）：该掉落物已被某人拿走 → 移除本地视觉（物品消失）；
        - accepted=False + too_far（本人）：物品仍在主机掉落列表 → 回滚 run_carried
          + 用广播的 drop 详情重建本地视觉（乐观拾取时 try_pickup 已从 self.drops 移除）；
        - accepted=False + already_taken（本人）：物品已被他人拿走 → 回滚 run_carried
          + 移除本地视觉（物品已消失）。
        """
        gs = self.window.game_state
        player_id = payload.get("player_id")
        net_id = payload.get("item_id")
        accepted = payload.get("accepted", False)
        reason = payload.get("reason")
        my_id = getattr(gs, "net_player_id", None)
        # 本人请求被拒：回滚乐观拾取（恢复拾取前 run_carried + run_potions 快照）
        if not accepted and my_id is not None and player_id == my_id:
            if self._pickup_snapshot is not None:
                snap = self._pickup_snapshot
                # 兼容旧版单值快照（仅 run_carried）与新版 (carried, potions) 元组快照
                if isinstance(snap, tuple):
                    gs.run_carried, gs.run_potions = snap
                else:
                    gs.run_carried = snap
            if reason == "too_far":
                # 物品仍在主机上：重建本地视觉掉落物（带主机权威 net_id/位置）
                self._remove_drop_visual(net_id)
                drop_info = payload.get("drop") or {}
                if drop_info and not any(d.net_id == net_id for d in self.drops):
                    self.drops.append(DropItem(
                        drop_info.get("x", 0.0), drop_info.get("y", 0.0),
                        drop_info.get("item_type", "gold"), drop_info.get("item_id", "gold"),
                        quantity=drop_info.get("quantity", 1),
                        level=drop_info.get("level", 1), net_id=net_id,
                    ))
                floating_texts.add(self.player.center_x, self.player.center_y + 20,
                                  "距离太远，拾取被拒绝!", arcade.color.RED)
            else:  # already_taken：物品已被他人拿走 → 移除视觉
                self._remove_drop_visual(net_id)
                floating_texts.add(self.player.center_x, self.player.center_y + 20,
                                  "物品已被其他玩家拾取!", arcade.color.RED)
        elif accepted:
            # 成功（本人乐观拾取已移除视觉；他人成功 → 移除本地视觉，物品消失）
            self._remove_drop_visual(net_id)
        # 本人请求结果（无论成败）：扣减本批待确认计数；本批全部确认后才清空快照，
        # 保证同批多个被拒请求都能回滚到同一拾取前状态（快照不逐帧覆盖，见 on_update）
        if my_id is not None and player_id == my_id and self._pending_pickup_count > 0:
            self._pending_pickup_count -= 1
            if self._pending_pickup_count == 0:
                self._pickup_snapshot = None

    def _apply_evac_result(self, payload: dict) -> None:
        """客户端应用主机 EVAC_RESULT（本人撤离成功）：按权威清单本地入库 + 进入观战

        - 仅处理本人（player_id == 本端 net_player_id）；他人撤离结果忽略（其余玩家继续）；
        - 按主机权威清单 commit_run_to_warehouse 写本地库（与单机撤离同一入库口径），
          随后 clear_run 清空携带；
        - 修复：客户端撤离成功不再回房等待，而是进入观战模式（跟随主机幽灵继续观看）——
          房间保留、连接保留，由主机 ROOM_ENDED(all_finished) 广播统一收口回房。
        """
        gs = self.window.game_state
        player_id = payload.get("player_id")
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id != my_id:
            return  # 他人撤离：本端无动作（其余玩家继续游戏）
        # 按主机权威清单还原并入库（tuple 键还原为入参口径）
        run_carried = self._deserialize_evac_carried(payload.get("run_carried") or {})
        commit_run_to_warehouse(gs.player_id, run_carried)
        clear_run(gs.run_carried)
        # 客户端本局药水已由主机记录进 EVAC_RESULT 载荷（run_carried 含 potion 键），
        # 本地 run_potions 不再重复入库，直接清空即可
        gs.run_potions = {}
        # 等级系统：客户端撤离成功经验（各端本地结算，客户端经 EVAC_RESULT 发放）
        _award_exp(self, EXP_EVAC)
        # 客户端撤离成功 → 进入观战模式（跟随主机幽灵继续观看）而非回房等待：
        # - _spectating=True：渲染隐藏本体/武器/读条，input_handler 屏蔽操作，相机跟随观战目标；
        # - _spectate_target_id=None：由 _spectate_camera_target 自动回退第一个存活幽灵（主机）；
        # - 连接保留、poll 继续运行，主机 ROOM_ENDED(all_finished) 广播时经 _apply_room_ended 回房。
        gs.net_wait_reason = "evac"
        self._spectating = True
        # 观战期 input_handler 早退（handle_key_release/handle_mouse_release 被观战守卫拦截）
        # 导致 _left_mouse_held/_chest_key_pressed 无法复位，这里手动清零：
        # 双保险防止幽灵持续攻击/拾取（修复客户端观战崩溃，配合 on_update 的观战守卫）
        self._left_mouse_held = False
        self._chest_key_pressed = False
        # 观战模式：相机改由观战段控制跟随幽灵，禁止 controller.update() 每帧拉回
        # 已撤离/阵亡的静止玩家（否则观战视角卡死在撤离点，修复场景2）
        self.controller.follow_player = False
        self._spectate_target_id = None
        # 观战提示（与主机 _enter_spectate 分流文案一致）
        floating_texts.add(self.player.center_x, self.player.center_y + 60,
                           "你已撤离，进入观战模式（V 键切换视角）",
                           arcade.color.GOLD, life=3.0, font_size=16)

    def _remove_drop_visual(self, net_id) -> None:
        """按网络 id 从本地视觉掉落列表移除（幂等；不存在则跳过）"""
        if not net_id:
            return
        for d in self.drops[:]:
            if d.net_id == net_id:
                self.drops.remove(d)
                break

    def _apply_player_death(self, payload: dict) -> None:
        """客户端收到主机 PLAYER_DEATH（本人死亡）：清装备 + 进入观战模式

        - 其余玩家/怪物不受影响继续游戏（死亡 = 单人事件，B9）；
        - 与放弃行动同路径：进入观战模式（连接保留、等待全员结束回房），
          而非直接回 LobbyView（避免 SettingsView 阻挡视图切换）。
        """
        # 已在观战模式（放弃行动/已死亡）：不再重复处理 PLAYER_DEATH，
        # 避免"你已阵亡"红字与"你已放弃行动"红字同时出现
        if self._spectating:
            return
        gs = self.window.game_state
        player_id = payload.get("player_id")
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id != my_id:
            return  # 非本人死亡事件：忽略（幽灵同步由快照处理）
        # 死亡丢失装备（与单机死亡同口径），进入观战模式
        gs.net_wait_reason = "dead"
        self._clear_run_equipment(gs)
        self._spectating = True
        self._left_mouse_held = False
        self._chest_key_pressed = False
        self._attack_debuffs = []
        self._attack_this_frame = False
        self.controller.follow_player = False
        self._spectate_target_id = None
        floating_texts.add(self.player.center_x, self.player.center_y + 60,
                           "你已阵亡，进入观战模式（V 键切换视角）",
                           arcade.color.RED, life=3.0, font_size=16)

    def _on_monster_death(self, monster):
        """怪物死亡回调 - 委托给 entity_callbacks"""
        on_monster_death(self, monster)

    def _handle_harvestable_combat(self, dt):
        """处理玩家对环境物的攻击 - 委托给 entity_callbacks"""
        handle_harvestable_combat(self, dt)

    def _client_visual_collisions(self):
        """客户端本地弹丸的纯视觉碰撞（伤害判定收敛主机，这里只做表现层反馈）

        - 弹丸 vs 环境物：命中即消失（穿透弹丸保留）+ 粒子 + 伤害数字 + 本地血条 clamp 扣减，
          实际摧毁/掉落由主机 env_destroyed/drop_spawn 广播驱动（_apply_map_change 置 hp=0）；
        - 弹丸 vs 远端怪物：普通弹丸命中即消失 + 粒子 + 伤害数字（血量由主机 DAMAGE_RESULT
          驱动，这里不调用 take_damage），穿透弹丸保留——仅激光枪可穿透怪物；
        - 只做视觉表现，不本地裁决伤害（主机权威：客户端仅按主机广播同步血量）。
        """
        gs = self.window.game_state
        if gs.net_mode != "client":
            return
        # 弹丸 vs 环境物（本地血条反馈 + 普通弹丸消失）
        for proj in list(self.combat.projectiles):
            if getattr(proj, "expired", False):
                continue
            for h in self.harvestables:
                if h.alive and arcade.check_for_collision(proj, h):
                    dmg = proj.damage
                    # 本地血条视觉反馈：clamp 最低 1，避免本地提前"死亡"与主机状态脱节
                    h.hp = max(1, h.hp - dmg)
                    floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                    particle_system.emit(h.center_x, h.center_y, 5, (150, 150, 150), speed=60, life=0.3, size=3)
                    # 普通弹丸命中环境物即消失；穿透弹丸保留继续飞行
                    if getattr(proj, "special", "") != "penetrating":
                        proj.remove_from_sprite_lists()
                    break
        # 弹丸 vs 远端怪物（纯视觉：普通弹丸命中即消失，穿透弹丸保留）
        for proj in list(self.combat.projectiles):
            if getattr(proj, "expired", False):
                continue
            for m in self.remote_monsters.values():
                if not getattr(m, "alive", True):
                    continue
                if arcade.check_for_collision(proj, m):
                    # 仅普通弹丸消失 + 反馈；穿透弹丸保留（激光枪可穿透怪物）
                    if getattr(proj, "special", "") != "penetrating":
                        proj.remove_from_sprite_lists()
                        sound_manager.play_monster_hit()
                        # 不在此显示本地伤害数字：怪物血量/伤害由主机 DAMAGE_RESULT 广播驱动，
                        # 本地 proj.damage 可能为升级武器实际值（72），与主机裁决值叠加会造成假伤害数字
                    break

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

    def _send_client_interaction_request(self, gs) -> None:
        """客户端发送交互请求给主机（宝箱/水井/火箭发射台）

        按E时检测附近可交互物，发送 INTERACTION_REQUEST 给主机裁决。
        主机处理后通过 MAP_CHANGE 广播结果，客户端镜像状态。
        """
        if not self._chest_key_pressed or self._spectating:
            return
        if gs.net_client is None:
            print("[Client] net_client is None, skip interaction")
            return

        player_x = self.player.center_x
        player_y = self.player.center_y
        interaction_type = None

        print(f"[Client] 检测交互: chests={len(self.chests)}, well={self.map_data.get('water_well')}, pads={len(self.rocket_pads)}")

        # 检测宝箱
        for i, chest in enumerate(self.chests):
            if chest.opened:
                continue
            dist = math.hypot(chest.center_x - player_x, chest.center_y - player_y)
            print(f"[Client] 宝箱 {i}: pos=({chest.center_x},{chest.center_y}), opened={chest.opened}, dist={dist:.1f}")
            if dist < 40:
                interaction_type = "chest"
                break

        # 检测水井
        if interaction_type is None:
            well = self.map_data.get("water_well")
            if well:
                dist = math.hypot(well[0] - player_x, well[1] - player_y)
                print(f"[Client] 水井: pos=({well[0]},{well[1]}), dist={dist:.1f}")
                if dist < 40:
                    interaction_type = "well"

        # 检测火箭发射台
        if interaction_type is None:
            for i, pad in enumerate(self.rocket_pads):
                dist = math.hypot(pad.center_x - player_x, pad.center_y - player_y)
                print(f"[Client] 火箭台 {i}: pos=({pad.center_x},{pad.center_y}), state={pad.state}, dist={dist:.1f}")
                if dist < 60 and pad.state in ("idle", "boss_defeated"):
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
            self._chest_key_pressed = False  # 消费按键
            print(f"[Client] 发送交互请求: {interaction_type}")

    def _handle_interaction_request(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端交互请求：验证距离 → 执行交互 → 广播结果

        安全校验：验证请求者位置与交互物距离，防止作弊。
        """
        if self.window.game_state.net_mode != "host":
            return

        player_id = payload.get("player_id", sender_id)
        interaction_type = payload.get("interaction_type", "")
        request_x = float(payload.get("x", 0))
        request_y = float(payload.get("y", 0))

        print(f"[Host] 收到交互请求: player={player_id}, type={interaction_type}, pos=({request_x},{request_y})")

        # 获取请求者幽灵（用于距离校验）
        ghost = self.remote_players.get(player_id)
        if ghost is None:
            print(f"[Host] 幽灵不存在: player={player_id}")
            return  # 幽灵不存在，忽略

        # 用客户端上报的位置校验距离（与 ATTACK_EVENT 同口径）
        ghost.center_x = request_x
        ghost.center_y = request_y

        if interaction_type == "chest":
            # 找到最近的未开启宝箱
            for i, chest in enumerate(self.chests):
                if chest.opened:
                    continue
                dist = math.hypot(chest.center_x - request_x, chest.center_y - request_y)
                if dist < 40:
                    # 执行开箱
                    from game.entity_callbacks import spawn_chest_loot, _award_exp
                    from config import EXP_CHEST
                    loot = chest.open_chest()
                    spawn_chest_loot(self, chest, loot)
                    _award_exp(self, EXP_CHEST)
                    print(f"[GameView] 客户端 {player_id} 开启宝箱 {i}")
                    break

        elif interaction_type == "well":
            well = self.map_data.get("water_well")
            if well:
                dist = math.hypot(well[0] - request_x, well[1] - request_y)
                if dist < 40:
                    # 执行水井交互
                    from game.entity_callbacks import handle_well_interaction
                    # 临时设置 _chest_key_pressed 和 player 为幽灵
                    old_player = self.player
                    old_key = self._chest_key_pressed
                    self.player = ghost
                    self._chest_key_pressed = True
                    # 修复：记录治疗前 HP，治疗后广播 POTION_ACK 给客户端
                    hp_before = ghost.hp
                    handle_well_interaction(self)
                    heal_amount = max(0, ghost.hp - hp_before)
                    self.player = old_player
                    self._chest_key_pressed = old_key
                    # 向客户端单播水井回血结果，客户端收到后更新本地玩家 HP
                    if heal_amount > 0:
                        gs.net_server.send_to(sender_id, MsgType.POTION_ACK, {
                            "player_id": sender_id,
                            "potion_id": "well",
                            "accepted": True,
                            "heal_amount": heal_amount,
                            "effect": "heal", "value": heal_amount, "duration": 0,
                        })
                    print(f"[GameView] 客户端 {player_id} 使用水井，回血 {heal_amount}")

        elif interaction_type == "rocket_pad":
            for i, pad in enumerate(self.rocket_pads):
                dist = math.hypot(pad.center_x - request_x, pad.center_y - request_y)
                if dist < 60:
                    if pad.state == "idle":
                        # 激活火箭台
                        from game.entity_callbacks import handle_rocket_pad_interaction
                        old_player = self.player
                        old_key = self._chest_key_pressed
                        self.player = ghost
                        self._chest_key_pressed = True
                        handle_rocket_pad_interaction(self)
                        self.player = old_player
                        self._chest_key_pressed = old_key
                        print(f"[GameView] 客户端 {player_id} 激活火箭台 {i}")
                    elif pad.state == "boss_defeated":
                        # 标记可选择状态（7/8键选择）
                        self._rocket_pad_menu = pad
                        print(f"[GameView] 客户端 {player_id} 打开火箭台菜单 {i}")
                    break

    def _handle_player_abandon(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端放弃行动通知：更新 _player_status → 触发全员结束判定

        客户端调用 _fail_run 后发送此消息，主机更新状态后 _check_all_finished
        可正确判定全员结束，广播 ROOM_ENDED 回房。
        """
        gs = self.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return

        player_id = payload.get("player_id", sender_id)
        reason = payload.get("reason", "放弃行动")

        # 更新玩家状态为 dead（与死亡同待遇，触发全员结束判定）
        if self._player_status.get(player_id) not in ("evac", "dead", "left"):
            self._player_status[player_id] = "dead"
            print(f"[GameView] 客户端 {player_id} 放弃行动: {reason}，已标记 dead")

        # 标记幽灵为不存活（设置 hp=0，alive 是只读 property：hp > 0）
        ghost = self.remote_players.get(player_id)
        if ghost is not None:
            ghost.hp = 0

    def _scatter_drops(self, drops, center_x, center_y, radius=30):
        """分散掉落物 - 委托给 entity_callbacks（带障碍物避让，防掉落物卡墙）"""
        scatter_drops(drops, center_x, center_y, radius, obstacles=self.obstacle_list)

    def _sync_obstacles(self):
        """同步障碍物 - 委托给 entity_callbacks"""
        sync_obstacles(self)

    def _create_boss_door_block(self):
        """在 BOSS 房间门洞处生成临时墙壁精灵，阻止玩家离开"""
        if self._boss_door_sprite is not None:
            return  # 已存在
        boss_door = self.map_data.get("boss_door")
        if not boss_door:
            return
        from config import TILE_SIZE
        # 门洞尺寸：128px 宽 × TILE_SIZE 厚
        door_w = boss_door["width"]
        door_h = TILE_SIZE
        side = boss_door["side"]
        # 门洞精灵（与墙壁同色）
        theme = self.map_data.get("theme", "forest")
        if theme == "desert":
            wall_color = (160, 130, 70)
        elif theme == "space":
            wall_color = (60, 60, 70)
        else:
            wall_color = (60, 70, 55)
        sprite = arcade.SpriteSolidColor(door_w, door_h, color=wall_color)
        sprite.center_x = boss_door["x"]
        sprite.center_y = boss_door["y"]
        self._boss_door_sprite = sprite
        self.wall_list.append(sprite)
        self.obstacle_list.append(sprite)

    def _remove_boss_door_block(self):
        """移除 BOSS 房间门洞处的临时墙壁精灵"""
        if self._boss_door_sprite is None:
            return
        sprite = self._boss_door_sprite
        if sprite in self.wall_list:
            self.wall_list.remove(sprite)
        if sprite in self.obstacle_list:
            self.obstacle_list.remove(sprite)
        self._boss_door_sprite = None

    def _respawn_harvestables(self, dt):
        """资源刷新 - 委托给 respawn"""
        respawn_harvestables(self, dt)

    def _respawn_monsters(self, dt):
        """怪物刷新 - 委托给 respawn"""
        respawn_monsters(self, dt)

    def _serialize_monsters(self) -> list:
        """序列化全部存活怪物为 MONSTER_SNAPSHOT 的 monsters 列表（主机权威）

        - 已死亡怪物（alive=False）跳过：客户端以「快照缺失即删除」感知怪物被击杀/移除；
        - 运行期补刷的新怪物在此惰性分配 net_id（存储在 m.net_id 上，存活期不变），
          与 setup() 中初始怪物的分配共用同一个单调计数器，保证全房唯一；
        - 载荷键名严格遵循 net/protocol.py MESSAGE_SCHEMAS["MONSTER_SNAPSHOT"]：
          net_id / monster_type / x / y / hp / max_hp / weapon / debuff / attack_anim。
        """
        snapshot = []
        for m in self.monsters:
            # 跳过已死亡怪物（不广播，客户端据此删除远端怪物）
            if hasattr(m, "alive") and not m.alive:
                continue
            net_id = getattr(m, "net_id", None)
            if net_id is None:
                # 补刷怪物首次序列化时分配 net_id（单调递增，不回收）
                net_id = self._next_monster_net_id
                self._next_monster_net_id += 1
                m.net_id = net_id
                self.net_id_to_monster[net_id] = m
            # 当前生效中的首个 debuff id（无则 None）；debuffs 元素为 dict（含 id 键）
            debuff_id = None
            if getattr(m, "debuffs", None):
                debuff_id = m.debuffs[0].get("id") if isinstance(m.debuffs[0], dict) else None
            # 装备快照字段：客户端渲染远端怪物护甲/头盔/武器颜色与等级（修复装备不同步）
            weapon = m.weapon if isinstance(getattr(m, "weapon", None), dict) else {}
            armor = m.armor if isinstance(getattr(m, "armor", None), dict) else {}
            helmet = m.helmet if isinstance(getattr(m, "helmet", None), dict) else {}
            snapshot.append({
                "net_id": net_id,
                "monster_type": type(m).__name__,  # 类名即 game.monsters 模块属性名，客户端据此实例化
                "x": m.center_x,
                "y": m.center_y,
                "hp": m.hp,
                "max_hp": m.max_hp,
                "weapon": weapon.get("name"),
                "weapon_color": list(weapon.get("color", (255, 255, 255))[:3]) if weapon else None,
                "weapon_level": weapon.get("level"),
                "armor": armor.get("name"),
                "armor_color": list(armor.get("color", (200, 200, 200))[:3]) if armor else None,
                "helmet": helmet.get("name"),
                "helmet_color": list(helmet.get("color", (200, 200, 200))[:3]) if helmet else None,
                "debuff": debuff_id,
                "attack_anim": getattr(m, "_attack_timer", 0.0),
            })
        return snapshot

    def _serialize_projectiles(self) -> dict:
        """序列化全部存活弹丸与激光为 PROJECTILE_SNAPSHOT（主机权威）

        - 怪物弹丸（skeleton_projectiles）与玩家弹丸（combat.projectiles）统一进
          projectiles 列表：修复「主机子弹客户端不可见」——之前只序列化怪物弹丸，
          玩家弹丸（含客户端上报远程攻击后主机生成的权威弹丸）从不进快照，
          客户端永远看不到主机发射的子弹；
        - 激光（combat.lasers，陨星炮）：进 lasers 列表，客户端按快照渲染远端激光
          （起点/角度/伤害/长度/宽度/剩余时长），修复「主机激光客户端不可见」；
        - 弹丸/激光首次序列化时惰性分配单调 proj_id（存储在对象上，存活期不变），
          与 setup() 初始弹丸共用同一个单调计数器，保证全房唯一；消亡后 id 不回收；
        - 载荷键名严格遵循 net/protocol.py MESSAGE_SCHEMAS["PROJECTILE_SNAPSHOT"]：
          proj_id / owner_id / x / y / vx / vy / damage / debuff（弹丸）；
          proj_id / owner_id / x / y / angle / damage / length / width / duration（激光）。
        """
        snapshot = []
        # 怪物弹丸（骷髅等远程怪物的权威弹丸）
        for p in self.skeleton_projectiles:
            proj_id = getattr(p, "proj_id", None)
            if proj_id is None:
                # 新弹丸首次序列化时分配 proj_id（单调递增，不回收）
                proj_id = self._next_proj_id
                self._next_proj_id += 1
                p.proj_id = proj_id
            snapshot.append({
                "proj_id": proj_id,
                "owner_id": getattr(p, "owner_net_id", 0),
                "x": p.center_x,
                "y": p.center_y,
                "vx": p.change_x,
                "vy": p.change_y,
                "damage": p.damage,
                "debuff": getattr(p, "debuff_id", None),
                "color": list(getattr(p, "color", (255, 100, 50))[:3]),
            })
        # 玩家弹丸（修复主机子弹客户端不可见：主机本地攻击与客户端上报攻击生成的
        # 权威弹丸都进快照；客户端按 owner_id==自己 跳过本地表现弹丸，其余照常渲染）
        for p in self.combat.projectiles:
            proj_id = getattr(p, "proj_id", None)
            if proj_id is None:
                proj_id = self._next_proj_id
                self._next_proj_id += 1
                p.proj_id = proj_id
            snapshot.append({
                "proj_id": proj_id,
                "owner_id": getattr(p, "owner_net_id", 0),
                "x": p.center_x,
                "y": p.center_y,
                "vx": p.change_x,
                "vy": p.change_y,
                "damage": p.damage,
                "debuff": getattr(p, "debuff_id", None),
                "color": list(getattr(p, "color", (255, 100, 50))[:3]),
            })
        # 激光（陨星炮）：起点=发射者当前位置、角度实时跟随，客户端按快照重绘
        lasers = []
        for b in self.combat.lasers:
            proj_id = getattr(b, "proj_id", None)
            if proj_id is None:
                proj_id = self._next_proj_id
                self._next_proj_id += 1
                b.proj_id = proj_id
            sx, sy = b.start_point
            lasers.append({
                "proj_id": proj_id,
                "owner_id": getattr(b, "owner_net_id", 0),
                "x": sx,
                "y": sy,
                "angle": b.angle,
                "damage": b.damage,
                "length": b.length,
                "width": b.width,
                "duration": max(0.0, b.duration),
            })
        return {"projectiles": snapshot, "lasers": lasers}

    def _apply_monster_snapshot(self, payload) -> None:
        """应用主机下发的 MONSTER_SNAPSHOT：按 net_id 增/改/删维护 self.remote_monsters

        - 已有 net_id → 原位更新（位置/HP/武器名/debuff/攻击动画）；
        - 新 net_id → 新建远端怪物对象：复用真实怪物类实例（仅作渲染数据容器，
          永不调用 update/try_attack，AI/碰撞由主机权威执行），不加入 self.monsters；
        - 本端存在但快照缺失的 net_id → 主机已击杀/移除，删除。
        插值渲染留待后续任务（B3），当前直接应用快照值。
        """
        monster_list = payload.get("monsters", []) if isinstance(payload, dict) else []
        snapshot_ids = set()
        for entry in monster_list:
            net_id = entry.get("net_id")
            if net_id is None:
                continue  # 非法条目：无 net_id 无法同步，跳过
            snapshot_ids.add(net_id)
            rm = self.remote_monsters.get(net_id)
            if rm is None:
                # 新建：按 monster_type（类名）实例化真实怪物类，未知类型回退 Zombie
                cls = getattr(monsters, entry.get("monster_type", ""), Zombie)
                rm = cls(center_x=entry.get("x", 0.0), center_y=entry.get("y", 0.0))
                rm.net_id = net_id
                self.remote_monsters[net_id] = rm
            # 原位更新核心表现数据（直接应用，不插值）
            rm.center_x = entry.get("x", rm.center_x)
            rm.center_y = entry.get("y", rm.center_y)
            rm.hp = entry.get("hp", rm.hp)
            rm.max_hp = entry.get("max_hp", rm.max_hp)
            # 武器名/debuff/攻击动画：以 net_* 前缀保存快照值供渲染任务使用，
            # 不覆盖怪物自身的 weapon dict（那是主机掉落逻辑用的结构）
            rm.net_weapon = entry.get("weapon")
            rm.net_debuff = entry.get("debuff")
            rm.net_attack_anim = entry.get("attack_anim", 0.0)
            # 装备颜色/等级快照（修复远端怪物装备与主机不一致）：
            # 客户端渲染护甲/头盔/武器颜色时优先用这些 net_* 值
            rm.net_weapon_color = entry.get("weapon_color")
            rm.net_weapon_level = entry.get("weapon_level")
            rm.net_armor = entry.get("armor")
            rm.net_armor_color = entry.get("armor_color")
            rm.net_helmet = entry.get("helmet")
            rm.net_helmet_color = entry.get("helmet_color")
        # 删除本端存在但快照缺失的怪物（主机已击杀/移除）
        for net_id in list(self.remote_monsters):
            if net_id not in snapshot_ids:
                del self.remote_monsters[net_id]

    def _apply_projectile_snapshot(self, payload) -> None:
        """应用主机下发的 PROJECTILE_SNAPSHOT：按 proj_id 增/改/删维护 remote_projectiles/remote_lasers

        - 新 proj_id → 新建轻量表现弹丸（arcade.SpriteSolidColor，标准弹丸尺寸），
          加入渲染列表并登记 _remote_proj_map 供后续 O(1) 查找；
        - 已有 proj_id → 原位更新位置（主机权威，纯视觉跟随，不做客户端物理/插值）；
        - 本端存在但快照缺失的 proj_id → 弹丸已在主机消亡（超时/碰墙/命中玩家），
          删除即命中消失。vx/vy/damage/debuff 以 net_* 前缀保存在弹丸对象上供后续任务扩展。
        - owner_id == 本端玩家 id 的弹丸跳过：那是本端自己发射的纯表现弹丸（本地已渲染），
          重复渲染会造成双重弹丸（修复主机子弹同步后引入的双重渲染问题）；
        - lasers 列表：维护远端激光表现对象（owner_id==自己 的激光同样跳过，本地已有表现）。
        """
        gs = self.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        proj_list = payload.get("projectiles", []) if isinstance(payload, dict) else []
        snapshot_ids = set()
        for entry in proj_list:
            proj_id = entry.get("proj_id")
            if proj_id is None:
                continue  # 非法条目：无 proj_id 无法同步，跳过
            # 自己发射的弹丸：本地已有纯表现弹丸（input_handler 本地生成），跳过避免双重渲染
            if my_id is not None and entry.get("owner_id") == my_id:
                continue
            snapshot_ids.add(proj_id)
            rp = self._remote_proj_map.get(proj_id)
            if rp is None:
                # 新建轻量表现弹丸：颜色取快照 color（修复主机弹丸颜色与本地不一致），
                # 缺省回退怪物弹丸默认橙红；尺寸取配置 PROJECTILE_SIZE
                snap_color = entry.get("color") or [255, 100, 50]
                rp = arcade.SpriteSolidColor(PROJECTILE_SIZE, PROJECTILE_SIZE,
                                             color=(int(snap_color[0]), int(snap_color[1]), int(snap_color[2])))
                rp.proj_id = proj_id
                self.remote_projectiles.append(rp)
                self._remote_proj_map[proj_id] = rp
            # 原位更新位置（主机权威），速度/伤害/debuff 快照值以 net_* 前缀保存
            rp.center_x = entry.get("x", rp.center_x)
            rp.center_y = entry.get("y", rp.center_y)
            rp.net_vx = entry.get("vx", 0.0)
            rp.net_vy = entry.get("vy", 0.0)
            rp.net_damage = entry.get("damage", 0.0)
            rp.net_debuff = entry.get("debuff")
        # 删除本端存在但快照缺失的弹丸（主机已消亡：超时/碰墙/命中玩家 = 命中消失）
        for proj_id in list(self._remote_proj_map):
            if proj_id not in snapshot_ids:
                rp = self._remote_proj_map.pop(proj_id)
                rp.remove_from_sprite_lists()
        # 远端激光同步（陨星炮）：按 proj_id 增/改/删维护 remote_lasers（纯表现层）
        laser_list = payload.get("lasers", []) if isinstance(payload, dict) else []
        laser_ids = set()
        for entry in laser_list:
            laser_id = entry.get("proj_id")
            if laser_id is None:
                continue
            # 自己发射的激光：本地已有纯表现激光（input_handler 本地 spawn_laser），跳过
            if my_id is not None and entry.get("owner_id") == my_id:
                continue
            laser_ids.add(laser_id)
            beam = self.remote_lasers.get(laser_id)
            if beam is None:
                beam = _RemoteLaser(laser_id)
                self.remote_lasers[laser_id] = beam
            # 原位更新（主机权威位置/角度/剩余时长，20Hz 快照足以平滑跟随）
            beam.x = entry.get("x", beam.x)
            beam.y = entry.get("y", beam.y)
            beam.angle = entry.get("angle", beam.angle)
            beam.length = entry.get("length", beam.length)
            beam.width = entry.get("width", beam.width)
            beam.duration = entry.get("duration", beam.duration)
        # 删除快照缺失的激光（主机激光已消失：duration 耗尽）
        for laser_id in list(self.remote_lasers):
            if laser_id not in laser_ids:
                del self.remote_lasers[laser_id]

    def _serialize_full_state(self) -> dict:
        """主机序列化全量世界状态（FULL_STATE）：发给晚期加入客户端的初始化数据

        - monsters 复用 _serialize_monsters 格式（存活怪物 id/位置/hp/装备/debuff）；
        - drops/chests/env_objects 序列化当前地面掉落、宝箱开启状态与环境物存活状态；
        - action_time_left 同步剩余行动时间，客户端据此初始化后只收增量快照（B13）。
        """
        gs = self.window.game_state
        # 地面掉落物（与 drop_spawn 广播格式一致：net_id/item_type/item_id/x/y/quantity/level）
        drops = []
        for d in self.drops:
            # 兜底分配网络 id（正常情况生命周期段已分配；此处防御晚期加入时遗漏）
            if getattr(d, "net_id", None) is None:
                d.net_id = f"drop_{self._next_drop_net_id}"
                self._next_drop_net_id += 1
            drops.append({
                "net_id": d.net_id,
                "item_type": getattr(d, "item_type", ""),
                "item_id": getattr(d, "item_id", ""),
                "x": d.center_x, "y": d.center_y,
                "quantity": getattr(d, "quantity", 1),
                "level": getattr(d, "level", 1),
            })
        # 宝箱：按列表序号作为稳定 id（运行期改动同步是 todo 20）
        chests = [{"id": i, "opened": c.opened,
                   "x": c.center_x, "y": c.center_y}
                  for i, c in enumerate(self.chests)]
        # 环境物（可采集物/水井等）：存活状态 + 资源类型
        env_objects = [{"id": i, "resource_type": getattr(h, "resource_type", ""),
                        "alive": getattr(h, "alive", True),
                        "x": h.center_x, "y": h.center_y}
                       for i, h in enumerate(self.harvestables)]
        return {
            "monsters": self._serialize_monsters(),
            "drops": drops,
            "chests": chests,
            "env_objects": env_objects,
            # 水井首次开启状态（仅沙漠主题）：晚期加入客户端据此镜像 _well_opened
            "well_opened": self._well_opened,
            "action_time_left": self._action_time_remaining or 0.0,
        }

    def _apply_full_state(self, payload) -> None:
        """客户端应用 FULL_STATE：晚期加入时初始化远端世界，之后只收增量快照

        - monsters：复用 _apply_monster_snapshot 的增/改/删逻辑初始化 remote_monsters；
        - 行动时间：以主机权威值校准本地倒计时（HUD 同步）；
        - drops：转为视觉 DropItem 加入 self.drops（Todo 18，晚期加入客户端即可见可拾取，
          与主机 drop_spawn 广播共用 net_id 口径）；
        - chests/env_objects（todo 20 收口）：按 id（列表序号，与绘制本地对象一致的口径）
          把已开宝箱 / 已摧毁环境物状态应用到本地坐标对象，渲染与碰撞据此跳过；
        - well_opened（todo 20 收口）：晚期加入客户端镜像主机水井首次开启状态，
          与主机交互/提示口径一致（此前只有运行期 well_used 增量广播，晚期加入者会漏）。
        """
        if not isinstance(payload, dict):
            return
        self._apply_monster_snapshot({"monsters": payload.get("monsters", [])})
        t = payload.get("action_time_left")
        if t is not None:
            self._action_time_remaining = t
            self.window.game_state.action_time_remaining = t
        # 水井状态：主机已首次开启 → 本地镜像（渲染/提示与主机一致）
        if payload.get("well_opened"):
            self._well_opened = True
        # FULL_STATE 掉落物转为视觉 DropItem（带主机权威 net_id；已存在则跳过）
        for entry in payload.get("drops", []) or []:
            net_id = entry.get("net_id")
            if not net_id or any(d.net_id == net_id for d in self.drops):
                continue
            self.drops.append(DropItem(
                entry.get("x", 0.0), entry.get("y", 0.0),
                entry.get("item_type", "gold"), entry.get("item_id", "gold"),
                quantity=entry.get("quantity", 1),
                level=entry.get("level", 1), net_id=net_id,
            ))
        # 宝箱状态：已开宝箱标记 opened（渲染不绘制、不可再开）
        for entry in payload.get("chests", []) or []:
            cid = entry.get("id")
            if isinstance(cid, int) and 0 <= cid < len(self.chests) and entry.get("opened"):
                self.chests[cid].opened = True
        # 环境物状态：已摧毁环境物置 hp=0（alive 是只读 property）+ 移出障碍物（渲染跳过、无碰撞）
        for entry in payload.get("env_objects", []) or []:
            eid = entry.get("id")
            if isinstance(eid, int) and 0 <= eid < len(self.harvestables) \
                    and not entry.get("alive", True):
                h = self.harvestables[eid]
                h.hp = 0
                if h in self.obstacle_list:
                    self.obstacle_list.remove(h)
        # 保留原始列表供调试/扩展使用（后续渲染直接读本地坐标对象状态）
        self._late_chests = payload.get("chests", [])
        self._late_env = payload.get("env_objects", [])

    def on_show_view(self):
        self.window.background_color = (20, 25, 20)
        # 修复：按 TAB 切走背包/升级面板时，按住的方向键释放事件发给了新视图，
        # 本视图收不到导致 _pressed 残留；返回时清空按键状态，避免角色"卡住一直走"
        if self.controller:
            self.controller.clear_keys()

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
        # 新手教程（阶段 4）：游戏内引导横幅（HUD 逻辑坐标系下绘制）
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active and tut.stage == 3:
            from views.tutorial import draw_in_game_tutorial
            self.window.default_camera.use()
            draw_in_game_tutorial(self, self._tut_tc)
        # BOSS 介绍向导弹窗（进入 BOSS 房间时弹出，4 页说明血条/锁定/技能机制）
        if self._boss_intro_active and self._boss_intro_pages:
            from views.tutorial import draw_tutorial_page
            self.window.default_camera.use()
            page = self._boss_intro_pages[min(self._boss_intro_idx, len(self._boss_intro_pages) - 1)]
            total = len(self._boss_intro_pages)
            self._boss_intro_next_rect, _ = draw_tutorial_page(
                self, page, self._boss_intro_idx, total,
                self._tut_tc, self._boss_intro_next_hover)

    def _apply_free_equip(self, d):
        """无背包拾取空槽位武器/装备/背包时，立即在局内生效（穿戴 / 加防御 / 获得容量）

        由 game/loot.py 的 try_pickup 在免费装备成功时回调触发；
        用于同步 GameState 与玩家实体的即时状态，避免仅入包而不生效。
        """
        gs = self.window.game_state
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
            self._cached_weapon_id = d.item_id
            self._cached_weapon_name = wdef.get("name", "武器")
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
            self.player.defense += edef.get("defense", 0)
            # 修复：生成装备被动效果并应用到玩家（与 setup() 一致）
            # 地面掉落无 effects 属性，需按等级动态生成；被动效果含 max_hp/regen/speed/damage/lifesteal/thorns/crit_chance
            effects = roll_effects_for_slot(getattr(d, "level", 1) or 1, d.item_type)
            for e in effects:
                eid, elvl = parse_effect_item(e)
                edata = _EFFECTS_DEFS.get(eid, {})
                if edata.get("type") == "passive":
                    pdata = effect_params(eid, elvl)
                    if eid == "max_hp":
                        self.player.max_hp += int(pdata.get("value", 25))
                        self.player.hp += int(pdata.get("value", 25))
                    elif eid == "regen":
                        self.player.regen_per_sec += pdata.get("value", 1)
                    elif eid == "speed":
                        self.player.gear_speed_mult *= (1.0 + pdata.get("value", 0.30))
                    elif eid == "defense":
                        self.player.defense += int(pdata.get("value", 3))
                    elif eid == "damage":
                        current = getattr(self.player, "equip_damage_mult", 1.0)
                        self.player.equip_damage_mult = current * (1.0 + pdata.get("value", 0.08))
                    elif eid == "lifesteal":
                        current = getattr(self.player, "equip_lifesteal", 0.0)
                        self.player.equip_lifesteal = current + pdata.get("value", 0.03)
                    elif eid == "thorns":
                        current = getattr(self.player, "equip_thorns", 0.0)
                        self.player.equip_thorns = current + pdata.get("value", 0.10)
                    elif eid == "crit_chance":
                        current = getattr(self.player, "crit_chance", 0.0)
                        self.player.crit_chance = current + pdata.get("value", 0.05)
            # 同步更新 HUD 缓存，使左侧装备栏即时显示新拾取的装备（含 effects 列表）
            if self._cached_equip is None:
                self._cached_equip = {}
            self._cached_equip[d.item_type] = {
                "id": None, "item_id": d.item_id,
                "name": edef.get("name", d.item_type),
                "defense": edef.get("defense", 0),
                "capacity": 0, "level": getattr(d, "level", 1) or 1, "effects": effects,
            }
        elif d.item_type == "backpack":
            from entities.equipment_defs import BACKPACKS
            bdef = BACKPACKS.get(d.item_id, {})
            # 获得容器即时生效：容量按背包定义设置，并同步到 GameState 供其他 View 使用
            self.player.backpack_capacity = bdef.get("capacity", 0)
            gs.backpack_capacity = self.player.backpack_capacity
            # 记录当前装备的背包 item_id，供背包视图装备栏显示/丢弃
            gs.equipped_backpack_id = d.item_id
            # 同步更新 HUD 缓存，使左侧装备栏即时显示新拾取的背包
            if self._cached_equip is None:
                self._cached_equip = {}
            self._cached_equip["backpack"] = {
                "id": None, "item_id": d.item_id,
                "name": bdef.get("name", "背包"),
                "defense": 0,
                "capacity": bdef.get("capacity", 0),
                "level": 1, "effects": [],
            }

    def _fold_run_potions(self, gs) -> None:
        """撤离前将本局药水槽（run_potions）并入 run_carried["potion"] 以便入库

        单机/主机本局拾取的药水存于 run_potions（不占容量、上限 RUN_POTION_SLOTS），
        撤离时并入 run_carried 的 potion 键走统一入库口径（commit_run_to_warehouse）。
        （联机客户端不走本函数：客户端药水拾取已被主机记录进 EVAC_RESULT 载荷，
        本地 run_potions 直接清空即可，避免重复入库。）
        """
        run_potions = getattr(gs, "run_potions", None)
        if not run_potions:
            return
        from entities.equipment_defs import POTIONS
        potion_slot = gs.run_carried.setdefault("potion", {})
        for item_id, qty in run_potions.items():
            if item_id in POTIONS and qty > 0:
                potion_slot[item_id] = potion_slot.get(item_id, 0) + qty
        run_potions.clear()

    def _add_equipped_to_carried(self, gs):
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

    def on_update(self, delta_time):
        self._frame += 1

        gs = self.window.game_state
        dt = min(delta_time, 0.05)

        # ── 联机网络同步（仅对应模式且已注入网络对象时执行；solo 模式零网络开销）──
        # 主机：怪物快照定时广播（NET_SNAPSHOT_HZ=20Hz 全量），客户端据此维护远端怪物。
        if gs.net_mode == "host" and gs.net_server is not None:
            self._snapshot_timer += dt
            if self._snapshot_timer >= 1.0 / NET_SNAPSHOT_HZ:
                self._snapshot_timer = 0.0
                # broadcast 线程安全，可从主线程直接调用；载荷键名见协议 MESSAGE_SCHEMAS
                gs.net_server.broadcast(MsgType.MONSTER_SNAPSHOT, {"monsters": self._serialize_monsters()})
                # 怪物弹丸快照同节拍广播：与怪物同一 20Hz 全量；弹丸消亡自动从列表消失，
                # 客户端收到缺失 proj_id 即删除（＝命中消失/碰墙/超时），无需额外事件消息。
                # 载荷含 projectiles（怪物+玩家弹丸）与 lasers（陨星炮激光）两个列表
                gs.net_server.broadcast(MsgType.PROJECTILE_SNAPSHOT, self._serialize_projectiles())
                # 玩家快照同节拍广播（Todo 15，HP 主机权威）：客户端校准本地/幽灵 HP 防漂移；
                # 完整玩家实体同步（位置插值渲染）是 todo 23，此处仅同步 HP 字段
                gs.net_server.broadcast(MsgType.PLAYER_SNAPSHOT, {"players": self._serialize_players()})
            # 行动时间周期广播（NET_ACTION_TIME_BCAST_SEC=1.0，独立于 20Hz 快照节拍）：
            # 倒计时只在主机递减，客户端显示以广播值为准——此前从未广播导致客户端
            # HUD 卡死在开局值（4:59/7:59），收到本消息的客户端据此更新剩余时间。
            self._action_time_bcast_timer += dt
            if self._action_time_bcast_timer >= NET_ACTION_TIME_BCAST_SEC:
                self._action_time_bcast_timer = 0.0
                gs.net_server.broadcast(MsgType.ACTION_TIME, {
                    "action_time_left": self._action_time_remaining or 0.0,
                })

        # 客户端：每帧排空入站消息，按 MONSTER_SNAPSHOT 增/改/删维护 remote_monsters；
        # DAMAGE_RESULT 由主机伤害判定广播（Todo 14），客户端按 net_id 应用扣血显示。
        if gs.net_mode == "client" and gs.net_client is not None:
            for msg_type, payload in gs.net_client.poll():
                if msg_type == MsgType.MONSTER_SNAPSHOT:
                    self._apply_monster_snapshot(payload)
                elif msg_type == MsgType.PROJECTILE_SNAPSHOT:
                    # 怪物弹丸快照：增/改/删维护 remote_projectiles（纯表现层渲染，主机权威位置）
                    self._apply_projectile_snapshot(payload)
                elif msg_type == MsgType.DAMAGE_RESULT:
                    # 主机伤害判定结果：远端怪物扣血 + 漂浮伤害文字 + 受击闪白
                    self._apply_damage_result(payload)
                elif msg_type == MsgType.PLAYER_HURT:
                    # 主机权威玩家受伤事件：本地扣血 + 受击反馈 / 远端幽灵同步 HP
                    self._apply_player_hurt(payload)
                elif msg_type == MsgType.PLAYER_SNAPSHOT:
                    # 主机玩家快照：校准本地与幽灵 HP（防漂移，权威值）
                    self._apply_player_snapshot(payload)
                elif msg_type == MsgType.POTION_ACK:
                    # 主机药水确认：本地生效（治疗数字）+ 全端可见
                    self._apply_potion_ack(payload)
                elif msg_type == MsgType.PLAYER_DEATH:
                    # 主机判定本人死亡：走单机失败结算 + 断开离开房间（其余玩家继续）
                    self._apply_player_death(payload)
                elif msg_type == MsgType.FULL_STATE:
                    # 晚期加入全量状态：初始化远端世界（怪物/掉落/宝箱/环境物/倒计时）
                    self._apply_full_state(payload)
                elif msg_type == MsgType.ACTION_TIME:
                    # 主机行动时间周期广播：客户端显示以广播值为准（本地不递减），
                    # 更新 HUD 剩余时间——修复客户端行动时间卡死不动（4:59/7:59）
                    t = payload.get("action_time_left")
                    if t is not None:
                        self._action_time_remaining = t
                        self.window.game_state.action_time_remaining = t
                elif msg_type == MsgType.MAP_CHANGE:
                    # 主机运行期改动广播：掉落物生成同步（drop_spawn）→ 本地建视觉掉落物
                    self._apply_map_change(payload)
                elif msg_type == MsgType.PICKUP_RESULT:
                    # 主机拾取仲裁结果：成功保持乐观状态 / 被拒回滚 run_carried + 移除视觉
                    self._apply_pickup_result(payload)
                elif msg_type == MsgType.EVAC_RESULT:
                    # 主机撤离结算清单（B12）：本人→按权威清单本地入库+断开+结算页；他人→忽略
                    self._apply_evac_result(payload)
                elif msg_type == MsgType.HEARTBEAT:
                    # 主机心跳回显：计算 RTT（往返延迟），供联机状态条 E3 显示
                    if self._hb_sent_at > 0:
                        self._net_rtt_ms = max(0.0, (time.time() - self._hb_sent_at) * 1000.0)
                        self._hb_sent_at = 0.0
                elif msg_type == MsgType.ROOM_ENDED:
                    # 房间结束（主机撤离/死亡/超时）：断开连接回大厅展示原因
                    self._apply_room_ended(payload)
                    return
            # 客户端本地攻速节流计时器递减（判定已移交主机，不经 combat 冷却）
            self._net_fire_cd = max(0.0, self._net_fire_cd - dt)
            # 远端怪物受击闪白计时衰减（远端怪物不进本地 AI/update，不自行递减）
            for rm in self.remote_monsters.values():
                if getattr(rm, "_hit_flash", 0) > 0:
                    rm._hit_flash = max(0.0, rm._hit_flash - dt)
            # 客户端 20Hz：上报本人实体快照（位置/朝向/HP），主机据此更新本端幽灵（Todo 23）
            self._client_snap_timer += dt
            if self._client_snap_timer >= 1.0 / NET_SNAPSHOT_HZ:
                self._client_snap_timer = 0.0
                self._send_player_snapshot()
            # 心跳：每 NET_HEARTBEAT_SEC 发一次（保活 + RTT 测量，协议 HEARTBEAT 双向）
            self._heartbeat_timer += dt
            if self._heartbeat_timer >= NET_HEARTBEAT_SEC:
                self._heartbeat_timer = 0.0
                self._heartbeat_seq += 1
                self._hb_sent_at = time.time()
                gs.net_client.send((MsgType.HEARTBEAT, {
                    "seq": self._heartbeat_seq,
                    "client_time": self._hb_sent_at,
                }))

        # 主机：每帧排空客户端入站消息（NetServer 经队列桥收包，每条为
        # {"player_id": int, "msg_type": str, "payload": dict}）。
        # 目前处理 ATTACK_EVENT（攻击判定收敛主机）与玩家注册（幽灵懒创建）。
        if gs.net_mode == "host" and gs.net_server is not None:
            # 主机本地玩家受击广播钩子（HP 主机权威）：注册一次，本地玩家被怪物/弹丸
            # 打掉血时广播 PLAYER_HURT，各端幽灵同步显示主机掉血
            if self.player.on_take_damage is None:
                host_id = getattr(gs, "net_player_id", None)
                if host_id is None:
                    host_id = 0  # 大厅未接入前的约定：主机固定玩家 id=0
                # 受击钩子：读取玩家待发附带效果（怪物攻击路径先设置 _pending_debuff
                # 再 take_damage），随 PLAYER_HURT 广播下发 debuff+等级（修复客户端
                # 玩家被怪物攻击时特殊效果未生效）
                self.player.on_take_damage = lambda actual, pid=host_id: self._broadcast_player_hurt(
                    pid, actual,
                    getattr(self.player, "_pending_debuff", None),
                    getattr(self.player, "_pending_debuff_level", 1),
                    getattr(self.player, "_pending_debuff_effects", None),
                )
            for inbound in gs.net_server.inbound_poll():
                if not isinstance(inbound, dict):
                    continue  # 非字典消息（理论不可达）：忽略
                sender_id = inbound.get("player_id", 0)
                # 晚期加入同步：该玩家首次发来任意消息 → 发送全量状态（之后只收增量快照）
                if sender_id not in self._full_state_sent:
                    self._full_state_sent.add(sender_id)
                    gs.net_server.send_to(sender_id, MsgType.FULL_STATE, self._serialize_full_state())
                    print(f"[GameView] 晚期加入玩家 {sender_id}：已发送全量状态")
                if inbound.get("msg_type") == MsgType.ATTACK_EVENT.name:
                    self._resolve_attack_event(
                        sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.SKILL_USE.name:
                    # 客户端技能释放请求：主机在对应幽灵上权威裁决（伤害/位移/护盾，
                    # 弹丸经 PROJECTILE_SNAPSHOT 同步，命中经 DAMAGE_RESULT 广播）
                    self._resolve_skill_use(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.POTION_USE.name:
                    # 药水使用请求：主机确认生效并广播 POTION_ACK（治疗数字全端可见）
                    self._handle_potion_use(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.PICKUP_REQUEST.name:
                    # 主机仲裁客户端拾取请求：先到先得 + 距离校验，广播 PICKUP_RESULT（Todo 18）
                    self._handle_pickup_request(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.EVAC_REQUEST.name:
                    # 主机：处理撤离请求（B12）：以主机权威 _players_run_carried 汇总该玩家
                    # 携带物清单并广播 EVAC_RESULT（各端据此调用 commit_run_to_warehouse 本地入库）
                    self._handle_evac_request(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.INTERACTION_REQUEST.name:
                    # 客户端交互请求：主机验证距离 → 执行交互 → 通过 MAP_CHANGE 广播结果
                    self._handle_interaction_request(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.PLAYER_ABANDON.name:
                    # 客户端放弃行动通知：更新 _player_status → 触发全员结束判定
                    self._handle_player_abandon(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.PLAYER_SNAPSHOT.name:
                    # 客户端 20Hz 上报本人实体：主机据此更新对应幽灵的位置/朝向/存活（Todo 23）
                    self._apply_client_snapshot(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.HEARTBEAT.name:
                    # 心跳回显：原样回给该客户端，客户端据此计算 RTT（保活同理）
                    gs.net_server.send_to(sender_id, MsgType.HEARTBEAT, inbound.get("payload") or {})
                # 其他客户端消息：确保该玩家幽灵已创建（多目标 AI 与 PLAYER_SNAPSHOT 需要）
                self._ensure_ghost(sender_id)

            # 主机权威幽灵死亡检测：HP 归零 → 单播 PLAYER_DEATH 给该玩家（其余玩家继续），
            # 幽灵标记死亡由 PLAYER_SNAPSHOT alive=False 下发，其他端幽灵随之消失（Todo 16）
            for pid, ghost in list(self.remote_players.items()):
                # 已撤离/离开的玩家不是死亡：其幽灵 alive=False 是撤离观战所致
                # （_send_player_snapshot 观战时上报 alive=False），不得误发 PLAYER_DEATH、
                # 不得把 _player_status 从 evac/left 覆盖为 dead（修复：撤离误判死亡破坏观战流程）
                if self._player_status.get(pid) in ("evac", "left"):
                    continue
                if not getattr(ghost, "alive", True) and not getattr(ghost, "_death_notified", False):
                    ghost._death_notified = True
                    self._player_status[pid] = "dead"  # 记录死亡状态（全员结束判定用）
                    gs.net_server.send_to(pid, MsgType.PLAYER_DEATH, {
                        "player_id": pid,
                        "killer_id": None,
                    })
                    print(f"[GameView] 玩家 {pid} 死亡，已通知该客户端")

            # 断线感知：服务器房间内已不在线的玩家标记 left（全员结束判定视为已结束）
            online_ids = set(gs.net_server.player_ids)
            for pid in list(self._player_status.keys()):
                if pid != 0 and pid not in online_ids and self._player_status.get(pid) not in ("evac", "dead", "left"):
                    self._player_status[pid] = "left"
                    print(f"[GameView] 玩家 {pid} 已离开房间，标记 left")

            # 观战期间全员结束检测：主机已结束（evac/dead）+ 全部客户端结束/离开 →
            # 广播 ROOM_ENDED(all_finished)（房间保留，主机回 host_wait 等待再次开局）
            if self._check_all_finished():
                self._broadcast_room_ended("all_finished")
                return

        # 行动时间倒计时（主机权威：倒计时递减与超时 _fail_run 裁决只在主机；客户端显示值以广播为准）
        # 观战模式：主机本局已结束（撤离/死亡），不再裁决超时（剩余客户端继续玩，全员结束统一收口）
        # TODO(联机): 主机权威逻辑，客户端跳过
        if gs.net_mode != "client" and not self._spectating:
            if self._action_time_remaining is not None and self._action_time_remaining > 0:
                self._action_time_remaining -= dt
                gs.action_time_remaining = self._action_time_remaining  # 同步到 GameState
                if self._action_time_remaining <= 0:
                    self._action_time_remaining = 0
                    gs.action_time_remaining = 0
                    self._fail_run("行动超时！未能在规定时间内撤离")
                    return

        # player 尚未初始化时跳过（setup 未完成或被提前调用 on_update）
        if self.player is None:
            return

        if not self.player.alive and not self._spectating:
            self._fail_run("你已阵亡！")
            return

        # 相机始终跟随玩家并居中（position 即视口中心）；观战模式跟随 V 键选定的目标
        if self._spectating:
            target = self._spectate_camera_target()
            # 观战目标为 None（全员已撤离/阵亡，暂无存活幽灵）：保持相机原位，
            # 等待主机 _check_all_finished 广播 ROOM_ENDED 收口（修复：勿回跳静止玩家）
            if target is not None:
                self.controller.camera.position = (target.center_x, target.center_y)
        else:
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

        # 护盾药水计时器：到期清空临时护盾（护盾吸收结算在 take_damage）
        if getattr(self.player, 'shield_effect_timer', 0) > 0:
            self.player.shield_effect_timer -= dt
            if self.player.shield_effect_timer <= 0:
                self.player.shield = 0.0
                self.player.shield_effect_timer = 0.0

        # 狂暴药水计时器：到期恢复伤害倍率（攻击结算乘 power_mult，见
        # character_skills.modify_attack_damage / combat.spawn_laser）
        if getattr(self.player, 'power_effect_timer', 0) > 0:
            self.player.power_effect_timer -= dt
            if self.player.power_effect_timer <= 0:
                self.player.power_mult = 1.0
                self.player.power_effect_timer = 0.0

        # 持续回复（HoT）与装备自然回血结算：各端本地结算本人 HP——客户端是本人 HP 权威源
        # （本地加血后随 PLAYER_SNAPSHOT 上报主机，主机采纳并转发全房，主机本地玩家同理）。
        # 修复「客户端装备被动效果（regen 加成）不全生效」：旧版只在主机结算、客户端不本地
        # 加血，客户端回血永远不生效。
        # 观战模式：玩家已撤离/死亡，不再结算回血（HP 保持结束态，避免幽灵复活显示）
        if not self._spectating:
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

        # 新手教程：检测玩家是否进入 BOSS 房间区域，设置 boss_taught = True
        tut = getattr(gs, "tutorial", None)
        if tut is not None and tut.active and tut.stage == 3 and not tut.boss_taught:
            boss_spawn = self.map_data.get("boss_spawn")
            if boss_spawn:
                # 计算玩家与 BOSS 生成点的距离（使用 TILE_SIZE 作为判定半径）
                dx = self.player.center_x - boss_spawn[0]
                dy = self.player.center_y - boss_spawn[1]
                if abs(dx) < TILE_SIZE * 3 and abs(dy) < TILE_SIZE * 3:
                    tut.boss_taught = True

        # BOSS 房间锁定逻辑
        boss_rect = self.map_data.get("boss_rect")
        if boss_rect and self.active_boss and self.active_boss.alive:
            # 检查玩家是否在 BOSS 房间内（含 1 块瓦片缓冲）
            in_boss_room = (boss_rect[0] - TILE_SIZE <= self.player.center_x <= boss_rect[2] + TILE_SIZE and
                           boss_rect[1] - TILE_SIZE <= self.player.center_y <= boss_rect[3] + TILE_SIZE)
            if in_boss_room and not self._boss_room_locked:
                # 进入 BOSS 房间：锁定门洞
                self._boss_room_locked = True
                self._create_boss_door_block()
                # 首次进入 BOSS 房间：弹出 BOSS 介绍向导弹窗
                if not self._boss_intro_shown:
                    from views.tutorial import build_boss_intro_pages
                    self._boss_intro_pages = build_boss_intro_pages()
                    self._boss_intro_active = True
                    self._boss_intro_idx = 0
                    self._boss_intro_shown = True
            elif not in_boss_room and self._boss_room_locked:
                # 尝试离开 BOSS 房间：推回边界
                # 计算最近的边界点并推回
                push_x = max(boss_rect[0] - TILE_SIZE, min(self.player.center_x, boss_rect[2] + TILE_SIZE))
                push_y = max(boss_rect[1] - TILE_SIZE, min(self.player.center_y, boss_rect[3] + TILE_SIZE))
                self.player.center_x = push_x
                self.player.center_y = push_y
        elif self._boss_room_locked and (not self.active_boss or not self.active_boss.alive):
            # BOSS 死亡：解锁门洞
            self._boss_room_locked = False
            self._remove_boss_door_block()

        # 环境物受击闪烁计时衰减（否则被攻击后会一直显示白圈）
        for h in self.harvestables:
            if h.alive:
                h.update(dt)

        # 资源/怪物随机补刷 + 怪物 AI 更新 + 怪物弹丸推进与碰撞（主机权威：世界模拟唯一执行者，
        # 弹丸命中玩家由主机裁决并广播受伤事件；客户端怪物/弹丸由快照渲染）
        # TODO(联机): 主机权威逻辑，客户端跳过
        if gs.net_mode != "client":
            # 资源随机刷新（被砍光后补刷，保持地图有可采集资源）
            self._respawn_harvestables(dt)

            # 野外怪物刷新（被击杀后补刷，保持地图有怪物）
            self._respawn_monsters(dt)

            # 怪物 AI
            # 近战怪 try_attack 返回 bool（是否命中，伤害已直接结算）；远程怪返回弹丸对象
            # 联机主机：目标 = 本地玩家 + 全部客户端幽灵（多目标 AI，todo 11 支持 players 列表）
            if gs.net_mode == "host":
                # 幽灵容器可能在循环中被懒创建（_ensure_ghost），先取快照避免迭代期间变更
                ghost_list = [g for g in self.remote_players.values() if getattr(g, "alive", True)]
                # 观战模式：主机玩家已撤离/死亡，不再作为怪物攻击目标（仅客户端幽灵被攻击）
                ai_targets = ghost_list if self._spectating else ([self.player] + ghost_list)
            else:
                ai_targets = None
            for m in self.monsters:
                if hasattr(m, 'alive') and m.alive:
                    if ai_targets is not None:
                        m.update(self.player.center_x, self.player.center_y, dt, players=ai_targets)
                        proj = m.try_attack(players=ai_targets)
                    else:
                        m.update(self.player.center_x, self.player.center_y, dt)
                        proj = m.try_attack(self.player)
                    # 仅当返回真实弹丸精灵时才加入弹丸列表（防御近战怪返回 True 被误加入导致崩溃）
                    if isinstance(proj, arcade.Sprite):
                        # 记录发射者怪物 net_id（弹丸快照 owner_id 用；solo 模式怪物无 net_id 时为 0）
                        proj.owner_net_id = getattr(m, "net_id", 0)
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
                # 弹丸命中玩家：本地玩家直结算；主机权威模式下幽灵由 on_take_damage 钩子广播 PLAYER_HURT
                # 观战模式：主机玩家已撤离/死亡，弹丸不再命中主机（剩余客户端幽灵照常受击）
                if not self._spectating and arcade.check_for_collision(p, self.player):
                    # 记录弹丸附带效果：受击钩子（PLAYER_HURT 广播）据此下发 debuff+等级
                    first_debuff = getattr(p, "debuff_id", None)
                    self.player._pending_debuff = first_debuff
                    self.player._pending_debuff_level = 1
                    self.player._pending_debuff_effects = getattr(p, "debuffs", [])  # 全部效果（联机广播用）
                    self.player.take_damage(p.damage)
                    # 弹丸附带 debuff（木乃伊毒弹/BOSS 冰冻弹 + 武器效果）施加到玩家
                    for eid, lvl in getattr(p, "debuffs", []):
                        if hasattr(self.player, "apply_debuff"):
                            self.player.apply_debuff(eid, lvl)
                    p.remove_from_sprite_lists()
                    continue
                if gs.net_mode == "host":
                    for ghost in list(self.remote_players.values()):
                        if not getattr(ghost, "alive", True):
                            continue
                        if arcade.check_for_collision(p, ghost):
                            # 幽灵受击：take_damage 内 on_take_damage 钩子自动广播 PLAYER_HURT
                            # （先记录附带效果，钩子据此下发 debuff+等级）
                            first_debuff = getattr(p, "debuff_id", None)
                            ghost._pending_debuff = first_debuff
                            ghost._pending_debuff_level = 1
                            ghost._pending_debuff_effects = getattr(p, "debuffs", [])
                            ghost.take_damage(p.damage)
                            for eid, lvl in getattr(p, "debuffs", []):
                                if hasattr(ghost, "apply_debuff"):
                                    ghost.apply_debuff(eid, lvl)
                            p.remove_from_sprite_lists()
                            break

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
        # 屏幕鼠标坐标转世界坐标（激光和全自动武器需要世界坐标计算方向；
        # 坐标系为逻辑分辨率，见 input_handler 注释）
        cam = self.controller.camera.position
        world_mx = self._mouse_x + cam[0] - WINDOW_WIDTH / 2
        world_my = self._mouse_y + cam[1] - WINDOW_HEIGHT / 2
        # 激光束更新（陨星炮神器：实时跟随鼠标方向）
        self.combat.update_lasers(dt, world_mx, world_my)
        # 玩家弹丸/激光命中怪物判定与反馈（主机权威：战斗伤害判定收敛主机；
        # 客户端只保留弹丸运动表现，命中由攻击事件→主机判定→广播伤害结果驱动）
        # TODO(联机): 主机权威逻辑，客户端跳过
        if gs.net_mode != "client":
            # 弹丸命中怪物（复用精灵列表，避免每帧新建）
            self._monster_sprite_list.clear()
            seen_ids = set()  # 防止同一怪物对象被重复添加（respawn 时可能出现）
            for m in self.monsters:
                if hasattr(m, 'alive') and m.alive and id(m) not in seen_ids:
                    self._monster_sprite_list.append(m)
                    seen_ids.add(id(m))
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
                # 主机权威：命中即广播 DAMAGE_RESULT（含客户端上报的远程攻击与主机本地攻击），
                # 各端据此同步怪物血量显示（攻击判定收敛主机）
                self._broadcast_damage(
                    m, actual, hit=True, crit=False,
                    debuffs=list(self._attack_debuffs),  # 完整列表（含等级），修复主机 debuff 不同步
                )
            # 攻速光环（冰霜领域神器）：每 AURA_SLOW_TICK 秒对玩家周围 aura_radius 内怪物施加减速
            # （主机权威：怪物状态收敛主机，客户端不本地裁决）
            if getattr(gs, "weapon_aura_slow", False) and not self._spectating:
                self._aura_tick -= dt
                if self._aura_tick <= 0:
                    self._aura_tick = AURA_SLOW_TICK
                    aura_radius = getattr(gs, "weapon_aura_radius", 0)
                    for m in self.monsters:
                        if not hasattr(m, 'alive') or not m.alive:
                            continue
                        dist = math.hypot(m.center_x - self.player.center_x,
                                          m.center_y - self.player.center_y)
                        if dist <= aura_radius and hasattr(m, "apply_debuff"):
                            m.apply_debuff("slow", AURA_SLOW_LEVEL)
        else:
            # 客户端：本地弹丸纯视觉碰撞（环境物/远端怪物），伤害判定仍收敛主机——
            # 普通弹丸命中环境物/怪物即消失（穿透弹丸保留），并显示粒子/伤害数字反馈
            self._client_visual_collisions()

        # 全自动武器：按住鼠标左键时持续射击（仅auto_fire标记的武器可连发）。
        # 观战守卫：观战期（已撤离/阵亡）屏蔽自动开火——否则 input_handler 早退导致
        # _left_mouse_held 无法复位，幽灵持续上报 ATTACK_EVENT/生成弹丸（修复客户端观战崩溃）
        if self._left_mouse_held and getattr(gs, 'weapon_auto_fire', False) and not self._spectating:
            if gs.net_mode == "client" and gs.net_client is not None:
                # 联机客户端：不上报本地命中判定，改为本地攻速节流 + 上报 ATTACK_EVENT，
                # 命中判定收敛主机（与 handle_mouse_press 客户端分支一致，只保留表现效果）
                if self._net_fire_cd <= 0:
                    self._net_fire_cd = 1.0 / max(0.1, getattr(gs, 'weapon_speed', 1.0))
                    angle = math.atan2(world_my - self.player.center_y,
                                       world_mx - self.player.center_x)
                    # 附带装备/武器附加 debuff 列表（元素为 (效果ID, 效果等级) 元组），
                    # 主机裁决命中时一并施加（与 handle_mouse_press 客户端分支一致）。
                    # x/y = 攻击瞬间玩家世界坐标：主机据此修正幽灵位置（20Hz 快照滞后），
                    # 避免近战扇形/远程弹丸因幽灵位置滞后判定 miss（修复客户端攻击打不中）
                    gs.net_client.send((MsgType.ATTACK_EVENT, {
                        "attacker_id": getattr(gs, "net_player_id", 0),  # 客户端联机玩家 id（大厅接入后提供）
                        "weapon": self._current_weapon_name(),
                        "angle": angle,
                        "x": self.player.center_x,
                        "y": self.player.center_y,
                        "debuffs": list(getattr(self, "_attack_debuffs", [])),
                        "timestamp": time.time() * 1000.0,
                    }))
                    self._player_attack_flash = 0.1
                    self._attack_this_frame = True
                    self._attack_kind = "ranged"
                    self._last_attack_range = getattr(gs, 'weapon_range', 250)
                    sound_manager.play_ranged_attack()
                    # 本地纯表现弹丸（仅渲染，命中判定收敛主机）：修复客户端远程子弹不可见
                    self.combat._cooldowns.pop(0, None)
                    if getattr(gs, 'weapon_special', '') == "laser":
                        self.combat.spawn_laser(
                            self.player, gs.weapon_damage,
                            world_mx, world_my,
                            length=getattr(gs, 'weapon_range', 250), width=24, duration=3.0,
                        )
                    else:
                        self.combat.ranged_attack(
                            self.player, gs.weapon_damage,
                            getattr(gs, 'weapon_proj_speed', 400),
                            world_mx, world_my,
                            getattr(gs, 'weapon_special', ''),
                            None, getattr(gs, 'weapon_speed', 1.0),
                            debuffs=None,
                            # 散射/吸血：本地纯表现弹丸与主机裁决口径一致（视觉对齐）
                            lifesteal=getattr(gs, 'weapon_lifesteal', LIFESTEAL_DEFAULT),
                            spread_count=getattr(gs, 'weapon_spread_count', SPREAD_COUNT_DEFAULT),
                            spread_angle=getattr(gs, 'weapon_spread_angle', SPREAD_ANGLE_DEFAULT),
                        )
            elif self.combat.can_attack():
                self.combat.ranged_attack(
                    self.player,
                    gs.weapon_damage,
                    getattr(gs, 'weapon_proj_speed', 400),
                    world_mx, world_my,
                    getattr(gs, 'weapon_special', ''),
                    weapon_speed=getattr(gs, 'weapon_speed', 1.0),
                    # 散射/吸血：单机命中判定在此弹丸上完成（check_monster_hits 统一结算）
                    lifesteal=getattr(gs, 'weapon_lifesteal', LIFESTEAL_DEFAULT),
                    spread_count=getattr(gs, 'weapon_spread_count', SPREAD_COUNT_DEFAULT),
                    spread_angle=getattr(gs, 'weapon_spread_angle', SPREAD_ANGLE_DEFAULT),
                )
                self._player_attack_flash = 0.1
                self._attack_this_frame = True
                self._attack_kind = "ranged"
                self._last_attack_range = getattr(gs, 'weapon_range', 250)
                sound_manager.play_ranged_attack()

        # 环境物受击判定 + 宝箱/水井/火箭台交互裁决 + 火箭台状态机与撤离裁决
        # （主机权威：破坏判定/开箱掉落/水井回血/BOSS生成/撤离成败全在主机；
        # 客户端只按主机广播显示状态，不本地裁决）
        # TODO(联机): 主机权威逻辑，客户端跳过
        if gs.net_mode != "client":
            # 观战模式：主机玩家已撤离/死亡，跳过玩家交互（input_handler 也已屏蔽按键），
            # 但火箭台状态机仍推进（倒计时广播依赖，客户端据此显示）
            if not self._spectating:
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
                # 观战模式：主机玩家已撤离/死亡，不裁决主机撤离（撤离仅限存活玩家）
                if pad.state == "evac_success" and not self._spectating:
                    # 检查玩家是否在发射台范围内
                    dist = ((self.player.center_x - pad.center_x) ** 2 +
                            (self.player.center_y - pad.center_y) ** 2) ** 0.5
                    if dist < 80:  # 玩家在范围内，撤离成功
                        # 将装备栏中的物品加入 run_carried 以便入库
                        self._add_equipped_to_carried(gs)
                        # 将本局药水槽并入 run_carried["potion"]（用户需求：本局药水可带出）
                        self._fold_run_potions(gs)
                        # 修复：删除局部导入（顶部已导入），否则会让 commit_run_to_warehouse/clear_run
                        # 成为 on_update 的局部变量，未走此分支时第 814 行报 UnboundLocalError
                        commit_run_to_warehouse(gs.player_id, gs.run_carried)
                        # 保存携带物品用于显示收益
                        carried_copy = dict(gs.run_carried) if hasattr(gs, 'run_carried') else {}
                        clear_run(gs.run_carried)
                        # 等级系统：火箭台撤离成功经验（各端本地结算，solo/host 发放）
                        _award_exp(self, EXP_EVAC)
                        # 增强提示：大字+粒子效果
                        floating_texts.add(self.player.center_x, self.player.center_y + 80,
                                           "撤离成功！战利品已存入仓库",
                                           arcade.color.GREEN, life=3.0, font_size=22)
                        particle_system.emit(self.player.center_x, self.player.center_y, 30,
                                            (255, 215, 0), speed=100, life=1.0, size=5)
                        sound_manager.play_level_up()
                        # 跳转到撤离结果页面
                        from views.evac_result_view import EvacResultView
                        if gs.net_mode == "host":
                            # 重构：联机主机火箭台撤离成功 = 本局单人结束，不关房 → 观战模式
                            self._enter_spectate("evac")
                            return
                        self.window.show_view(EvacResultView(self.window_ref, success=True, run_carried=carried_copy))
                        return
                    else:  # 玩家不在范围内，撤离失败
                        pad.state = "destroyed"  # 标记发射台已失效
                        # 修复：不再在地图内弹出失败文字，统一使用撤离结果页面提示
                        particle_system.emit(self.player.center_x, self.player.center_y, 20,
                                            (255, 50, 50), speed=80, life=0.8, size=4)
                        sound_manager.play_hurt()
                        if gs.net_mode == "host":
                            # 重构：联机主机火箭台撤离失败 = 死亡语义，清装备 → 观战模式
                            self._clear_run_equipment(gs)
                            self._enter_spectate("dead")
                            return
                        # 修复：单机火箭台撤离失败 = 死亡语义，与主机分支/超时一致，先清装备再跳失败页
                        # （原实现漏调 _clear_run_equipment，导致单机撤离失败只丢金币、装备保留）
                        self._clear_run_equipment(gs)
                        # 单机：跳转到撤离结果页面
                        from views.evac_result_view import EvacResultView
                        self.window.show_view(EvacResultView(self.window_ref, success=False))
                        return

        # 客户端交互请求：按E时检测附近可交互物，发送 INTERACTION_REQUEST 给主机裁决
        # （放在 if gs.net_mode != "client" 块外部，确保客户端能执行）
        if gs.net_mode == "client" and gs.net_client is not None:
            if getattr(self, '_chest_key_pressed', False) and not self._spectating:
                self._send_client_interaction_request(gs)

        # 掉落物生命周期（主机权威：掉落生成/过期由主机管理并随快照同步，客户端不本地推进）
        # TODO(联机): 主机权威逻辑，客户端跳过
        if gs.net_mode != "client":
            # 掉落物更新
            for d in self.drops[:]:
                d.update(dt)
                if d.expired:
                    self.drops.remove(d)

            # 联机主机：为新生成的掉落物分配网络 id 并广播 drop_spawn（MAP_CHANGE extra 携带列表，
            # 客户端据此建本地视觉掉落物；solo 模式无 net_id 分配，不走广播）
            if gs.net_mode == "host" and gs.net_server is not None:
                new_drops = [d for d in self.drops if d.net_id is None]
                if new_drops:
                    for d in new_drops:
                        d.net_id = f"drop_{self._next_drop_net_id}"
                        self._next_drop_net_id += 1
                    gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                        "obj_id": "drops",
                        "change_type": "drop_spawn",
                        "state": {},
                        "extra": {"drops": [{
                            "net_id": d.net_id,
                            "item_type": d.item_type,
                            "item_id": d.item_id,
                            "x": d.center_x, "y": d.center_y,
                            "quantity": d.quantity, "level": d.level,
                        } for d in new_drops]},
                    })

            # 联机主机：运行期地图改动广播（宝箱开启/环境物摧毁/水井首次开启/火箭台状态与撤离
            # 倒计时，Todo 20，D2）。与 drop_spawn 同一检测模式：客户端据此把本地确定性地图对象
            # 与主机权威状态对齐（渲染/碰撞据此跳过已开宝箱、已摧毁环境物）。
            self._broadcast_map_changes()

        # 按E拾取（带效果）
        old_carried = copy.deepcopy(gs.run_carried) if hasattr(gs, 'run_carried') else {}
        picked, skipped_full, skipped_no_bag = (False, False, False)
        # 观战守卫：观战期屏蔽拾取——否则 input_handler 早退导致 _chest_key_pressed 无法复位，
        # 幽灵持续上报 PICKUP_REQUEST（修复客户端观战崩溃）
        if getattr(self, '_chest_key_pressed', False) and not self._spectating:
            if gs.net_mode == "client" and gs.net_client is not None:
                # 联机客户端：乐观拾取（try_pickup 立即生效并从 self.drops 移除视觉），
                # 记录拾取前快照供主机拒绝时回滚；逐掉落物上报 PICKUP_REQUEST 请主机仲裁。
                # 快照只在“上一批请求已全部确认”后重新记录（_pending_pickup_count==0），
                # 避免按住 E 期间逐帧覆盖快照 → 主机拒绝时回滚到已含本次拾取的状态。
                if self._pending_pickup_count == 0:
                    # 快照覆盖 run_carried + run_potions：药水进入本局药水槽，被拒时一并回滚
                    self._pickup_snapshot = (old_carried, dict(getattr(gs, "run_potions", {}) or {}))
                picked, skipped_full, skipped_no_bag = try_pickup(
                    self.player, self.drops, gs.run_carried,
                    equipped_weapon_id=gs.equipped_weapon_id or gs.current_weapon_item_id,
                    equipped_helmet_id=gs.equipped_helmet_id,
                    equipped_armor_id=gs.equipped_armor_id,
                    on_free_equip=self._apply_free_equip,
                    run_potions=gs.run_potions,
                )
                for d in picked:
                    if getattr(d, "net_id", None):
                        # 上报拾取请求：附带拾取瞬间玩家世界坐标，主机据此做距离校验
                        # （与客户端本地 try_pickup 判定口径一致，避免幽灵位置滞后误判 too_far）
                        self._pending_pickup_count += 1
                        gs.net_client.send((MsgType.PICKUP_REQUEST, {
                            "player_id": getattr(gs, "net_player_id", 0),
                            "item_id": d.net_id,
                            "x": self.player.center_x,
                            "y": self.player.center_y,
                        }))
            else:
                # solo 或联机主机：本地权威拾取（主机 self.drops 即权威列表）
                picked, skipped_full, skipped_no_bag = try_pickup(
                    self.player, self.drops, gs.run_carried,
                    equipped_weapon_id=gs.equipped_weapon_id or gs.current_weapon_item_id,
                    equipped_helmet_id=gs.equipped_helmet_id,
                    equipped_armor_id=gs.equipped_armor_id,
                    on_free_equip=self._apply_free_equip,
                    run_potions=gs.run_potions,
                )
                # 新手教程：拾取到物品 → 记录拾取数（游戏内引导推进用）
                if picked:
                    tut = getattr(gs, "tutorial", None)
                    if tut is not None and tut.active and tut.stage == 3:
                        tut.pickup_count += 1
                if picked and gs.net_mode == "host" and gs.net_server is not None:
                    # 联机主机：主机本地拾取=权威结果 → 记录主机携带物（撤离结算用）
                    # + 广播 PICKUP_RESULT（accepted=True）让各客户端移除该视觉掉落物
                    host_id = getattr(gs, "net_player_id", None) or 0
                    for d in picked:
                        self._record_player_pickup(host_id, d)
                        if getattr(d, "net_id", None):
                            gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
                                "player_id": host_id, "item_id": d.net_id,
                                "accepted": True, "reason": None,
                            })

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

        # 撤离读条更新（观战模式：主机已撤离/死亡，不再触发撤离结算）
        evac_result = None if self._spectating else self.evac.update(
            self.player, self.map_data["evac_points"], delta_time)

        # 客户端撤离完成：不本地直接入库（数据源在主机 _players_run_carried），
        # 发 EVAC_REQUEST 请求主机下发权威结算清单，按 EVAC_RESULT 清单入库（B12）。
        # _evac_request_sent 单次闸门：读条完成帧可能连续命中，只发一次请求。
        if gs.net_mode == "client" and evac_result == "evacuated" and not self._evac_request_sent:
            self._evac_request_sent = True
            my_id = getattr(gs, "net_player_id", None)
            if my_id is not None and gs.net_client is not None:
                gs.net_client.send((MsgType.EVAC_REQUEST, {"player_id": my_id}))

        # 撤离完成本地结算（主机权威：撤离=房间结束，由主机裁决并下发结算清单；
        # 客户端不本地直接入库，改走 B12「请求→清单→按清单入库」流）
        # TODO(联机): 主机权威逻辑，客户端跳过
        if gs.net_mode != "client":
            # 检测撤离完成
            if evac_result == "evacuated":
                # 将装备栏中的物品加入 run_carried 以便入库
                self._add_equipped_to_carried(gs)
                # 将本局药水槽并入 run_carried["potion"]（用户需求：本局药水可带出）
                self._fold_run_potions(gs)
                # 撤离成功：提交战利品到仓库
                commit_run_to_warehouse(gs.player_id, gs.run_carried)
                # 保存携带物品用于显示收益（先复制再清空）
                carried_copy = dict(gs.run_carried) if hasattr(gs, 'run_carried') else {}
                clear_run(gs.run_carried)
                # 等级系统：撤离成功经验（各端本地结算，solo/host 撤离点成功发放）
                _award_exp(self, EXP_EVAC)
                # 增强提示：大字+粒子效果
                floating_texts.add(self.player.center_x, self.player.center_y + 80,
                                   "撤离成功！战利品已存入仓库",
                                   arcade.color.GREEN, life=3.0, font_size=22)
                particle_system.emit(self.player.center_x, self.player.center_y, 30,
                                    (255, 215, 0), speed=100, life=1.0, size=5)
                sound_manager.play_evac()  # 撤离成功专属音效
                if gs.net_mode == "host":
                    # 重构：联机主机撤离成功 = 本局单人结束，不关房 → 进入观战模式
                    # （房间保留、后台继续模拟，剩余客户端继续玩，全员结束后回房等待）
                    self._enter_spectate("evac")
                    return
                # 单机：跳转撤离结果页面（与火箭发射台撤离保持一致）
                # 新手教程：撤离成功 → 撤离结果页展示火箭发射台教学（阶段 5 接管）
                tut = getattr(gs, "tutorial", None)
                if tut is not None and tut.active and tut.stage == 3:
                    tut.stage = 4
                    tut.page = 0
                from views.evac_result_view import EvacResultView
                self.window.show_view(EvacResultView(self.window_ref, success=True, run_carried=carried_copy))
                return

        # 宝箱按键标志重置（在 handle_chest_interaction 中消费后重置）
        if self._chest_key_pressed and not picked:
            pass  # 保持状态直到交互完成

    def _clear_run_equipment(self, gs) -> None:
        """清空本次携带数据并删除数据库中的已装备物品（死亡/撤离失败 = 丢失所有装备）

        与单机失败结算同口径；联机主机/客户端死亡与撤离失败均复用（回房等待或观战前清装）。
        """
        # 清空本次携带数据
        if hasattr(gs, 'run_carried'):
            gs.run_carried = {}
        # 本局药水槽一并清空（死亡/撤离失败 = 药水随携带物丢失）
        if hasattr(gs, 'run_potions'):
            gs.run_potions = {}
        # 从数据库中删除装备（撤离失败 = 死亡，丢失所有装备）
        pid = gs.player_id
        if pid:
            from db.connection import _conn
            with _conn() as c:
                # 删除已装备的武器（equipped_weapon_id 是 weapons 表的 row id）
                weapon_db_id = getattr(gs, 'equipped_weapon_id', None)
                if weapon_db_id is not None:
                    c.execute("DELETE FROM weapons WHERE id=? AND player_id=?", (weapon_db_id, pid))
                # 删除已装备的头盔/护甲/背包（equipment 表中 is_equipped=1 的行）
                c.execute("DELETE FROM equipment WHERE player_id=? AND is_equipped=1", (pid,))
                # 删除仓库中所有药水（死亡/撤离失败 = 药水全部丢失，与武器装备同口径）
                c.execute("DELETE FROM potions WHERE player_id=?", (pid,))
        # 重置游戏状态中的装备引用
        gs.equipped_weapon_id = None
        gs.current_weapon_kind = "melee"
        gs.current_weapon_id = None
        gs.current_weapon_item_id = None
        gs.equipped_helmet_id = None
        gs.equipped_armor_id = None
        gs.equipped_backpack_id = None
        # 重置武器扩展机制字段（死亡/清装后避免光环/吸血残留作用于观战或无武器状态）
        gs.weapon_lifesteal = LIFESTEAL_DEFAULT
        gs.weapon_spread_count = SPREAD_COUNT_DEFAULT
        gs.weapon_spread_angle = SPREAD_ANGLE_DEFAULT
        gs.weapon_aura_slow = False
        gs.weapon_aura_radius = 0

    def _fail_run(self, reason: str):
        """行动失败统一处理：超时/死亡 → 清空携带物 → 删除数据库中的装备

        单机：清装备后展示失败结算页；
        联机主机：清装备后进入观战模式（房间保留、后台模拟，等待全员结束回房）；
        联机客户端：清装备后进入观战模式（与撤离观战同路径，连接保留、等待全员结束回房）。
        """
        # 防御性检查：窗口关闭时 player 可能尚未初始化（setup 未完成），直接跳过
        if self.player is None:
            return
        gs = self.window.game_state
        if gs.net_mode == "host":
            # 联机主机死亡/超时：清装备 → 观战模式（不关房、不广播 ROOM_ENDED，
            # 房间生命周期与单局解耦：剩余客户端继续玩，全员结束后回房等待）
            self._clear_run_equipment(gs)
            self._enter_spectate("dead")
            return
        if gs.net_mode == "client":
            # 联机客户端死亡/超时（理论经 _apply_player_death 处理，此处兜底）：
            # 清装备 → 通知主机放弃行动 → 进入观战模式（与撤离观战同路径，连接保留、等待全员结束回房）
            self._clear_run_equipment(gs)
            # 通知主机：更新 _player_status → 触发全员结束判定（修复观战者卡住）
            if gs.net_client is not None:
                gs.net_client.send((MsgType.PLAYER_ABANDON, {
                    "player_id": getattr(gs, "net_player_id", 0),
                    "reason": reason,
                }))
            # 客户端放弃 → 进入观战模式（与撤离观战同路径）：
            # - _spectating=True：渲染隐藏本体/武器/读条，input_handler 屏蔽操作，相机跟随观战目标；
            # - _spectate_target_id=None：由 _spectate_camera_target 自动回退第一个存活幽灵；
            # - 连接保留、poll 继续运行，主机 ROOM_ENDED(all_finished) 广播时经 _apply_room_ended 回房。
            gs.net_wait_reason = "dead"
            self._spectating = True
            # 观战期 input_handler 早退（handle_key_release/handle_mouse_release 被观战守卫拦截）
            # 导致 _left_mouse_held/_chest_key_pressed 无法复位，这里手动清零：
            # 双保险防止幽灵持续攻击/拾取（配合 on_update 的观战守卫）
            self._left_mouse_held = False
            self._chest_key_pressed = False
            # 观战模式：相机改由观战段控制跟随幽灵，禁止 controller.update() 每帧拉回
            # 已撤离/阵亡的静止玩家（否则观战视角卡死在撤离点）
            self.controller.follow_player = False
            self._spectate_target_id = None
            # 观战提示
            floating_texts.add(self.player.center_x, self.player.center_y + 60,
                               "你已放弃行动，进入观战模式（V 键切换视角）",
                               arcade.color.RED, life=3.0, font_size=16)
            return
        # 单机：清装备 + 失败结算页
        self._clear_run_equipment(gs)
        # 修复：不再在地图内弹出失败文字，统一使用撤离结果页面提示
        # 增强提示：粒子效果
        particle_system.emit(self.player.center_x, self.player.center_y, 20,
                            (255, 50, 50), speed=80, life=0.8, size=4)
        sound_manager.play_hurt()  # 使用受伤音效作为失败音效
        # 跳转到撤离结果页面
        from views.evac_result_view import EvacResultView
        self.window.show_view(EvacResultView(self.window_ref, success=False))

    def _enter_spectate(self, outcome: str) -> None:
        """主机撤离/死亡后进入观战模式：房间保留、后台继续模拟，禁操作禁结算

        - outcome: "evac"（撤离成功）/ "dead"（死亡/撤离失败/超时）——写入 _player_status[0]，
          供全员结束判定使用；
        - 观战期间世界继续模拟（怪物 AI/掉落/快照照常），主机玩家不再受攻击、不再结算；
        - 相机默认跟随观战目标（V 键循环切换，见 input_handler）。
        """
        self._spectating = True
        # 观战期 input_handler 早退（handle_key_release/handle_mouse_release 被观战守卫拦截）
        # 导致 _left_mouse_held/_chest_key_pressed 无法复位，这里手动清零：
        # 双保险防止幽灵持续攻击/拾取（修复客户端观战崩溃，配合 on_update 的观战守卫）
        self._left_mouse_held = False
        self._chest_key_pressed = False
        # 防御性加固（主机观战崩溃排查）：观战期不再攻击，清空残留攻击状态——
        # _attack_debuffs 残留会导致观战期间意外广播陈旧 debuff（引用已释放的装备效果），
        # _attack_this_frame 残留会让观战期多结算一帧近战伤害
        self._attack_debuffs = []
        self._attack_this_frame = False
        # 观战模式：相机改由观战段控制跟随幽灵，禁止 controller.update() 每帧拉回
        # 已撤离/阵亡的静止玩家（否则观战视角卡死在撤离点，修复场景2）
        self.controller.follow_player = False
        # 默认观战目标 = 第一个存活客户端幽灵（修复：之前默认 0（自己玩家）导致相机跟随
        # 撤离点静止的玩家，视角卡死在撤离点无法移动）
        first_ghost = next(
            (pid for pid, g in self.remote_players.items()
             if getattr(g, "alive", True) and self._player_status.get(pid, "alive") == "alive"),
            None,
        )
        self._spectate_target_id = first_ghost if first_ghost is not None else None
        self._player_status[0] = outcome
        print(f"[GameView] 主机进入观战模式: {outcome}")
        # 观战提示（撤离成功/阵亡分流文案）
        tip = ("你已撤离，进入观战模式（V 键切换视角）" if outcome == "evac"
               else "你已阵亡，进入观战模式（V 键切换视角）")
        floating_texts.add(self.player.center_x, self.player.center_y + 60, tip,
                           arcade.color.GOLD if outcome == "evac" else arcade.color.RED,
                           life=3.0, font_size=16)

    def _cycle_spectate_target(self) -> None:
        """V 键：循环切换观战跟随目标（仅存活客户端幽灵，不包含自己玩家）

        修复：原候选含 id=0（自己玩家），观战时自己已撤离/阵亡且不在幽灵表，
        跟随自己会卡在撤离点视角；观战目标应始终为存活客户端玩家。
        """
        if not self._spectating:
            return
        # 候选：所有存活幽灵（alive 且状态为 alive）
        alive_ids = []
        for pid, ghost in self.remote_players.items():
            if (getattr(ghost, "alive", True)
                    and self._player_status.get(pid, "alive") == "alive"):
                alive_ids.append(pid)
        if not alive_ids:
            return
        cur = self._spectate_target_id if self._spectate_target_id in alive_ids else alive_ids[0]
        idx = alive_ids.index(cur)
        self._spectate_target_id = alive_ids[(idx + 1) % len(alive_ids)]

    def _spectate_camera_target(self):
        """观战跟随目标实体：目标 id 对应的幽灵（存活优先），无效则回退第一个存活幽灵

        修复：之前无效时回退 self.player（自己玩家），观战时自己已撤离停在撤离点，
        相机跟随静止玩家导致视角卡死；观战应始终跟随客户端幽灵（其位置由
        PLAYER_SNAPSHOT 20Hz 同步，跟随即同步该玩家实时视角）。
        修复2：回退循环补 _player_status==alive 过滤（与 _enter_spectate/_cycle_spectate_target
        一致）——否则会跟随「已撤离/已阵亡」的冻结幽灵（alive 属性仍为 True，
        但位置已停在撤离点不再移动），导致视角再次卡死；且无存活幽灵时返回
        None 而非 self.player（自己已撤离静止，跟随自己仍是卡死）。
        """
        tid = self._spectate_target_id
        if tid is not None and tid in self.remote_players:
            ghost = self.remote_players[tid]
            if (getattr(ghost, "alive", True)
                    and self._player_status.get(tid, "alive") == "alive"):
                return ghost
        # 目标缺失/死亡/已撤离：回退到第一个存活且未结束的幽灵（无则返回 None，相机保持原位）
        for pid, ghost in self.remote_players.items():
            if (getattr(ghost, "alive", True)
                    and self._player_status.get(pid, "alive") == "alive"):
                return ghost
        return None

    def _check_all_finished(self) -> bool:
        """主机观战期间全员结束判定：主机已结束（evac/dead）+ 全部客户端结束/离开

        - 满足条件时广播 ROOM_ENDED(all_finished)（房间保留，回房等待再次开局）；
        - 断线客户端：server.player_ids 不再包含 → 标记 left（此处按在线玩家判定）。
        """
        gs = self.window.game_state
        if not self._spectating or gs.net_mode != "host" or gs.net_server is None:
            return False
        if self._player_status.get(0, "alive") not in ("evac", "dead"):
            return False  # 主机本局未结束：不判定
        # 在线玩家全部结束（evac/dead/left 之一）即全员结束
        for pid in gs.net_server.player_ids:
            if pid == 0:
                continue  # 主机自己已在上方判定
            if self._player_status.get(pid, "alive") not in ("evac", "dead", "left"):
                return False
        return True

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
