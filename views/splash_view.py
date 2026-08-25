"""开屏动画视图 - 品牌展示 + 光效过渡（增强版）

分阶段动画：
1. 品牌展示（0-4秒）：标题+副标题淡入 + 背景粒子效果 + 装饰元素
2. 光效过渡（4秒后）：多层光效叠加 + 径向扩散 + 射线效果
3. 切换到开始界面

支持任意键跳过，音效播放。
"""

import math
import random
import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT


class Particle:
    """浮动粒子"""
    
    def __init__(self, x, y, size, speed, alpha):
        self.x = x
        self.y = y
        self.size = size
        self.speed = speed
        self.alpha = alpha
        self.angle = random.uniform(0, math.pi * 2)
        self.rot_speed = random.uniform(-0.5, 0.5)
    
    def update(self, delta_time):
        """更新粒子位置"""
        self.y += self.speed * delta_time
        self.angle += self.rot_speed * delta_time
        # 微小的水平摆动
        self.x += math.sin(self.angle) * 0.3
        
        # 如果粒子移出屏幕，重置到底部
        if self.y > WINDOW_HEIGHT + 20:
            self.y = -20
            self.x = random.uniform(50, WINDOW_WIDTH - 50)


class SplashView(arcade.View):
    """开屏动画：品牌展示 + 光效过渡（增强版）"""

    # 阶段常量
    PHASE_LOGO = 0      # 品牌展示
    PHASE_GLOW = 1      # 光效过渡
    PHASE_EXIT = 2      # 切换视图

    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._phase = self.PHASE_LOGO
        self._timer = 0.0          # 阶段内计时器
        self._total_time = 0.0     # 总计时器
        self._logo_alpha = 0       # 标题透明度 (0-255)
        self._subtitle_alpha = 0   # 副标题透明度 (0-255)
        self._glow_alpha = 0       # 光效透明度 (0-255)
        self._skip_hint_alpha = 0  # 跳过提示透明度 (0-255)
        self._sound = None         # 音效对象
        self._finished = False     # 防止重复触发切换
        
        # 粒子系统
        self._particles = []
        self._init_particles()
        
        # 装饰元素
        self._sword_alpha = 0      # 剑图标透明度
        self._shield_alpha = 0     # 盾牌图标透明度
        self._chest_alpha = 0      # 宝箱图标透明度
        
        # 光效参数
        self._glow_rings = []      # 光环列表
        self._rays = []            # 射线列表

        # 持久 Text 对象（避免 arcade.draw_text 每帧重建纹理）
        cx = WINDOW_WIDTH // 2
        cy = WINDOW_HEIGHT // 2
        self._text_title_shadow = arcade.Text(
            "打怪升级", cx + 3, cy + 63,
            (*arcade.color.BLACK[:3], 0), 48, anchor_x="center", bold=True)
        self._text_title = arcade.Text(
            "打怪升级", cx, cy + 60,
            (*arcade.color.GOLD[:3], 0), 48, anchor_x="center", bold=True)
        self._text_subtitle = arcade.Text(
            "2D Action RPG", cx, cy + 10,
            (*arcade.color.LIGHT_GRAY[:3], 0), 18, anchor_x="center")
        self._text_skip = arcade.Text(
            "按任意键跳过", WINDOW_WIDTH - 20, 20,
            (*arcade.color.GRAY[:3], 0), 12, anchor_x="right")

    def _init_particles(self):
        """初始化粒子系统"""
        self._particles = []
        for _ in range(50):  # 50个粒子
            x = random.uniform(50, WINDOW_WIDTH - 50)
            y = random.uniform(-100, WINDOW_HEIGHT)
            size = random.uniform(2, 5)
            speed = random.uniform(20, 60)
            alpha = random.randint(50, 150)
            self._particles.append(Particle(x, y, size, speed, alpha))

    def on_show_view(self):
        """视图显示时初始化"""
        self.window.background_color = (15, 18, 25)  # 更深的背景
        self._phase = self.PHASE_LOGO
        self._timer = 0.0
        self._total_time = 0.0
        self._logo_alpha = 0
        self._subtitle_alpha = 0
        self._glow_alpha = 0
        self._skip_hint_alpha = 0
        self._finished = False
        self._sword_alpha = 0
        self._shield_alpha = 0
        self._chest_alpha = 0
        self._glow_rings = []
        self._rays = []
        
        # 重新初始化粒子
        self._init_particles()
        
        # 加载并播放音效
        self._load_sound()

    def _load_sound(self):
        """加载启动音效"""
        try:
            import os
            sound_path = os.path.join(os.path.dirname(__file__), "..", "assets", "sounds", "splash.wav")
            if os.path.exists(sound_path):
                self._sound = arcade.load_sound(sound_path)
                arcade.play_sound(self._sound, volume=0.6)
        except Exception:
            self._sound = None

    def on_update(self, delta_time):
        """更新动画状态"""
        if self._finished:
            return

        self._total_time += delta_time
        
        # 更新粒子
        for particle in self._particles:
            particle.update(delta_time)

        if self._phase == self.PHASE_LOGO:
            # 品牌展示阶段（0-4秒）
            self._timer += delta_time
            
            # 标题淡入（0-1.5秒，带弹性效果）
            if self._timer < 1.5:
                # 缓入效果
                progress = self._timer / 1.5
                eased = 1 - math.pow(1 - progress, 3)  # 三次缓入
                self._logo_alpha = int(255 * eased)
            else:
                self._logo_alpha = 255
            
            # 副标题淡入（0.8-2秒）
            if self._timer > 0.8:
                progress = min(1.0, (self._timer - 0.8) / 1.2)
                self._subtitle_alpha = int(255 * progress)
            
            # 装饰元素依次出现
            if self._timer > 1.5:
                progress = min(1.0, (self._timer - 1.5) / 0.8)
                self._sword_alpha = int(255 * progress)
            
            if self._timer > 2.0:
                progress = min(1.0, (self._timer - 2.0) / 0.8)
                self._shield_alpha = int(255 * progress)
            
            if self._timer > 2.5:
                progress = min(1.0, (self._timer - 2.5) / 0.8)
                self._chest_alpha = int(255 * progress)
            
            # 4秒后进入光效阶段
            if self._timer >= 4.0:
                self._phase = self.PHASE_GLOW
                self._timer = 0.0
                # 创建初始光环
                self._create_glow_ring()

        elif self._phase == self.PHASE_GLOW:
            # 光效过渡阶段（2.5秒完成）
            self._timer += delta_time
            
            # 标题脉动（正弦波，周期0.8秒）
            pulse = math.sin(self._total_time * math.pi * 2.5) * 30 + 225
            self._logo_alpha = int(pulse)
            
            # 副标题保持可见
            self._subtitle_alpha = 255
            
            # 装饰元素保持可见
            self._sword_alpha = 255
            self._shield_alpha = 255
            self._chest_alpha = 255
            
            # 创建新的光环
            if self._timer % 0.3 < delta_time:  # 每0.3秒创建一个新光环
                self._create_glow_ring()
            
            # 更新光环
            for ring in self._glow_rings[:]:
                ring['radius'] += ring['speed'] * delta_time
                ring['alpha'] -= ring['fade_speed'] * delta_time
                if ring['alpha'] <= 0:
                    self._glow_rings.remove(ring)
            
            # 创建射线
            if len(self._rays) < 12 and self._timer > 0.5:
                self._create_ray()
            
            # 更新射线
            for ray in self._rays[:]:
                ray['length'] += ray['speed'] * delta_time
                ray['alpha'] -= ray['fade_speed'] * delta_time
                if ray['alpha'] <= 0:
                    self._rays.remove(ray)
            
            # 光效渐变（2.5秒完成）
            self._glow_alpha = min(255, int(255 * (self._timer / 2.5)))
            
            # 2.5秒后切换
            if self._timer >= 2.5:
                self._phase = self.PHASE_EXIT
                self._finish()
        
        # 跳过提示（0.5秒后显示）
        if self._total_time > 0.5 and self._skip_hint_alpha < 255:
            self._skip_hint_alpha = min(255, self._skip_hint_alpha + 12)

    def _create_glow_ring(self):
        """创建光环效果"""
        self._glow_rings.append({
            'x': WINDOW_WIDTH // 2,
            'y': WINDOW_HEIGHT // 2,
            'radius': 10,
            'speed': 150,
            'alpha': 200,
            'fade_speed': 100
        })

    def _create_ray(self):
        """创建射线效果"""
        angle = random.uniform(0, math.pi * 2)
        self._rays.append({
            'x': WINDOW_WIDTH // 2,
            'y': WINDOW_HEIGHT // 2,
            'angle': angle,
            'length': 50,
            'speed': 300,
            'alpha': 180,
            'fade_speed': 80,
            'width': random.uniform(2, 6)
        })

    def on_draw(self):
        """绘制动画"""
        self.clear()
        
        # 绘制背景粒子
        for particle in self._particles:
            if particle.alpha > 0:
                arcade.draw_circle_filled(
                    particle.x, particle.y, particle.size,
                    (*arcade.color.CYAN[:3], int(particle.alpha * 0.6))
                )
        
        # 绘制装饰元素（剑、盾牌、宝箱轮廓）
        self._draw_decorations()
        
        # 绘制光环效果
        for ring in self._glow_rings:
            if ring['alpha'] > 0:
                # 多层光环
                for i in range(3):
                    offset = i * 3
                    alpha = int(ring['alpha'] * (1 - i * 0.3))
                    arcade.draw_circle_outline(
                        ring['x'], ring['y'], ring['radius'] + offset,
                        (*arcade.color.GOLD[:3], max(0, alpha)), 2
                    )
        
        # 绘制射线效果
        for ray in self._rays:
            if ray['alpha'] > 0:
                # 绘制渐变射线（优化版本，减少绘制调用）
                steps = min(20, int(ray['length'] / 10))  # 限制绘制步数
                for i in range(steps):
                    progress = i / steps
                    current_alpha = int(ray['alpha'] * (1 - progress * 0.8))
                    if current_alpha > 0:
                        point_x = ray['x'] + math.cos(ray['angle']) * (ray['length'] * progress)
                        point_y = ray['y'] + math.sin(ray['angle']) * (ray['length'] * progress)
                        size = ray['width'] * (1 - progress * 0.5)
                        arcade.draw_circle_filled(
                            point_x, point_y, size,
                            (*arcade.color.YELLOW[:3], current_alpha)
                        )

        # 绘制标题 "打怪升级"
        if self._logo_alpha > 0:
            # 标题阴影
            self._text_title_shadow.color = (*arcade.color.BLACK[:3], int(self._logo_alpha * 0.5))
            self._text_title_shadow.draw()
            # 主标题
            self._text_title.color = (*arcade.color.GOLD[:3], self._logo_alpha)
            self._text_title.draw()

        # 绘制副标题 "2D Action RPG"
        if self._subtitle_alpha > 0:
            self._text_subtitle.color = (*arcade.color.LIGHT_GRAY[:3], self._subtitle_alpha)
            self._text_subtitle.draw()

        # 绘制光效覆盖层（多层渐变）
        if self._glow_alpha > 0:
            # 内层白色光效
            glow_rect = arcade.XYWH(
                WINDOW_WIDTH // 2,
                WINDOW_HEIGHT // 2,
                WINDOW_WIDTH,
                WINDOW_HEIGHT
            )
            arcade.draw_rect_filled(
                glow_rect,
                (255, 255, 255, int(self._glow_alpha * 0.8))
            )
            
            # 外层金色光晕
            if self._glow_alpha > 100:
                outer_alpha = int((self._glow_alpha - 100) * 0.5)
                arcade.draw_rect_filled(
                    glow_rect,
                    (*arcade.color.GOLD[:3], outer_alpha)
                )

        # 绘制跳过提示（右下角）
        if self._skip_hint_alpha > 0:
            self._text_skip.color = (*arcade.color.GRAY[:3], self._skip_hint_alpha)
            self._text_skip.draw()

    def _draw_decorations(self):
        """绘制装饰元素（简化版图标轮廓）"""
        center_x = WINDOW_WIDTH // 2
        center_y = WINDOW_HEIGHT // 2
        
        # 剑图标（左侧）
        if self._sword_alpha > 0:
            sword_x = center_x - 180
            sword_y = center_y + 60
            # 剑身
            arcade.draw_line(
                sword_x, sword_y - 40, sword_x, sword_y + 40,
                (*arcade.color.SILVER[:3], self._sword_alpha), 3
            )
            # 剑柄
            arcade.draw_line(
                sword_x - 15, sword_y - 40, sword_x + 15, sword_y - 40,
                (*arcade.color.DARK_BROWN[:3], self._sword_alpha), 4
            )
            # 剑尖
            arcade.draw_line(
                sword_x, sword_y + 40, sword_x, sword_y + 55,
                (*arcade.color.SILVER[:3], self._sword_alpha), 2
            )

        # 盾牌图标（右侧）
        if self._shield_alpha > 0:
            shield_x = center_x + 180
            shield_y = center_y + 60
            # 盾牌轮廓（简化六边形）
            points = []
            for i in range(6):
                angle = math.pi / 3 * i - math.pi / 6
                px = shield_x + math.cos(angle) * 30
                py = shield_y + math.sin(angle) * 35
                points.append((px, py))
            # 绘制盾牌边框
            for i in range(len(points)):
                x1, y1 = points[i]
                x2, y2 = points[(i + 1) % len(points)]
                arcade.draw_line(
                    x1, y1, x2, y2,
                    (*arcade.color.GOLD[:3], self._shield_alpha), 2
                )
            # 盾牌中心装饰
            arcade.draw_circle_filled(
                shield_x, shield_y, 8,
                (*arcade.color.RED[:3], self._shield_alpha)
            )

        # 宝箱图标（下方）
        if self._chest_alpha > 0:
            chest_x = center_x
            chest_y = center_y - 120
            # 宝箱主体
            arcade.draw_rect_outline(
                arcade.XYWH(chest_x, chest_y, 50, 35),
                (*arcade.color.DARK_BROWN[:3], self._chest_alpha), 3
            )
            # 宝箱盖子
            arcade.draw_line(
                chest_x - 25, chest_y + 17, chest_x + 25, chest_y + 17,
                (*arcade.color.DARK_BROWN[:3], self._chest_alpha), 3
            )
            # 宝箱锁
            arcade.draw_circle_filled(
                chest_x, chest_y + 17, 5,
                (*arcade.color.GOLD[:3], self._chest_alpha)
            )

    def on_key_press(self, key, modifiers):
        """任意键跳过"""
        self._finish()

    def on_mouse_press(self, x, y, button, modifiers):
        """鼠标点击跳过"""
        self._finish()

    def _finish(self):
        """完成动画，切换到开始界面"""
        if self._finished:
            return

        self._finished = True

        # 停止音效
        try:
            if self._sound:
                arcade.stop_sound(self._sound)
        except Exception:
            pass

        # 切换到开始界面
        from views.start_view import StartView
        self.window.show_view(StartView(self.window_ref))
