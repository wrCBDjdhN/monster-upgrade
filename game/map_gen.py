"""随机地图生成：房间 + 野外

生成流程：
1. 在地图上随机放置不重叠的房间
2. 每个房间有一扇门通向野外
3. 房间内生成资源点和怪物刷新点
4. 野外生成怪物、金币和可破坏环境物

返回数据结构：
- rooms: 房间列表
- walls: 墙壁矩形列表（有门洞）
- resources: 房间内资源点
- evac_points: 撤离点
- spawn_points: 房间内怪物刷新点
- wild_spawns: 野外怪物刷新点
- wild_coins: 野外金币位置
- chest_positions: 宝箱位置
- harvestables: 野外可破坏环境物
"""

import random
from config import (
    MAP_WIDTH, MAP_HEIGHT, TILE_SIZE,
    ROOM_MIN, ROOM_MAX, EVAC_RADIUS,
    ROCKET_PAD_SIZE,
)

# 房间最小间距（像素）：保证房间之间留出足够通道，避免门洞正对邻房墙导致无法进出
ROOM_SPACING = 320
# 野外怪物出生点的半宽缓冲（最大怪物体型约 48px），防止怪物出生时卡进墙壁
MONSTER_HALF = 30
# 水井守卫围绕井口分布的半径（与生成守卫处保持一致）
WELL_GUARD_RADIUS = 120


class Room:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        # 随机选择门的方向: 0=上, 1=下, 2=左, 3=右
        self.door_side = 0
        self.door_pos = 0  # 门在墙上的位置

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def overlaps(self, other, pad=ROOM_SPACING):
        # 增大房间最小间距，避免相邻房间门洞正对时玩家出门即被邻房墙堵住而无法进入
        return not (
            self.x + self.w + pad <= other.x or other.x + other.w + pad <= self.x or
            self.y + self.h + pad <= other.y or other.y + other.h + pad <= self.y
        )

    def __eq__(self, other):
        return (self.x, self.y, self.w, self.h) == (other.x, other.y, other.w, other.h)


