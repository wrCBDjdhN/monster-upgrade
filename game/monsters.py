"""怪物 AI —— 全部怪物基于参数化基类 + MONSTER_CONFIGS 数据驱动

包含 13 种怪物：
- 近战（继承 _MeleeMonsterBase）：Zombie / MummyMelee / BossZombie / BossMummy / Assault
- 远程（继承 _RangedMonsterBase）：Skeleton / MummyRanged / Camel / BossSkeleton / Sniper / Bandit / RocketTroop / BossSpace

数据驱动说明：
- 所有怪物数值（hp/damage/speed/size/color/aggro_range/弹丸参数/debuff_id/boss 标记等）
  统一在 entities/monster_defs.py 的 MONSTER_CONFIGS 中定义，禁止在此文件重复硬编码数值。
- 各怪物类 __init__ 只需 super().__init__(**MONSTER_CONFIGS["类名"]) 从配置取数。
- Zombie 与 Skeleton 已从独立实现重构为复用 _MeleeMonsterBase / _RangedMonsterBase
  （删除约 290 行重复方法，行为不变）。
- RocketTroop 保留 try_attack 覆写：发射带爆炸属性的 AOE 弹丸。

AI 行为：
- 怪物在索敌距离内追踪玩家；近战直接冲向玩家攻击
- 远程怪物保持 120~200 距离，远了靠近、近了后退
- 超出索敌距离则停止追击

碰撞检测：
- 使用 _can_move_to() 进行墙壁碰撞检测
- 怪物不能穿墙，但可以通过门离开房间
"""


import math
import arcade
from config import MAP_WIDTH, MAP_HEIGHT, TILE_SIZE, PROJECTILE_SIZE, PROJECTILE_LIFETIME
from entities.monster_defs import MONSTER_CONFIGS


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


# 单槽墙索引缓存：(walls, _WallGrid)。同局共享同一 walls 列表，切图（新列表）自动重建
_wall_grid_cache = None  # (walls, _WallGrid) | None


def _can_move_to(new_x, new_y, size, walls):
    """检查怪物能否移动到 (new_x, new_y)，不穿墙，不限制房间（可通过门离开）"""
    global _wall_grid_cache
    half = size
    # 空间索引：只检查目标位置附近网格内的墙（性能优化，行为与原全量遍历一致）
    if _wall_grid_cache is None or _wall_grid_cache[0] is not walls:
        _wall_grid_cache = (walls, _WallGrid(walls))
    for wx, wy, ww, wh in _wall_grid_cache[1].nearby(new_x, new_y):
        if (new_x + half > wx and new_x - half < wx + ww and
            new_y + half > wy and new_y - half < wy + wh):
            return False
    # 检查地图边界
    if new_x - half < 0 or new_x + half > MAP_WIDTH:
        return False
    if new_y - half < 0 or new_y + half > MAP_HEIGHT:
        return False
    return True


def _select_target(players, center_x, center_y):
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


