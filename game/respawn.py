"""资源和怪物刷新逻辑"""

import math
import random
from config import TILE_SIZE, MONSTER_GEAR_LEVEL_RANGE, MONSTER_WEAPON_LEVEL_RANGE, MONSTER_SPAWN_MIN_DIST, HARVEST_SPAWN_MIN_DIST, EVAC_WAVE_HP_GROWTH, EVAC_WAVE_DAMAGE_GROWTH, EVAC_WAVE_SPEED_GROWTH, EVAC_WAVE_SPEED_CAP, EVAC_WAVE_SPAWN_MIN_DIST, EVAC_WAVE_SPAWN_MAX_DIST, EVAC_WAVE_SPAWN_MIN_PLAYER_DIST, ELITE_SPAWN_MIN_DIST, ELITE_MAX_ALIVE
from game.harvestable import HarvestableEntity
from game.monsters import Zombie, Skeleton, MummyMelee, MummyRanged, Camel, Sniper, Assault, Bandit, RocketTroop
from game.monster_utils import assign_monster_armor, assign_monster_helmet, assign_monster_weapon
from entities.monster_defs import MONSTER_COMPOSITIONS, ELITE_CONFIG

# 主题对应的资源类型池（沙漠多仙人掌）
THEME_HARVEST_TYPES = {
    "forest": ["tree", "ore", "stone"],
    "desert": ["cactus", "tree", "ore", "stone"],
}
# 主题对应的怪物类型池（沙漠混入木乃伊与骆驼；BOSS 由 T7 单独生成，不参与刷新）
THEME_MONSTER_TYPES = {
    "forest": ["zombie", "skeleton"],
    "desert": ["mummy_melee", "mummy_ranged", "camel", "zombie", "skeleton"],
    "space": ["sniper", "assault", "bandit", "rocket_troop"],
}

# 主题怪物类型名（小写标识）到类名的映射：THEME_MONSTER_TYPES 用小写，
# MONSTER_CLASS_MAP 用类名键，spawn_wave 借这张表按主题随机取怪
THEME_MONSTER_CLASS_NAMES = {
    "zombie": "Zombie",
    "skeleton": "Skeleton",
    "mummy_melee": "MummyMelee",
    "mummy_ranged": "MummyRanged",
    "camel": "Camel",
    "sniper": "Sniper",
    "assault": "Assault",
    "bandit": "Bandit",
    "rocket_troop": "RocketTroop",
}

# 怪物类名到实例化函数的映射
MONSTER_CLASS_MAP = {
    "Zombie": Zombie,
    "Skeleton": Skeleton,
    "MummyMelee": MummyMelee,
    "MummyRanged": MummyRanged,
    "Camel": Camel,
    "Sniper": Sniper,
    "Assault": Assault,
    "Bandit": Bandit,
    "RocketTroop": RocketTroop,
}


def _in_excluded_zone(x, y, map_data):
    """判断坐标是否位于排除区域（金字塔 BOSS 建筑/水井附近），避免刷新物落入特殊区域"""
    boss_spawn = map_data.get("boss_spawn")
    if boss_spawn:
        # BOSS 建筑区域（约 5 块瓦片大小）内不刷新
        if abs(x - boss_spawn[0]) < TILE_SIZE * 3 and abs(y - boss_spawn[1]) < TILE_SIZE * 3:
            return True
    well = map_data.get("water_well")
    if well:
        # 水井周围（含 3 个木乃伊守卫）不刷新
        if abs(x - well[0]) < 150 and abs(y - well[1]) < 150:
            return True
    return False


