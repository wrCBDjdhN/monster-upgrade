"""资源和怪物刷新逻辑"""

import math
import random
from config import TILE_SIZE, MONSTER_GEAR_LEVEL_RANGE, MONSTER_WEAPON_LEVEL_RANGE, MONSTER_SPAWN_MIN_DIST, HARVEST_SPAWN_MIN_DIST
from game.harvestable import HarvestableEntity
from game.monsters import Zombie, Skeleton, MummyMelee, MummyRanged, Camel, Sniper, Assault, Bandit, RocketTroop
from game.monster_utils import assign_monster_armor, assign_monster_helmet, assign_monster_weapon
from entities.monster_defs import MONSTER_COMPOSITIONS

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


def _find_valid_spawn_position(view, walls, rooms, map_w, map_h, min_dist_from_others=80):
    """寻找有效的刷新位置（不在排除区域、房间内、墙壁上、与其他实体过近）"""
    for _ in range(60):  # 最多尝试60次
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
        # 不与现有怪物/玩家/资源过近（欧几里得距离，避免在玩家身边二次刷新）
        too_close = False
        for m in view.monsters:
            if m.alive and abs(m.center_x - x) < min_dist_from_others and abs(m.center_y - y) < min_dist_from_others:
                too_close = True
                break
        if too_close:
            continue
        # 怪物刷新需与玩家保持最小距离（超出屏幕可视范围）
        if math.hypot(view.player.center_x - x, view.player.center_y - y) < MONSTER_SPAWN_MIN_DIST:
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