class Projectile(arcade.SpriteSolidColor):
    """远程弹丸"""
    def __init__(self, center_x, center_y, target_x, target_y, speed, damage, color=(255, 100, 50), debuff_id=None, size=PROJECTILE_SIZE, special=None):
        super().__init__(size, size, color=color)
        self.center_x = center_x
        self.center_y = center_y
        self.damage = damage
        self._lifetime = PROJECTILE_LIFETIME
        self.debuff_id = debuff_id  # 命中时附加的 debuff（木乃伊远程毒弹/骷髅BOSS冰冻弹）
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

    def update(self, delta_time: float):
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

    def __init__(self, center_x, center_y, size, color, hp, damage, speed,
                 attack_delay, aggro_range, debuff_id=None, is_boss=False,
                 required_weapon_level=0):
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
        # 行为参数
        self._size = size
        self._aggro_range = aggro_range
        self._attack_delay = attack_delay

    def set_on_death(self, cb):
        self._on_death_cb = cb

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        """更新怪物 AI（单目标或多目标模式）

        - 单目标（players=None，单机默认）：沿用原有逻辑，以传入的
          player_x/player_y 为追击目标，行为与之前完全一致。
        - 多目标（players 为玩家列表，联机主机模式）：每帧选取最近存活玩家
          作为当前目标；当前目标死亡/离开后，下一帧自动切换到下一个最近玩家，
          不会原地空转。
        """
        if not self.alive:
            return
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)
        # 附加效果结算（中毒/燃烧掉血、冰冻/减速、眩晕）
        self._update_debuffs(delta_time)
        if self._stunned:
            # 眩晕：无法移动和攻击
            self._attack_timer = max(0, self._attack_timer - delta_time)
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
        # 直接冲向玩家
        move_x = (dx / dist) * self.speed * self._debuff_speed_mult * delta_time
        move_y = (dy / dist) * self.speed * self._debuff_speed_mult * delta_time
        new_x = self.center_x + move_x
        new_y = self.center_y + move_y
        if _can_move_to(new_x, new_y, self._size, self._walls):
            self.center_x = new_x
            self.center_y = new_y
        self._attack_timer = max(0, self._attack_timer - delta_time)

    def try_attack(self, player=None, players=None) -> bool:
        """近战攻击：命中当前目标并结算伤害

        单目标模式传 player（单机路径）；多目标模式传 players 列表，
        自动选取最近存活玩家作为攻击目标（目标切换即时生效）。
        """
        if not self.alive:
            return False
        if self._stunned:
            # 眩晕状态下无法攻击
            return False
        if players is not None:
            # 多目标模式：重新选取最近存活玩家作为攻击目标
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            # 无目标（多目标模式下无存活玩家，或单目标未传入玩家）无法攻击
            return False
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < self._size + 20 and self._attack_timer <= 0:
            self._attack_timer = self._attack_delay
            # 记录本次攻击附带效果：受击钩子（联机 PLAYER_HURT 广播）据此把 debuff+等级
            # 一并下发客户端（修复客户端玩家被怪物攻击时特殊效果未生效）
            player._pending_debuff = self.debuff_id
            player._pending_debuff_level = 1
            player.take_damage(self.damage)
            # 附加效果（如木乃伊攻击附加中毒）
            if self.debuff_id and hasattr(player, "apply_debuff"):
                player.apply_debuff(self.debuff_id)
            return True
        return False

    def take_damage(self, amount: int):
        """受到伤害，先扣头盔和护甲"""
        helmet_def = self.helmet.get("defense", 0) if self.helmet else 0
        armor_def = self.armor.get("defense", 0) if self.armor else 0
        total_def = helmet_def + armor_def
        actual = max(1, amount - total_def)  # 至少1点伤害
        self.hp -= actual
        self._hit_flash = 0.15
        if self.hp <= 0 and self._on_death_cb:
            self._on_death_cb(self)
        return actual

    def apply_debuff(self, effect_id: str, level: int = 1):
        from entities.effects_defs import EFFECTS, effect_params
        effect = effect_params(effect_id, level)
        if not effect or effect.get("type") != "debuff":
            return
        for d in self.debuffs:
            if d["id"] == effect_id:
                # 同类效果刷新时长，并同步效果等级（取较高者）
                d["duration"] = effect.get("duration", 1.0)
                d["level"] = max(d.get("level", 1), level)
                return
        self.debuffs.append({"id": effect_id, "level": level, "duration": effect.get("duration", 1.0)})
        self._recalc_debuffs()

    def _recalc_debuffs(self):
        """重算减速倍率与眩晕状态"""
        from entities.effects_defs import EFFECTS, effect_params
        self._debuff_speed_mult = 1.0
        self._stunned = False
        for d in self.debuffs:
            effect = effect_params(d["id"], d.get("level", 1))
            slow = effect.get("slow", 0)
            if slow:
                self._debuff_speed_mult *= (1.0 - slow)
            if effect.get("stun"):
                self._stunned = True

    def _update_debuffs(self, delta_time: float):
        """每 0.5 秒结算一次持续伤害，递减剩余时长"""
        if not self.debuffs:
            return
        self._debuff_tick -= delta_time
        if self._debuff_tick <= 0:
            self._debuff_tick = 0.5
            from entities.effects_defs import EFFECTS, effect_params
            for d in list(self.debuffs):
                effect = effect_params(d["id"], d.get("level", 1))
                dmg = effect.get("value", 0)
                if dmg > 0:
                    self.take_damage(dmg)
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

    def __init__(self, center_x, center_y, size, color, hp, damage, speed,
                 attack_delay, aggro_range, proj_speed, proj_size, proj_color,
                 debuff_id=None, is_boss=False, required_weapon_level=0):
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
        # 行为参数
        self._size = size
        self._aggro_range = aggro_range
        self._attack_delay = attack_delay

    def set_on_death(self, cb):
        self._on_death_cb = cb

    def update(self, player_x: float = 0.0, player_y: float = 0.0,
               delta_time: float = 0.0, players=None):
        """更新怪物 AI（单目标或多目标模式）

        - 单目标（players=None，单机默认）：沿用原有逻辑，以传入的
          player_x/player_y 为追击目标，行为与之前完全一致。
        - 多目标（players 为玩家列表，联机主机模式）：每帧选取最近存活玩家
          作为当前目标；当前目标死亡/离开后，下一帧自动切换到下一个最近玩家，
          不会原地空转。
        """
        if not self.alive:
            return
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)
        # 附加效果结算
        self._update_debuffs(delta_time)
        if self._stunned:
            self._attack_timer = max(0, self._attack_timer - delta_time)
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
        # 保持距离 120~200
        move_x, move_y = 0, 0
        if dist > 200:
            move_x = (dx / dist) * self.speed * self._debuff_speed_mult * delta_time
            move_y = (dy / dist) * self.speed * self._debuff_speed_mult * delta_time
        elif dist < 120:
            move_x = -(dx / dist) * self.speed * self._debuff_speed_mult * delta_time * 0.5
            move_y = -(dy / dist) * self.speed * self._debuff_speed_mult * delta_time * 0.5
        if move_x != 0 or move_y != 0:
            new_x = self.center_x + move_x
            new_y = self.center_y + move_y
            if _can_move_to(new_x, new_y, self._size, self._walls):
                self.center_x = new_x
                self.center_y = new_y
        self._attack_timer = max(0, self._attack_timer - delta_time)

    def try_attack(self, player=None, players=None) -> Projectile | None:
        """远程攻击：朝当前目标发射弹丸

        单目标模式传 player（单机路径）；多目标模式传 players 列表，
        自动选取最近存活玩家作为攻击目标（目标切换即时生效）。
        """
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
            return None
        if players is not None:
            # 多目标模式：重新选取最近存活玩家作为攻击目标
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            # 无目标（多目标模式下无存活玩家，或单目标未传入玩家）无法攻击
            return None
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < self._aggro_range and self._attack_timer <= 0:
            self._attack_timer = self._attack_delay
            return Projectile(
                self.center_x, self.center_y,
                player.center_x, player.center_y,
                self._proj_speed, self.damage,
                color=self._proj_color, debuff_id=self.debuff_id, size=self._proj_size,
            )
        return None

    def take_damage(self, amount: int):
        """受到伤害，先扣头盔和护甲"""
        helmet_def = self.helmet.get("defense", 0) if self.helmet else 0
        armor_def = self.armor.get("defense", 0) if self.armor else 0
        total_def = helmet_def + armor_def
        actual = max(1, amount - total_def)  # 至少1点伤害
        self.hp -= actual
        self._hit_flash = 0.15
        if self.hp <= 0 and self._on_death_cb:
            self._on_death_cb(self)
        return actual

    def apply_debuff(self, effect_id: str, level: int = 1):
        from entities.effects_defs import EFFECTS, effect_params
        effect = effect_params(effect_id, level)
        if not effect or effect.get("type") != "debuff":
            return
        for d in self.debuffs:
            if d["id"] == effect_id:
                d["duration"] = effect.get("duration", 1.0)
                return
        self.debuffs.append({"id": effect_id, "duration": effect.get("duration", 1.0)})
        self._recalc_debuffs()

    def _recalc_debuffs(self):
        """重算减速倍率与眩晕状态"""
        from entities.effects_defs import EFFECTS, effect_params
        self._debuff_speed_mult = 1.0
        self._stunned = False
        for d in self.debuffs:
            effect = effect_params(d["id"], d.get("level", 1))
            slow = effect.get("slow", 0)
            if slow:
                self._debuff_speed_mult *= (1.0 - slow)
            if effect.get("stun"):
                self._stunned = True

    def _update_debuffs(self, delta_time: float):
        """每 0.5 秒结算一次持续伤害，递减剩余时长"""
        if not self.debuffs:
            return
        self._debuff_tick -= delta_time
        if self._debuff_tick <= 0:
            self._debuff_tick = 0.5
            from entities.effects_defs import EFFECTS, effect_params
            for d in list(self.debuffs):
                effect = effect_params(d["id"], d.get("level", 1))
                dmg = effect.get("value", 0)
                if dmg > 0:
                    self.take_damage(dmg)
        for d in list(self.debuffs):
            d["duration"] -= delta_time
            if d["duration"] <= 0:
                self.debuffs.remove(d)
        self._recalc_debuffs()

    @property
    def alive(self) -> bool:
        return self.hp > 0