def _find_valid_spawn_position(view, walls, rooms, map_w, map_h, min_dist_from_others=80,
                              ring_center=None, ring_min_dist=0.0, ring_max_dist=0.0,
                              min_player_dist=None):
    """寻找有效的刷新位置（不在排除区域、房间内、墙壁上、与其他实体过近）

    ring_center/ring_min_dist/ring_max_dist：可选「外围环带」约束——以 ring_center 为圆心，
    只接受落在 [ring_min_dist, ring_max_dist] 环带内的候选点（供撤离波次从外围进场）。
    min_player_dist：与玩家的最小距离（像素）。留空时用全局口径 MONSTER_SPAWN_MIN_DIST
    （超出屏幕可视范围，避免野外刷新时贴脸出现）；撤离波次走"环带 + 小间距"口径，
    因为玩家本来就守在撤离点上，仍套 600 会让每波怪步行 9~18 秒才到，45 秒防守期里
    第 3/4 波根本赶不到（用户缺陷⑦修复 2026-09-26）。
    两组环带参数与 min_player_dist 留空时，行为与原来完全一致（随机全图刷新）。
    """
    for _ in range(60):  # 最多尝试60次
        x = random.randint(TILE_SIZE * 3, max(TILE_SIZE * 3 + 1, map_w - TILE_SIZE * 3))
        y = random.randint(TILE_SIZE * 3, max(TILE_SIZE * 3 + 1, map_h - TILE_SIZE * 3))

        # 外围环带约束：必须落在以 ring_center 为圆心的指定环带内
        if ring_center is not None and ring_max_dist > 0:
            ring_dist = math.hypot(x - ring_center[0], y - ring_center[1])
            if ring_dist < ring_min_dist or ring_dist > ring_max_dist:
                continue

        # 不在 BOSS 建筑/水井排除区域
        if _in_excluded_zone(x, y, view.map_data):
            continue
        # 不在房间内
        in_room = False
        for room in rooms:
            if (room.x - TILE_SIZE <= x <= room.x + room.w + TILE_SIZE and
                    room.y - TILE_SIZE <= y <= room.y + room.h + TILE_SIZE):
                in_room = True
                break
        if in_room:
            continue
        # 不与墙壁重合
        on_wall = False
        for wx, wy, ww, wh in walls:
            if wx <= x <= wx + ww and wy <= y <= wy + wh:
                on_wall = True
                break
        if on_wall:
            continue
        # 不与现有怪物/玩家/资源过近（欧几里得距离，避免在玩家身边二次刷新）
        too_close = False
        for m in view.monsters:
            if m.alive and abs(m.center_x - x) < min_dist_from_others and abs(m.center_y - y) < min_dist_from_others:
                too_close = True
                break
        if too_close:
            continue
        # 怪物刷新需与玩家保持最小距离（超出屏幕可视范围；
        # 撤离波次传 EVAC_WAVE_SPAWN_MIN_PLAYER_DIST 走"环带内小间距"口径，见函数 docstring）
        player_min_dist = (MONSTER_SPAWN_MIN_DIST if min_player_dist is None
                           else min_player_dist)
        if math.hypot(view.player.center_x - x, view.player.center_y - y) < player_min_dist:
            continue
        
        return x, y
    return None, None