def generate_map(seed: int, num_rooms: int = 6, theme: str = "forest") -> dict:
    """
    地图设计：
    - 房间散布在地图上，每个房间有一扇门通向野外
    - 野外是房间之间的空地，会刷新金币和怪物
    - theme: 主题（forest=幽暗森林 / desert=沙漠荒地），决定怪物/资源池与 BOSS 建筑样式

    返回:
      rooms:       list[Room]
      walls:       list[(x,y,w,h)]       墙壁矩形（有门洞）
      resources:   list[(x,y,type)]       房间内资源点
      evac_points: list[(x,y)]            撤离点
      spawn_points: list[(x,y,type)]      房间内怪物刷新点
      wild_spawns: list[(x,y,type)]       野外怪物刷新点
      wild_coins:  list[(x,y)]            野外金币位置
      map_size:    (w, h)
      theme:       str                   当前主题
      boss_spawn:  (x,y)|None            BOSS 建筑内部生成点（每局 1 个）
      boss_type:   str|None              BOSS 类型（boss_zombie/boss_skeleton/boss_mummy）
      water_well:  (x,y)|None            水井位置（仅沙漠主题）
      water_well_guards: list[(x,y,type)] 水井守卫（仅沙漠主题，固定 3 个）
    """
    # 主题非法时回退到森林
    if theme not in ("forest", "desert", "space"):
        theme = "forest"
    # 房间内/野外怪物池（沙漠混入木乃伊与骆驼，保留僵尸骷髅；space=航天基地守卫部队）
    monster_types = (
        ["zombie", "skeleton"]
        if theme == "forest"
        else ["mummy_melee", "mummy_ranged", "camel", "zombie", "skeleton"]
        if theme == "desert"
        else ["sniper", "assault", "bandit", "rocket_troop"]
    )
    # 野外可破坏环境物池（沙漠加入仙人掌；space 暂用现有类型占位）
    harvestable_types = (
        ["tree", "ore", "stone"]
        if theme == "forest"
        else ["cactus", "tree", "ore", "stone"]
        if theme == "desert"
        else ["ore", "stone", "tree"]  # 航天基地：矿石/石头为主
    )
    rng = random.Random(seed)
    rooms: list[Room] = []

    # 生成不重叠房间
    attempts = 0
    while len(rooms) < num_rooms and attempts < 500:
        w = rng.randint(ROOM_MIN, ROOM_MAX) * TILE_SIZE
        h = rng.randint(ROOM_MIN, ROOM_MAX) * TILE_SIZE
        x = rng.randint(TILE_SIZE * 2, MAP_WIDTH - w - TILE_SIZE * 2)
        y = rng.randint(TILE_SIZE * 2, MAP_HEIGHT - h - TILE_SIZE * 2)
        new_room = Room(x, y, w, h)
        if not any(new_room.overlaps(r) for r in rooms):
            # 选择门的方向和位置：优先选择门口通道不被邻近房间堵住的方向
            # （修复：房间过近时若门恰好朝向邻房，玩家出门即被邻房墙挡住，房间无法进入）
            door_sides = [0, 1, 2, 3]
            rng.shuffle(door_sides)
            chosen_side = None
            for side in door_sides:
                # 门口外侧通道矩形（房间边界向外扩 ROOM_SPACING），与任一房间相交即视为被堵
                if side == 0:  # 上
                    gate = (x - ROOM_SPACING, y - ROOM_SPACING, x + w + ROOM_SPACING, y)
                elif side == 1:  # 下
                    gate = (x - ROOM_SPACING, y + h, x + w + ROOM_SPACING, y + h + ROOM_SPACING)
                elif side == 2:  # 左
                    gate = (x - ROOM_SPACING, y - ROOM_SPACING, x, y + h + ROOM_SPACING)
                else:  # 右
                    gate = (x + w, y - ROOM_SPACING, x + w + ROOM_SPACING, y + h + ROOM_SPACING)
                blocked = any(
                    not (other.x + other.w <= gate[0] or other.x >= gate[2] or
                         other.y + other.h <= gate[1] or other.y >= gate[3])
                    for other in rooms
                )
                if not blocked:
                    chosen_side = side
                    break
            if chosen_side is None:
                chosen_side = rng.randint(0, 3)  # 全部方向都被堵时兜底随机
            new_room.door_side = chosen_side
            if chosen_side in (0, 1):  # 上/下门洞在水平方向取位置
                new_room.door_pos = rng.randint(x + TILE_SIZE * 2, x + w - TILE_SIZE * 2)
            else:  # 左/右门洞在垂直方向取位置
                new_room.door_pos = rng.randint(y + TILE_SIZE * 2, y + h - TILE_SIZE * 2)
            rooms.append(new_room)
        attempts += 1

    # ── 墙壁（房间边界，在门处开洞）──
    walls = []
    tw = TILE_SIZE
    door_width = TILE_SIZE * 2  # 门宽 128 像素

    for r in rooms:
        # 上墙
        if r.door_side == 0:  # 门在上
            door_x = r.door_pos
            walls.append((r.x - tw, r.y - tw, door_x - (r.x - tw), tw))
            walls.append((door_x + door_width, r.y - tw, (r.x + r.w + tw) - (door_x + door_width), tw))
        else:
            walls.append((r.x - tw, r.y - tw, r.w + 2 * tw, tw))

        # 下墙
        if r.door_side == 1:  # 门在下
            door_x = r.door_pos
            walls.append((r.x - tw, r.y + r.h, door_x - (r.x - tw), tw))
            walls.append((door_x + door_width, r.y + r.h, (r.x + r.w + tw) - (door_x + door_width), tw))
        else:
            walls.append((r.x - tw, r.y + r.h, r.w + 2 * tw, tw))

        # 左墙
        if r.door_side == 2:  # 门在左
            door_y = r.door_pos
            walls.append((r.x - tw, r.y, tw, door_y - r.y))
            walls.append((r.x - tw, door_y + door_width, tw, (r.y + r.h) - (door_y + door_width)))
        else:
            walls.append((r.x - tw, r.y, tw, r.h))

        # 右墙
        if r.door_side == 3:  # 门在右
            door_y = r.door_pos
            walls.append((r.x + r.w, r.y, tw, door_y - r.y))
            walls.append((r.x + r.w, door_y + door_width, tw, (r.y + r.h) - (door_y + door_width)))
        else:
            walls.append((r.x + r.w, r.y, tw, r.h))

    # ── BOSS 建筑（每局 1 个）：森林=角落 BOSS 房 / 沙漠=金字塔 ──
    # 用墙体围成封闭建筑，留一个门洞入口，内部中心为 BOSS 生成点
    boss_spawn = None
    boss_type = None
    if theme == "forest":
        # 森林 BOSS 随机为 BOSS 僵尸或 BOSS 骷髅
        boss_type = rng.choice(["boss_zombie", "boss_skeleton"])
    elif theme == "desert":
        # 沙漠金字塔内为木乃伊 BOSS
        boss_type = "boss_mummy"
    else:  # space 航天基地 BOSS
        boss_type = "boss_space"

    # 四个角落区域作为候选（远离出生房 rooms[0]）
    corner_rects = [
        (TILE_SIZE * 2, TILE_SIZE * 2, MAP_WIDTH // 3, MAP_HEIGHT // 3),
        (MAP_WIDTH - MAP_WIDTH // 3 - TILE_SIZE * 2, TILE_SIZE * 2, MAP_WIDTH // 3, MAP_HEIGHT // 3),
        (TILE_SIZE * 2, MAP_HEIGHT - MAP_HEIGHT // 3 - TILE_SIZE * 2, MAP_WIDTH // 3, MAP_HEIGHT // 3),
        (MAP_WIDTH - MAP_WIDTH // 3 - TILE_SIZE * 2, MAP_HEIGHT - MAP_HEIGHT // 3 - TILE_SIZE * 2, MAP_WIDTH // 3, MAP_HEIGHT // 3),
    ]
    rng.shuffle(corner_rects)
    # 建筑尺寸：5~7 块瓦片（金字塔略大）
    boss_w = rng.randint(5, 7) * TILE_SIZE
    boss_h = rng.randint(5, 7) * TILE_SIZE

    for crx, cry, crw, crh in corner_rects:
        placed = False
        for _try in range(20):  # 每个角落区域多次尝试，提高放置成功率（修复后门洞双向避让更严格，调高尝试次数补偿）
            bx = rng.randint(crx, crx + crw - boss_w)
            by = rng.randint(cry, cry + crh - boss_h)
            # 检查与现有房间是否过近（留 2 块瓦片间距）
            too_close = False
            for r in rooms:
                if not (
                    bx + boss_w + TILE_SIZE * 2 <= r.x or r.x + r.w + TILE_SIZE * 2 <= bx or
                    by + boss_h + TILE_SIZE * 2 <= r.y or r.y + r.h + TILE_SIZE * 2 <= by
                ):
                    too_close = True
                    break
            if too_close:
                continue
            # 修复：BOSS 建筑与房间门洞走廊双向避让。
            # 房间门洞选择阶段只避让邻近房间（rooms），BOSS 建筑是后生成的，未参与门洞避让；
            # 若 BOSS 墙正对门洞且间距过近（仅 2 瓦片），玩家出门即被 BOSS 墙挡住，无法进入/被困。
            # 修复点：
            # 1) AABB 判定公式修正（原 `boss_rect[0]+boss_rect[2]` 把角点坐标相加，判定恒失效）；
            # 2) 检查范围从仅出生房（rooms[0]）扩展到全部房间门洞走廊；
            # 3) 新增 BOSS 自身门洞走廊（朝地图中心延伸）不被任一房间（含墙）堵住的检查。
            # 门洞开在朝向地图中心的一侧（先算门洞，供走廊避让检查使用）
            center_x, center_y = MAP_WIDTH // 2, MAP_HEIGHT // 2
            b_center_x = bx + boss_w // 2
            b_center_y = by + boss_h // 2
            door_side = 0
            if abs(b_center_x - center_x) >= abs(b_center_y - center_y):
                door_side = 3 if b_center_x < center_x else 2  # 门在右 / 左
            else:
                door_side = 0 if b_center_y < center_y else 1  # 门在上 / 下
            # 门洞位置取建筑边长中点附近；门洞中心（房间门洞走廊以门洞中心为基准，BOSS 门洞同样取中心）
            door_pos = (boss_w if door_side in (0, 1) else boss_h) // 2
            door_cx = bx + door_pos + door_width // 2  # 上/下门洞的水平中心
            door_cy = by + door_pos + door_width // 2  # 左/右门洞的垂直中心
            half = door_width  # 门宽 128px，走廊每侧留一扇门宽余量
            boss_rect = (bx - tw, by - tw, bx + boss_w + tw, by + boss_h + tw)
            # 1) BOSS 建筑（含墙）不得堵住任一房间的门洞出口走廊
            door_blocked = False
            for r in rooms:
                if r.door_side == 0:  # 上
                    door_exit = (r.door_pos - half, r.y - ROOM_SPACING,
                                 r.door_pos + half, r.y)
                elif r.door_side == 1:  # 下
                    door_exit = (r.door_pos - half, r.y + r.h,
                                 r.door_pos + half, r.y + r.h + ROOM_SPACING)
                elif r.door_side == 2:  # 左
                    door_exit = (r.x - ROOM_SPACING, r.door_pos - half,
                                 r.x, r.door_pos + half)
                else:  # 右
                    door_exit = (r.x + r.w, r.door_pos - half,
                                 r.x + r.w + ROOM_SPACING, r.door_pos + half)
                # 修正后的 AABB 相交判定（角点比较，含边界重叠视为堵门）
                if not (
                    boss_rect[2] <= door_exit[0] or door_exit[2] <= boss_rect[0] or
                    boss_rect[3] <= door_exit[1] or door_exit[3] <= boss_rect[1]
                ):
                    door_blocked = True
                    break
            if door_blocked:
                continue
            # 2) BOSS 自身门洞走廊（朝地图中心、向外延伸 ROOM_SPACING）不得被任一房间（含墙）堵住
            if door_side == 0:  # 上
                boss_door_exit = (door_cx - half, by - tw - ROOM_SPACING,
                                  door_cx + half, by - tw)
            elif door_side == 1:  # 下
                boss_door_exit = (door_cx - half, by + boss_h + tw,
                                  door_cx + half, by + boss_h + tw + ROOM_SPACING)
            elif door_side == 2:  # 左
                boss_door_exit = (bx - tw - ROOM_SPACING, door_cy - half,
                                  bx - tw, door_cy + half)
            else:  # 右
                boss_door_exit = (bx + boss_w + tw, door_cy - half,
                                  bx + boss_w + tw + ROOM_SPACING, door_cy + half)
            boss_door_blocked = False
            for r in rooms:
                # 房间含墙矩形（房间墙体厚 tw）
                room_rect = (r.x - tw, r.y - tw, r.x + r.w + tw, r.y + r.h + tw)
                if not (
                    boss_door_exit[2] <= room_rect[0] or room_rect[2] <= boss_door_exit[0] or
                    boss_door_exit[3] <= room_rect[1] or room_rect[3] <= boss_door_exit[1]
                ):
                    boss_door_blocked = True
                    break
            if boss_door_blocked:
                continue

            if door_side == 0:  # 门在上墙
                door_x = bx + door_pos
                walls.append((bx - tw, by - tw, door_x - (bx - tw), tw))
                walls.append((door_x + door_width, by - tw, (bx + boss_w + tw) - (door_x + door_width), tw))
                walls.append((bx - tw, by, tw, boss_h))
                walls.append((bx + boss_w, by, tw, boss_h))
                walls.append((bx - tw, by + boss_h, boss_w + 2 * tw, tw))
            elif door_side == 1:  # 门在下墙
                door_x = bx + door_pos
                walls.append((bx - tw, by - tw, boss_w + 2 * tw, tw))
                walls.append((bx - tw, by, tw, boss_h))
                walls.append((bx + boss_w, by, tw, boss_h))
                walls.append((bx - tw, by + boss_h, door_x - (bx - tw), tw))
                walls.append((door_x + door_width, by + boss_h, (bx + boss_w + tw) - (door_x + door_width), tw))
            elif door_side == 2:  # 门在左墙
                door_y = by + door_pos
                walls.append((bx - tw, by - tw, boss_w + 2 * tw, tw))
                walls.append((bx - tw, by, tw, door_y - by))
                walls.append((bx - tw, door_y + door_width, tw, (by + boss_h) - (door_y + door_width)))
                walls.append((bx + boss_w, by, tw, boss_h))
                walls.append((bx - tw, by + boss_h, boss_w + 2 * tw, tw))
            else:  # 门在右墙
                door_y = by + door_pos
                walls.append((bx - tw, by - tw, boss_w + 2 * tw, tw))
                walls.append((bx + boss_w, by, tw, door_y - by))
                walls.append((bx + boss_w, door_y + door_width, tw, (by + boss_h) - (door_y + door_width)))
                walls.append((bx - tw, by, tw, boss_h))
                walls.append((bx - tw, by + boss_h, boss_w + 2 * tw, tw))
            # 记录 BOSS 生成点（建筑内部中心）
            boss_spawn = (bx + boss_w // 2, by + boss_h // 2)
            # 保存门洞坐标（供游戏逻辑生成临时墙壁锁定房间）
            boss_door = None
            if door_side == 0:  # 门在上墙
                boss_door = {"x": door_cx, "y": by - tw, "side": 0, "width": door_width}
            elif door_side == 1:  # 门在下墙
                boss_door = {"x": door_cx, "y": by + boss_h + tw, "side": 1, "width": door_width}
            elif door_side == 2:  # 门在左墙
                boss_door = {"x": bx - tw, "y": door_cy, "side": 2, "width": door_width}
            else:  # 门在右墙
                boss_door = {"x": bx + boss_w + tw, "y": door_cy, "side": 3, "width": door_width}
            placed = True
            break
        if placed:
            break  # 已放置成功，跳出角落循环

    # 建筑矩形（用于其他生成点避让）
    boss_rect = None
    if boss_spawn:
        boss_rect = (bx, by, bx + boss_w, bx + boss_h)

    def _in_building(px, py):
        """判断坐标是否落在 BOSS 建筑内部（含 1 块瓦片缓冲）"""
        if boss_rect is None:
            return False
        return (boss_rect[0] - TILE_SIZE <= px <= boss_rect[2] + TILE_SIZE and
                boss_rect[1] - TILE_SIZE <= py <= boss_rect[3] + TILE_SIZE)

    def _wild_free(px, py, margin=0):
        """判断坐标是否位于野外空闲处（不在任何房间/BOSS 建筑内）

        margin 为物体半宽缓冲（像素）：用于发射台/水井/怪物等有体积的物体，
        防止其中心虽在野外但边缘与墙壁重叠。
        """
        for r in rooms:
            if (r.x - TILE_SIZE - margin <= px <= r.x + r.w + TILE_SIZE + margin and
                r.y - TILE_SIZE - margin <= py <= r.y + r.h + TILE_SIZE + margin):
                return False
        return not _in_building(px, py)

    # ── 撤离点（space 主题无撤离点，改用发射台）──
    evac_points = []
    if theme != "space":
        candidate_rooms = rooms[1:] if len(rooms) > 1 else rooms
        evac_rooms = rng.sample(candidate_rooms, min(2, len(candidate_rooms)))
        for r in evac_rooms:
            ex, ey = r.center
            evac_points.append((ex, ey))

    # ── 火箭发射台（仅 space 主题，1~4 个随机分布在野外）──
    rocket_pads = []
    if theme == "space":
        from config import ROCKET_PAD_COUNT_MIN, ROCKET_PAD_COUNT_MAX
        pad_count = rng.randint(ROCKET_PAD_COUNT_MIN, ROCKET_PAD_COUNT_MAX)
        for _ in range(pad_count):
            for _attempt in range(40):
                px = rng.randint(TILE_SIZE * 4, MAP_WIDTH - TILE_SIZE * 4)
                py = rng.randint(TILE_SIZE * 4, MAP_HEIGHT - TILE_SIZE * 4)
                # 修复：发射台需距房间/BOSS 建筑留足自身半径（ROCKET_PAD_SIZE），否则会与墙壁重合
                if _wild_free(px, py, ROCKET_PAD_SIZE):
                    rocket_pads.append((px, py))
                    break

    # ── 房间内资源点（每个房间 3~5 个，避开撤离点避免重叠不可见）──
    resource_types = ["wood", "stone", "ore"]
    resources = []
    MIN_RES_EVAC_DIST = EVAC_RADIUS + 40  # 资源点与撤离点最小距离
    for r in rooms:
        n = rng.randint(3, 5)
        for _ in range(n):
            # 默认位置（房间中心偏移），确保即使 20 次尝试都失败也有有效坐标
            rx = r.x + r.w // 2
            ry = r.y + r.h // 2
            # 尝试找到一个不与任何撤离点重合的位置
            for _attempt in range(20):
                rx = rng.randint(r.x + TILE_SIZE, r.x + r.w - TILE_SIZE)
                ry = rng.randint(r.y + TILE_SIZE, r.y + r.h - TILE_SIZE)
                too_close = False
                for ex, ey in evac_points:
                    if ((rx - ex) ** 2 + (ry - ey) ** 2) ** 0.5 < MIN_RES_EVAC_DIST:
                        too_close = True
                        break
                if not too_close:
                    break
            resources.append((rx, ry, rng.choice(resource_types)))

    # ── 房间内怪物刷新点（每个房间 2~4 个，出生房间除外）──
    spawn_points = []
    for idx, r in enumerate(rooms):
        if idx == 0:
            continue  # 出生房间不刷怪物，避免玩家刚出生就被攻击
        n = rng.randint(2, 4)  # 增加怪物数量
        for _ in range(n):
            sx = rng.randint(r.x + TILE_SIZE * 2, r.x + r.w - TILE_SIZE * 2)
            sy = rng.randint(r.y + TILE_SIZE * 2, r.y + r.h - TILE_SIZE * 2)
            spawn_points.append((sx, sy, rng.choice(monster_types)))

    # ── 野外怪物刷新点（地图上随机分布，避开房间与 BOSS 建筑）──
    wild_spawns = []
    for _ in range(8):
        wx = rng.randint(TILE_SIZE * 3, MAP_WIDTH - TILE_SIZE * 3)
        wy = rng.randint(TILE_SIZE * 3, MAP_HEIGHT - TILE_SIZE * 3)
        # 修复：需附怪物半宽缓冲，否则野外怪出生点可能压在房间墙壁上而卡住
        if not _wild_free(wx, wy, MONSTER_HALF):
            continue
        mtype = rng.choice(monster_types)
        if theme == "space" and mtype == "bandit":
            # 修复：航天基地土匪 3~5 只成群生成（围绕中心点聚拢），而非单只出现
            group_n = rng.randint(3, 5)
            for _i in range(group_n):
                ox, oy = wx, wy
                for _off in range(8):  # 聚拢偏移点，找到不卡墙的位置
                    ox = wx + rng.randint(-60, 60)
                    oy = wy + rng.randint(-60, 60)
                    if _wild_free(ox, oy, MONSTER_HALF):
                        break
                wild_spawns.append((ox, oy, "bandit"))
        else:
            wild_spawns.append((wx, wy, mtype))

    # ── 野外金币（地图上随机分布）──
    wild_coins = []
    for _ in range(15):
        wx = rng.randint(TILE_SIZE * 3, MAP_WIDTH - TILE_SIZE * 3)
        wy = rng.randint(TILE_SIZE * 3, MAP_HEIGHT - TILE_SIZE * 3)
        if _wild_free(wx, wy):
            wild_coins.append((wx, wy))

    # ── 宝箱位置（每个房间1个，但跳过出生房间0，避免宝箱压在玩家出生点上）──
    # 同时确保宝箱不与撤离点重合（保持至少 150 像素距离）
    chest_positions = []
    MIN_CHEST_EVAC_DIST = 150  # 宝箱与撤离点最小距离
    for idx, r in enumerate(rooms):
        if idx == 0:
            continue  # 出生房间不刷宝箱，防止出生点出现障碍物
        # 尝试找到一个不与撤离点重合的位置
        for _attempt in range(20):
            cx = rng.randint(r.x + TILE_SIZE * 2, r.x + r.w - TILE_SIZE * 2)
            cy = rng.randint(r.y + TILE_SIZE * 2, r.y + r.h - TILE_SIZE * 2)
            # 检查是否与任何撤离点过近
            too_close = False
            for ex, ey in evac_points:
                if ((cx - ex) ** 2 + (cy - ey) ** 2) ** 0.5 < MIN_CHEST_EVAC_DIST:
                    too_close = True
                    break
            if not too_close:
                chest_positions.append((cx, cy))
                break
        else:
            # 如果20次尝试都失败，使用房间中心偏移位置
            cx, cy = r.center
            chest_positions.append((cx + TILE_SIZE, cy + TILE_SIZE))

    # ── 野外可破坏环境物（森林=树/矿石/石头；沙漠=仙人掌/树/矿石/石头）──
    harvestables = []
    for _ in range(25):
        for _attempt in range(20):
            hx = rng.randint(TILE_SIZE * 3, MAP_WIDTH - TILE_SIZE * 3)
            hy = rng.randint(TILE_SIZE * 3, MAP_HEIGHT - TILE_SIZE * 3)
            if _wild_free(hx, hy, 16):  # 附少量缓冲，避免可破坏物边缘压墙
                harvestables.append((hx, hy, rng.choice(harvestable_types)))
                break

    # ── 水井（仅沙漠主题）：固定 1 口，周围固定刷新 3 个木乃伊守卫 ──
    water_well = None
    water_well_guards = []
    if theme == "desert":
        # 在地图野外随机找一个不与房间/BOSS 建筑重叠的位置
        # 修复：margin 需覆盖守卫环绕半径（WELL_GUARD_RADIUS）+ 守卫半宽，否则周围守卫会卡进房间墙壁
        for _attempt in range(40):
            wx = rng.randint(TILE_SIZE * 4, MAP_WIDTH - TILE_SIZE * 4)
            wy = rng.randint(TILE_SIZE * 4, MAP_HEIGHT - TILE_SIZE * 4)
            if _wild_free(wx, wy, WELL_GUARD_RADIUS + MONSTER_HALF):
                water_well = (wx, wy)
                break
        if water_well:
            # 3 个木乃伊近战守卫均匀分布在井口四周（半径 WELL_GUARD_RADIUS 像素）
            import math
            for i in range(3):
                angle = math.radians(i * 120)
                gx = int(water_well[0] + WELL_GUARD_RADIUS * math.cos(angle))
                gy = int(water_well[1] + WELL_GUARD_RADIUS * math.sin(angle))
                water_well_guards.append((gx, gy, "mummy_melee"))

    return {
        "rooms": rooms,
        "walls": walls,
        "resources": resources,
        "evac_points": evac_points,
        "spawn_points": spawn_points,
        "wild_spawns": wild_spawns,
        "wild_coins": wild_coins,
        "map_size": (MAP_WIDTH, MAP_HEIGHT),
        "chest_positions": chest_positions,
        "harvestables": harvestables,
        "theme": theme,
        "boss_spawn": boss_spawn,
        "boss_type": boss_type,
        "boss_rect": boss_rect,
        "boss_door": boss_door,
        "water_well": water_well,
        "water_well_guards": water_well_guards,
        "rocket_pads": rocket_pads,
    }