class Zombie(_MeleeMonsterBase):
    """僵尸（近战）：血薄攻低但成群刷新，追踪玩家并近距离攻击"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Zombie"])


class Skeleton(_RangedMonsterBase):
    """骷髅（远程）：保持 120~200 距离并发射弹丸"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Skeleton"])


class MummyMelee(_MeleeMonsterBase):
    """木乃伊（近战）：血厚攻高，攻击附加中毒"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["MummyMelee"])


class MummyRanged(_RangedMonsterBase):
    """木乃伊（远程）：发射附中毒的弹丸"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["MummyRanged"])


class Camel(_RangedMonsterBase):
    """骆驼：高血量高移速，吐口水远程攻击（弹丸略慢可躲避）"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Camel"])


class BossZombie(_MeleeMonsterBase):
    """BOSS 僵尸：血厚攻高移速慢，攻击附加燃烧，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossZombie"])


class BossSkeleton(_RangedMonsterBase):
    """BOSS 骷髅：远程弹丸附加冰冻，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossSkeleton"])


class BossMummy(_MeleeMonsterBase):
    """木乃伊 BOSS（金字塔守护者）：血厚攻高移速慢，攻击附加中毒，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossMummy"])


class Sniper(_RangedMonsterBase):
    """狙击兵（远程型，高伤低血）：使用狙击枪，索敌距离超远"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Sniper"])


class Assault(_MeleeMonsterBase):
    """突击兵（近战型，高血高甲）：使用步枪近距离作战"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Assault"])