def respawn_harvestables(view, dt):
    """资源随机刷新：被砍光后地图会长期空荡，这里每隔一段时间在空地补刷，
    保证地图上始终有可采集资源（上限 _harvest_cap）。"""
    view._harvest_respawn_timer += dt
    if view._harvest_respawn_timer < view._harvest_respawn_interval:
        return
    view._harvest_respawn_timer = 0.0

    # 修复：不再物理移除 dead 占位对象。联机下压缩列表（列表推导剔除 hp<=0）
    # 会导致主机/客户端 harvestables 列表长度不同、env_objects 序号漂移，
    # 后续 env_destroyed/env_damage 的 obj_id（列表序号）两端对不上。
    # dead 占位由渲染层（h.alive 过滤）与 sync_obstacles（只加存活）忽略，
    # 数量上限由 alive_count（统计存活）控制，占位不会影响刷新逻辑。
    alive_count = sum(1 for h in view.harvestables if h.alive)
    if alive_count >= view._harvest_cap:
        return

    rooms = view.map_data.get("rooms", [])
    walls = view.map_data.get("walls", [])
    map_w, map_h = view.map_data.get("map_size", (0, 0))
    # 按主题选择资源类型池
    theme = view.map_data.get("theme", "forest")
    types = THEME_HARVEST_TYPES.get(theme, THEME_HARVEST_TYPES["forest"])
    need = min(view._harvest_respawn_batch, view._harvest_cap - alive_count)
    attempts = 0
    spawned = 0
    while spawned < need and attempts < 60:
        attempts += 1
        x = random.randint(TILE_SIZE * 3, max(TILE_SIZE * 3 + 1, map_w - TILE_SIZE * 3))
        y = random.randint(TILE_SIZE * 3, max(TILE_SIZE * 3 + 1, map_h - TILE_SIZE * 3))
        # 不在 BOSS 建筑/水井排除区域
        if _in_excluded_zone(x, y, view.map_data):
            continue
        # 不在房间内
        in_room = False
        for room in rooms:
            if (room.x - TILE_SIZE <= x <= room.x + room.w + TILE_SIZE and
                    room.y - TILE_SIZE <= y <= room.y + room.h + TILE_SIZE):
                in_room = True
                break
        if in_room:
            continue
        # 不与墙壁重合
        on_wall = False
        for wx, wy, ww, wh in walls:
            if wx <= x <= wx + ww and wy <= y <= wy + wh:
                on_wall = True
                break
        if on_wall:
            continue
        # 不与现有资源/玩家过近（欧几里得距离，避免在玩家身边二次刷新）
        too_close = False
        for h in view.harvestables:
            if h.alive and abs(h.center_x - x) < 50 and abs(h.center_y - y) < 50:
                too_close = True
                break
        if too_close:
            continue
        # 环境物刷新需与玩家保持最小距离（超出屏幕可视范围）
        if math.hypot(view.player.center_x - x, view.player.center_y - y) < HARVEST_SPAWN_MIN_DIST:
            continue
        h = HarvestableEntity(x, y, random.choice(types))
        view.harvestables.append(h)
        view.obstacle_list.append(h)
        # 联机主机：广播新刷资源（修复「二次刷新资源客户端不可见」）——
        # 客户端据此在本地追加 HarvestableEntity（obj_id=列表序号，与
        # env_destroyed/env_damage 同口径）；solo 模式 view 无该方法，跳过。
        _broadcast_env_spawn = getattr(view, "_broadcast_env_spawn", None)
        if _broadcast_env_spawn is not None:
            _broadcast_env_spawn(len(view.harvestables) - 1, h.center_x, h.center_y, h.resource_type)
        spawned += 1


def _spawn_monster_group(view, composition, theme, walls, rooms, map_w, map_h):
    """根据组合配置刷新一组怪物
    
    Args:
        view: 游戏视图
        composition: 组合配置 {"type": str, "monsters": [(类名, 最小数量, 最大数量), ...]}
        theme: 地图主题
        walls: 墙壁列表
        rooms: 房间列表
        map_w, map_h: 地图尺寸
    
    Returns:
        list: 生成的怪物列表
    """
    spawned_monsters = []
    
    for monster_type, min_count, max_count in composition["monsters"]:
        count = random.randint(min_count, max_count)
        
        for _ in range(count):
            # 寻找有效刷新位置
            x, y = _find_valid_spawn_position(view, walls, rooms, map_w, map_h)
            if x is None:
                continue
            
            # 实例化怪物
            monster_class = MONSTER_CLASS_MAP.get(monster_type)
            if not monster_class:
                continue
            
            m = monster_class(center_x=x, center_y=y)
            m.set_on_death(view._on_monster_death)
            m._walls = walls
            # 注入建筑查询/伤害回调（怪物不持有 GameView 引用）
            view.build_system.attach_monster(m)
            
            # 分配装备
            assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
            assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
            assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)
            
            view.monsters.append(m)
            spawned_monsters.append(m)
    
    return spawned_monsters


