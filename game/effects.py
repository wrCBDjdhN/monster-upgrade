"""粒子系统与视觉效果（对象池优化版）

性能优化：
- Particle 对象池：预分配复用，避免每帧 new/delete 触发 GC
- FloatingText 对象池：预分配复用，arcade.Text 纹理缓存自动复用
- numpy 向量化粒子发射计算（角度/速度批量生成）
"""

import math
import random
import numpy as np
import arcade
from .batch_shapes import ShapeBatch  # 批量绘制：粒子一次 draw call 提交（性能优化）


# ── 粒子对象池 ──
_PARTICLE_POOL: list = []  # 空闲粒子缓存池
_PARTICLE_POOL_MAX = 2000  # 池上限（防止内存无限增长）


class Particle:
    """单个粒子（__slots__ 内存紧凑）"""
    __slots__ = ('x', 'y', 'vx', 'vy', 'life', 'max_life', 'color', 'size', 'gravity')

    def __init__(self, x=0, y=0, vx=0, vy=0, life=0, color=(255, 255, 255), size=3, gravity=0):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.life = life
        self.max_life = life
        self.color = color
        self.size = size
        self.gravity = gravity

    def reset(self, x, y, vx, vy, life, color, size=3, gravity=0):
        """重置粒子状态（对象池复用，避免重新分配内存）"""
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


def _acquire_particle() -> Particle:
    """从对象池获取一个粒子（池空则新建）"""
    if _PARTICLE_POOL:
        return _PARTICLE_POOL.pop()
    return Particle()


def _release_particle(p: Particle):
    """归还粒子到对象池（池满则丢弃让 GC 回收）"""
    if len(_PARTICLE_POOL) < _PARTICLE_POOL_MAX:
        _PARTICLE_POOL.append(p)


class ParticleSystem:
    """粒子系统管理器（对象池优化）"""

    def __init__(self):
        self.particles: list[Particle] = []

    def update(self, dt):
        # 就地过滤存活粒子：先更新后原地压缩，避免每帧新建列表分配内存
        write = 0
        for i in range(len(self.particles)):
            p = self.particles[i]
            p.update(dt)
            if p.life > 0:
                self.particles[write] = p
                write += 1
            else:
                _release_particle(p)  # 归还到对象池
        # 截断已死粒子
        del self.particles[write:]

    def draw(self):
        """批量绘制全部粒子（ShapeBatch 一次 draw call，替代逐粒子立即模式绘制）"""
        if not self.particles:
            return
        batch = ShapeBatch()
        for p in self.particles:
            alpha = p.alpha
            if alpha <= 0:
                continue
            color = (p.color[0], p.color[1], p.color[2], alpha)
            size = int(p.size * (p.life / p.max_life))
            if size > 0:
                batch.circle(p.x, p.y, size, color)
        batch.draw()

    def emit(self, x, y, count, color, speed=100, life=0.5, size=3, gravity=0, spread=360, angle=None):
        """发射粒子（numpy 向量化 + 对象池）

        angle=None：向 0~spread 度均匀随机方向扩散（默认 360° 全向）；
        指定 angle：朝该方向发射（spread 为角度抖动范围）。
        """
        if count <= 0:
            return
        # numpy 批量生成角度和偏移（一次调用替代 count 次 random）
        if angle is None:
            angles = np.random.uniform(0, math.radians(spread), count)
            ox = np.random.uniform(-5, 5, count)
            oy = np.random.uniform(-5, 5, count)
            psizes = size * np.random.uniform(0.8, 1.2, count)
        else:
            angles = np.radians(angle + np.random.uniform(-spread, spread, count))
            ox = np.random.uniform(-3, 3, count)
            oy = np.random.uniform(-3, 3, count)
            psizes = np.full(count, size)
            gravity = 0
        speeds = np.random.uniform(speed * 0.5, speed * 1.5, count)
        lifetimes = life * np.random.uniform(0.7, 1.3, count)
        cos_a = np.cos(angles)
        sin_a = np.sin(angles)

        for i in range(count):
            p = _acquire_particle()
            p.reset(
                x + ox[i], y + oy[i],
                cos_a[i] * speeds[i], sin_a[i] * speeds[i],
                lifetimes[i], color, psizes[i], gravity,
            )
            self.particles.append(p)

    def emit_directional(self, x, y, count, angle, color, speed=100, life=0.5, size=3, spread=30):
        """朝特定方向发射粒子（委托 emit，保持原接口不变）"""
        self.emit(x, y, count, color, speed=speed, life=life, size=size,
                  spread=spread, angle=angle)


