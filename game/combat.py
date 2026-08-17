"""玩家战斗系统：近战挥砍 + 远程弹丸 + 攻速冷却

核心类：CombatSystem
- 管理攻击冷却时间
- 近战：扇形命中检测，120° 扇形范围
- 远程：生成弹丸，飞行碰撞检测

攻击机制：
- 近战攻击有冷却时间（基于武器伤害）
- 远程攻击生成弹丸，弹丸有飞行时间限制
- 弹丸碰墙或超时后消失
- 特殊弹丸：穿透（laser_gun）、爆炸（rocket_launcher）

冷却机制（联机支持）：
- 冷却按攻击者 id 分别记录（_cooldowns 字典），各玩家独立计时
- 单人模式调用方不传 attacker_id，默认使用 0 号键，行为与旧版完全一致
"""

import math
import arcade
from config import (
    ATTACK_COOLDOWN, MELEE_RANGE, MELEE_ARC_DEGREES,
    PROJECTILE_SPEED, PROJECTILE_SIZE, PROJECTILE_LIFETIME,
    ROCKET_TROOP_AOE_RADIUS,
)
from .effects import particle_system  # 爆炸粒子效果
from .batch_shapes import ShapeBatch  # 批量绘制：激光一次 draw call 提交（性能优化）


class CombatSystem:
    def __init__(self, wall_list: arcade.SpriteList = None):
        # 攻击冷却字典：{攻击者 id: 剩余冷却秒}，各攻击者独立冷却
        # 联机时主机代跑多个玩家的战斗，玩家 A 攻击不会消耗玩家 B 的冷却
        self._cooldowns: dict[int, float] = {}
        self.projectiles: arcade.SpriteList = arcade.SpriteList()
        self.wall_list = wall_list or arcade.SpriteList()
        # 激光束列表（陨星炮神器武器用）
        self.lasers: list = []

    def update(self, delta_time: float):
        # 各攻击者的冷却独立递减，归零后移除该键（与旧版单值递减语义一致）
        for k in list(self._cooldowns):
            v = self._cooldowns[k] - delta_time
            if v <= 0:
                del self._cooldowns[k]
            else:
                self._cooldowns[k] = v
        # 更新弹丸
        for p in self.projectiles:
            p.update(delta_time)
        # 移除过期弹丸 或 碰墙弹丸
        for p in [p for p in self.projectiles]:
            if p.expired:
                p.remove_from_sprite_lists()
                continue
            if arcade.check_for_collision_with_list(p, self.wall_list):
                # 爆炸弹丸碰墙时触发爆炸特效
                if p.special == "explosive":
                    self._emit_explosion(p.center_x, p.center_y)
                p.remove_from_sprite_lists()

    def _emit_explosion(self, x: float, y: float):
        """发射爆炸粒子特效"""
        particle_system.emit(
            x, y, 40,
            (255, 120, 30),  # 橙红色火焰
            speed=200, life=0.4, size=6,
            gravity=50, spread=360,
        )
        particle_system.emit(
            x, y, 20,
            (255, 200, 50),  # 明亮黄色火花
            speed=150, life=0.3, size=4,
            gravity=30, spread=360,
        )

    def can_attack(self, attacker_id: int = 0) -> bool:
        """指定攻击者是否可攻击（冷却已归零）

        attacker_id：攻击者标识；默认 0 = 单人模式（调用方不传则使用默认键）
        """
        return self._cooldowns.get(attacker_id, 0.0) <= 0

    def melee_attack(self, player, monsters: arcade.SpriteList, weapon_damage: float, weapon_range: float, mouse_x: float = 0, mouse_y: float = 0, weapon_speed: float = 1.0, attacker_id: int = 0, lifesteal: float = 0.0) -> list:
        """近战攻击：扇形命中检测，返回被击中的怪物列表

        attacker_id：攻击者标识（联机时各玩家独立冷却），默认 0 = 单人模式
        lifesteal：吸血比例（0~1），命中造成实际伤害的该比例转化为攻击者生命回复
        """
        if not self.can_attack(attacker_id):
            return []

        # 冷却时间 = 1 / attack_speed（attack_speed 越高，冷却越短，攻击越快）
        self._cooldowns[attacker_id] = 1.0 / max(0.1, weapon_speed)

        # 角色被动攻击修正（法师+20%/骑士低血+30%/刺客暴击，见 character_skills.py）
        from game.character_skills import modify_attack_damage  # 延迟导入避免循环依赖
        weapon_damage = modify_attack_damage(player, weapon_damage)

        # 获取鼠标方向角度
        dx = mouse_x - player.center_x
        dy = mouse_y - player.center_y
        attack_angle = math.degrees(math.atan2(dy, dx))

        hit = []  # [(monster, actual_damage)]
        for m in monsters:
            if not hasattr(m, 'alive') or not m.alive:
                continue
            mdx = m.center_x - player.center_x
            mdy = m.center_y - player.center_y
            dist = math.hypot(mdx, mdy)
            if dist > weapon_range + 30:
                continue
            monster_angle = math.degrees(math.atan2(mdy, mdx))
            angle_diff = abs((monster_angle - attack_angle + 180) % 360 - 180)
            if angle_diff <= MELEE_ARC_DEGREES / 2:
                # 记录击杀归属（等级经验：主机按 last_attacker_id 判断是否本端玩家击杀）
                m.last_attacker_id = attacker_id
                actual = m.take_damage(round(weapon_damage))
                hit.append((m, actual))
        # 吸血：按全部命中造成的实际伤害合计回血（如吸血剑 lifesteal=0.15）
        if lifesteal > 0 and hit:
            heal_amount = round(sum(actual for _, actual in hit) * lifesteal)
            if heal_amount > 0 and hasattr(player, "heal"):
                player.heal(heal_amount)
        return hit

    def ranged_attack(self, player, weapon_damage: float, weapon_proj_speed: float, mouse_x: float = 0, mouse_y: float = 0, weapon_special: str = "", debuff_id: str = None, weapon_speed: float = 1.0, attacker_id: int = 0, debuffs: list = None, lifesteal: float = 0.0, spread_count: int = 1, spread_angle: float = 0.0) -> None:
        """远程攻击：生成弹丸，支持特殊属性（穿透/爆炸）与附带 debuff

        attacker_id：攻击者标识（联机时各玩家独立冷却），默认 0 = 单人模式
        debuffs：弹丸附带的 debuff 列表（元素为 (效果ID, 效果等级) 元组），
                 联机客户端装备附加效果由此携带（debuff_id 保留兼容，二者合并应用）
        lifesteal：吸血比例（0~1），命中实际伤害的该比例转化为攻击者生命回复
        spread_count：一次发射的弹丸数量（>1 时散射，如三连散射炮 spread_count=3）
        spread_angle：相邻弹丸的夹角（度），弹丸围绕鼠标方向均匀分布
        """
        if not self.can_attack(attacker_id):
            return

        # 冷却时间 = 1 / attack_speed（attack_speed 越高，冷却越短，攻击越快）
        self._cooldowns[attacker_id] = 1.0 / max(0.1, weapon_speed)

        # 角色被动攻击修正（法师+20%/骑士低血+30%/刺客暴击，见 character_skills.py）
        from game.character_skills import modify_attack_damage  # 延迟导入避免循环依赖
        weapon_damage = modify_attack_damage(player, weapon_damage)

        dx = mouse_x - player.center_x
        dy = mouse_y - player.center_y
        dist = math.hypot(dx, dy)
        if dist == 0:
            return
        speed = weapon_proj_speed if weapon_proj_speed else PROJECTILE_SPEED
        base_angle = math.atan2(dy, dx)
        # 散射：以鼠标方向为基准，弹丸按夹角均匀分布（中心对称）
        count = max(1, int(spread_count))
        for i in range(count):
            if count > 1:
                # 第 i 发相对基准角的偏移：从 -(n-1)/2 到 +(n-1)/2 均匀分布
                offset = (i - (count - 1) / 2.0) * math.radians(spread_angle)
                angle = base_angle + offset
                tx = player.center_x + math.cos(angle) * dist
                ty = player.center_y + math.sin(angle) * dist
            else:
                tx, ty = mouse_x, mouse_y
            proj = _PlayerProjectile(
                player.center_x, player.center_y,
                tx, ty,
                speed, round(weapon_damage),
                special=weapon_special,
                debuff_id=debuff_id,
                debuffs=debuffs,
                owner_net_id=attacker_id,
                owner_player=player,
                lifesteal=lifesteal,
            )
            self.projectiles.append(proj)

    def check_monster_hits(self, monsters: arcade.SpriteList) -> list:
        """检查弹丸命中怪物，支持穿透和爆炸效果，返回 [(monster, actual_damage)] 列表"""
        hit_monsters = []  # [(monster, actual_damage)]
        hit_set = set()    # 已命中怪物集合：O(1) 查重，替代每次重建列表线性查找（性能优化）
        for proj in list(self.projectiles):
            # 本弹丸独立命中的怪物（吸血按本弹丸实际伤害结算，避免累计其他弹丸命中）
            proj_hits = []
            hits = arcade.check_for_collision_with_list(proj, monsters)
            if hits:
                for m in hits:
                    if hasattr(m, 'alive') and m.alive:
                        # 弹丸命中归属记录（等级经验：主机按 last_attacker_id 判断是否本端玩家击杀）
                        m.last_attacker_id = proj.owner_net_id
                        # 弹丸附带 debuff 施加到被命中怪物（含权杖随机效果与联机客户端装备附加效果，
                        # 兼容旧 debuff_id：已合并进 proj.debuffs）
                        for eid, lvl in getattr(proj, "debuffs", []):
                            if hasattr(m, "apply_debuff"):
                                m.apply_debuff(eid, lvl)
                        # 穿透弹丸：只对未被击中的怪物造成伤害
                        if proj.special == "penetrating":
                            if m not in hit_set:
                                actual = m.take_damage(proj.damage)
                                hit_monsters.append((m, actual))
                                hit_set.add(m)
                                proj_hits.append((m, actual))
                        # 爆炸弹丸：对命中点周围所有怪物造成伤害
                        elif proj.special == "explosive":
                            explosion_radius = 80  # 爆炸范围
                            # 爆炸粒子特效
                            self._emit_explosion(proj.center_x, proj.center_y)
                            for m2 in monsters:
                                if hasattr(m2, 'alive') and m2.alive:
                                    dist = math.hypot(m2.center_x - m.center_x, m2.center_y - m.center_y)
                                    if dist <= explosion_radius and m2 not in hit_set:
                                        # 爆炸波及怪物同样记录归属（等级经验判定用）
                                        m2.last_attacker_id = proj.owner_net_id
                                        actual = m2.take_damage(proj.damage)
                                        hit_monsters.append((m2, actual))
                                        hit_set.add(m2)
                                        proj_hits.append((m2, actual))
                            proj.remove_from_sprite_lists()
                            break
                        else:
                            actual = m.take_damage(proj.damage)
                            if m not in hit_set:
                                hit_monsters.append((m, actual))
                                hit_set.add(m)
                                proj_hits.append((m, actual))
                # 非穿透弹丸命中后移除
                if proj.special != "penetrating" and proj in self.projectiles:
                    proj.remove_from_sprite_lists()
            # 吸血：本弹丸命中造成实际伤害后，按比例回复攻击者生命（如吸血剑）
            if getattr(proj, "lifesteal", 0) > 0 and proj_hits:
                heal_amount = round(
                    sum(actual for _, actual in proj_hits) * proj.lifesteal
                )
                if heal_amount > 0 and getattr(proj, "owner_player", None) is not None:
                    proj.owner_player.heal(heal_amount)
        return hit_monsters

    def check_projectile_aoe(self, player, aoe_radius: float = ROCKET_TROOP_AOE_RADIUS) -> list:
        """检查怪物弹丸的 AOE 伤害：火箭兵弹丸爆炸时对玩家周围造成范围伤害

        注意：这是怪物弹丸对玩家的 AOE，不是玩家弹丸对怪物的 AOE
        返回 [(target, actual_damage)] 列表（目前只有玩家一个目标）
        """
        # 此方法由 game_view.py 在怪物弹丸命中玩家时调用
        # 实际 AOE 逻辑在 game_view.py 的弹丸碰撞检测中处理
        return []

    def emit_aoe_explosion(self, x: float, y: float, radius: float, damage: float, player):
        """AOE 爆炸：对半径内的玩家造成伤害并触发爆炸特效（供 game_view.py 调用）"""
        dist = math.hypot(player.center_x - x, player.center_y - y)
        if dist <= radius:
            actual = player.take_damage(round(damage))
            # 爆炸粒子特效
            self._emit_explosion(x, y)
            return actual
        return 0

    def spawn_laser(self, player, damage: float, mouse_x: float, mouse_y: float,
                    length: float = 600, width: float = 24, duration: float = 3.0,
                    attacker_id: int = 0, debuffs: list = None):
        """陨星炮：放出一道持续激光，实时跟随鼠标方向，可穿透墙壁

        冷却 = 激光持续时间 + 0.3 秒空隙，防止无缝连发
        attacker_id：攻击者标识（联机时各玩家独立冷却），默认 0 = 单人模式
        debuffs：激光附带 debuff 列表（元素为 (效果ID, 效果等级) 元组），
                 联机客户端装备附加效果由此携带，命中时一并施加
        """
        if not self.can_attack(attacker_id):
            return None
        self._cooldowns[attacker_id] = duration + 0.3
        beam = LaserBeam(player, damage, length, width, duration, mouse_x, mouse_y,
                         debuffs=debuffs, owner_net_id=attacker_id)
        self.lasers.append(beam)
        return beam

    def update_lasers(self, delta_time: float, mouse_x: float, mouse_y: float):
        """更新所有激光（角度实时跟随鼠标），移除过期激光"""
        for beam in list(self.lasers):
            beam.update(delta_time, mouse_x, mouse_y)
            if beam.expired:
                self.lasers.remove(beam)

    def check_laser_hits(self, monsters) -> list:
        """激光命中检测：对激光路径上的怪物持续造成伤害

        返回 [(monster, actual_damage)] 列表（每0.1秒结算一次）
        """
        hit_monsters = []
        for beam in self.lasers:
            hit_monsters.extend(beam.hit_monsters(monsters))
        return hit_monsters

    def draw(self):
        self.projectiles.draw()
        # 绘制激光束（即时模式，支持半透明光晕）
        for beam in self.lasers:
            beam.draw()