def respawn_monsters(view, dt):
    """野外怪物刷新：按组合刷新，保证地图上始终有怪物（上限 _monster_cap）。"""
    view._monster_respawn_timer += dt
    if view._monster_respawn_timer < view._monster_respawn_interval:
        return
    view._monster_respawn_timer = 0.0

    # 清理已死亡的怪物对象
    dead = [m for m in view.monsters if not m.alive]
    if dead:
        view.monsters = [m for m in view.monsters if m.alive]

    alive_count = len(view.monsters)
    if alive_count >= view._monster_cap:
        return

    rooms = view.map_data.get("rooms", [])
    walls = view.map_data.get("walls", [])
    map_w, map_h = view.map_data.get("map_size", (0, 0))
    theme = view.map_data.get("theme", "forest")
    
    # 获取当前主题的组合配置
    compositions = MONSTER_COMPOSITIONS.get(theme, MONSTER_COMPOSITIONS["forest"])
    
    # 计算需要刷新的怪物数量
    need = min(view._monster_respawn_batch, view._monster_cap - alive_count)
    
    # 按权重选择组合类型
    total_weight = sum(comp["weight"] for comp in compositions)
    r = random.uniform(0, total_weight)
    cumulative = 0
    selected_composition = compositions[0]
    for comp in compositions:
        cumulative += comp["weight"]
        if r <= cumulative:
            selected_composition = comp
            break
    
    # 刷新怪物组合
    _spawn_monster_group(view, selected_composition, theme, walls, rooms, map_w, map_h)


def spawn_wave(view, theme, origin_xy, count, aggro_xy=None):
    """生成一波进攻撤离点的怪物（阶段 2 防守撤离专用）。

    与野外刷新（respawn_monsters）的区别：
    - 从撤离点「外围环带」刷新（EVAC_WAVE_SPAWN_MIN/MAX_DIST），避免贴脸刷怪，
      怪物会一路推进到撤离点；
    - 每只怪都标记 aggro_point，朝撤离点移动并攻击（见 monster_base._update_evac_aggro）；
    - 波次强度递增：血量/伤害/移速按 wave_index 线性加成，wave_index 单调递增存在
      GameView 上（主机裁决，客户端不自行刷怪）。

    Args:
        view: 游戏视图（提供地图数据、怪物列表、死亡回调、建筑系统）
        theme: 地图主题（forest/desert/space），决定怪物类型池
        origin_xy: 撤离点坐标 (x, y)，作为刷新区中心与进攻目标
        count: 本波怪物数量
        aggro_xy: 进攻目标坐标；默认与 origin_xy 相同

    Returns:
        list: 本次生成的怪物列表
    """
    spawned_monsters = []
    if count <= 0:
        return spawned_monsters

    walls = view.map_data.get("walls", [])
    rooms = view.map_data.get("rooms", [])
    map_w, map_h = view.map_data.get("map_size", (0, 0))

    # 波次序号单调递增（存于 GameView，随地图重开重置）
    wave_index = int(getattr(view, "evac_wave_index", 0)) + 1
    view.evac_wave_index = wave_index
    # 强度加成：第 n 波按 (n-1) 线性叠加，移速有上限
    steps = wave_index - 1
    hp_mult = 1.0 + EVAC_WAVE_HP_GROWTH * steps
    dmg_mult = 1.0 + EVAC_WAVE_DAMAGE_GROWTH * steps
    spd_mult = min(EVAC_WAVE_SPEED_CAP, 1.0 + EVAC_WAVE_SPEED_GROWTH * steps)

    # 主题怪物池（不含 BOSS，BOSS 由火箭发射台/金字塔单独生成）
    type_pool = THEME_MONSTER_TYPES.get(theme) or THEME_MONSTER_TYPES["forest"]
    target_xy = aggro_xy if aggro_xy is not None else origin_xy

    for _ in range(count):
        # 外围环带找位：离撤离点足够远（不会凭空出现在点旁边），
        # 且与玩家的最小距离单独收紧（玩家守在撤离点上时仍能按期抵达）
        x, y = _find_valid_spawn_position(
            view, walls, rooms, map_w, map_h,
            ring_center=origin_xy,
            ring_min_dist=EVAC_WAVE_SPAWN_MIN_DIST,
            ring_max_dist=EVAC_WAVE_SPAWN_MAX_DIST,
            min_player_dist=EVAC_WAVE_SPAWN_MIN_PLAYER_DIST,
        )
        if x is None:
            continue

        class_name = THEME_MONSTER_CLASS_NAMES.get(random.choice(type_pool))
        monster_class = MONSTER_CLASS_MAP.get(class_name) if class_name else None
        if not monster_class:
            continue

        m = monster_class(center_x=x, center_y=y)
        m.set_on_death(view._on_monster_death)
        m._walls = walls
        # 注入建筑查询/伤害回调（怪物不持有 GameView 引用）
        view.build_system.attach_monster(m)

        # 分配装备（沿用野外刷新口径）
        assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
        assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
        assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)

        # 波次强度加成（血量上限与当前值同步放大，保持满血进场）
        m.max_hp = int(m.max_hp * hp_mult)
        m.hp = m.max_hp
        m.damage = m.damage * dmg_mult
        m.speed = m.speed * spd_mult

        # 标记进攻目标：撤离点引用经 GameView 注入（怪物不持有 GameView）
        m.aggro_point = tuple(target_xy)
        m.set_evac_point_provider(getattr(view, "get_evac_point", None))

        view.monsters.append(m)
        spawned_monsters.append(m)

    return spawned_monsters


