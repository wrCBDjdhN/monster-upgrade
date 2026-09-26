"""局内建造系统：放置校验、资源扣减、建筑实体生命周期、塔/陷阱行为。
联机约定：host 权威执行 place/remove/damage；client 经 MAP_CHANGE 同步（阶段10）。
设计要点：建筑不进 PhysicsEngineSimple 的 obstacle_list——玩家可自由穿过
己方建筑（避免 _wiggle_until_free 弹开与自锁），怪物经 _WallGrid 被阻挡。
"""
import math
import arcade
from config import BUILD_GRID, BUILD_RANGE, MAP_HEIGHT, MAP_WIDTH, TOWER_TARGET_MAX
from entities.build_defs import BUILDS


class Building:
    """单个建筑实体（由渲染层统一绘制血条与方块）"""
    def __init__(self, bid: int, kind: str, x: float, y: float):
        self.bid, self.kind, self.x, self.y = bid, kind, x, y
        self.max_hp = BUILDS[kind]["hp"]
        self.hp = self.max_hp
        # 是否注册为怪物移动障碍：路障/箭塔阻挡，陷阱为地面触发物不阻挡
        self.blocks_monsters = bool(BUILDS[kind].get("blocks_monsters", True))
        self.sprite = arcade.SpriteSolidColor(
            BUILDS[kind]["size"], BUILDS[kind]["size"], BUILDS[kind]["color"])
        self.sprite.position = (x, y)
        self._fire_timer = 0.0   # 塔攻击计时
        self._sprung = False     # 陷阱是否已触发


def _apply_building_slow(monster, build_config: dict) -> None:
    """按建筑数据施加减速（塔类/陷阱类通用）

    口径同原陷阱分支：优先 monster.apply_slow(mult, dur)（数据驱动覆写标准 slow 倍率），
    缺失该方法时回落 apply_debuff("slow", 1)。建筑无 slow_mult 字段则不施加。
    """
    slow_mult = build_config.get("slow_mult")
    if slow_mult is None:
        return
    slow_duration = build_config.get("slow_duration")
    apply_slow = getattr(monster, "apply_slow", None)
    if apply_slow is not None and slow_duration is not None:
        apply_slow(slow_mult, slow_duration)
    elif hasattr(monster, "apply_debuff"):
        monster.apply_debuff("slow", 1)


def _apply_building_stun(monster, build_config: dict) -> None:
    """按建筑数据 stun_duration 施加眩晕（建筑无 stun_duration 字段则不施加）

    复用怪物既有眩晕通道：apply_debuff("stun", 1)（与 monster_utils 技能眩晕、角色技能同机制），
    再照 monster_base.apply_slow 的做法覆写该 debuff 层的 duration 以吃建筑数据时长，
    最后 _recalc_debuffs() 让 `_stunned` 生效——不新增任何怪物状态字段。
    """
    stun_duration = build_config.get("stun_duration")
    if stun_duration is None:
        return
    apply_debuff = getattr(monster, "apply_debuff", None)
    if apply_debuff is None:
        return
    apply_debuff("stun", 1)
    for debuff in getattr(monster, "debuffs", []):
        if debuff.get("id") == "stun":
            debuff["duration"] = stun_duration
            break
    recalc = getattr(monster, "_recalc_debuffs", None)
    if recalc is not None:
        recalc()


