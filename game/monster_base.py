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
import arcade
from config import MAP_WIDTH, MAP_HEIGHT, TILE_SIZE, PROJECTILE_SIZE, PROJECTILE_LIFETIME, MONSTER_AGGRO_RANGE_MULT, MONSTER_AGGRO_RANGE_BASE, PLAYER_SIZE, DEBUFF_TICK_INTERVAL, HIT_FLASH_DURATION, RANGED_KEEP_MIN, RANGED_KEEP_MAX


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

# _has_line_of_sight 复用容器：避免每帧每怪物分配新 list+set（性能优化，单线程安全）
_los_candidates: list = []
_los_seen: set = set()


def _get_wall_grid(walls):
    """获取（并缓存）墙体空间索引：同一 walls 列表只构建一次，供碰撞与视线检测复用"""
    global _wall_grid_cache
    if _wall_grid_cache is None or _wall_grid_cache[0] is not walls:
        _wall_grid_cache = (walls, _WallGrid(walls))
    return _wall_grid_cache[1]


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
        # 技能提示计时器递减
        if getattr(self, '_skill_prompt_timer', 0) > 0:
            self._skill_prompt_timer = max(0, self._skill_prompt_timer - delta_time)
        # 技能 buff 计时器递减（狂暴/骨盾/战术撤退等临时效果到期恢复）
        from game.monster_utils import update_skill_buffs
        update_skill_buffs(self, delta_time)
        # 附加效果结算（中毒/燃烧掉血、冰冻/减速、眩晕）
        self._update_debuffs(delta_time)
        # 装备被动回血（自然恢复等效果）
        if self.regen_per_sec > 0 and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.regen_per_sec * delta_time)
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
        if _can_move_to(new_x, new_y, self._size, self._walls):
            self.center_x = new_x
            self.center_y = new_y
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
            # 攻击时触发技能提示 + 实际效果（概率50%，随机选择技能1或技能2）
            import random
            if random.random() < 0.5:
                from game.monster_utils import apply_skill_prompt_and_effect
                skill_idx = random.randint(0, 1)
                apply_skill_prompt_and_effect(self, skill_idx, target=player)
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
            player.take_damage(self.damage)
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
        total_def = max(0, total_def)  # 防御不低于0
        actual = max(1, amount - total_def)
        # 易伤增幅（debuff 传入 + debuffs 列表累加）
        vuln_mult = 1.0 + vulnerable
        for d in self.debuffs:
            if d["id"] == "vulnerable":
                from entities.effects_defs import effect_params
                vuln_mult += effect_params("vulnerable", d.get("level", 1)).get("vulnerable", 0)
        actual = max(1, round(actual * vuln_mult))
        self.hp -= actual
        self._hit_flash = HIT_FLASH_DURATION
        if self.hp <= 0 and self._on_death_cb:
            self._on_death_cb(self)
        return actual

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

    def _recalc_debuffs(self):
        """重算减速倍率与眩晕状态"""
        from entities.effects_defs import effect_params
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
        # 技能提示计时器递减
        if getattr(self, '_skill_prompt_timer', 0) > 0:
            self._skill_prompt_timer = max(0, self._skill_prompt_timer - delta_time)
        # 技能 buff 计时器递减（狂暴/骨盾/战术撤退等临时效果到期恢复）
        from game.monster_utils import update_skill_buffs
        update_skill_buffs(self, delta_time)
        # 附加效果结算
        self._update_debuffs(delta_time)
        # 装备被动回血（自然恢复等效果）
        if self.regen_per_sec > 0 and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.regen_per_sec * delta_time)
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
            if _can_move_to(new_x, new_y, self._size, self._walls):
                self.center_x = new_x
                self.center_y = new_y
            else:
                # 整体移动被挡：分轴尝试，允许怪物沿墙滑行绕过墙角进入门洞
                if _can_move_to(new_x, self.center_y, self._size, self._walls):
                    self.center_x = new_x
                if _can_move_to(self.center_x, new_y, self._size, self._walls):
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
        # 隔墙（无视线）不开火：与索敌规则一致（边缘视线），防止远程怪隔着墙射击
        if dist < self._aggro_range and self._attack_timer <= 0 and _has_line_of_sight(
                self.center_x, self.center_y, player.center_x, player.center_y, self._walls,
                self._size, PLAYER_SIZE):
            self._attack_timer = self._attack_delay
            # 攻击时触发技能提示 + 实际效果（概率50%，随机选择技能1或技能2）
            import random as _rand
            if _rand.random() < 0.5:
                from game.monster_utils import apply_skill_prompt_and_effect
                skill_idx = _rand.randint(0, 1)
                apply_skill_prompt_and_effect(self, skill_idx, target=player)
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
        total_def = max(0, total_def)
        actual = max(1, amount - total_def)
        vuln_mult = 1.0 + vulnerable
        for d in self.debuffs:
            if d["id"] == "vulnerable":
                from entities.effects_defs import effect_params
                vuln_mult += effect_params("vulnerable", d.get("level", 1)).get("vulnerable", 0)
        actual = max(1, round(actual * vuln_mult))
        self.hp -= actual
        self._hit_flash = HIT_FLASH_DURATION
        if self.hp <= 0 and self._on_death_cb:
            self._on_death_cb(self)
        return actual

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

    def _recalc_debuffs(self):
        """重算减速倍率与眩晕状态"""
        from entities.effects_defs import effect_params
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