def spawn_minion_group(view, kind, count, origin_xy, spread=30):
    """在 origin_xy 周围按 spread 半径散布生成 count 只指定类型小怪。

    供阶段 3 精英词缀复用：分裂（死亡时）与召唤（周期）共用同一套生成逻辑，
    死亡回调/墙体/建筑查询/装备分配口径与野外刷新一致（见 _spawn_monster_group）。

    Args:
        view: 游戏视图（提供地图数据、怪物列表、死亡回调、建筑系统）
        kind: 小写类型标识（THEME_MONSTER_CLASS_NAMES 的键，如 "zombie"）
        count: 生成数量
        origin_xy: 中心坐标 (x, y)（精英怪当前位置）
        spread: 散布半径（像素），实际落点为半径内的随机偏移

    Returns:
        list: 本次生成的小怪列表（数量可能少于 count，落点越界/类型非法时跳过）
    """
    spawned = []
    if count <= 0:
        return spawned

    walls = view.map_data.get("walls", [])
    theme = view.map_data.get("theme", "forest")
    class_name = THEME_MONSTER_CLASS_NAMES.get(kind)
    monster_class = MONSTER_CLASS_MAP.get(class_name) if class_name else None
    if not monster_class:
        return spawned

    for _ in range(count):
        # 以 origin 为中心随机角度 + [0, spread] 半径偏移，避免小怪完全重叠
        angle = random.uniform(0, math.tau)
        radius = random.uniform(0, spread)
        x = origin_xy[0] + math.cos(angle) * radius
        y = origin_xy[1] + math.sin(angle) * radius
        # 越界（地图边缘外）跳过
        map_w, map_h = view.map_data.get("map_size", (0, 0))
        if map_w and map_h and not (TILE_SIZE <= x <= map_w - TILE_SIZE
                                   and TILE_SIZE <= y <= map_h - TILE_SIZE):
            continue

        m = monster_class(center_x=x, center_y=y)
        m.set_on_death(view._on_monster_death)
        m._walls = walls
        view.build_system.attach_monster(m)
        # 召唤计数标记：on_affix_update 据此统计场上召唤怪数以执行 summon_max_alive 上限
        m.affix_summoned = True
        assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
        assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
        assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)
        view.monsters.append(m)
        spawned.append(m)

    return spawned


