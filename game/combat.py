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
"""

import math
import arcade
from config import (
    ATTACK_COOLDOWN, MELEE_RANGE, MELEE_ARC_DEGREES,
    PROJECTILE_SPEED, PROJECTILE_SIZE, PROJECTILE_LIFETIME,
    ROCKET_TROOP_AOE_RADIUS,
)
from .effects import particle_system  # 爆炸粒子效果


class CombatSystem:
    def __init__(self, wall_list: arcade.SpriteList = None):
        self._cooldown = 0.0
        self.projectiles: arcade.SpriteList = arcade.SpriteList()
        self.wall_list = wall_list or arcade.SpriteList()
        # 激光束列表（陨星炮神器武器用）
        self.lasers: list = []

    def update(self, delta_time: float):
        self._cooldown = max(0, self._cooldown - delta_time)
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

    def can_attack(self) -> bool:
        return self._cooldown <= 0

    def melee_attack(self, player, monsters: arcade.SpriteList, weapon_damage: float, weapon_range: float, mouse_x: float = 0, mouse_y: float = 0) -> list:
        """近战攻击：扇形命中检测，返回被击中的怪物列表"""
        if not self.can_attack():
            return []

        # 拳头冷却短一些（0.4秒），武器冷却基于伤害
        if weapon_damage <= 8:
            self._cooldown = 0.4
        else:
            self._cooldown = 1.0 / max(0.1, weapon_damage * 0.1)

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
                actual = m.take_damage(round(weapon_damage))
                hit.append((m, actual))
        return hit

    def ranged_attack(self, player, weapon_damage: float, weapon_proj_speed: float, mouse_x: float = 0, mouse_y: float = 0, weapon_special: str = "", debuff_id: str = None) -> None:
        """远程攻击：生成弹丸，支持特殊属性（穿透/爆炸）与附带 debuff"""
        if not self.can_attack():
            return

        self._cooldown = 1.0 / max(0.1, weapon_damage * 0.08)

        dx = mouse_x - player.center_x
        dy = mouse_y - player.center_y
        dist = math.hypot(dx, dy)
        if dist == 0:
            return
        speed = weapon_proj_speed if weapon_proj_speed else PROJECTILE_SPEED
        proj = _PlayerProjectile(
            player.center_x, player.center_y,
            mouse_x, mouse_y,
            speed, round(weapon_damage),
            special=weapon_special,
            debuff_id=debuff_id,
        )
        self.projectiles.append(proj)

    def check_monster_hits(self, monsters: arcade.SpriteList) -> list:
        """检查弹丸命中怪物，支持穿透和爆炸效果，返回 [(monster, actual_damage)] 列表"""
        hit_monsters = []  # [(monster, actual_damage)]
        for proj in list(self.projectiles):
            hits = arcade.check_for_collision_with_list(proj, monsters)
            if hits:
                for m in hits:
                    if hasattr(m, 'alive') and m.alive:
                        # 弹丸附带 debuff（如权杖随机效果）施加到被命中怪物
                        if getattr(proj, "debuff_id", None) and hasattr(m, "apply_debuff"):
                            m.apply_debuff(proj.debuff_id)
                        # 穿透弹丸：只对未被击中的怪物造成伤害
                        if proj.special == "penetrating":
                            if m not in [h for h, _ in hit_monsters]:
                                actual = m.take_damage(proj.damage)
                                hit_monsters.append((m, actual))
                        # 爆炸弹丸：对命中点周围所有怪物造成伤害
                        elif proj.special == "explosive":
                            explosion_radius = 80  # 爆炸范围
                            # 爆炸粒子特效
                            self._emit_explosion(proj.center_x, proj.center_y)
                            for m2 in monsters:
                                if hasattr(m2, 'alive') and m2.alive:
                                    dist = math.hypot(m2.center_x - m.center_x, m2.center_y - m.center_y)
                                    if dist <= explosion_radius and m2 not in [h for h, _ in hit_monsters]:
                                        actual = m2.take_damage(proj.damage)
                                        hit_monsters.append((m2, actual))
                            proj.remove_from_sprite_lists()
                            break
                        else:
                            actual = m.take_damage(proj.damage)
                            if m not in [h for h, _ in hit_monsters]:
                                hit_monsters.append((m, actual))
                # 非穿透弹丸命中后移除
                if proj.special != "penetrating" and proj in self.projectiles:
                    proj.remove_from_sprite_lists()
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
                    length: float = 600, width: float = 24, duration: float = 3.0):
        """陨星炮：放出一道持续激光，实时跟随鼠标方向，可穿透墙壁

        冷却 = 激光持续时间 + 0.3 秒空隙，防止无缝连发
        """
        if not self.can_attack():
            return None
        self._cooldown = duration + 0.3
        beam = LaserBeam(player, damage, length, width, duration, mouse_x, mouse_y)
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
                 duration: float = 3.0, mouse_x: float = 0.0, mouse_y: float = 0.0):
        self.player = player
        self.damage = damage  # 每秒伤害（DPS）
        self.length = length
        self.width = width
        self.duration = duration
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
                actual = m.take_damage(dmg)
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
        """绘制激光（即时绘制模式，三层：外圈光晕 + 主体 + 中心亮核）"""
        if self.expired:
            return
        sx, sy = self.start_point
        ex, ey = self.end_point
        # 外圈光晕（半透明）
        arcade.draw_line(sx, sy, ex, ey, (255, 100, 255, 60), self.width + 10)
        # 主体
        arcade.draw_line(sx, sy, ex, ey, (255, 255, 255), self.width)
        # 中心亮核
        arcade.draw_line(sx, sy, ex, ey, (255, 180, 255), max(3, self.width // 3))


class _PlayerProjectile(arcade.SpriteSolidColor):
    def __init__(self, cx, cy, tx, ty, speed, damage, special=None, debuff_id=None):
        super().__init__(PROJECTILE_SIZE, PROJECTILE_SIZE, color=(255, 255, 100))
        self.center_x = cx
        self.center_y = cy
        self.damage = damage
        self.special = special  # 穿透/爆炸属性
        self.debuff_id = debuff_id  # 弹丸附带 debuff（如权杖随机效果）
        self._lifetime = PROJECTILE_LIFETIME
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
