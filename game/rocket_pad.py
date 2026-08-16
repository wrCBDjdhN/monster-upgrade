"""火箭发射台状态机：IDLE → ACTIVATED → BOSS_SPAWNED → BOSS_DEFEATED → DESTROYED/EVACUATING → EVAC_SUCCESS"""

import math
import random
from config import (
    ROCKET_PAD_SIZE, ROCKET_PAD_INTERACT_RANGE, ROCKET_PAD_BOSS_SPAWN_DELAY,
    ROCKET_PAD_COUNTDOWN, ROCKET_PAD_DESTROY_REWARD_GOLD_MIN,
    ROCKET_PAD_DESTROY_REWARD_GOLD_MAX, ROCKET_PAD_DESTROY_REWARD_RESOURCE_MIN,
    ROCKET_PAD_DESTROY_REWARD_RESOURCE_MAX,
)


class RocketPad:
    """火箭发射台：玩家靠近按 E 激活 → 召唤 BOSS → 击败后可选炸毁/启用"""

    # 状态常量
    IDLE = "idle"
    ACTIVATED = "activated"
    BOSS_SPAWNED = "boss_spawned"
    BOSS_DEFEATED = "boss_defeated"
    DESTROYED = "destroyed"
    EVACUATING = "evacuating"
    EVAC_SUCCESS = "evac_success"

    def __init__(self, center_x: float, center_y: float):
        self.center_x = center_x
        self.center_y = center_y
        self.state = self.IDLE
        self._boss_spawn_timer = 0.0      # 激活后 BOSS 出现延迟
        self._countdown_timer = 0.0        # 撤离倒计时
        self._boss = None                  # 召唤的 BOSS 引用
        self._boss_defeated = False        # BOSS 是否已被击败
        self._interact_cooldown = 0.0      # 交互冷却（防止连续按 E）

    def is_active(self) -> bool:
        """发射台是否处于可用状态（IDLE）"""
        return self.state == self.IDLE

    def is_evacuating(self) -> bool:
        """发射台是否处于撤离倒计时"""
        return self.state == self.EVACUATING

    def get_countdown(self) -> float:
        """获取剩余撤离时间"""
        return self._countdown_timer

    def update(self, delta_time: float):
        """更新状态机"""
        if self._interact_cooldown > 0:
            self._interact_cooldown -= delta_time

        if self.state == self.ACTIVATED:
            self._boss_spawn_timer -= delta_time
            if self._boss_spawn_timer <= 0:
                self.state = self.BOSS_SPAWNED

        elif self.state == self.EVACUATING:
            self._countdown_timer -= delta_time
            if self._countdown_timer <= 0:
                self.state = self.EVAC_SUCCESS

    def activate(self) -> bool:
        """激活发射台（召唤 BOSS），返回是否成功"""
        if self.state != self.IDLE or self._interact_cooldown > 0:
            return False
        self.state = self.ACTIVATED
        self._boss_spawn_timer = ROCKET_PAD_BOSS_SPAWN_DELAY
        self._interact_cooldown = 1.0
        return True

    def set_boss(self, boss):
        """设置 BOSS 引用（BOSS 被击败时调用）"""
        self._boss = boss

    def on_boss_defeated(self):
        """BOSS 被击败，进入 BOSS_DEFEATED 状态

        修复：BOSS 在 activate() 后立即生成（见 entity_callbacks），
        而状态需等 ROCKET_PAD_BOSS_SPAWN_DELAY 秒才从 ACTIVATED 转 BOSS_SPAWNED。
        若 BOSS 在延迟期内被提前击杀，只认 BOSS_SPAWNED 会导致状态机永久卡死，
        因此 ACTIVATED / BOSS_SPAWNED 两个状态均允许结算。
        """
        if self.state in (self.ACTIVATED, self.BOSS_SPAWNED):
            self.state = self.BOSS_DEFEATED
            self._boss_defeated = True

    def destroy(self) -> dict:
        """炸毁发射台，返回奖励信息（金币与资源总量均为随机范围）"""
        if self.state != self.BOSS_DEFEATED:
            return {}
        self.state = self.DESTROYED
        return {
            "gold": random.randint(ROCKET_PAD_DESTROY_REWARD_GOLD_MIN,
                                   ROCKET_PAD_DESTROY_REWARD_GOLD_MAX),
            "resources": random.randint(ROCKET_PAD_DESTROY_REWARD_RESOURCE_MIN,
                                        ROCKET_PAD_DESTROY_REWARD_RESOURCE_MAX),
        }

    def start_evacuation(self):
        """启用发射台，开始撤离倒计时"""
        if self.state != self.BOSS_DEFEATED:
            return
        self.state = self.EVACUATING
        self._countdown_timer = ROCKET_PAD_COUNTDOWN

    def get_interact_prompt(self) -> str:
        """获取交互提示文本"""
        if self.state == self.IDLE:
            return "按E激活火箭发射台"
        elif self.state == self.BOSS_DEFEATED:
            return "按7炸毁 | 按8启用撤离"
        elif self.state == self.EVACUATING:
            mins = int(self._countdown_timer) // 60
            secs = int(self._countdown_timer) % 60
            return f"撤离倒计时: {mins}:{secs:02d}"
        return ""

    def draw(self, batch=None):
        """绘制发射台"""
        if self.state == self.DESTROYED:
            return  # 已炸毁不绘制
        # 颜色根据状态变化
        color_map = {
            self.IDLE: (200, 200, 50),        # 黄色（待激活）
            self.ACTIVATED: (255, 150, 50),    # 橙色（激活中）
            self.BOSS_SPAWNED: (255, 80, 30),  # 红色（BOSS 出现）
            self.BOSS_DEFEATED: (100, 255, 100),  # 绿色（可选择）
            self.EVACUATING: (0, 255, 100),    # 亮绿（撤离中）
        }
        color = color_map.get(self.state, (200, 200, 50))
        import arcade
        size = ROCKET_PAD_SIZE * 2
        # 绘制发射台底座
        if batch is not None:
            batch.rect(self.center_x, self.center_y, size, size, color)
        else:
            arcade.draw_rect_filled(
                arcade.XYWH(self.center_x, self.center_y, size, size), color
            )