class LaserBeam:
    """陨星炮激光束：从玩家位置出发，实时跟随鼠标方向，持续造成伤害

    - 起点始终为玩家当前位置（玩家移动时激光跟随）
    - 方向实时跟随鼠标位置转动
    - 可穿透墙壁，不因碰撞消失
    - 命中即结算一次完整伤害（单次伤害 = damage），每个目标独立1秒冷却（DPS = damage），进入路径立即受伤
    """

    def __init__(self, player, damage: float, length: float = 600, width: float = 24,
                 duration: float = 3.0, mouse_x: float = 0.0, mouse_y: float = 0.0,
                 debuffs: list = None, owner_net_id: int = 0):
        self.player = player
        self.damage = damage  # 每秒伤害（DPS）
        self.length = length
        self.width = width
        self.duration = duration
        # 激光附带 debuff 列表（元素为 (效果ID, 效果等级) 元组）：联机客户端装备附加效果由此携带
        self.debuffs: list = list(debuffs or [])
        # 联机：发射者玩家 id（主机序列化 PROJECTILE_SNAPSHOT 的 lasers 用 owner_id 标识，
        # 客户端据此跳过自己发射的激光——本地已有纯表现激光，避免双重渲染）
        self.owner_net_id = owner_net_id
        # 联机：激光网络 id（主机首次序列化时惰性分配，存活期不变）
        self.proj_id = None
        self._target_cooldowns = {}  # {id(目标): 剩余冷却秒}，每个目标独立冷却，命中即结算
        self._hit_interval = 1.0  # 每个目标每1秒最多受到一次完整伤害（单次伤害与武器面板一致）
        self.expired = False
        self.angle = math.atan2(mouse_y - player.center_y, mouse_x - player.center_x)

    @property
    def start_point(self):
        """激光起点 = 玩家当前位置（移动时激光跟随玩家）"""
        return (self.player.center_x, self.player.center_y)

    @property
    def end_point(self):
        sx, sy = self.start_point
        return (sx + math.cos(self.angle) * self.length,
                sy + math.sin(self.angle) * self.length)

    def update(self, delta_time: float, mouse_x: float, mouse_y: float):
        self.duration -= delta_time
        if self.duration <= 0:
            self.expired = True
            return
        # 实时跟随鼠标方向（光束枪模式）
        self.angle = math.atan2(mouse_y - self.player.center_y, mouse_x - self.player.center_x)
        # 各目标独立冷却递减
        for k in list(self._target_cooldowns):
            v = self._target_cooldowns[k] - delta_time
            if v <= 0:
                del self._target_cooldowns[k]
            else:
                self._target_cooldowns[k] = v

    @staticmethod
    def _point_segment_distance(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
        """点到线段的最短距离"""
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        cx, cy = x1 + t * dx, y1 + t * dy
        return math.hypot(px - cx, py - cy)

    def hit_monsters(self, monsters) -> list:
        """激光命中检测：路径上的怪物命中即结算完整伤害，每个目标独立1秒冷却"""
        if self.expired:
            return []
        hits = []
        sx, sy = self.start_point
        ex, ey = self.end_point
        dmg = max(1, round(self.damage * self._hit_interval))
        for m in monsters:
            if not hasattr(m, 'alive') or not m.alive:
                continue
            # 每个目标独立冷却：冷却中不再结算（避免同目标每帧重复受伤）
            if self._target_cooldowns.get(id(m), 0) > 0:
                continue
            # 怪物半径按宽高一半取平均
            radius = (getattr(m, 'width', 16) + getattr(m, 'height', 16)) / 4.0
            if self._point_segment_distance(m.center_x, m.center_y, sx, sy, ex, ey) <= self.width / 2 + radius:
                self._target_cooldowns[id(m)] = self._hit_interval
                # 激光击杀归属记录（等级经验：主机按 last_attacker_id 判断是否本端玩家击杀）
                m.last_attacker_id = self.owner_net_id
                actual = m.take_damage(dmg)
                # 激光附带 debuff 施加到被命中怪物（联机客户端装备附加效果，命中即生效）
                for eid, lvl in self.debuffs:
                    if hasattr(m, "apply_debuff"):
                        m.apply_debuff(eid, lvl)
                hits.append((m, actual))
        return hits

    def hit_harvestables(self, harvestables) -> list:
        """激光命中环境物检测：矿石/树木/石头等命中即结算完整伤害，每个目标独立1秒冷却"""
        if self.expired:
            return []
        hits = []
        sx, sy = self.start_point
        ex, ey = self.end_point
        dmg = max(1, round(self.damage * self._hit_interval))
        for h in harvestables:
            if not h.alive:
                continue
            # 每个目标独立冷却：冷却中不再结算（避免同目标每帧重复受伤）
            if self._target_cooldowns.get(id(h), 0) > 0:
                continue
            # 环境物半径按宽高一半取平均
            radius = (getattr(h, 'width', 16) + getattr(h, 'height', 16)) / 4.0
            if self._point_segment_distance(h.center_x, h.center_y, sx, sy, ex, ey) <= self.width / 2 + radius:
                self._target_cooldowns[id(h)] = self._hit_interval
                h.take_damage(dmg)
                hits.append((h, dmg))
        return hits

    def draw(self):
        """绘制激光（批量绘制：ShapeBatch.line 一次 draw call 提交三层，替代即时模式）"""
        if self.expired:
            return
        sx, sy = self.start_point
        ex, ey = self.end_point
        batch = ShapeBatch()
        # 外圈光晕（半透明）
        batch.line(sx, sy, ex, ey, (255, 100, 255, 60), self.width + 10)
        # 主体
        batch.line(sx, sy, ex, ey, (255, 255, 255), self.width)
        # 中心亮核
        batch.line(sx, sy, ex, ey, (255, 180, 255), max(3, self.width // 3))
        batch.draw()


class _PlayerProjectile(arcade.SpriteSolidColor):
    def __init__(self, cx, cy, tx, ty, speed, damage, special=None, debuff_id=None, debuffs=None, owner_net_id: int = 0, owner_player=None, lifesteal: float = 0.0):
        super().__init__(PROJECTILE_SIZE, PROJECTILE_SIZE, color=(255, 255, 100))
        self.center_x = cx
        self.center_y = cy
        self.damage = damage
        self.special = special  # 穿透/爆炸属性
        self.debuff_id = debuff_id  # 弹丸附带 debuff（如权杖随机效果）
        # 弹丸附带 debuff 列表（元素为 (效果ID, 效果等级)）：联机客户端装备附加效果由此携带，
        # 命中时全部施加（与 debuff_id 兼容：二者合并后统一应用）
        self.debuffs: list = list(debuffs or [])
        if debuff_id and debuff_id not in [d[0] for d in self.debuffs]:
            self.debuffs.append((debuff_id, 1))
        self._lifetime = PROJECTILE_LIFETIME
        # 联机：发射者玩家 id（主机序列化 PROJECTILE_SNAPSHOT 用 owner_id 标识，
        # 客户端据此跳过自己发射的弹丸——本地已有纯表现弹丸，避免双重渲染）
        self.owner_net_id = owner_net_id
        # 弹丸发射者玩家对象（吸血用，命中时按实际伤害比例回血；单机=玩家本人，联机主机=幽灵）
        self.owner_player = owner_player
        # 吸血比例（0~1）：命中怪物后按实际伤害的该比例回复攻击者生命
        self.lifesteal = lifesteal
        # 联机：弹丸网络 id（主机首次序列化时惰性分配，存活期不变）
        self.proj_id = None
        dx = tx - cx
        dy = ty - cy
        dist = math.hypot(dx, dy)
        if dist > 0:
            self.change_x = (dx / dist) * speed
            self.change_y = (dy / dist) * speed
        else:
            self.change_x = speed
            self.change_y = 0
        # 爆炸弹丸使用红色
        if special == "explosive":
            self.color = (255, 80, 30)
        # 穿透弹丸使用青色
        elif special == "penetrating":
            self.color = (0, 220, 255)

    def update(self, delta_time: float = 0):
        self.center_x += self.change_x * delta_time
        self.center_y += self.change_y * delta_time
        self._lifetime -= delta_time

    @property
    def expired(self) -> bool:
        return self._lifetime <= 0