class BuildSystem:
    def __init__(self, view):
        self.view = view
        # 与 GameState 共用列表，确保联机快照和视图读取的是同一份运行时状态
        game_state = view.window.game_state
        self.buildings: list[Building] = game_state.buildings
        self.buildings.clear()
        self._next_bid = 1

    def snap(self, x: float, y: float) -> tuple[float, float]:
        """网格吸附：round 到 BUILD_GRID 倍数"""
        return (round(x / BUILD_GRID) * BUILD_GRID,
                round(y / BUILD_GRID) * BUILD_GRID)

    def clear(self) -> None:
        """清空本局建筑并反注册所有怪物碰撞网格。"""
        for building in list(self.buildings):
            self.remove(building.bid)
        self.buildings.clear()

    def get_buildings(self) -> list[Building]:
        """提供给怪物的建筑查询回调（返回共享列表的只读使用约定）。"""
        return self.buildings

    def damage_building(self, bid: int, amount: float) -> bool:
        """提供给怪物的建筑伤害回调。"""
        return self.take_damage(bid, amount)

    def attach_monster(self, monster) -> None:
        """为怪物注入建筑回调，避免怪物持有 GameView 引用。"""
        attach = getattr(monster, "set_building_callbacks", None)
        if attach is not None:
            attach(self.get_buildings, self.damage_building)


    def can_place(self, x: float, y: float, kind: str) -> tuple[bool, str]:
        """按吸附位置校验边界、玩家距离、墙体、占用和携带资源。"""
        if kind not in BUILDS:
            return False, "未知建筑"
        game_state = self.view.window.game_state
        if game_state.net_mode == "client":
            # 阶段1 未实现客户端建造请求协议：本地只做预览，禁止扣资源/落建筑
            return False, "联机建造由房主裁决"
        player = getattr(self.view, "player", None)
        if player is None:
            return False, "无法建造"
        sx, sy = self.snap(x, y)
        half = BUILDS[kind]["size"] / 2
        if sx - half < 0 or sx + half > MAP_WIDTH:
            return False, "超出地图边界"
        if sy - half < 0 or sy + half > MAP_HEIGHT:
            return False, "超出地图边界"
        dist = math.hypot(sx - player.center_x, sy - player.center_y)
        if dist > BUILD_RANGE:
            return False, "太远了"
        # 不压墙：目标格中心点落在 wall_list 任一墙内则拒绝
        if arcade.get_sprites_at_point((sx, sy), self.view.wall_list):
            return False, "不能建在墙上"
        # 不与现有建筑重叠（中心距小于两格）
        for building in self.buildings:
            if math.hypot(building.x - sx, building.y - sy) < BUILD_GRID * 2:
                return False, "位置已被占用"
        # 资源足够：run_carried["resource"] 为 {item_id: qty}
        cost = BUILDS[kind]["cost"]
        resources = game_state.run_carried.get("resource", {})
        for resource_id, needed in cost.items():
            if resources.get(resource_id, 0) < needed:
                return False, "资源不足"
        return True, ""

    def place(self, x: float, y: float, kind: str) -> Building | None:
        """扣资源、创建建筑并注册怪物碰撞网格；失败时返回 None。"""
        ok, _reason = self.can_place(x, y, kind)
        if not ok:
            return None
        game_state = self.view.window.game_state
        cost = BUILDS[kind]["cost"]
        resources = game_state.run_carried.setdefault("resource", {})
        for resource_id, needed in cost.items():
            resources[resource_id] = resources.get(resource_id, 0) - needed
            if resources[resource_id] <= 0:
                del resources[resource_id]
        sx, sy = self.snap(x, y)
        building = Building(self._next_bid, kind, sx, sy)
        self._next_bid += 1
        self.buildings.append(building)
        # 仅阻挡型建筑注册怪物碰撞网格；陷阱为地面触发物，需保持可踩踏
        if building.blocks_monsters:
            half = BUILDS[kind]["size"] / 2
            self.view.monster_grid.add_rect(sx, sy, half)
        return building

    def remove(self, bid: int) -> None:
        """移出列表并反注册怪物碰撞网格（被摧毁/局末清场共用）。"""
        for index, building in enumerate(self.buildings):
            if building.bid == bid:
                if building.blocks_monsters:
                    half = BUILDS[building.kind]["size"] / 2
                    self.view.monster_grid.remove_rect(building.x, building.y, half)
                del self.buildings[index]
                return

    def take_damage(self, bid: int, amount: float) -> bool:
        """扣除建筑生命值；归零时移除并返回 True。"""
        for building in self.buildings:
            if building.bid == bid:
                building.hp -= amount
                if building.hp <= 0:
                    self.remove(bid)
                    return True
                return False
        return False

    def update(self, dt: float) -> None:
        """按建筑能力更新行为（塔类 fire_cd / 陷阱类 trigger_range）；只由 host/solo 调用方驱动。"""
        if self.view.window.game_state.net_mode == "client":
            return
        from game.effects import floating_texts, particle_system
        # 建筑可能在怪物死亡回调/陷阱耗尽时移除，使用快照避免迭代中失效
        for building in list(self.buildings):
            if building.hp <= 0:
                continue
            build_config = BUILDS[building.kind]
            # 能力化调度：有 fire_cd 视为塔类——自动索敌开火（箭塔/瞭望塔/霜冻塔共用）
            if build_config.get("fire_cd") is not None:
                building._fire_timer -= dt
                if building._fire_timer > 0:
                    continue
                building._fire_timer = build_config["fire_cd"]
                targets = []
                for monster in self.view.monsters:
                    if not getattr(monster, "alive", True):
                        continue
                    distance = math.hypot(
                        monster.center_x - building.x,
                        monster.center_y - building.y,
                    )
                    if distance <= build_config["range"]:
                        targets.append((distance, monster))
                targets.sort(key=lambda item: item[0])
                # 目标上限取建筑数据，缺省回落到 config.TOWER_TARGET_MAX
                target_max = build_config.get("target_max", TOWER_TARGET_MAX)
                for _distance, monster in targets[:target_max]:
                    actual = monster.take_damage(build_config["damage"])
                    if actual is None:
                        actual = build_config["damage"]
                    floating_texts.add_damage(
                        monster.center_x, monster.center_y + 25, actual
                    )
                    particle_system.emit(
                        monster.center_x,
                        monster.center_y,
                        4,
                        (255, 220, 120),
                        speed=60,
                        life=0.3,
                        size=3,
                    )
                    # 塔类附带减速（霜冻塔）：命中后按建筑数据施加，复用减速通道
                    _apply_building_slow(monster, build_config)
            # 能力化调度：有 trigger_range 且未触发视为陷阱类——踩中即生效并耗尽
            elif build_config.get("trigger_range") is not None and not building._sprung:
                for monster in self.view.monsters:
                    if not getattr(monster, "alive", True):
                        continue
                    distance = math.hypot(
                        monster.center_x - building.x,
                        monster.center_y - building.y,
                    )
                    if distance > build_config["trigger_range"]:
                        continue
                    actual = monster.take_damage(build_config["damage"])
                    if actual is None:
                        actual = build_config["damage"]
                    _apply_building_slow(monster, build_config)
                    # 陷阱类附带眩晕（捕兽夹）：复用怪物 apply_debuff("stun") 通道
                    _apply_building_stun(monster, build_config)
                    building._sprung = True
                    particle_system.emit(
                        building.x,
                        building.y,
                        10,
                        (120, 200, 120),
                        speed=80,
                        life=0.5,
                        size=4,
                    )
                    self.take_damage(building.bid, building.hp)
                    break