"""怪物基类与基础设施 —— 参数化基类 + 碰撞/视线/索敌辅助

本文件为 game/monsters.py 拆分出的基础设施部分：
- _WallGrid：墙体空间索引（碰撞与视线检测复用）
- _can_move_to / _has_line_of_sight / _select_target 等辅助函数
- Projectile：远程弹丸
- _MeleeMonsterBase / _RangedMonsterBase：近战/远程怪物参数化基类（AI 唯一实现处）

数据驱动说明：
- 所有怪物数值（hp/damage/speed/size/color/aggro_range/弹丸参数/debuff_id/boss 标记等）
  统一在 entities/monster_defs.py 的 MONSTER_CONFIGS 中定义，禁止在此文件重复硬编码数值。
- 具体怪物薄类（Zombie/Skeleton 等 13 种）在 game/monsters.py 中定义，
  构造只需 super().__init__(**MONSTER_CONFIGS["类名"]) 从配置取数。

AI 行为：
- 怪物在索敌距离内追踪玩家；近战直接冲向玩家攻击
- 远程怪物保持 120~200 距离，远了靠近、近了后退
- 超出索敌距离则停止追击

碰撞检测：
- 使用 _can_move_to() 进行墙壁碰撞检测
- 怪物不能穿墙，但可以通过门离开房间
"""


import math
from collections import deque

import arcade
from config import (
    MAP_WIDTH, MAP_HEIGHT, TILE_SIZE, PROJECTILE_SIZE, PROJECTILE_LIFETIME,
    MONSTER_AGGRO_RANGE_MULT, MONSTER_AGGRO_RANGE_BASE, PLAYER_SIZE, DEBUFF_TICK_INTERVAL,
    HIT_FLASH_DURATION, RANGED_KEEP_MIN, RANGED_KEEP_MAX, BUILDING_ATTACK_SEARCH_RANGE,
    MONSTER_NAV_CELL_PAD, MONSTER_NAV_REFRESH_INTERVAL, MONSTER_NAV_WAYPOINT_LOOKAHEAD,
    MONSTER_NAV_WAYPOINT_ARRIVE_RATIO, MONSTER_NAV_WAYPOINT_STUCK_FRAMES,
    MONSTER_NAV_STUCK_FRAMES, MONSTER_NAV_MOVE_EPS, MONSTER_NAV_UNSTICK_SEARCH_RING,
)