# ── 漂浮文字对象池 ──
_FLOAT_TEXT_POOL: list = []
_FLOAT_TEXT_POOL_MAX = 200


class FloatingText:
    """漂浮文字效果（对象池复用）"""
    __slots__ = ('x', 'y', 'text', 'color', 'life', 'max_life', 'vy', 'font_size', '_text')

    def __init__(self, x=0, y=0, text="", color=(255, 255, 255), life=1.0, font_size=14, vy=50):
        self.x = x
        self.y = y
        self.text = text
        self.color = color
        self.life = life
        self.max_life = life
        self.vy = vy
        self.font_size = font_size
        self._text = None

    def reset(self, x, y, text, color, life=1.0, font_size=14, vy=50):
        """重置漂浮文字状态（对象池复用）"""
        self.x = x
        self.y = y
        self.text = text
        self.color = color
        self.life = life
        self.max_life = life
        self.vy = vy
        self.font_size = font_size
        # 不清空 _text（arcade.Text 纹理缓存复用，下次 set value 时自动重绘）

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


def _acquire_float_text() -> FloatingText:
    """从对象池获取一个 FloatingText"""
    if _FLOAT_TEXT_POOL:
        return _FLOAT_TEXT_POOL.pop()
    return FloatingText()


def _release_float_text(ft: FloatingText):
    """归还 FloatingText 到对象池"""
    if len(_FLOAT_TEXT_POOL) < _FLOAT_TEXT_POOL_MAX:
        _FLOAT_TEXT_POOL.append(ft)


class FloatingTextManager:
    """漂浮文字管理器（对象池优化）"""

    def __init__(self):
        self.texts: list[FloatingText] = []
        # 跟踪最近添加的文字位置，用于避免重叠
        self._recent_positions: list[tuple[float, float, float]] = []  # (x, y, timestamp)
        self._overlap_offset = 18  # 重叠时的垂直偏移量

    def update(self, dt):
        # 就地过滤存活文字：先更新后原地压缩，避免每帧新建列表分配内存
        write = 0
        for i in range(len(self.texts)):
            t = self.texts[i]
            t.update(dt)
            if t.life > 0:
                self.texts[write] = t
                write += 1
            else:
                _release_float_text(t)  # 归还到对象池
        del self.texts[write:]
        # 就地清理过期的位置记录
        write = 0
        for i in range(len(self._recent_positions)):
            rx, ry, life = self._recent_positions[i]
            life -= dt
            if life > 0:
                self._recent_positions[write] = (rx, ry, life)
                write += 1
        del self._recent_positions[write:]

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
        """添加漂浮文字（对象池复用 + 自动避免重叠）"""
        offset = self._find_offset(x, y)
        ft = _acquire_float_text()
        ft.reset(x, y + offset, text, color, life, font_size, vy)
        self.texts.append(ft)
        self._recent_positions.append((x, y + offset, life))

    def add_gold(self, x, y, amount):
        """金币拾取提示"""
        self.add(x, y + 20, f"+{amount}金币", (255, 215, 0), life=1.2, font_size=16, vy=60)

    def add_resource(self, x, y, name, amount):
        """资源拾取提示"""
        self.add(x, y + 20, f"+{amount} {name}", (180, 220, 180), life=1.0, font_size=12, vy=50)

    def add_weapon(self, x, y, name):
        """武器拾取提示"""
        self.add(x, y + 20, f"获得 {name}!", (100, 200, 255), life=1.5, font_size=16, vy=40)

    def add_damage(self, x, y, amount):
        """伤害数字（取整避免浮点显示）"""
        self.add(x, y, f"-{int(amount)}", (255, 80, 80), life=0.8, font_size=12, vy=80)

    def add_heal(self, x, y, amount):
        """治疗数字（取整避免浮点显示）"""
        self.add(x, y, f"+{int(amount)}", (80, 255, 80), life=0.8, font_size=12, vy=60)


# 全局实例
particle_system = ParticleSystem()
floating_texts = FloatingTextManager()
