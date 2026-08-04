"""粒子系统与视觉效果"""

import math
import random
import arcade


class Particle:
    """单个粒子"""
    __slots__ = ('x', 'y', 'vx', 'vy', 'life', 'max_life', 'color', 'size', 'gravity')

    def __init__(self, x, y, vx, vy, life, color, size=3, gravity=0):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.life = life
        self.max_life = life
        self.color = color
        self.size = size
        self.gravity = gravity

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy -= self.gravity * dt
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    @property
    def alpha(self):
        return max(0, min(255, int(255 * (self.life / self.max_life))))


class ParticleSystem:
    """粒子系统管理器"""

    def __init__(self):
        self.particles: list[Particle] = []

    def update(self, dt):
        for p in self.particles:
            p.update(dt)
        self.particles = [p for p in self.particles if p.alive]

    def draw(self):
        for p in self.particles:
            alpha = p.alpha
            color = (p.color[0], p.color[1], p.color[2], alpha)
            size = int(p.size * (p.life / p.max_life))
            if size > 0:
                arcade.draw_circle_filled(p.x, p.y, size, color)

    def emit(self, x, y, count, color, speed=100, life=0.5, size=3, gravity=0, spread=360):
        """发射粒子"""
        for _ in range(count):
            angle = random.uniform(0, math.radians(spread))
            spd = random.uniform(speed * 0.5, speed * 1.5)
            vx = math.cos(angle) * spd
            vy = math.sin(angle) * spd
            p = Particle(
                x + random.uniform(-5, 5),
                y + random.uniform(-5, 5),
                vx, vy,
                life * random.uniform(0.7, 1.3),
                color,
                size * random.uniform(0.8, 1.2),
                gravity,
            )
            self.particles.append(p)

    def emit_directional(self, x, y, count, angle, color, speed=100, life=0.5, size=3, spread=30):
        """朝特定方向发射粒子"""
        for _ in range(count):
            a = math.radians(angle + random.uniform(-spread, spread))
            spd = random.uniform(speed * 0.5, speed * 1.5)
            vx = math.cos(a) * spd
            vy = math.sin(a) * spd
            p = Particle(
                x + random.uniform(-3, 3),
                y + random.uniform(-3, 3),
                vx, vy,
                life * random.uniform(0.7, 1.3),
                color,
                size,
                0,
            )
            self.particles.append(p)


class FloatingText:
    """漂浮文字效果"""
    __slots__ = ('x', 'y', 'text', 'color', 'life', 'max_life', 'vy', 'font_size', '_text')

    def __init__(self, x, y, text, color, life=1.0, font_size=14, vy=50):
        self.x = x
        self.y = y
        self.text = text
        self.color = color
        self.life = life
        self.max_life = life
        self.vy = vy
        self.font_size = font_size
        self._text = None

    def update(self, dt):
        self.y += self.vy * dt
        self.vy *= 0.98  # 减速
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    def draw(self):
        # 复用持久 arcade.Text：仅 alpha 每帧变化（着色器 uniform，不重绘纹理），
        # 文本不变则不重设 value -> 避免 draw_text 每帧重建纹理导致卡顿。
        if self._text is None:
            self._text = arcade.Text(
                self.text, self.x, self.y,
                (self.color[0], self.color[1], self.color[2]),
                self.font_size, anchor_x="center", anchor_y="center", bold=True)
        elif self._text.value != self.text:
            self._text.value = self.text
        self._text.position = (self.x, self.y)
        self._text.alpha = max(0, min(255, int(255 * (self.life / self.max_life))))
        self._text.draw()


class FloatingTextManager:
    """漂浮文字管理器"""

    def __init__(self):
        self.texts: list[FloatingText] = []
        # 跟踪最近添加的文字位置，用于避免重叠
        self._recent_positions: list[tuple[float, float, float]] = []  # (x, y, timestamp)
        self._overlap_offset = 18  # 重叠时的垂直偏移量

    def update(self, dt):
        for t in self.texts:
            t.update(dt)
        self.texts = [t for t in self.texts if t.alive]
        # 清理过期的位置记录
        self._recent_positions = [(x, y, life - dt) for x, y, life in self._recent_positions if life - dt > 0]

    def draw(self):
        for t in self.texts:
            t.draw()

    def _find_offset(self, x: float, y: float) -> float:
        """查找当前位置的垂直偏移量，避免文字重叠"""
        offset = 0
        for rx, ry, _ in self._recent_positions:
            if abs(rx - x) < 30 and abs(ry - (y + offset)) < 15:
                offset += self._overlap_offset
        return offset

    def add(self, x, y, text, color=(255, 255, 255), life=1.0, font_size=14, vy=50):
        """添加漂浮文字（自动避免重叠）"""
        offset = self._find_offset(x, y)
        ft = FloatingText(x, y + offset, text, color, life, font_size, vy)
        self.texts.append(ft)
        self._recent_positions.append((x, y + offset, life))

    def add_gold(self, x, y, amount):
        """金币拾取提示"""
        self.add(x, y + 20, f"+{amount}G", (255, 215, 0), life=1.2, font_size=16, vy=60)

    def add_resource(self, x, y, name, amount):
        """资源拾取提示"""
        self.add(x, y + 20, f"+{amount} {name}", (180, 220, 180), life=1.0, font_size=12, vy=50)

    def add_weapon(self, x, y, name):
        """武器拾取提示"""
        self.add(x, y + 20, f"获得 {name}!", (100, 200, 255), life=1.5, font_size=16, vy=40)

    def add_damage(self, x, y, amount):
        """伤害数字"""
        self.add(x, y, f"-{amount}", (255, 80, 80), life=0.8, font_size=12, vy=80)

    def add_heal(self, x, y, amount):
        """治疗数字"""
        self.add(x, y, f"+{amount}", (80, 255, 80), life=0.8, font_size=12, vy=60)


# 全局实例
particle_system = ParticleSystem()
floating_texts = FloatingTextManager()