class Bandit(_RangedMonsterBase):
    """土匪（远程型，低血低甲，成群刷新）：使用手枪或石锤"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["Bandit"])


class RocketTroop(_RangedMonsterBase):
    """火箭兵（远程型，高血高甲，AOE 弹丸）：使用火箭筒"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["RocketTroop"])

    def try_attack(self, player=None, players=None) -> Projectile | None:
        """火箭兵攻击：发射带爆炸属性的弹丸（命中玩家时触发 AOE 范围伤害）

        单目标模式传 player（单机路径）；多目标模式传 players 列表，
        自动选取最近存活玩家作为攻击目标。
        """
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
            return None
        if players is not None:
            # 多目标模式：重新选取最近存活玩家作为攻击目标
            player = _select_target(players, self.center_x, self.center_y)
        if player is None:
            # 无目标（多目标模式下无存活玩家，或单目标未传入玩家）无法攻击
            return None
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < self._aggro_range and self._attack_timer <= 0:
            self._attack_timer = self._attack_delay
            return Projectile(
                self.center_x, self.center_y,
                player.center_x, player.center_y,
                self._proj_speed, self.damage,
                color=self._proj_color, debuff_id=self.debuff_id, size=self._proj_size,
                special="explosive",  # 火箭弹丸爆炸属性
            )
        return None


class BossSpace(_RangedMonsterBase):
    """航天 BOSS（远程型，激光枪）：血厚攻高移速慢，攻击附加燃烧，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["BossSpace"])
