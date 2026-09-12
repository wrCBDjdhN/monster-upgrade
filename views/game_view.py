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
    # 倒地/救援系统
    DOWNED_TIMEOUT, RESCUE_DISTANCE, RESCUE_DURATION, REVIVE_HP,
)
from net.protocol import MsgType  # 联机消息类型枚举（MONSTER_SNAPSHOT 等）
from game.map_gen import generate_map
from game.player import Player, PlayerController
from game.monsters import Zombie, MummyMelee  # 刷怪映射兜底（未知类型回退用）
from game.combat import CombatSystem
from game.batch_shapes import ShapeBatch
from game.loot import DropItem, roll_loot, try_pickup
from views.game.network_sync import NetworkSyncManager
from views.game.spectate_system import SpectateManager
from views.game.pickup_loot import PickupLootManager
from views.game.evac_manager import EvacManager
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

    def draw(self, batch=None):
        """绘制远端激光（与 combat.LaserBeam 三层画法一致：外圈光晕 + 主体 + 中心亮核）

        batch：可选 ShapeBatch 批量绘制对象；传入则用 batch.line() 替代即时模式
        arcade.draw_line()，减少 draw call 次数（性能优化）。
        """
        if self.duration <= 0:
            return
        ex = self.x + math.cos(self.angle) * self.length
        ey = self.y + math.sin(self.angle) * self.length
        if batch is not None:
            batch.line(self.x, self.y, ex, ey, (255, 100, 255, 60), self.width + 10)
            batch.line(self.x, self.y, ex, ey, (255, 255, 255), self.width)
            batch.line(self.x, self.y, ex, ey, (255, 180, 255), max(3, self.width // 3))
        else:
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
        # 倒地/救援系统状态（联机模式）
        self._downed_players: dict[int, dict] = {}  # {player_id: {"x", "y", "timer"}} 倒地玩家信息
        self._rescuing = False            # 是否正在救援（读条中）
        self._rescue_target: int | None = None  # 正在救援的玩家 id
        self._rescue_timer = 0.0          # 救援读条计时器
        self._rescue_progress = 0.0       # 救援进度 0~1（渲染用）
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
        # 精灵列表（wall_list/obstacle_list 启用空间哈希，碰撞检测从 O(P×W) 降至哈希查找）
        self.wall_list = arcade.SpriteList(use_spatial_hash=True)
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
        self.active_boss = None  # 当前激活的 BOSS 实例（仅在玩家进入 BOSS 房间/火箭台激活时设置，控制 HP 血条显示）
        self._boss_instance = None  # BOSS 实例引用（setup 时创建，用于房间锁定判定，不触发 HP 血条）
        self._boss_door_sprite = None  # BOSS 房间门洞遮挡精灵（BOSS 存活时显示，死亡后移除）
        self._boss_room_locked = False  # BOSS 房间是否已锁定（玩家无法离开）
        # BOSS 介绍向导弹窗（进入 BOSS 房间时首次弹出，展示 4 页说明）

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
        # ── 管理器实例（Phase 1 拆分）──
        self.net_sync = NetworkSyncManager(self)
        self.spectate = SpectateManager(self)
        self.pickup_loot = PickupLootManager(self)
        self.evac_manager = EvacManager(self)

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

        # 墙壁精灵（参与碰撞，启用空间哈希加速碰撞查询）
        self.wall_list = arcade.SpriteList(use_spatial_hash=True)
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
                self._boss_instance = boss  # 保存 BOSS 引用（不设置 active_boss，进入房间时才激活血条）

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
        """委托 → net_sync._broadcast_damage"""
        return self.net_sync._broadcast_damage(self, monster, damage, hit, crit, debuffs)

    def _resolve_attack_event(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._resolve_attack_event"""
        return self.net_sync._resolve_attack_event(self, sender_id, payload)

    def _resolve_skill_use(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._resolve_skill_use"""
        return self.net_sync._resolve_skill_use(self, sender_id, payload)

    def _apply_damage_result(self, payload: dict) -> None:
        """委托 → net_sync._apply_damage_result"""
        return self.net_sync._apply_damage_result(self, payload)

    def _ensure_ghost(self, player_id: int) -> Player:
        """委托 → net_sync._ensure_ghost"""
        return self.net_sync._ensure_ghost(self, player_id)

    def _broadcast_player_hurt(self, player_id: int, damage: float,
                               debuff=None, debuff_level: int = 1,
                               debuffs: list = None) -> None:
        """委托 → net_sync._broadcast_player_hurt"""
        return self.net_sync._broadcast_player_hurt(self, player_id, damage, debuff, debuff_level, debuffs)

    def _serialize_players(self) -> list:
        """委托 → net_sync._serialize_players"""
        return self.net_sync._serialize_players(self)

    def _apply_player_hurt(self, payload: dict) -> None:
        """委托 → net_sync._apply_player_hurt"""
        return self.net_sync._apply_player_hurt(self, payload)

    def _apply_player_snapshot(self, payload) -> None:
        """委托 → net_sync._apply_player_snapshot"""
        return self.net_sync._apply_player_snapshot(self, payload)

    def _send_player_snapshot(self) -> None:
        """委托 → net_sync._send_player_snapshot"""
        return self.net_sync._send_player_snapshot(self)

    def _apply_client_snapshot(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._apply_client_snapshot"""
        return self.net_sync._apply_client_snapshot(self, sender_id, payload)

    def _broadcast_room_ended(self, reason: str, close_room: bool = False) -> None:
        """委托 → net_sync._broadcast_room_ended"""
        return self.net_sync._broadcast_room_ended(self, reason, close_room)

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
        """委托 → net_sync._apply_room_ended"""
        return self.net_sync._apply_room_ended(self, payload)

    def _handle_potion_use(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._handle_potion_use"""
        return self.net_sync._handle_potion_use(self, sender_id, payload)

    def _reject_potion(self, sender_id: int, potion_id: str) -> None:
        """广播药水使用被拒（库存不足/无效药水）：客户端保持原状"""
        gs = self.window.game_state
        gs.net_server.broadcast(MsgType.POTION_ACK, {
            "player_id": sender_id, "potion_id": potion_id,
            "accepted": False, "heal_amount": 0.0,
        })

    def _apply_potion_ack(self, payload: dict) -> None:
        """委托 → net_sync._apply_potion_ack"""
        return self.net_sync._apply_potion_ack(self, payload)

    def _record_player_pickup(self, player_id: int, drop: DropItem) -> None:
        """委托 → pickup_loot._record_player_pickup"""
        return self.pickup_loot._record_player_pickup(self, player_id, drop)

    def _handle_pickup_request(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._handle_pickup_request"""
        return self.net_sync._handle_pickup_request(self, sender_id, payload)

    def _handle_evac_request(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._handle_evac_request"""
        return self.net_sync._handle_evac_request(self, sender_id, payload)

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
        """委托 → net_sync._apply_map_change"""
        return self.net_sync._apply_map_change(self, payload)

    def _broadcast_map_changes(self) -> None:
        """委托 → net_sync._broadcast_map_changes"""
        return self.net_sync._broadcast_map_changes(self)

    def _broadcast_env_damage(self, obj_id: int, damage: float) -> None:
        """委托 → net_sync._broadcast_env_damage"""
        return self.net_sync._broadcast_env_damage(self, obj_id, damage)

    def _broadcast_env_spawn(self, obj_id: int, x: float, y: float, resource_type: str) -> None:
        """委托 → net_sync._broadcast_env_spawn"""
        return self.net_sync._broadcast_env_spawn(self, obj_id, x, y, resource_type)

    def _apply_pickup_result(self, payload: dict) -> None:
        """委托 → net_sync._apply_pickup_result"""
        return self.net_sync._apply_pickup_result(self, payload)

    def _apply_evac_result(self, payload: dict) -> None:
        """委托 → net_sync._apply_evac_result"""
        return self.net_sync._apply_evac_result(self, payload)

    def _remove_drop_visual(self, net_id) -> None:
        """按网络 id 从本地视觉掉落列表移除（幂等；不存在则跳过）"""
        if not net_id:
            return
        for d in self.drops[:]:
            if d.net_id == net_id:
                self.drops.remove(d)
                break

    def _apply_player_death(self, payload: dict) -> None:
        """委托 → net_sync._apply_player_death"""
        return self.net_sync._apply_player_death(self, payload)

    # ── 倒地/救援系统 ──

    def _player_downed(self) -> None:
        """委托 → spectate._player_downed"""
        return self.spectate._player_downed(self)

    def _apply_player_downed(self, payload: dict) -> None:
        """委托 → net_sync._apply_player_downed"""
        return self.net_sync._apply_player_downed(self, payload)

    def _apply_rescue_result(self, payload: dict) -> None:
        """委托 → net_sync._apply_rescue_result"""
        return self.net_sync._apply_rescue_result(self, payload)

    def _apply_player_revived(self, payload: dict) -> None:
        """委托 → net_sync._apply_player_revived"""
        return self.net_sync._apply_player_revived(self, payload)

    def _apply_spectate_leave(self, payload: dict) -> None:
        """主机收到 SPECTATE_LEAVE：玩家主动退出观战 → 视为真死，清装备"""
        gs = self.window.game_state
        player_id = payload.get("player_id")
        if player_id == 0:
            # 主机自己退出观战：清装备 + 观战
            self._clear_run_equipment(gs)
            self._enter_spectate("dead")
        else:
            # 客户端退出观战：标记真死
            self._downed_players.pop(player_id, None)
            self._player_status[player_id] = "dead"
            # 通知该客户端真死
            gs.net_server.send_to(player_id, MsgType.PLAYER_DEATH, {
                "player_id": player_id,
                "killer_id": None,
            })

    def _handle_rescue_request(self, sender_id: int, payload: dict) -> None:
        """主机处理 RESCUE_REQUEST：裁决距离并执行救援"""
        gs = self.window.game_state
        rescuer_id = payload.get("rescuer_id")
        target_id = payload.get("target_id")
        # 校验：被救者必须处于倒地状态
        if self._player_status.get(target_id) != "downed":
            return
        # 校验：救援者必须存活
        if self._player_status.get(rescuer_id) != "alive":
            return
        # 获取被救者位置
        downed_info = self._downed_players.get(target_id)
        if downed_info is None:
            return
        target_x, target_y = downed_info["x"], downed_info["y"]
        # 获取救援者位置
        if rescuer_id == 0:
            rescuer_x, rescuer_y = self.player.center_x, self.player.center_y
        else:
            ghost = self.remote_players.get(rescuer_id)
            if ghost is None:
                return
            rescuer_x, rescuer_y = ghost.center_x, ghost.center_y
        # 距离校验
        dist = math.hypot(rescuer_x - target_x, rescuer_y - target_y)
        if dist > RESCUE_DISTANCE:
            # 距离太远：发送失败结果
            gs.net_server.send_to(rescuer_id, MsgType.RESCUE_RESULT, {
                "target_id": target_id, "rescuer_id": rescuer_id,
                "success": False, "hp": 0,
            })
            return
        # 执行救援：被救者 HP 恢复为 REVIVE_HP
        del self._downed_players[target_id]
        self._player_status[target_id] = "alive"
        # 广播救援成功
        gs.net_server.broadcast(MsgType.RESCUE_RESULT, {
            "target_id": target_id, "rescuer_id": rescuer_id,
            "success": True, "hp": REVIVE_HP,
        })
        gs.net_server.broadcast(MsgType.PLAYER_REVIVED, {
            "player_id": target_id, "hp": REVIVE_HP,
        })
        # 如果被救者是幽灵：恢复其 HP
        ghost = self.remote_players.get(target_id)
        if ghost is not None:
            ghost.hp = REVIVE_HP
            ghost.downed = False
        print(f"[GameView] 玩家 {rescuer_id} 成功救援玩家 {target_id}，HP 恢复为 {REVIVE_HP}")

    def _try_rescue(self) -> None:
        """本地玩家尝试救援附近的倒地队友（E 键触发）"""
        gs = self.window.game_state
        if gs.net_mode == "solo" or self._spectating:
            return
        if self.player.downed:
            return  # 倒地玩家不能救援
        # 查找最近的倒地玩家
        nearest_id = None
        nearest_dist = float("inf")
        for pid, dp in self._downed_players.items():
            if pid == 0 and gs.net_mode == "host":
                continue  # 主机不救援自己
            if pid == getattr(gs, "net_player_id", None):
                continue  # 不救援自己
            dist = math.hypot(
                self.player.center_x - dp["x"],
                self.player.center_y - dp["y"],
            )
            if dist < RESCUE_DISTANCE and dist < nearest_dist:
                nearest_dist = dist
                nearest_id = pid
        if nearest_id is None:
            return  # 附近没有倒地玩家
        # 开始救援读条
        self._rescuing = True
        self._rescue_target = nearest_id
        self._rescue_timer = 0.0
        self._rescue_progress = 0.0

    def _update_rescue(self, dt: float) -> None:
        """委托 → spectate._update_rescue"""
        return self.spectate._update_rescue(self, dt)

    def _on_monster_death(self, monster):
        """怪物死亡回调 - 委托给 entity_callbacks"""
        on_monster_death(self, monster)

    def _handle_harvestable_combat(self, dt):
        """委托 → pickup_loot._handle_harvestable_combat"""
        return self.pickup_loot._handle_harvestable_combat(self, dt)

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
        """委托 → pickup_loot._handle_chest_interaction"""
        return self.pickup_loot._handle_chest_interaction(self)

    def _handle_well_interaction(self):
        """委托 → pickup_loot._handle_well_interaction"""
        return self.pickup_loot._handle_well_interaction(self)

    def _handle_rocket_pad_interaction(self):
        """委托 → pickup_loot._handle_rocket_pad_interaction"""
        return self.pickup_loot._handle_rocket_pad_interaction(self)

    def _send_client_interaction_request(self, gs) -> None:
        """委托 → pickup_loot._send_client_interaction_request"""
        return self.pickup_loot._send_client_interaction_request(self, gs)

    def _handle_interaction_request(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._handle_interaction_request"""
        return self.net_sync._handle_interaction_request(self, sender_id, payload)

    def _handle_player_abandon(self, sender_id: int, payload: dict) -> None:
        """委托 → net_sync._handle_player_abandon"""
        return self.net_sync._handle_player_abandon(self, sender_id, payload)

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
        """委托 → net_sync._serialize_monsters"""
        return self.net_sync._serialize_monsters(self)

    def _serialize_projectiles(self) -> dict:
        """委托 → net_sync._serialize_projectiles"""
        return self.net_sync._serialize_projectiles(self)

    def _apply_monster_snapshot(self, payload) -> None:
        """委托 → net_sync._apply_monster_snapshot"""
        return self.net_sync._apply_monster_snapshot(self, payload)

    def _apply_projectile_snapshot(self, payload) -> None:
        """委托 → net_sync._apply_projectile_snapshot"""
        return self.net_sync._apply_projectile_snapshot(self, payload)

    def _serialize_full_state(self) -> dict:
        """委托 → net_sync._serialize_full_state"""
        return self.net_sync._serialize_full_state(self)

    def _apply_full_state(self, payload) -> None:
        """委托 → net_sync._apply_full_state"""
        return self.net_sync._apply_full_state(self, payload)

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
        # 新手教程（阶段 4）：游戏内引导横幅 + BOSS 介绍弹窗（HUD 逻辑坐标系下绘制）
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active and tut.stage == 3:
            from views.tutorial import draw_in_game_tutorial
            self.window.default_camera.use()
            draw_in_game_tutorial(self, self._tut_tc)

    def _apply_free_equip(self, d):
        """委托 → pickup_loot._apply_free_equip"""
        return self.pickup_loot._apply_free_equip(self, d)

    def _fold_run_potions(self, gs) -> None:
        """委托 → evac_manager._fold_run_potions"""
        return self.evac_manager._fold_run_potions(self, gs)

    def _add_equipped_to_carried(self, gs):
        """委托 → pickup_loot._add_equipped_to_carried"""
        return self.pickup_loot._add_equipped_to_carried(self, gs)

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
                elif msg_type == MsgType.PLAYER_DOWNED:
                    # 主机广播某玩家倒地：标记倒地状态（渲染用）
                    self._apply_player_downed(payload)
                elif msg_type == MsgType.RESCUE_RESULT:
                    # 主机广播救援结果：成功恢复 HP / 失败保持倒地
                    self._apply_rescue_result(payload)
                elif msg_type == MsgType.PLAYER_REVIVED:
                    # 主机广播玩家复活成功：恢复该玩家实体
                    self._apply_player_revived(payload)
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
                elif inbound.get("msg_type") == MsgType.RESCUE_REQUEST.name:
                    # 客户端请求救援倒地玩家：主机裁决距离并执行救援
                    self._handle_rescue_request(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.SPECTATE_LEAVE.name:
                    # 客户端主动退出观战：视为真死，清装备
                    self._apply_spectate_leave(inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.PLAYER_SNAPSHOT.name:
                    # 客户端 20Hz 上报本人实体：主机据此更新对应幽灵的位置/朝向/存活（Todo 23）
                    self._apply_client_snapshot(sender_id, inbound.get("payload") or {})
                elif inbound.get("msg_type") == MsgType.HEARTBEAT.name:
                    # 心跳回显：原样回给该客户端，客户端据此计算 RTT（保活同理）
                    gs.net_server.send_to(sender_id, MsgType.HEARTBEAT, inbound.get("payload") or {})
                # 其他客户端消息：确保该玩家幽灵已创建（多目标 AI 与 PLAYER_SNAPSHOT 需要）
                self._ensure_ghost(sender_id)

            # 主机权威幽灵倒地检测：HP 归零 → 广播 PLAYER_DOWNED（可被救援），
            # 超时未被救则发 PLAYER_DEATH（真死）；幽灵标记由 PLAYER_SNAPSHOT alive=False 下发
            for pid, ghost in list(self.remote_players.items()):
                # 已撤离/离开的玩家不是死亡：其幽灵 alive=False 是撤离观战所致
                # （_send_player_snapshot 观战时上报 alive=False），不得误发倒地/死亡通知
                if self._player_status.get(pid) in ("evac", "left", "dead"):
                    continue
                if not getattr(ghost, "alive", True):
                    # HP=0 但尚未通知过倒地 → 发送倒地通知
                    if not getattr(ghost, "_downed_notified", False):
                        ghost._downed_notified = True
                        self._player_status[pid] = "downed"
                        self._downed_players[pid] = {
                            "x": ghost.center_x, "y": ghost.center_y,
                            "timer": DOWNED_TIMEOUT,
                        }
                        gs.net_server.send_to(pid, MsgType.PLAYER_DOWNED, {
                            "player_id": pid,
                            "x": ghost.center_x, "y": ghost.center_y,
                        })
                        # 广播给其他客户端（显示倒地标记）
                        gs.net_server.broadcast(MsgType.PLAYER_DOWNED, {
                            "player_id": pid,
                            "x": ghost.center_x, "y": ghost.center_y,
                        }, exclude=pid)
                        print(f"[GameView] 玩家 {pid} 倒地，等待救援（{DOWNED_TIMEOUT}秒超时）")
                    # 已倒地：递减超时计时器，超时则真死
                    elif pid in self._downed_players:
                        dp = self._downed_players[pid]
                        dp["timer"] -= dt
                        if dp["timer"] <= 0:
                            # 超时真死：发 PLAYER_DEATH 给该玩家
                            del self._downed_players[pid]
                            self._player_status[pid] = "dead"
                            gs.net_server.send_to(pid, MsgType.PLAYER_DEATH, {
                                "player_id": pid,
                                "killer_id": None,
                            })
                            print(f"[GameView] 玩家 {pid} 倒地超时，已阵亡")

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
            # 联机模式：进入倒地状态（等待救援），非联机直接失败
            if gs.net_mode in ("host", "client"):
                self._player_downed()
            else:
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

        # 倒地/救援系统更新（联机模式）
        if gs.net_mode in ("host", "client"):
            # 更新倒地玩家计时器（远程玩家倒地超时）
            for pid in list(self._downed_players.keys()):
                dp = self._downed_players[pid]
                dp["timer"] -= dt
                if dp["timer"] <= 0:
                    # 超时真死
                    del self._downed_players[pid]
                    self._player_status[pid] = "dead"
                    if gs.net_mode == "host":
                        gs.net_server.send_to(pid, MsgType.PLAYER_DEATH, {
                            "player_id": pid, "killer_id": None,
                        })
                    print(f"[GameView] 玩家 {pid} 倒地超时，已阵亡")
            # 更新救援读条
            self._update_rescue(dt)

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

        # BOSS 房间锁定逻辑
        boss_rect = self.map_data.get("boss_rect")
        boss = self._boss_instance or self.active_boss
        if boss_rect and boss and boss.alive:
            # 检查玩家是否在 BOSS 房间内（含 1 块瓦片缓冲）
            in_boss_room = (boss_rect[0] - TILE_SIZE <= self.player.center_x <= boss_rect[2] + TILE_SIZE and
                           boss_rect[1] - TILE_SIZE <= self.player.center_y <= boss_rect[3] + TILE_SIZE)
            if in_boss_room and not self._boss_room_locked:
                # 检查玩家是否在门洞区域（避免在门洞处创建门块导致卡墙）
                boss_door = self.map_data.get("boss_door")
                in_door_gap = False
                if boss_door:
                    door_x = boss_door["x"]
                    door_y = boss_door["y"]
                    door_w = boss_door["width"]
                    door_h = TILE_SIZE
                    # 检查玩家是否在门块区域内
                    if (door_x - door_w / 2 <= self.player.center_x <= door_x + door_w / 2 and
                        door_y - door_h / 2 <= self.player.center_y <= door_y + door_h / 2):
                        in_door_gap = True
                # 只有玩家不在门洞区域时才锁定门洞
                if not in_door_gap:
                    # 进入 BOSS 房间：锁定门洞 + 激活 BOSS 血条
                    self._boss_room_locked = True
                    self.active_boss = boss  # 玩家进入 BOSS 房间才激活屏幕顶部 BOSS 血条
                    self._create_boss_door_block()
            elif not in_boss_room and self._boss_room_locked:
                # 尝试离开 BOSS 房间：推回边界
                # 计算最近的边界点并推回
                push_x = max(boss_rect[0] - TILE_SIZE, min(self.player.center_x, boss_rect[2] + TILE_SIZE))
                push_y = max(boss_rect[1] - TILE_SIZE, min(self.player.center_y, boss_rect[3] + TILE_SIZE))
                self.player.center_x = push_x
                self.player.center_y = push_y
        elif self._boss_room_locked and (not boss or not boss.alive):
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
        """委托 → evac_manager._clear_run_equipment"""
        return self.evac_manager._clear_run_equipment(self, gs)

    def _fail_run(self, reason: str):
        """委托 → evac_manager._fail_run"""
        return self.evac_manager._fail_run(self, reason)

    def _enter_spectate(self, outcome: str) -> None:
        """委托 → spectate._enter_spectate"""
        return self.spectate._enter_spectate(self, outcome)

    def _cycle_spectate_target(self) -> None:
        """委托 → spectate._cycle_spectate_target"""
        return self.spectate._cycle_spectate_target(self)

    def _spectate_camera_target(self):
        """委托 → spectate._spectate_camera_target"""
        return self.spectate._spectate_camera_target(self)

    def _check_all_finished(self) -> bool:
        """委托 → spectate._check_all_finished"""
        return self.spectate._check_all_finished(self)

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