class _WallGrid:
    """墙体空间索引：按网格分桶，查询时只遍历附近网格内的墙，避免每帧全量遍历

    同一局内所有怪物共享同一份 walls 列表（game_view 统一赋值），
    因此索引只构建一次，配合下方单槽缓存复用。
    """

    def __init__(self, walls, cell=TILE_SIZE * 4):
        self.cell = cell
        self.buckets = {}  # (gx, gy) -> [(wx, wy, ww, wh), ...]
        for wx, wy, ww, wh in walls:
            if ww <= 0 or wh <= 0:
                continue
            x0 = wx // cell
            x1 = (wx + ww) // cell
            y0 = wy // cell
            y1 = (wy + wh) // cell
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    self.buckets.setdefault((gx, gy), []).append((wx, wy, ww, wh))

    def nearby(self, x: float, y: float) -> list:
        """返回位置 (x, y) 所在网格及其 8 邻域内的候选墙列表"""
        gx = int(x // self.cell)
        gy = int(y // self.cell)
        result = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                result.extend(self.buckets.get((gx + dx, gy + dy), ()))
        return result

    def add_rect(self, x: float, y: float, half: float) -> None:
        """将建筑矩形注册到空间索引，供后续 _can_move_to 查询。"""
        if half <= 0:
            return
        rect = (x - half, y - half, half * 2, half * 2)
        wx, wy, ww, wh = rect
        x0 = wx // self.cell
        x1 = (wx + ww) // self.cell
        y0 = wy // self.cell
        y1 = (wy + wh) // self.cell
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                self.buckets.setdefault((gx, gy), []).append(rect)

    def remove_rect(self, x: float, y: float, half: float) -> None:
        """从空间索引注销建筑矩形，避免摧毁后残留碰撞。"""
        if half <= 0:
            return
        rect = (x - half, y - half, half * 2, half * 2)
        wx, wy, ww, wh = rect
        x0 = wx // self.cell
        x1 = (wx + ww) // self.cell
        y0 = wy // self.cell
        y1 = (wy + wh) // self.cell
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                bucket = self.buckets.get((gx, gy))
                if not bucket:
                    continue
                try:
                    bucket.remove(rect)
                except ValueError:
                    pass
                if not bucket:
                    self.buckets.pop((gx, gy), None)


# 墙索引使用小型 LRU：不同调用方短暂持有不同 walls 列表时均能保留建筑注册。
# 缓存值保留 walls 强引用，避免列表释放后 id(walls) 被复用而误命中旧索引。
_WALL_GRID_CACHE_CAPACITY = 4
_wall_grid_cache: dict[int, tuple[list, _WallGrid]] = {}

# _has_line_of_sight 复用容器：避免每帧每怪物分配新 list+set（性能优化，单线程安全）
_los_candidates: list = []
_los_seen: set = set()


def _get_wall_grid(walls: list) -> _WallGrid:
    """获取墙体空间索引；按列表 id 维护容量 4 的 LRU，命中时保持同一索引对象。"""
    cache_key = id(walls)
    cached = _wall_grid_cache.pop(cache_key, None)
    if cached is not None:
        # dict 保持插入序，重新插入即可把最近使用的索引移到 LRU 尾部。
        _wall_grid_cache[cache_key] = cached
        return cached[1]

    if len(_wall_grid_cache) >= _WALL_GRID_CACHE_CAPACITY:
        oldest_key = next(iter(_wall_grid_cache))
        _wall_grid_cache.pop(oldest_key)
    grid = _WallGrid(walls)
    _wall_grid_cache[cache_key] = (walls, grid)
    return grid


def _can_move_to(new_x: float, new_y: float, size: float, walls: list) -> bool:
    """检查怪物能否移动到 (new_x, new_y)，不穿墙，不限制房间（可通过门离开）"""
    half = size
    # 空间索引：只检查目标位置附近网格内的墙（性能优化，行为与原全量遍历一致）
    for wx, wy, ww, wh in _get_wall_grid(walls).nearby(new_x, new_y):
        if (new_x + half > wx and new_x - half < wx + ww and
            new_y + half > wy and new_y - half < wy + wh):
            return False
    # 检查地图边界
    if new_x - half < 0 or new_x + half > MAP_WIDTH:
        return False
    if new_y - half < 0 or new_y + half > MAP_HEIGHT:
        return False
    return True


# ══ BFS 寻路（用户缺陷⑦⑧修复 2026-09-26）═════════════════════════════════════
# 背景：怪物 AI 原本只有「直线逼近 + 分轴滑动」，完全没有路径概念。房间四面墙只开一个门洞，
# 贴墙行进时切向分量≈0 → 永久死锁（缺陷⑦：防守波怪永远打不到撤离点）；
# 追击时凹角处整步与两次单轴探测全部失败 → 位移永久为 0（缺陷⑧：进不了玩家所在的房间）。
# 这里补一层网格 BFS：门就是「墙矩形之间的空隙」，只要门洞格判为可通行，BFS 自动学会绕门，
# 不需要任何门洞元数据。
#
# ⚠ 陷阱（已踩过一次，务必保留本注释）：格边长必须 == TILE_SIZE（64px）。
#   门宽 = TILE_SIZE*2 = 128px；若把格边长放大到 ≥128，门洞宽度不足以容下一整格，
#   门洞格中心必然落在墙矩形覆盖范围内 → 全图判死 → BFS 找不到路径 → 行为退化回"原地卡死"。
_NAV_CELL = TILE_SIZE

# 寻路占位网格缓存：与 _get_wall_grid 同样按列表 id 维护容量 4 的 LRU；
# 缓存值保留 walls 强引用，避免 walls 释放后 id 复用而误命中旧占位图。
_NAV_GRID_CACHE_CAPACITY = 4
_nav_grid_cache: dict[int, tuple[list, "_NavGrid"]] = {}


class _NavGrid:
    """寻路占位网格：按 TILE_SIZE 粒度预计算「格中心是否被静态墙体覆盖」

    - 阻挡判定 = 墙矩形向外膨胀 MONSTER_NAV_CELL_PAD 后是否覆盖格中心（门洞两侧各留出空隙，
      128px 门洞在 pad≤32 时至少保证 1 个格中心可通行）；
    - 只由静态 walls 生成一次并整体缓存；局内建造的建筑不进此网格——建筑碰撞仍由
      _can_move_to 逐帧最终裁决，BFS 只负责绕开静态墙体，不会因此穿墙；
    - 越界列/行（格中心落在地图外）直接判死，与 _can_move_to 的地图边界判定一致。
    """

    def __init__(self, walls: list, cell: int = _NAV_CELL) -> None:
        self.cell = cell
        self.cols = int(MAP_WIDTH // cell) + 1
        self.rows = int(MAP_HEIGHT // cell) + 1
        self.blocked = bytearray(self.cols * self.rows)
        half = cell * 0.5
        pad = MONSTER_NAV_CELL_PAD
        for wx, wy, ww, wh in walls:
            if ww <= 0 or wh <= 0:
                continue
            # 只有膨胀后可能压到格中心的格才需要判定（由格中心反推格号区间）
            gx0 = int(math.floor((wx - pad - half) / cell)) + 1
            gx1 = int(math.floor((wx + ww + pad - half) / cell))
            gy0 = int(math.floor((wy - pad - half) / cell)) + 1
            gy1 = int(math.floor((wy + wh + pad - half) / cell))
            for gx in range(max(0, gx0), min(self.cols - 1, gx1) + 1):
                cx = gx * cell + half
                for gy in range(max(0, gy0), min(self.rows - 1, gy1) + 1):
                    cy = gy * cell + half
                    if (wx - pad < cx < wx + ww + pad and
                            wy - pad < cy < wy + wh + pad):
                        self.blocked[gy * self.cols + gx] = 1
        # 最后一列/行的格中心已越出地图边界，按 _can_move_to 口径判死
        last_gx = self.cols - 1
        last_gy = self.rows - 1
        x_over = last_gx * cell + half > MAP_WIDTH
        y_over = last_gy * cell + half > MAP_HEIGHT
        if x_over:
            for gy in range(self.rows):
                self.blocked[gy * self.cols + last_gx] = 1
        if y_over:
            for gx in range(self.cols):
                self.blocked[last_gy * self.cols + gx] = 1

    def is_free(self, gx: int, gy: int) -> bool:
        """格 (gx, gy) 是否可通行（越界视为不可通行）"""
        if gx < 0 or gy < 0 or gx >= self.cols or gy >= self.rows:
            return False
        return self.blocked[gy * self.cols + gx] == 0

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        """世界坐标 → 格号；越界返回 None"""
        gx = int(x // self.cell)
        gy = int(y // self.cell)
        if gx < 0 or gy < 0 or gx >= self.cols or gy >= self.rows:
            return None
        return gx, gy

    def nearest_free(self, gx: int, gy: int, max_ring: int = 3) -> tuple[int, int] | None:
        """找离 (gx, gy) 最近的空旷格（怪物贴墙时自身格可能被膨胀边距判死），找不到返回 None"""
        if self.is_free(gx, gy):
            return gx, gy
        for ring in range(1, max_ring + 1):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if max(abs(dx), abs(dy)) != ring:
                        continue  # 只扫当前环
                    nx, ny = gx + dx, gy + dy
                    if self.is_free(nx, ny):
                        return nx, ny
        return None


def _get_nav_grid(walls: list) -> _NavGrid:
    """获取寻路占位网格；按列表 id 维护容量 4 的 LRU（与 _get_wall_grid 同模式）。"""
    cache_key = id(walls)
    cached = _nav_grid_cache.pop(cache_key, None)
    if cached is not None:
        _nav_grid_cache[cache_key] = cached
        return cached[1]

    if len(_nav_grid_cache) >= _NAV_GRID_CACHE_CAPACITY:
        _nav_grid_cache.pop(next(iter(_nav_grid_cache)))
    grid = _NavGrid(walls)
    _nav_grid_cache[cache_key] = (walls, grid)
    return grid


def _nav_bfs_path(walls: list, start_x: float, start_y: float,
                  target_x: float, target_y: float) -> list[tuple[float, float]]:
    """在墙体占位网格上做 4 邻域 BFS，返回「隔格取中心」的航点列表

    - 格边长必须 == TILE_SIZE（见上方 _NAV_CELL 注释：格 ≥ 门宽会把门洞整格判死）；
    - 起点/终点格若被膨胀边距判死（怪物贴墙站立时可能发生），取最近空旷格代替；
    - 航点 = 路径上第 MONSTER_NAV_WAYPOINT_LOOKAHEAD 格的中心（去头去尾后从后往前跳），
      逐个跟随可避免逐格锯齿抖动；
    - 无路径时返回空列表，调用方回退「直线 + 分轴滑动」，行为不劣化。
    """
    grid = _get_nav_grid(walls)
    start_cell = grid.cell_of(start_x, start_y)
    goal_cell = grid.cell_of(target_x, target_y)
    if start_cell is None or goal_cell is None:
        return []
    start_free = grid.nearest_free(*start_cell)
    goal_free = grid.nearest_free(*goal_cell)
    if start_free is None or goal_free is None:
        return []
    if start_free == goal_free:
        return []  # 已在同一空旷格内，无需航点

    cols = grid.cols
    blocked = grid.blocked
    start_idx = start_free[1] * cols + start_free[0]
    goal_idx = goal_free[1] * cols + goal_free[0]
    # parent 同时充当 visited 标记（-1 = 未访问）
    parent = [-1] * len(blocked)
    parent[start_idx] = start_idx
    queue = deque((start_idx,))
    found = False
    while queue:
        cur = queue.popleft()
        if cur == goal_idx:
            found = True
            break
        cx = cur % cols
        cy = cur // cols
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if nx < 0 or ny < 0 or nx >= cols or ny >= grid.rows:
                continue
            nxt = ny * cols + nx
            if parent[nxt] != -1 or blocked[nxt]:
                continue
            parent[nxt] = cur
            queue.append(nxt)
    if not found:
        return []  # BFS 无路径 → 调用方回退原直线逻辑

    # 回溯路径格（起点 → 终点）
    cells: list[tuple[int, int]] = []
    idx = goal_idx
    while True:
        cells.append((idx % cols, idx // cols))
        if idx == start_idx:
            break
        idx = parent[idx]
    cells.reverse()

    half = grid.cell * 0.5
    mid = cells[1:-1]  # 去掉起点格与终点格：终点由调用方直线收敛
    if not mid:
        return []
    waypoints: list[tuple[float, float]] = []
    end = len(mid)
    while end > 0:
        start = max(0, end - MONSTER_NAV_WAYPOINT_LOOKAHEAD)
        waypoints.append((mid[start][0] * grid.cell + half, mid[start][1] * grid.cell + half))
        end = start
    waypoints.reverse()
    return waypoints


def _nav_reset(monster) -> None:
    """退出 BFS 航点模式并清空卡死计数（恢复原直线追击/进攻）"""
    monster._nav_mode = False
    monster._nav_stuck_frames = 0
    monster._nav_waypoints = []
    monster._nav_goal = None
    monster._nav_timer = 0.0
    monster._nav_wp_stuck = 0


def _nav_replan(monster, target_x: float, target_y: float) -> bool:
    """为怪物重算 BFS 航点队列；无路径时清空并返回 False（调用方回退直线逻辑）"""
    waypoints = _nav_bfs_path(monster._walls, monster.center_x, monster.center_y,
                              target_x, target_y)
    cell = _get_nav_grid(monster._walls).cell_of(target_x, target_y)
    monster._nav_waypoints = waypoints
    monster._nav_goal = cell
    monster._nav_timer = MONSTER_NAV_REFRESH_INTERVAL
    monster._nav_wp_stuck = 0
    return bool(waypoints)


def _nav_follow(monster, target_x: float, target_y: float, delta_time: float) -> bool:
    """沿 BFS 航点推进怪物（进攻撤离点 / 追击共用）

    - 航点到达（距当前航点 < MONSTER_NAV_WAYPOINT_ARRIVE_RATIO × 格边长）或定时
      （MONSTER_NAV_REFRESH_INTERVAL）到期、目标格变化时重算路径；
    - 位移的最终碰撞权限仍在 _can_move_to：整步 → 分轴滑动，与原逻辑同一口径；
    - 某航点在物理上不可达（贴墙/被建筑卡住）时连续无位移达阈值即丢弃该航点重规划；
    - 返回 True 表示本帧位移已由航点结算（调用方不要再走直线）；False 表示无路径/
      航点耗尽，调用方回退原直线 + 分轴滑动，行为与修复前一致。
    """
    monster._nav_timer -= delta_time
    if (not monster._nav_waypoints
            or monster._nav_timer <= 0.0
            or monster._nav_goal != _get_nav_grid(monster._walls).cell_of(target_x, target_y)):
        if not _nav_replan(monster, target_x, target_y):
            return False
    waypoints = monster._nav_waypoints
    if not waypoints:
        return False

    wx, wy = waypoints[0]
    arrive = _get_nav_grid(monster._walls).cell * MONSTER_NAV_WAYPOINT_ARRIVE_RATIO
    if math.hypot(wx - monster.center_x, wy - monster.center_y) < arrive:
        waypoints.pop(0)  # 已抵达当前航点 → 顺延到下一个
        monster._nav_wp_stuck = 0
        if not waypoints:
            return False  # 航点耗尽 → 交回调用方直线收敛到目标
        wx, wy = waypoints[0]

    before_x = monster.center_x
    before_y = monster.center_y
    dx = wx - before_x
    dy = wy - before_y
    dist = math.hypot(dx, dy)
    if dist <= 0:
        waypoints.pop(0)
        return False
    step = monster.speed * monster._debuff_speed_mult * delta_time
    new_x = before_x + dx / dist * step
    new_y = before_y + dy / dist * step
    if _can_move_to(new_x, new_y, monster._size, monster._walls):
        monster.center_x = new_x
        monster.center_y = new_y
    else:
        # 受阻时沿轴滑动（与原直线逻辑同一口径，最终碰撞仍由 _can_move_to 裁决）
        if _can_move_to(new_x, monster.center_y, monster._size, monster._walls):
            monster.center_x = new_x
        if _can_move_to(monster.center_x, new_y, monster._size, monster._walls):
            monster.center_y = new_y

    moved = math.hypot(monster.center_x - before_x, monster.center_y - before_y)
    if moved < MONSTER_NAV_MOVE_EPS:
        # 该航点物理上不可达 → 计数到阈值后丢弃并强制重规划（防原地抖动死循环）
        monster._nav_wp_stuck += 1
        if monster._nav_wp_stuck >= MONSTER_NAV_WAYPOINT_STUCK_FRAMES:
            monster._nav_wp_stuck = 0
            monster._nav_waypoints.pop(0)
            if not monster._nav_waypoints:
                return False
            monster._nav_timer = 0.0  # 下一帧立即重算
    else:
        monster._nav_wp_stuck = 0
    return True


def _nav_chase_step(monster, target_x: float, target_y: float,
                    delta_time: float, intent: bool) -> bool:
    """追击卡死检测 + 航点模式开关（近战/远程追击段共用）

    - 缺陷⑧根因：追击用「整步 → X → Y」贪心，凹角处三次探测全部失败 → 位移永久为 0；
    - 判定：连续 MONSTER_NAV_STUCK_FRAMES 帧「有移动意图但位移 < MONSTER_NAV_MOVE_EPS」
      且与目标不在同一格（贴住玩家不算卡死）→ 置 _nav_mode，转入 BFS 航点绕墙；
    - 退出：航点模式内无路径/航点耗尽即 _nav_reset（调用方继续原直线追击）；
      直线整步恢复畅通时由调用方显式 _nav_reset。
    - 返回 True 表示本帧位移已由航点结算，调用方不要再走直线追击。
    """
    moved = math.hypot(monster.center_x - monster._nav_last_x,
                       monster.center_y - monster._nav_last_y)
    monster._nav_last_x = monster.center_x
    monster._nav_last_y = monster.center_y

    if monster._nav_mode:
        if _nav_follow(monster, target_x, target_y, delta_time):
            return True
        _nav_reset(monster)
        return False

    if intent and moved < MONSTER_NAV_MOVE_EPS:
        monster._nav_stuck_frames += 1
        if monster._nav_stuck_frames >= MONSTER_NAV_STUCK_FRAMES:
            monster._nav_stuck_frames = 0
            grid = _get_nav_grid(monster._walls)
            here = grid.cell_of(monster.center_x, monster.center_y)
            there = grid.cell_of(target_x, target_y)
            if here is not None and there is not None and here != there:
                monster._nav_mode = True  # 进入航点模式（无路径时下一帧立即回退）
                if _nav_follow(monster, target_x, target_y, delta_time):
                    return True
                _nav_reset(monster)
        return False

    monster._nav_stuck_frames = 0
    return False


def _nav_unstick(monster) -> bool:
    """把「身体嵌在墙体/边界里」的怪物挪到最近合法位置，脱困后继续原逻辑

    背景（用户缺陷⑦现场「怪物 240~343px 聚集在房间边缘不动」的真凶）：刷新器
    _find_valid_spawn_position 只校验「中心点」不在墙内，怪物身体（AABB 半宽 _size）
    仍可能嵌进墙体。此时 _can_move_to 对任意方向都判失败 → 整步/分轴滑动全废，
    BFS 航点第一步也迈不出去 → 位移永久为 0，寻路层再怎么算都救不回来。

    做法：按格逐环外扩找最近的「身体合法」点，只吸附到 _can_move_to 校验通过的位置，
    不放宽任何碰撞判定；找不到合法点则返回 False，行为与修复前完全一致。
    """
    if _can_move_to(monster.center_x, monster.center_y, monster._size, monster._walls):
        return False  # 位置本来就合法 → 无需脱困（绝大多数帧走这条快路径）
    cell = _NAV_CELL
    cx, cy = monster.center_x, monster.center_y
    for ring in range(1, MONSTER_NAV_UNSTICK_SEARCH_RING + 1):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if max(abs(dx), abs(dy)) != ring:
                    continue  # 只扫当前环
                nx = cx + dx * cell
                ny = cy + dy * cell
                if _can_move_to(nx, ny, monster._size, monster._walls):
                    monster.center_x = nx
                    monster.center_y = ny
                    _nav_reset(monster)  # 位置已变 → 旧航点/卡死计数全部作废
                    return True
    return False


def _segment_intersects_rect(x0, y0, x1, y1, rx, ry, rw, rh):
    """线段 (x0,y0)-(x1,y1) 与轴对齐矩形是否相交（slab 法，精确无漏检）

    端点相切（t0/t1 落在 [0,1] 边界）视为相交：怪物贴墙站立时，视线从
    墙面出发即为被挡，符合物理直觉。返回 True = 线段穿过/触及该墙。
    """
    dx = x1 - x0
    dy = y1 - y0
    t0, t1 = 0.0, 1.0
    # 依次对 X、Y 两个轴做 slab 裁剪
    for p, d, lo, hi in ((x0, dx, rx, rx + rw), (y0, dy, ry, ry + rh)):
        if abs(d) < 1e-9:
            # 线段与轴平行：若起点在该轴投影之外则无交集
            if p < lo or p > hi:
                return False
        else:
            ta = (lo - p) / d
            tb = (hi - p) / d
            if ta > tb:
                ta, tb = tb, ta
            t0 = max(t0, ta)
            t1 = min(t1, tb)
            if t0 > t1:
                return False
    return True


def _has_line_of_sight(x0: float, y0: float, x1: float, y1: float, walls: list, r0: float = 0, r1: float = 0) -> bool:
    """检查两点之间是否有视线（连线未被墙壁阻挡）

    修复"怪物隔墙索敌"：怪物与玩家之间隔着墙时不得索敌/追击/攻击。
    修复"拐角误判卡住"：r0/r1 为起点/终点实体半径，检测改为"怪物表面→玩家表面"
    （两端各向内侧缩进半径），玩家在墙角只露出部分身体即算可见——360° 视野被墙
    遮挡后，拐角方向仍保留下来的视野部分可以看到玩家。
    实现：沿线以 TILE_SIZE/2 步长采样网格坐标收集候选墙（配合 _WallGrid 空间索引），
    再用 slab 法做精确线段-矩形相交判定（避免稀疏采样漏检导致隔墙误判）。
    """
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)
    if dist <= 0:
        return True
    # 两端向内侧缩进各自半径（不越过线段中点），把"中心连线"变为"边缘连线"
    inset0 = min(r0, dist * 0.5)
    inset1 = min(r1, dist * 0.5)
    sx = x0 + dx / dist * inset0
    sy = y0 + dy / dist * inset0
    ex = x1 - dx / dist * inset1
    ey = y1 - dy / dist * inset1
    seg_dx = ex - sx
    seg_dy = ey - sy
    seg_len = math.hypot(seg_dx, seg_dy)
    if seg_len <= 0:
        return True
    # 沿缩进后的线段采样网格坐标，收集线段经过的所有候选墙（去重）
    # 使用模块级复用容器，避免每帧每怪物每帧分配新 list+set（性能优化）
    global _los_candidates, _los_seen
    _los_candidates.clear()
    _los_seen.clear()
    grid = _get_wall_grid(walls)
    step = TILE_SIZE / 2
    n = max(1, int(seg_len / step) + 1)  # 向上取整，保证采样步长 ≤ TILE_SIZE/2，不漏网格
    for i in range(n + 1):
        t = i / n
        px = sx + seg_dx * t
        py = sy + seg_dy * t
        for w in grid.nearby(px, py):
            if w not in _los_seen:
                _los_seen.add(w)
                _los_candidates.append(w)
    # 对候选墙做精确线段-矩形相交判定
    for wx, wy, ww, wh in _los_candidates:
        if _segment_intersects_rect(sx, sy, ex, ey, wx, wy, ww, wh):
            return False
    return True


def _select_target(players, center_x: float, center_y: float):
    """从玩家列表中选取最近的存活玩家作为怪物目标

    规则：遍历所有存活玩家，返回与怪物坐标（center_x, center_y）欧氏距离
    最近的一个；没有存活玩家时返回 None（怪物保持待机，不追击不攻击）。
    players 可为玩家列表/任意可迭代对象，也可为单个玩家对象（自动包装为列表）；
    存活判断优先使用 alive 属性（Player.alive 返回 hp>0），无 alive 属性的
    对象回退到 hp>0，两者皆缺失时视为存活（联机远端幽灵兼容）。
    """
    # 兼容传入单个玩家对象（而非列表）：包装为列表统一处理
    if hasattr(players, "center_x"):
        players = [players]
    best = None
    best_dist_sq = None
    for p in players:
        # 存活判断：优先 alive 属性，回退 hp>0，均缺失视为存活
        alive = getattr(p, "alive", None)
        if alive is None:
            alive = getattr(p, "hp", 1) > 0
        if not alive:
            continue
        dx = p.center_x - center_x
        dy = p.center_y - center_y
        # 用平方距离比较，避免每个玩家都开根号
        d_sq = dx * dx + dy * dy
        if best_dist_sq is None or d_sq < best_dist_sq:
            best_dist_sq = d_sq
            best = p
    return best


def _find_nearest_building(buildings, x: float, y: float, max_dist: float):
    """返回搜索半径内最近的存活建筑；怪物仅保存回调，不持有 GameView。"""
    nearest = None
    best_dist_sq = max_dist * max_dist
    for building in buildings or ():
        if getattr(building, "hp", 0) <= 0:
            continue
        # 只转攻阻挡型建筑；陷阱为地面触发物，怪物应直接踩上而非拆除
        if not getattr(building, "blocks_monsters", True):
            continue
        bx = getattr(building, "x", getattr(building, "center_x", 0.0))
        by = getattr(building, "y", getattr(building, "center_y", 0.0))
        dist_sq = (bx - x) ** 2 + (by - y) ** 2
        if dist_sq <= best_dist_sq:
            nearest = building
            best_dist_sq = dist_sq
    return nearest


def _building_is_active(monster, building) -> bool:
    """确认锁定建筑仍在建筑列表中且尚未被摧毁。"""
    if building is None or getattr(building, "hp", 0) <= 0:
        return False
    lookup = getattr(monster, "_build_lookup", None)
    if lookup is None:
        return False
    buildings = lookup() or ()
    return any(item is building for item in buildings)


def _building_attack_distance(monster, building) -> float:
    """计算怪物与建筑边缘接触所需的攻击距离。"""
    sprite = getattr(building, "sprite", None)
    building_size = float(getattr(sprite, "width", 0) or 0)
    return monster._size + building_size * 0.5


def _move_toward_building(monster, building, delta_time: float) -> None:
    """让受阻怪物朝当前建筑移动，仍复用墙体空间索引做碰撞。"""
    bx = getattr(building, "x", getattr(building, "center_x", monster.center_x))
    by = getattr(building, "y", getattr(building, "center_y", monster.center_y))
    dx = bx - monster.center_x
    dy = by - monster.center_y
    dist = math.hypot(dx, dy)
    if dist <= 0 or dist <= _building_attack_distance(monster, building):
        return
    move_x = (dx / dist) * monster.speed * monster._debuff_speed_mult * delta_time
    move_y = (dy / dist) * monster.speed * monster._debuff_speed_mult * delta_time
    new_x = monster.center_x + move_x
    new_y = monster.center_y + move_y
    if _can_move_to(new_x, new_y, monster._size, monster._walls):
        monster.center_x = new_x
        monster.center_y = new_y
        return
    if _can_move_to(new_x, monster.center_y, monster._size, monster._walls):
        monster.center_x = new_x
    if _can_move_to(monster.center_x, new_y, monster._size, monster._walls):
        monster.center_y = new_y


def _try_attack_building(monster, building) -> bool | None:
    """按怪物原有攻击节奏攻击建筑；返回 None 表示应恢复攻击玩家。"""
    if not _building_is_active(monster, building):
        monster.attack_building = None
        return None
    bx = getattr(building, "x", getattr(building, "center_x", monster.center_x))
    by = getattr(building, "y", getattr(building, "center_y", monster.center_y))
    dist = math.hypot(bx - monster.center_x, by - monster.center_y)
    if dist > _building_attack_distance(monster, building) or monster._attack_timer > 0:
        return False
    damage_cb = getattr(monster, "_build_damage_cb", None)
    if damage_cb is None:
        monster.attack_building = None
        return None
    monster._attack_timer = monster._attack_delay
    destroyed = bool(damage_cb(building.bid, monster.damage))
    if not destroyed:
        from game.effects import particle_system
        particle_system.emit(bx, by, 3, (220, 180, 100), speed=50, life=0.2, size=2)
    if destroyed:
        monster.attack_building = None
    return True


def _evac_aggro_attack_distance(monster, point) -> float:
    """怪物接触撤离点并发起攻击所需的距离（沿用近战 try_attack 的 _size + 20 口径）。"""
    del point  # 撤离点尺寸由渲染层决定，攻击距离与近战攻击距离保持同一口径
    return monster._size + 20


def _update_evac_aggro(monster, delta_time: float) -> bool:
    """阶段 2 防守撤离：怪物朝撤离点推进并攻击（进攻波专用）。

    - 撤离点引用经 set_evac_point_provider 注入（怪物不持有 GameView），因此撤离点
      被摧毁（destroyed）或已完成防守（secured）后，怪物立刻清空目标恢复普通索敌 AI；
    - aggro_point 只是"该怪属于进攻波"的标记与非空判断依据，实际坐标以 provider
      取到的当前撤离点为准（航天图 BOSS 陨落后新建的撤离点坐标可能不同）；
    - 推进与碰撞复用现有墙体判定（分轴滑动），不受玩家索敌距离/视线限制；
    - 缺陷⑦修复 2026-09-26：房间只有一个门洞，"直线 + 分轴滑动"会让贴墙怪切向分量≈0
      而永久死锁（永远进不了攻击距离、撤离点不掉血）；推进改为先走 BFS 航点（_nav_follow），
      无路径/航点耗尽才回退原直线逻辑。本段依旧不接入任何视线判定。

    Returns:
        bool: True 表示本帧已由撤离点目标接管，调用方应直接 return，不再走玩家索敌逻辑。
    """
    if monster.aggro_point is None:
        return False
    provider = getattr(monster, "_evac_point_provider", None)
    point = provider() if provider is not None else None
    # 撤离点不存在或不在防守中 → 清空目标，恢复普通 AI
    if point is None or getattr(point, "state", None) != "defending":
        monster.aggro_point = None
        return False

    px = float(getattr(point, "x", monster.aggro_point[0]))
    py = float(getattr(point, "y", monster.aggro_point[1]))
    monster.aggro_point = (px, py)  # 与撤离点实际位置同步
    dx = px - monster.center_x
    dy = py - monster.center_y
    dist = math.hypot(dx, dy)
    attack_dist = _evac_aggro_attack_distance(monster, point)

    if dist > attack_dist and dist > 0:
        # 缺陷⑦修复：优先沿 BFS 航点绕墙推进（门洞天然可通行，怪物能真正进屋打到撤离点）。
        # 无路径/航点耗尽时返回 False → 落回下方原直线 + 分轴滑动逻辑（行为不劣化）。
        # 设计约束不变：本段不受玩家索敌距离/视线限制。
        if _nav_follow(monster, px, py, delta_time):
            monster._attack_timer = max(0, monster._attack_timer - delta_time)
            return True
        move_x = (dx / dist) * monster.speed * monster._debuff_speed_mult * delta_time
        move_y = (dy / dist) * monster.speed * monster._debuff_speed_mult * delta_time
        new_x = monster.center_x + move_x
        new_y = monster.center_y + move_y
        if _can_move_to(new_x, new_y, monster._size, monster._walls):
            # 撤离点路线恢复畅通后解除建筑锁定
            monster.attack_building = None
            # 直线路线恢复畅通 → 退出 BFS 航点模式（缺陷⑦修复的退出条件）
            _nav_reset(monster)
            monster.center_x = new_x
            monster.center_y = new_y
        else:
            # 受阻时沿轴滑动，允许沿墙绕过墙角进入门洞
            if _can_move_to(new_x, monster.center_y, monster._size, monster._walls):
                monster.center_x = new_x
            if _can_move_to(monster.center_x, new_y, monster._size, monster._walls):
                monster.center_y = new_y
        monster._attack_timer = max(0, monster._attack_timer - delta_time)
        return True

    # 已进入攻击距离：按自身攻击节奏对撤离点结算伤害（特效口径同 _try_attack_building）
    monster._attack_timer = max(0, monster._attack_timer - delta_time)
    if monster._attack_timer <= 0:
        monster._attack_timer = monster._attack_delay
        from game.effects import particle_system
        particle_system.emit(px, py, 3, (255, 90, 90), speed=50, life=0.2, size=2)
        point.take_damage(monster.damage)
    return True


class Projectile(arcade.SpriteSolidColor):
    """远程弹丸

    持续向目标方向直线飞行，碰墙或超时后消失；特殊弹丸（explosive）碰墙触发爆炸。
    """
    def __init__(self, center_x: float, center_y: float, target_x: float, target_y: float,
                 speed: float, damage: float, color: tuple = (255, 100, 50),
                 debuff_id: str | None = None, size: int = PROJECTILE_SIZE,
                 special: str | None = None, debuffs: list | None = None):
        """初始化弹丸精灵并计算飞行方向向量

        center_x/y：起始坐标；target_x/y：目标坐标（方向向量由此计算，不追踪）
        speed：每秒像素速度；damage：命中伤害；color：弹丸颜色
        debuff_id：命中附加的 debuff（怪物弹丸用）；special：特殊属性（"explosive"=爆炸）
        debuffs：武器附加效果列表 [((效果ID, 效果等级), ...]，怪物装备武器带来的额外效果
        """
        super().__init__(size, size, color=color)
        self.center_x = center_x
        self.center_y = center_y
        self.damage = damage
        self._lifetime = PROJECTILE_LIFETIME
        self.debuff_id = debuff_id  # 命中时附加的 debuff（木乃伊远程毒弹/骷髅BOSS冰冻弹）
        self.debuffs = debuffs or []  # 武器附加效果列表 [(效果ID, 效果等级), ...]（怪物装备武器带来的额外效果）
        self.special = special  # 弹丸特殊属性（火箭兵弹丸 special="explosive" 爆炸）
        dx = target_x - center_x
        dy = target_y - center_y
        dist = math.hypot(dx, dy)
        if dist > 0:
            self.change_x = (dx / dist) * speed
            self.change_y = (dy / dist) * speed
        else:
            self.change_x = speed
            self.change_y = 0

    def update(self, delta_time: float) -> None:
        """按帧递进弹丸位置并递减生命周期"""
        self.center_x += self.change_x * delta_time
        self.center_y += self.change_y * delta_time
        self._lifetime -= delta_time

    @property
    def expired(self) -> bool:
        return self._lifetime <= 0


class _MeleeMonsterBase(arcade.SpriteSolidColor):
    """近战怪物参数化基类：复用 Zombie 的 AI 行为，数值由子类传入

    供 木乃伊(近战) / BOSS僵尸 / 木乃伊BOSS 使用。
    """

    def __init__(self, center_x: float, center_y: float, size: int, color: tuple,
                 hp: int, damage: float, speed: float, attack_delay: float,
                 aggro_range: int, debuff_id: str | None = None,
                 is_boss: bool = False, required_weapon_level: int = 0):
        """初始化近战怪物——所有数值由 entities/monster_defs.py 的 MONSTER_CONFIGS 传入

        size：逻辑半径（精灵尺寸 = size*2）；hp/damage/speed：基础战斗数值
        attack_delay：攻击冷却（秒）；aggro_range：索敌距离（像素）
        debuff_id：攻击附加 debuff（如木乃伊中毒）；is_boss：BOSS 标记
        """
        super().__init__(size * 2, size * 2, color=color)
        self.center_x = center_x
        self.center_y = center_y
        self.hp = hp
        self.max_hp = hp
        self.damage = damage
        self.speed = speed
        self._attack_timer = 0.0
        self._on_death_cb = None
        self._hit_flash = 0.0
        self.room_bounds = None
        self._walls = []
        # 局内建造：仅保存建筑查询/伤害回调，不持有 GameView，便于联机与召唤怪物复用
        self.attack_building = None
        self._build_lookup = None
        self._build_damage_cb = None
        # 阶段 2 防守撤离：进攻目标标记（非空表示该怪属于进攻波）+ 撤离点引用 provider
        self.aggro_point = None
        self._evac_point_provider = None
        # BFS 寻路状态（缺陷⑦⑧修复 2026-09-26）：私有属性不参与联机快照同步
        self._nav_waypoints: list[tuple[float, float]] = []
        self._nav_goal = None
        self._nav_timer = 0.0
        self._nav_mode = False
        self._nav_stuck_frames = 0
        self._nav_wp_stuck = 0
        self._nav_last_x = center_x
        self._nav_last_y = center_y
        # 联机幽灵实例标记：由网络层置位，置位后本地不推进 AI（防 AI 抖动与双份伤害）
        self.net_ghost = False
        # 阶段 3 精英词缀：affix=None 即普通怪（钩子入口一次判空即返回）
        self.affix = None
        self._affix_state = {}
        self._affix_view_provider = None
        # 阶段 3 护盾词缀：护盾值（吸收伤害，0=无护盾）；max_shield 供 HUD 算护盾条比例
        self.shield = 0
        self.max_shield = 0
        # BOSS/精英标记（is_elite 由 spawn_elite 置位，供掉落/渲染/HUD 识别）
        self.is_elite = False
        self.elite_drop = False
        # 护甲系统
        self.armor = None
        self.armor_drop_id = None
        # 头盔系统
        self.helmet = None
        self.helmet_drop_id = None
        # 武器系统：怪物携带武器，击败后掉落自身武器（等级由分配时决定）
        self.weapon = None          # {"item_id": str, "name": str, "color": tuple, "level": int}
        # 最后攻击者网络 id（默认 0=单机/本端；等级经验按此归属判断击杀者）
        self.last_attacker_id = 0
        # debuff 系统
        self.debuffs = []
        self._debuff_tick = 0.0
        self._debuff_speed_mult = 1.0
        self._stunned = False
        # 攻击附加效果（如中毒）
        self.debuff_id = debuff_id
        # BOSS 标记（用于刷新排除与 Lv5+ 装备门槛）
        self.is_boss = is_boss
        self.required_weapon_level = required_weapon_level
        # 装备被动效果：回血速度（由 assign_monster_* 按装备效果累加）
        self.regen_per_sec = 0.0
        # 行为参数
        self._size = size
        # 索敌距离 = max(保底, 配置值 × 倍率)：保底覆盖玩家可视范围（屏幕半对角），
        # 保证"玩家能看到怪物→怪物就能索敌"；远程/狙击/BOSS 保留更远的个体差异
        self._aggro_range = max(MONSTER_AGGRO_RANGE_BASE, int(aggro_range * MONSTER_AGGRO_RANGE_MULT))
        self._attack_delay = attack_delay

    def set_on_death(self, cb) -> None:
        """注册死亡回调——怪物 hp≤0 时被调用，传入自身实例"""
        self._on_death_cb = cb

    def set_building_callbacks(self, build_lookup, build_damage_cb) -> None:
        """注入建筑查询与伤害回调，避免怪物直接持有视图对象。"""
        self._build_lookup = build_lookup
        self._build_damage_cb = build_damage_cb

    def set_evac_point_provider(self, provider) -> None:
        """注入「当前撤离点」引用 provider（阶段 2 进攻波用）。

        只保存回调不持有视图；provider 返回 None 时等价于没有撤离点，
        怪物会清空 aggro_point 并恢复普通索敌 AI。
        """
        self._evac_point_provider = provider

    def set_affix_view_provider(self, provider) -> None:
        """注入「当前 GameView」引用 provider（阶段 3 精英词缀用）。

        与 _evac_point_provider 同模式：只保存回调不直接持有视图；
        provider 返回 None 时词缀的「需要场景」行为（分裂/召唤/火墙区）安全跳过。
        """
        self._affix_view_provider = provider

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        """更新怪物 AI（单目标或多目标模式）

        - 单目标（players=None，单机默认）：沿用原有逻辑，以传入的
          player_x/player_y 为追击目标，行为与之前完全一致。
        - 多目标（players 为玩家列表，联机主机模式）：每帧选取最近存活玩家
          作为当前目标；当前目标死亡/离开后，下一帧自动切换到下一个最近玩家，
          不会原地空转。
        """
        # 联机幽灵实例：位置/动画完全由主机快照驱动，本地一律不推进 AI（防抖动与双份伤害）
        if getattr(self, "net_ghost", False):
            return
        if not self.alive:
            return
        # 缺陷⑦补充：刷新点只校验「中心点」不在墙内，怪物身体（AABB 半宽 _size）可能嵌在
        # 墙体/边界里 —— 此时 _can_move_to 对任意方向都失败，直线滑动与 BFS 航点全部失效，
        # 位移永久为 0。这里先脱困到最近合法点（只吸附 _can_move_to 校验通过的位置），
        # 快路径只是一次 _can_move_to 查询，绝大多数帧不做任何事。
        _nav_unstick(self)
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)
        # 技能提示计时器递减
        if getattr(self, '_skill_prompt_timer', 0) > 0:
            self._skill_prompt_timer = max(0, self._skill_prompt_timer - delta_time)
        # 技能范围圈特效计时器递减（_skill_vfx_timer 由 monster_utils 施放时写入初值，
        # 归零后渲染层不再画圈；用 getattr 兜底旧/未施法实例）
        if getattr(self, "_skill_vfx_timer", 0) > 0:
            self._skill_vfx_timer = max(0.0, self._skill_vfx_timer - delta_time)
        # 技能 buff 计时器递减（狂暴/骨盾/战术撤退等临时效果到期恢复）
        # 技能冷却递减：按距离分档 + 冷却取代原 50% 概率随机（用户需求 2026-09-26）后，
        # 冷却表 monster._skill_cds 必须逐帧推进，否则技能永不再放
        from game.monster_utils import update_skill_buffs, update_skill_cooldowns
        update_skill_buffs(self, delta_time)
        update_skill_cooldowns(self, delta_time)
        # 附加效果结算（中毒/燃烧掉血、冰冻/减速、眩晕）
        self._update_debuffs(delta_time)
        # 阶段 3 精英词缀：狂暴半血提速 / 火墙近身灼烧 / 召唤周期召怪
        # （普通怪 affix=None，钩子入口一次判空即返回）
        if self.alive and self.affix is not None:
            from game.monster_affixes import on_affix_update
            view = self._affix_view_provider() if self._affix_view_provider else None
            on_affix_update(self, delta_time, view)
        # 装备被动回血（自然恢复等效果）
        if self.regen_per_sec > 0 and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.regen_per_sec * delta_time)
        if self._stunned:
            # 眩晕：无法移动和攻击
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 阶段 2 防守撤离：进攻波目标为撤离点时接管本帧 AI
        if _update_evac_aggro(self, delta_time):
            return
        if players is not None:
            # 多目标模式：选取最近存活玩家作为当前目标，无存活玩家则待机
            target = _select_target(players, self.center_x, self.center_y)
            if target is None:
                self._attack_timer = max(0, self._attack_timer - delta_time)
                return
            player_x = target.center_x
            player_y = target.center_y
        dx = player_x - self.center_x
        dy = player_y - self.center_y
        dist = math.hypot(dx, dy)
        # 超出索敌距离则不追击
        if dist > self._aggro_range or dist == 0:
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 隔墙（无视线）不索敌：边缘视线（怪物表面→玩家表面），拐角露出部分身体即可看到
        if not _has_line_of_sight(self.center_x, self.center_y, player_x, player_y, self._walls,
                                  self._size, PLAYER_SIZE):
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 直接冲向玩家；撞墙时沿轴滑动（分轴尝试），避免卡在门口墙角
        move_x = (dx / dist) * self.speed * self._debuff_speed_mult * delta_time
        move_y = (dy / dist) * self.speed * self._debuff_speed_mult * delta_time
        new_x = self.center_x + move_x
        new_y = self.center_y + move_y
        # 缺陷⑧修复：连续多帧「想动却几乎不动」（凹角处整步与两次单轴探测全失败）→ 转 BFS 航点绕墙
        if _nav_chase_step(self, player_x, player_y, delta_time,
                           move_x != 0.0 or move_y != 0.0):
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        if _can_move_to(new_x, new_y, self._size, self._walls):
            # 玩家路线恢复畅通后解除建筑锁定，回到原玩家追击目标
            self.attack_building = None
            # 直线追击恢复畅通 → 退出 BFS 航点模式（缺陷⑧修复的退出条件）
            _nav_reset(self)
            self.center_x = new_x
            self.center_y = new_y
        else:
            # 受阻时只在附近寻找建筑，找到后转攻建筑；无建筑才沿墙滑动
            target = self.attack_building
            if target is not None and not _building_is_active(self, target):
                target = None
                self.attack_building = None
            if target is None and self._build_lookup is not None:
                target = _find_nearest_building(
                    self._build_lookup() or (), self.center_x, self.center_y,
                    BUILDING_ATTACK_SEARCH_RANGE,
                )
            if target is not None:
                self.attack_building = target
                _move_toward_building(self, target, delta_time)
            else:
                # 整体移动被挡：分轴尝试，允许怪物沿墙滑行绕过墙角进入门洞
                if _can_move_to(new_x, self.center_y, self._size, self._walls):
                    self.center_x = new_x
                if _can_move_to(self.center_x, new_y, self._size, self._walls):
                    self.center_y = new_y
        self._attack_timer = max(0, self._attack_timer - delta_time)

    def try_attack(self, player=None, players=None) -> bool:
        """近战攻击：命中当前目标并结算伤害

        单目标模式传 player（单机路径）；多目标模式传 players 列表，
        自动选取最近存活玩家作为攻击目标（目标切换即时生效）。
        """
        # 联机幽灵实例：开火与伤害由主机快照驱动，本地一律不发弹丸/不结算伤害
        if getattr(self, "net_ghost", False):
            return False
        if not self.alive:
            return False
        if self._stunned:
            # 眩晕状态下无法攻击
            return False
        # 阶段 2：进攻撤离点期间不在这里攻击玩家（伤害已在 _update_evac_aggro 结算）
        if self.aggro_point is not None:
            return False
        if self.attack_building is not None:
            building_result = _try_attack_building(self, self.attack_building)
            if building_result is not None:
                return building_result
        if players is not None:
            # 多目标模式：重新选取最近存活玩家作为攻击目标
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            # 无目标（多目标模式下无存活玩家，或单目标未传入玩家）无法攻击
            return False
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < self._size + 20 and self._attack_timer <= 0:
            self._attack_timer = self._attack_delay
            # 攻击时触发技能提示 + 实际效果
            # 按距离分档 + 冷却取代原 50% 概率随机（用户需求 2026-09-26）：
            # pick_skill_index 按 dist 选距离档位技能，返回 None 表示该档技能冷却中，本次不放
            from game.monster_utils import (
                apply_skill_prompt_and_effect, collect_skill_nearby_players,
                pick_skill_index,
            )
            skill_idx = pick_skill_index(self, dist)
            if skill_idx is not None:
                # 范围技能（沙尘暴/手雷投掷）需要附近玩家列表：联机传 players 全量，
                # 单机路径只有 player（由 fallback 兜底）；单目标技能不读该参数
                apply_skill_prompt_and_effect(
                    self, skill_idx, target=player,
                    nearby_players=collect_skill_nearby_players(
                        self, skill_idx, players, player),
                )
            # 汇总本次攻击的全部附带效果：怪物自带 debuff + 武器携带效果
            combined_debuffs = []
            if self.debuff_id:
                combined_debuffs.append((self.debuff_id, 1))
            # 武器效果（assign_monster_weapon 已复制 effects/debuff 到 weapon 字典）
            wdebuff = (self.weapon or {}).get("debuff")
            if wdebuff:
                combined_debuffs.append((wdebuff, 1))
            for eid, lvl in (self.weapon or {}).get("effects", []):
                if eid not in ("max_hp", "regen", "speed", "defense", "damage", "lifesteal", "thorns", "crit_chance"):  # 跳过被动效果，只传 debuff
                    combined_debuffs.append((eid, lvl))
            # 记录本次攻击附带效果：受击钩子（联机 PLAYER_HURT 广播）据此把 debuff+等级
            # 一并下发客户端（修复客户端玩家被怪物攻击时特殊效果未生效）
            # 联机同步：优先使用第一个 debuff，其余效果在客户端 apply_debuff 逐个施加
            first_debuff = combined_debuffs[0][0] if combined_debuffs else None
            player._pending_debuff = first_debuff
            player._pending_debuff_level = combined_debuffs[0][1] if combined_debuffs else 1
            player._pending_debuff_effects = combined_debuffs  # 全部效果列表（联机广播用）
            actual_player_dmg = player.take_damage(self.damage)
            # 阶段5 祝福：玩家荆棘反伤（equip_thorns 聚合了装备 thorns + 祝福 thorns）——
            # 按实际承受伤害的百分比反弹给近战来源，口径与怪物荆棘（combat.py 荆棘反伤）一致。
            # 取 actual 而非 self.damage：护甲减伤/护盾吸收后的实际掉血量才是反伤基数。
            thorns_pct = float(getattr(player, "equip_thorns", 0.0) or 0.0)
            if thorns_pct > 0 and actual_player_dmg > 0:
                self.take_damage(max(1, round(actual_player_dmg * thorns_pct)))
            # 逐个施加全部效果（含怪物自带 + 武器 debuff）
            for eid, lvl in combined_debuffs:
                if hasattr(player, "apply_debuff"):
                    player.apply_debuff(eid, lvl)
            return True
        return False

    def take_damage(self, amount: int, vulnerable: float = 0.0):
        """受到伤害，先扣头盔和护甲，再考虑易伤增幅

        vulnerable：外部传入的易伤比例（来自 debuff），叠加后增加受到的伤害
        """
        helmet_def = self.helmet.get("defense", 0) if self.helmet else 0
        armor_def = self.armor.get("defense", 0) if self.armor else 0
        total_def = helmet_def + armor_def
        # 破甲 debuff 减少防御
        for d in self.debuffs:
            if d["id"] == "armor_break":
                from entities.effects_defs import effect_params
                total_def -= effect_params("armor_break", d.get("level", 1)).get("armor_break", 0)
        # 骨盾技能：吸收固定减伤，原实现赋值后无读取点
        # （赋值在 monster_utils.apply_skill_effect，到期清零在 update_skill_buffs）
        skill_def = int(getattr(self, "_skill_defense_bonus", 0) or 0)
        if skill_def > 0:
            total_def += skill_def
        total_def = max(0, total_def)  # 防御不低于0
        actual = max(1, amount - total_def)
        # 易伤增幅（debuff 传入 + debuffs 列表累加）
        vuln_mult = 1.0 + vulnerable
        for d in self.debuffs:
            if d["id"] == "vulnerable":
                from entities.effects_defs import effect_params
                vuln_mult += effect_params("vulnerable", d.get("level", 1)).get("vulnerable", 0)
        actual = max(1, round(actual * vuln_mult))
        # 阶段 3 护盾词缀：伤害先扣护盾，护盾耗尽后溢出部分才计入生命值
        # （普通怪 shield=0，此分支只多一次属性判定的开销）
        dealt = actual
        if self.shield > 0:
            absorbed = min(self.shield, actual)
            self.shield -= absorbed
            actual -= absorbed
        self.hp -= actual
        self._hit_flash = HIT_FLASH_DURATION
        if self.hp <= 0 and self._on_death_cb:
            # 阶段 3 精英词缀死亡效果（分裂/火墙）须在死亡回调之前触发，
            # 保证 view.monsters / view.fire_zones 追加时怪物仍在场上
            if self.affix is not None:
                from game.monster_affixes import on_affix_death
                view = self._affix_view_provider() if self._affix_view_provider else None
                if view is not None:
                    on_affix_death(self, view)
            self._on_death_cb(self)
        # 返回护盾吸收前的伤害值：伤害飘字按实际打击力度显示，
        # 不因护盾全额吸收而显示 0
        return dealt

    def apply_debuff(self, effect_id: str, level: int = 1) -> None:
        """对怪物施加 debuff——流血可叠加最多 3 层，同类刷新时长；其他效果刷新时长与等级"""
        from entities.effects_defs import EFFECTS, effect_params
        effect = effect_params(effect_id, level)
        if not effect or effect.get("type") != "debuff":
            return
        # 流血可叠加（最多3层），同类刷新时长
        if effect.get("stacks"):
            stack_count = sum(1 for d in self.debuffs if d["id"] == effect_id)
            if stack_count < 3:
                self.debuffs.append({"id": effect_id, "level": level, "duration": effect.get("duration", 3.0)})
            else:
                # 已达上限：刷新最早一层的时长
                for d in self.debuffs:
                    if d["id"] == effect_id:
                        d["duration"] = effect.get("duration", 3.0)
                        d["level"] = max(d.get("level", 1), level)
                        break
            self._recalc_debuffs()
            return
        for d in self.debuffs:
            if d["id"] == effect_id:
                d["duration"] = effect.get("duration", 1.0)
                d["level"] = max(d.get("level", 1), level)
                return
        self.debuffs.append({"id": effect_id, "level": level, "duration": effect.get("duration", 1.0)})
        self._recalc_debuffs()

    def apply_slow(self, slow_mult: float, duration: float) -> None:
        """施加自定义减速（建造陷阱等数据驱动来源传入倍率与时长，覆写标准 slow 参数）"""
        self.apply_debuff("slow", 1)
        for d in self.debuffs:
            if d["id"] == "slow":
                d["slow_mult"] = slow_mult
                d["duration"] = duration
                break
        self._recalc_debuffs()

    def _recalc_debuffs(self):
        """重算减速倍率与眩晕状态"""
        from entities.effects_defs import effect_params
        self._debuff_speed_mult = 1.0
        self._stunned = False
        for d in self.debuffs:
            effect = effect_params(d["id"], d.get("level", 1))
            # 建筑陷阱等数据驱动来源可写入 slow_mult 覆写标准 slow 效果倍率
            slow = d.get("slow_mult", effect.get("slow", 0))
            if slow:
                self._debuff_speed_mult *= (1.0 - slow)
            if effect.get("stun"):
                self._stunned = True

    def _update_debuffs(self, delta_time: float):
        """每 0.5 秒结算一次持续伤害（含流血叠加），递减剩余时长"""
        if not self.debuffs:
            return
        self._debuff_tick -= delta_time
        if self._debuff_tick <= 0:
            self._debuff_tick = DEBUFF_TICK_INTERVAL
            from entities.effects_defs import effect_params
            # 按效果 id 汇总层数后统一结算（流血多层合并伤害）
            dot_damage = {}  # {effect_id: total_dmg_per_tick}
            for d in list(self.debuffs):
                effect = effect_params(d["id"], d.get("level", 1))
                dmg = effect.get("value", 0)
                if dmg > 0 and effect.get("type") == "debuff":
                    dot_damage[d["id"]] = dot_damage.get(d["id"], 0) + dmg
            for eid, total_dmg in dot_damage.items():
                self.take_damage(total_dmg)
        for d in list(self.debuffs):
            d["duration"] -= delta_time
            if d["duration"] <= 0:
                self.debuffs.remove(d)
        self._recalc_debuffs()

    @property
    def alive(self) -> bool:
        return self.hp > 0


class _RangedMonsterBase(arcade.SpriteSolidColor):
    """远程怪物参数化基类：复用 Skeleton 的 AI 行为（保持距离+弹丸攻击）

    供 木乃伊(远程) / 骆驼 / BOSS骷髅 使用。
    """

    def __init__(self, center_x: float, center_y: float, size: int, color: tuple,
                 hp: int, damage: float, speed: float, attack_delay: float,
                 aggro_range: int, proj_speed: float, proj_size: int,
                 proj_color: tuple, debuff_id: str | None = None,
                 is_boss: bool = False, required_weapon_level: int = 0):
        """初始化远程怪物——所有数值由 entities/monster_defs.py 的 MONSTER_CONFIGS 传入

        与近战基类相比多了弹丸参数：proj_speed/proj_size/proj_color 控制弹丸外观与飞行
        """
        super().__init__(size * 2, size * 2, color=color)
        self.center_x = center_x
        self.center_y = center_y
        self.hp = hp
        self.max_hp = hp
        self.damage = damage
        self.speed = speed
        self._attack_timer = 0.0
        self._on_death_cb = None
        self._hit_flash = 0.0
        self.room_bounds = None
        self._walls = []
        # 局内建造：仅保存建筑查询/伤害回调，不持有 GameView，便于联机与召唤怪物复用
        self.attack_building = None
        self._build_lookup = None
        self._build_damage_cb = None
        # 阶段 2 防守撤离：进攻目标标记（非空表示该怪属于进攻波）+ 撤离点引用 provider
        self.aggro_point = None
        self._evac_point_provider = None
        # BFS 寻路状态（缺陷⑦⑧修复 2026-09-26）：私有属性不参与联机快照同步
        self._nav_waypoints: list[tuple[float, float]] = []
        self._nav_goal = None
        self._nav_timer = 0.0
        self._nav_mode = False
        self._nav_stuck_frames = 0
        self._nav_wp_stuck = 0
        self._nav_last_x = center_x
        self._nav_last_y = center_y
        # 联机幽灵实例标记：由网络层置位，置位后本地不推进 AI（防 AI 抖动与双份伤害）
        self.net_ghost = False
        # 阶段 3 精英词缀：affix=None 即普通怪（钩子入口一次判空即返回）
        self.affix = None
        self._affix_state = {}
        self._affix_view_provider = None
        # 阶段 3 护盾词缀：护盾值（吸收伤害，0=无护盾）；max_shield 供 HUD 算护盾条比例
        self.shield = 0
        self.max_shield = 0
        # BOSS/精英标记（is_elite 由 spawn_elite 置位，供掉落/渲染/HUD 识别）
        self.is_elite = False
        self.elite_drop = False
        # 护甲系统
        self.armor = None
        self.armor_drop_id = None
        # 头盔系统
        self.helmet = None
        self.helmet_drop_id = None
        # 武器系统：怪物携带武器，击败后掉落自身武器（等级由分配时决定）
        self.weapon = None          # {"item_id": str, "name": str, "color": tuple, "level": int}
        # 最后攻击者网络 id（默认 0=单机/本端；等级经验按此归属判断击杀者）
        self.last_attacker_id = 0
        # debuff 系统
        self.debuffs = []
        self._debuff_tick = 0.0
        self._debuff_speed_mult = 1.0
        self._stunned = False
        # 弹丸参数与附加效果
        self.debuff_id = debuff_id
        self._proj_speed = proj_speed
        self._proj_size = proj_size
        self._proj_color = proj_color
        # BOSS 标记
        self.is_boss = is_boss
        self.required_weapon_level = required_weapon_level
        # 装备被动效果：回血速度（由 assign_monster_* 按装备效果累加）
        self.regen_per_sec = 0.0
        # 行为参数
        self._size = size
        # 索敌距离 = max(保底, 配置值 × 倍率)：保底覆盖玩家可视范围（屏幕半对角），
        # 保证"玩家能看到怪物→怪物就能索敌"；远程/狙击/BOSS 保留更远的个体差异
        self._aggro_range = max(MONSTER_AGGRO_RANGE_BASE, int(aggro_range * MONSTER_AGGRO_RANGE_MULT))
        self._attack_delay = attack_delay

    def set_on_death(self, cb) -> None:
        """注册死亡回调——怪物 hp≤0 时被调用，传入自身实例"""
        self._on_death_cb = cb

    def set_building_callbacks(self, build_lookup, build_damage_cb) -> None:
        """注入建筑查询与伤害回调，避免怪物直接持有视图对象。"""
        self._build_lookup = build_lookup
        self._build_damage_cb = build_damage_cb

    def set_evac_point_provider(self, provider) -> None:
        """注入「当前撤离点」引用 provider（阶段 2 进攻波用）。

        只保存回调不持有视图；provider 返回 None 时等价于没有撤离点，
        怪物会清空 aggro_point 并恢复普通索敌 AI。
        """
        self._evac_point_provider = provider

    def set_affix_view_provider(self, provider) -> None:
        """注入「当前 GameView」引用 provider（阶段 3 精英词缀用）。

        与 _evac_point_provider 同模式：只保存回调不直接持有视图；
        provider 返回 None 时词缀的「需要场景」行为（分裂/召唤/火墙区）安全跳过。
        """
        self._affix_view_provider = provider

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        """更新怪物 AI（单目标或多目标模式）

        - 单目标（players=None，单机默认）：沿用原有逻辑，以传入的
          player_x/player_y 为追击目标，行为与之前完全一致。
        - 多目标（players 为玩家列表，联机主机模式）：每帧选取最近存活玩家
          作为当前目标；当前目标死亡/离开后，下一帧自动切换到下一个最近玩家，
          不会原地空转。
        """
        # 联机幽灵实例：位置/动画完全由主机快照驱动，本地一律不推进 AI（防抖动与双份伤害）
        if getattr(self, "net_ghost", False):
            return
        if not self.alive:
            return
        # 缺陷⑦补充：刷新点只校验「中心点」不在墙内，怪物身体（AABB 半宽 _size）可能嵌在
        # 墙体/边界里 —— 此时 _can_move_to 对任意方向都失败，直线滑动与 BFS 航点全部失效，
        # 位移永久为 0。这里先脱困到最近合法点（只吸附 _can_move_to 校验通过的位置），
        # 快路径只是一次 _can_move_to 查询，绝大多数帧不做任何事。
        _nav_unstick(self)
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)
        # 技能提示计时器递减
        if getattr(self, '_skill_prompt_timer', 0) > 0:
            self._skill_prompt_timer = max(0, self._skill_prompt_timer - delta_time)
        # 技能范围圈特效计时器递减（_skill_vfx_timer 由 monster_utils 施放时写入初值，
        # 归零后渲染层不再画圈；用 getattr 兜底旧/未施法实例）
        if getattr(self, "_skill_vfx_timer", 0) > 0:
            self._skill_vfx_timer = max(0.0, self._skill_vfx_timer - delta_time)
        # 技能 buff 计时器递减（狂暴/骨盾/战术撤退等临时效果到期恢复）
        # 技能冷却递减：按距离分档 + 冷却取代原 50% 概率随机（用户需求 2026-09-26）后，
        # 冷却表 monster._skill_cds 必须逐帧推进，否则技能永不再放
        from game.monster_utils import update_skill_buffs, update_skill_cooldowns
        update_skill_buffs(self, delta_time)
        update_skill_cooldowns(self, delta_time)
        # 附加效果结算
        self._update_debuffs(delta_time)
        # 阶段 3 精英词缀：狂暴半血提速 / 火墙近身灼烧 / 召唤周期召怪
        # （普通怪 affix=None，钩子入口一次判空即返回）
        if self.alive and self.affix is not None:
            from game.monster_affixes import on_affix_update
            view = self._affix_view_provider() if self._affix_view_provider else None
            on_affix_update(self, delta_time, view)
        # 装备被动回血（自然恢复等效果）
        if self.regen_per_sec > 0 and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.regen_per_sec * delta_time)
        if self._stunned:
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 阶段 2 防守撤离：进攻波目标为撤离点时接管本帧 AI
        if _update_evac_aggro(self, delta_time):
            return
        if players is not None:
            # 多目标模式：选取最近存活玩家作为当前目标，无存活玩家则待机
            target = _select_target(players, self.center_x, self.center_y)
            if target is None:
                self._attack_timer = max(0, self._attack_timer - delta_time)
                return
            player_x = target.center_x
            player_y = target.center_y
        dx = player_x - self.center_x
        dy = player_y - self.center_y
        dist = math.hypot(dx, dy)
        # 超出索敌距离则不追击
        if dist > self._aggro_range or dist == 0:
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 隔墙（无视线）不索敌：边缘视线（怪物表面→玩家表面），拐角露出部分身体即可看到
        if not _has_line_of_sight(self.center_x, self.center_y, player_x, player_y, self._walls,
                                  self._size, PLAYER_SIZE):
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 保持距离 120~200；撞墙时沿轴滑动（分轴尝试），避免卡在门口墙角
        move_x, move_y = 0, 0
        if dist > RANGED_KEEP_MAX:
            move_x = (dx / dist) * self.speed * self._debuff_speed_mult * delta_time
            move_y = (dy / dist) * self.speed * self._debuff_speed_mult * delta_time
        elif dist < RANGED_KEEP_MIN:
            move_x = -(dx / dist) * self.speed * self._debuff_speed_mult * delta_time * 0.5
            move_y = -(dy / dist) * self.speed * self._debuff_speed_mult * delta_time * 0.5
        if move_x != 0 or move_y != 0:
            new_x = self.center_x + move_x
            new_y = self.center_y + move_y
            # 缺陷⑧修复：连续多帧「想动却几乎不动」（凹角处整步与两次单轴探测全失败）→ 转 BFS 航点绕墙
            if _nav_chase_step(self, player_x, player_y, delta_time,
                               move_x != 0.0 or move_y != 0.0):
                self._attack_timer = max(0, self._attack_timer - delta_time)
                return
            if _can_move_to(new_x, new_y, self._size, self._walls):
                # 玩家路线恢复畅通后解除建筑锁定，回到原玩家追击目标
                self.attack_building = None
                # 直线追击恢复畅通 → 退出 BFS 航点模式（缺陷⑧修复的退出条件）
                _nav_reset(self)
                self.center_x = new_x
                self.center_y = new_y
            else:
                # 受阻时只在附近寻找建筑，找到后转攻建筑；无建筑才沿墙滑动
                target = self.attack_building
                if target is not None and not _building_is_active(self, target):
                    target = None
                    self.attack_building = None
                if target is None and self._build_lookup is not None:
                    target = _find_nearest_building(
                        self._build_lookup() or (), self.center_x, self.center_y,
                        BUILDING_ATTACK_SEARCH_RANGE,
                    )
                if target is not None:
                    self.attack_building = target
                    _move_toward_building(self, target, delta_time)
                else:
                    # 整体移动被挡：分轴尝试，允许怪物沿墙滑行绕过墙角进入门洞
                    if _can_move_to(new_x, self.center_y, self._size, self._walls):
                        self.center_x = new_x
                    if _can_move_to(self.center_x, new_y, self._size, self._walls):
                        self.center_y = new_y
        else:
            # 没有移动需求时不再继续拆建筑，优先维持远程距离
            self.attack_building = None
        self._attack_timer = max(0, self._attack_timer - delta_time)

    def try_attack(self, player=None, players=None) -> Projectile | None:
        """远程攻击：朝当前目标发射弹丸

        单目标模式传 player（单机路径）；多目标模式传 players 列表，
        自动选取最近存活玩家作为攻击目标（目标切换即时生效）。
        """
        # 联机幽灵实例：开火与伤害由主机快照驱动，本地一律不发弹丸/不结算伤害
        if getattr(self, "net_ghost", False):
            return None
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
            return None
        # 阶段 2：进攻撤离点期间不在这里攻击玩家（伤害已在 _update_evac_aggro 结算）
        if self.aggro_point is not None:
            return None
        if self.attack_building is not None:
            building_result = _try_attack_building(self, self.attack_building)
            if building_result is not None:
                # 建筑攻击直接结算伤害，不生成玩家弹丸
                return None
        if players is not None:
            # 多目标模式：重新选取最近存活玩家作为攻击目标
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            # 无目标（多目标模式下无存活玩家，或单目标未传入玩家）无法攻击
            return None
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        # 隔墙（无视线）不开火：与索敌规则一致（边缘视线），防止远程怪隔着墙射击
        if dist < self._aggro_range and self._attack_timer <= 0 and _has_line_of_sight(
                self.center_x, self.center_y, player.center_x, player.center_y, self._walls,
                self._size, PLAYER_SIZE):
            self._attack_timer = self._attack_delay
            # 攻击时触发技能提示 + 实际效果
            # 按距离分档 + 冷却取代原 50% 概率随机（用户需求 2026-09-26）：
            # pick_skill_index 按 dist 选距离档位技能，返回 None 表示该档技能冷却中，本次不放
            from game.monster_utils import (
                apply_skill_prompt_and_effect, collect_skill_nearby_players,
                pick_skill_index,
            )
            skill_idx = pick_skill_index(self, dist)
            if skill_idx is not None:
                # 范围技能（沙尘暴/手雷投掷）需要附近玩家列表：联机传 players 全量，
                # 单机路径只有 player（由 fallback 兜底）；单目标技能不读该参数
                apply_skill_prompt_and_effect(
                    self, skill_idx, target=player,
                    nearby_players=collect_skill_nearby_players(
                        self, skill_idx, players, player),
                )
            # 汇总弹丸附带效果：怪物自带 debuff + 武器携带效果
            combined_debuffs = []
            if self.debuff_id:
                combined_debuffs.append((self.debuff_id, 1))
            wdebuff = (self.weapon or {}).get("debuff")
            if wdebuff:
                combined_debuffs.append((wdebuff, 1))
            for eid, lvl in (self.weapon or {}).get("effects", []):
                if eid not in ("max_hp", "regen", "speed"):
                    combined_debuffs.append((eid, lvl))
            return Projectile(
                self.center_x, self.center_y,
                player.center_x, player.center_y,
                self._proj_speed, self.damage,
                color=self._proj_color, debuff_id=self.debuff_id, size=self._proj_size,
                debuffs=combined_debuffs,
            )
        return None

    def take_damage(self, amount: int, vulnerable: float = 0.0):
        """受到伤害，先扣头盔和护甲，再考虑易伤增幅"""
        helmet_def = self.helmet.get("defense", 0) if self.helmet else 0
        armor_def = self.armor.get("defense", 0) if self.armor else 0
        total_def = helmet_def + armor_def
        for d in self.debuffs:
            if d["id"] == "armor_break":
                from entities.effects_defs import effect_params
                total_def -= effect_params("armor_break", d.get("level", 1)).get("armor_break", 0)
        # 骨盾技能：吸收固定减伤，原实现赋值后无读取点（与近战基类同一口径）
        skill_def = int(getattr(self, "_skill_defense_bonus", 0) or 0)
        if skill_def > 0:
            total_def += skill_def
        total_def = max(0, total_def)
        actual = max(1, amount - total_def)
        vuln_mult = 1.0 + vulnerable
        for d in self.debuffs:
            if d["id"] == "vulnerable":
                from entities.effects_defs import effect_params
                vuln_mult += effect_params("vulnerable", d.get("level", 1)).get("vulnerable", 0)
        actual = max(1, round(actual * vuln_mult))
        # 阶段 3 护盾词缀：伤害先扣护盾，护盾耗尽后溢出部分才计入生命值
        # （普通怪 shield=0，此分支只多一次属性判定的开销）
        dealt = actual
        if self.shield > 0:
            absorbed = min(self.shield, actual)
            self.shield -= absorbed
            actual -= absorbed
        self.hp -= actual
        self._hit_flash = HIT_FLASH_DURATION
        if self.hp <= 0 and self._on_death_cb:
            # 阶段 3 精英词缀死亡效果（分裂/火墙）须在死亡回调之前触发，
            # 保证 view.monsters / view.fire_zones 追加时怪物仍在场上
            if self.affix is not None:
                from game.monster_affixes import on_affix_death
                view = self._affix_view_provider() if self._affix_view_provider else None
                if view is not None:
                    on_affix_death(self, view)
            self._on_death_cb(self)
        # 返回护盾吸收前的伤害值：伤害飘字按实际打击力度显示，
        # 不因护盾全额吸收而显示 0
        return dealt

    def apply_debuff(self, effect_id: str, level: int = 1) -> None:
        """对怪物施加 debuff——流血可叠加最多 3 层，同类刷新时长；其他效果刷新时长与等级"""
        from entities.effects_defs import EFFECTS, effect_params
        effect = effect_params(effect_id, level)
        if not effect or effect.get("type") != "debuff":
            return
        if effect.get("stacks"):
            stack_count = sum(1 for d in self.debuffs if d["id"] == effect_id)
            if stack_count < 3:
                self.debuffs.append({"id": effect_id, "level": level, "duration": effect.get("duration", 3.0)})
            else:
                for d in self.debuffs:
                    if d["id"] == effect_id:
                        d["duration"] = effect.get("duration", 3.0)
                        d["level"] = max(d.get("level", 1), level)
                        break
            self._recalc_debuffs()
            return
        for d in self.debuffs:
            if d["id"] == effect_id:
                d["duration"] = effect.get("duration", 1.0)
                d["level"] = max(d.get("level", 1), level)
                return
        self.debuffs.append({"id": effect_id, "level": level, "duration": effect.get("duration", 1.0)})
        self._recalc_debuffs()

    def apply_slow(self, slow_mult: float, duration: float) -> None:
        """施加自定义减速（建造陷阱等数据驱动来源传入倍率与时长，覆写标准 slow 参数）"""
        self.apply_debuff("slow", 1)
        for d in self.debuffs:
            if d["id"] == "slow":
                d["slow_mult"] = slow_mult
                d["duration"] = duration
                break
        self._recalc_debuffs()

    def _recalc_debuffs(self):
        """重算减速倍率与眩晕状态"""
        from entities.effects_defs import effect_params
        self._debuff_speed_mult = 1.0
        self._stunned = False
        for d in self.debuffs:
            effect = effect_params(d["id"], d.get("level", 1))
            # 建筑陷阱等数据驱动来源可写入 slow_mult 覆写标准 slow 效果倍率
            slow = d.get("slow_mult", effect.get("slow", 0))
            if slow:
                self._debuff_speed_mult *= (1.0 - slow)
            if effect.get("stun"):
                self._stunned = True

    def _update_debuffs(self, delta_time: float):
        """每 0.5 秒结算一次持续伤害（含流血叠加），递减剩余时长"""
        if not self.debuffs:
            return
        self._debuff_tick -= delta_time
        if self._debuff_tick <= 0:
            self._debuff_tick = DEBUFF_TICK_INTERVAL
            from entities.effects_defs import effect_params
            dot_damage = {}
            for d in list(self.debuffs):
                effect = effect_params(d["id"], d.get("level", 1))
                dmg = effect.get("value", 0)
                if dmg > 0 and effect.get("type") == "debuff":
                    dot_damage[d["id"]] = dot_damage.get(d["id"], 0) + dmg
            for eid, total_dmg in dot_damage.items():
                self.take_damage(total_dmg)
        for d in list(self.debuffs):
            d["duration"] -= delta_time
            if d["duration"] <= 0:
                self.debuffs.remove(d)
        self._recalc_debuffs()

    @property
    def alive(self) -> bool:
        return self.hp > 0