def spawn_elite(view):
    """刷新一只带随机词缀的精英怪（阶段 3）。

    与野外刷新（respawn_monsters）的区别：
    - 强制离玩家至少 ELITE_SPAWN_MIN_DIST（find_valid_spawn_position 自身已保证
      MONSTER_SPAWN_MIN_DIST，此处再用更严格的 min_dist_from_others 复核一次）；
    - 血量 ×ELITE_CONFIG['hp_mult']、伤害 ×ELITE_CONFIG['damage_mult']；
    - 从 ELITE_AFFIXES 随机抽一个词缀交给 apply_affix；
    - 置 is_elite / elite_drop 标记（供掉落加奖与 HUD 标记消费）。

    Returns:
        精英怪实例；找不到有效刷新位置时返回 None
    """
    from game.monster_affixes import apply_affix, roll_affix

    theme = view.map_data.get("theme", "forest")
    walls = view.map_data.get("walls", [])
    rooms = view.map_data.get("rooms", [])
    map_w, map_h = view.map_data.get("map_size", (0, 0))

    # 主题怪池（不含 BOSS，BOSS 由火箭发射台/金字塔单独生成）
    type_pool = THEME_MONSTER_TYPES.get(theme) or THEME_MONSTER_TYPES["forest"]
    class_name = THEME_MONSTER_CLASS_NAMES.get(random.choice(type_pool))
    monster_class = MONSTER_CLASS_MAP.get(class_name) if class_name else None
    if not monster_class:
        return None

    x, y = _find_valid_spawn_position(view, walls, rooms, map_w, map_h,
                                      min_dist_from_others=ELITE_SPAWN_MIN_DIST)
    if x is None:
        return None

    m = monster_class(center_x=x, center_y=y)
    m.set_on_death(view._on_monster_death)
    m._walls = walls
    view.build_system.attach_monster(m)
    # 注入 GameView 引用 provider：词缀的分裂/召唤/火墙需要访问场景
    # （不直接持有视图，与 _evac_point_provider 同一模式）
    m.set_affix_view_provider(lambda v=view: v)

    assign_monster_armor(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
    assign_monster_helmet(m, level=random.randint(*MONSTER_GEAR_LEVEL_RANGE), theme=theme)
    assign_monster_weapon(m, level=random.randint(*MONSTER_WEAPON_LEVEL_RANGE), theme=theme)

    # 精英倍率（血量上限与当前值同步放大，保持满血进场）
    m.max_hp = int(m.max_hp * ELITE_CONFIG["hp_mult"])
    m.hp = m.max_hp
    m.damage = m.damage * ELITE_CONFIG["damage_mult"]

    # 随机词缀 + 标记
    apply_affix(m, roll_affix())
    m.is_elite = True
    m.elite_drop = True

    view.monsters.append(m)
    return m


def update_elite_spawner(view, dt) -> None:
    """精英刷新计时器（阶段 3 定时驱动方，挂在 game_view.on_update 的 host/solo 分支）

    规则：
    - 开局累计满 ELITE_CONFIG['interval'] 秒后首刷，之后同周期刷新；
    - 场上存活精英少于 ELITE_MAX_ALIVE 只才刷（同一时间最多 ELITE_MAX_ALIVE 只）；
    - spawn_elite 内部已做「距玩家足够远」与主题怪池校验，找不到位置时本次跳过，
      计时器照常归零，下个周期重试。
    """
    timer = getattr(view, "_elite_spawn_timer", 0.0) + dt
    if timer < ELITE_CONFIG["interval"]:
        view._elite_spawn_timer = timer
        return
    view._elite_spawn_timer = 0.0
    alive_elites = sum(1 for m in view.monsters
                       if getattr(m, "alive", False) and getattr(m, "is_elite", False))
    if alive_elites >= ELITE_MAX_ALIVE:
        return
    spawn_elite(view)
