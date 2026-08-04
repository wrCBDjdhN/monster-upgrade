"""僵尸(近战) 与 骷髅(远程) 怪物 AI

包含三个核心类：
1. Zombie: 近战怪物，追踪玩家并近距离攻击
2. Skeleton: 远程怪物，保持距离并发射弹丸
3. Projectile: 骷髅发射的远程弹丸

AI 行为：
- 怪物在索敌范围内追踪玩家
- Zombie 直接冲向玩家攻击
- Skeleton 保持 120~200 距离，远了靠近、近了后退
- 超出索敌距离则停止追击

碰撞检测：
- 使用 _can_move_to() 进行墙壁碰撞检测
- 怪物不能穿墙，但可以通过门离开房间
"""

import math
import arcade
from config import (
    MAP_WIDTH, MAP_HEIGHT,
    ZOMBIE_HP, ZOMBIE_DAMAGE, ZOMBIE_SPEED, ZOMBIE_ATTACK_DELAY, ZOMBIE_SIZE, ZOMBIE_COLOR,
    ZOMBIE_AGGRO_RANGE,
    SKELETON_HP, SKELETON_DAMAGE, SKELETON_SPEED, SKELETON_ATTACK_DELAY,
    SKELETON_SIZE, SKELETON_COLOR, SKELETON_AGGRO_RANGE,
    PROJECTILE_SPEED, PROJECTILE_SIZE, PROJECTILE_LIFETIME,
    # ── 沙漠荒地新怪物 ──
    MUMMY_HP, MUMMY_DAMAGE, MUMMY_SPEED, MUMMY_ATTACK_DELAY, MUMMY_SIZE, MUMMY_COLOR,
    MUMMY_AGGRO_RANGE, MUMMY_DEBUFF_ID,
    MUMMY_RANGED_HP, MUMMY_RANGED_DAMAGE, MUMMY_RANGED_SPEED, MUMMY_RANGED_ATTACK_DELAY,
    MUMMY_RANGED_SIZE, MUMMY_RANGED_COLOR, MUMMY_RANGED_AGGRO_RANGE,
    MUMMY_RANGED_PROJECTILE_SPEED, MUMMY_RANGED_PROJECTILE_SIZE, MUMMY_RANGED_DEBUFF_ID,
    CAMEL_HP, CAMEL_DAMAGE, CAMEL_SPEED, CAMEL_ATTACK_DELAY, CAMEL_SIZE, CAMEL_COLOR,
    CAMEL_AGGRO_RANGE, CAMEL_PROJECTILE_SPEED, CAMEL_PROJECTILE_SIZE,
    BOSS_HP_MULT, BOSS_DAMAGE_MULT, BOSS_SPEED_MULT, BOSS_REQUIRED_LEVEL,
    BOSS_ZOMBIE_DEBUFF_ID, BOSS_SKELETON_DEBUFF_ID, BOSS_MUMMY_DEBUFF_ID,
    # ── 航天基地新怪物 ──
    SNIPER_HP, SNIPER_DAMAGE, SNIPER_SPEED, SNIPER_ATTACK_DELAY, SNIPER_SIZE, SNIPER_COLOR,
    SNIPER_AGGRO_RANGE, SNIPER_PROJECTILE_SPEED, SNIPER_PROJECTILE_SIZE,
    ASSAULT_HP, ASSAULT_DAMAGE, ASSAULT_SPEED, ASSAULT_ATTACK_DELAY, ASSAULT_SIZE, ASSAULT_COLOR,
    ASSAULT_AGGRO_RANGE,
    BANDIT_HP, BANDIT_DAMAGE, BANDIT_SPEED, BANDIT_ATTACK_DELAY, BANDIT_SIZE, BANDIT_COLOR,
    BANDIT_AGGRO_RANGE, BANDIT_PROJECTILE_SPEED, BANDIT_PROJECTILE_SIZE,
    ROCKET_TROOP_HP, ROCKET_TROOP_DAMAGE, ROCKET_TROOP_SPEED, ROCKET_TROOP_ATTACK_DELAY,
    ROCKET_TROOP_SIZE, ROCKET_TROOP_COLOR, ROCKET_TROOP_AGGRO_RANGE,
    ROCKET_TROOP_PROJECTILE_SPEED, ROCKET_TROOP_PROJECTILE_SIZE,
    BOSS_SPACE_HP, BOSS_SPACE_DAMAGE, BOSS_SPACE_SPEED, BOSS_SPACE_ATTACK_DELAY,
    BOSS_SPACE_SIZE, BOSS_SPACE_COLOR, BOSS_SPACE_AGGRO_RANGE, BOSS_SPACE_DEBUFF_ID,
    BOSS_HP_MULT, BOSS_DAMAGE_MULT, BOSS_SPEED_MULT, BOSS_REQUIRED_LEVEL,
)


def _can_move_to(new_x, new_y, size, walls):
    """检查怪物能否移动到 (new_x, new_y)，不穿墙，不限制房间（可通过门离开）"""
    half = size
    # 检查墙壁碰撞（简单 AABB）
    for wx, wy, ww, wh in walls:
        if ww <= 0 or wh <= 0:
            continue
        if (new_x + half > wx and new_x - half < wx + ww and
            new_y + half > wy and new_y - half < wy + wh):
            return False
    # 检查地图边界
    if new_x - half < 0 or new_x + half > MAP_WIDTH:
        return False
    if new_y - half < 0 or new_y + half > MAP_HEIGHT:
        return False
    return True


class Zombie(arcade.SpriteSolidColor):
    def __init__(self, center_x=0, center_y=0):
        super().__init__(ZOMBIE_SIZE * 2, ZOMBIE_SIZE * 2, color=ZOMBIE_COLOR)
        self.center_x = center_x
        self.center_y = center_y
        self.hp = ZOMBIE_HP
        self.max_hp = ZOMBIE_HP
        self.damage = ZOMBIE_DAMAGE
        self.speed = ZOMBIE_SPEED
        self._attack_timer = 0.0
        self._on_death_cb = None
        self._hit_flash = 0.0       # 受击闪光计时
        self._walls = []            # 可碰撞墙壁列表
        # 护甲系统
        self.armor = None           # {"item_id": str, "name": str, "defense": int, "color": tuple}
        self.armor_drop_id = None   # 护甲掉落ID（击败后可能掉落）
        # 头盔系统
        self.helmet = None          # {"item_id": str, "name": str, "defense": int, "color": tuple}
        self.helmet_drop_id = None  # 头盔掉落ID（击败后可能掉落）
        # 武器系统：怪物携带武器，击败后掉落自身武器（等级由分配时决定）
        self.weapon = None          # {"item_id": str, "name": str, "color": tuple, "level": int}
        # 附加效果系统（玩家武器/装备施加的中毒、燃烧、冰冻、减速、眩晕）
        self.debuffs = []           # 当前生效的 debuff 列表 [{"id","duration",...}]
        self._debuff_tick = 0.0     # 持续伤害 tick 计时（每0.5秒结算一次）
        self._debuff_speed_mult = 1.0  # 减速倍率（冰冻/减速效果叠乘）
        self._stunned = False       # 眩晕状态（无法移动和攻击）

    def set_on_death(self, cb):
        self._on_death_cb = cb

    def update(self, player_x: float, player_y: float, delta_time: float):
        if not self.alive:
            return
        # 受击闪光衰减
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)
        # 附加效果结算（中毒/燃烧掉血、冰冻/减速、眩晕）
        self._update_debuffs(delta_time)
        if self._stunned:
            # 眩晕：无法移动和攻击
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        # 朝玩家移动（带碰撞检测 + 索敌距离）
        dx = player_x - self.center_x
        dy = player_y - self.center_y
        dist = math.hypot(dx, dy)
        # 超出索敌距离则不追击
        if dist > ZOMBIE_AGGRO_RANGE or dist == 0:
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
        new_x = self.center_x + (dx / dist) * self.speed * self._debuff_speed_mult * delta_time
        new_y = self.center_y + (dy / dist) * self.speed * self._debuff_speed_mult * delta_time
        if _can_move_to(new_x, new_y, ZOMBIE_SIZE, self._walls):
            self.center_x = new_x
            self.center_y = new_y
        # 攻击计时
        self._attack_timer = max(0, self._attack_timer - delta_time)

    def try_attack(self, player) -> bool:
        if not self.alive:
            return False
        if self._stunned:
            # 眩晕状态下无法攻击
            return False
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < ZOMBIE_SIZE + 20 and self._attack_timer <= 0:
            player.take_damage(self.damage)
            self._attack_timer = ZOMBIE_ATTACK_DELAY
            return True
        return False

    def take_damage(self, amount: int):
        """受到伤害，先扣头盔和护甲"""
        helmet_def = self.helmet.get("defense", 0) if self.helmet else 0
        armor_def = self.armor.get("defense", 0) if self.armor else 0
        total_def = helmet_def + armor_def
        actual = max(1, amount - total_def)  # 至少1点伤害
        self.hp -= actual
        self._hit_flash = 0.15     # 受击闪白
        if self.hp <= 0 and self._on_death_cb:
            self._on_death_cb(self)
        return actual  # 返回实际伤害

    def apply_debuff(self, effect_id: str, level: int = 1):
        """施加附加效果（中毒/燃烧/冰冻/减速/眩晕），同类刷新持续时间（level 为效果等级，等级越高数值越强）"""
        from entities.effects_defs import EFFECTS, effect_params
        effect = effect_params(effect_id, level)
        if not effect or effect.get("type") != "debuff":
            return
        # 同类效果刷新时长，并同步效果等级（取较高者）
        for d in self.debuffs:
            if d["id"] == effect_id:
                d["duration"] = effect.get("duration", 1.0)
                d["level"] = max(d.get("level", 1), level)
                return
        self.debuffs.append({
            "id": effect_id,
            "level": level,
            "duration": effect.get("duration", 1.0),
        })
        # 立即重算减速/眩晕，确保施加瞬间即生效（而非等下一帧结算）
        self._recalc_debuffs()

    def _recalc_debuffs(self):
        """重新计算减速倍率与眩晕状态（施加瞬间与每帧结算时调用）"""
        from entities.effects_defs import EFFECTS, effect_params
        self._debuff_speed_mult = 1.0
        self._stunned = False
        for d in self.debuffs:
            effect = effect_params(d["id"], d.get("level", 1))
            if effect.get("slow"):
                self._debuff_speed_mult *= (1.0 - effect["slow"])
            if effect.get("stun"):
                self._stunned = True

    def _update_debuffs(self, delta_time: float):
        """每帧结算附加效果：中毒/燃烧按tick掉血，冰冻/减速降速，眩晕无法行动"""
        if not self.debuffs:
            return
        from entities.effects_defs import EFFECTS, effect_params
        self._debuff_tick -= delta_time
        if self._debuff_tick <= 0:
            self._debuff_tick = 0.5  # 每0.5秒结算一次持续伤害
            for d in list(self.debuffs):
                effect = effect_params(d["id"], d.get("level", 1))
                dmg = effect.get("value", 0)
                if dmg > 0:
                    # 中毒/燃烧：持续掉血（死亡回调在 take_damage 内处理）
                    self.take_damage(dmg)
        # 持续时间递减并清理过期效果
        for d in list(self.debuffs):
            d["duration"] -= delta_time
            if d["duration"] <= 0:
                self.debuffs.remove(d)
        # 重新计算减速倍率与眩晕状态
        self._recalc_debuffs()

    @property
    def alive(self) -> bool:
        return self.hp > 0


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


class Skeleton(arcade.SpriteSolidColor):
    def __init__(self, center_x=0, center_y=0):
        super().__init__(SKELETON_SIZE * 2, SKELETON_SIZE * 2, color=SKELETON_COLOR)
        self.center_x = center_x
        self.center_y = center_y
        self.hp = SKELETON_HP
        self.max_hp = SKELETON_HP
        self.damage = SKELETON_DAMAGE
        self.speed = SKELETON_SPEED
        self._attack_timer = 0.0
        self._on_death_cb = None
        self._hit_flash = 0.0
        self._walls = []
        # 护甲系统
        self.armor = None           # {"item_id": str, "name": str, "defense": int, "color": tuple}
        self.armor_drop_id = None   # 护甲掉落ID（击败后可能掉落）
        # 头盔系统
        self.helmet = None          # {"item_id": str, "name": str, "defense": int, "color": tuple}
        self.helmet_drop_id = None  # 头盔掉落ID（击败后可能掉落）
        # 武器系统：怪物携带武器，击败后掉落自身武器（等级由分配时决定）
        self.weapon = None          # {"item_id": str, "name": str, "color": tuple, "level": int}
        # 附加效果系统（玩家武器/装备施加的中毒、燃烧、冰冻、减速、眩晕）
        self.debuffs = []           # 当前生效的 debuff 列表 [{"id","duration",...}]
        self._debuff_tick = 0.0     # 持续伤害 tick 计时（每0.5秒结算一次）
        self._debuff_speed_mult = 1.0  # 减速倍率（冰冻/减速效果叠乘）
        self._stunned = False       # 眩晕状态（无法移动和攻击）

    def apply_debuff(self, effect_id: str, level: int = 1):
        """施加附加效果（中毒/燃烧/冰冻/减速/眩晕），同类刷新持续时间（level 为效果等级，等级越高数值越强）"""
        from entities.effects_defs import EFFECTS, effect_params
        effect = effect_params(effect_id, level)
        if not effect or effect.get("type") != "debuff":
            return
        # 同类效果刷新时长，并同步效果等级（取较高者）
        for d in self.debuffs:
            if d["id"] == effect_id:
                d["duration"] = effect.get("duration", 1.0)
                d["level"] = max(d.get("level", 1), level)
                return
        self.debuffs.append({
            "id": effect_id,
            "level": level,
            "duration": effect.get("duration", 1.0),
        })
        # 立即重算减速/眩晕，确保施加瞬间即生效
        self._recalc_debuffs()

    def _recalc_debuffs(self):
        """重新计算减速倍率与眩晕状态（施加瞬间与每帧结算时调用）"""
        from entities.effects_defs import EFFECTS, effect_params
        self._debuff_speed_mult = 1.0
        self._stunned = False
        for d in self.debuffs:
            effect = effect_params(d["id"], d.get("level", 1))
            if effect.get("slow"):
                self._debuff_speed_mult *= (1.0 - effect["slow"])
            if effect.get("stun"):
                self._stunned = True

    def _update_debuffs(self, delta_time: float):
        """每帧结算附加效果：中毒/燃烧按tick掉血，冰冻/减速降速，眩晕无法行动"""
        if not self.debuffs:
            return
        from entities.effects_defs import EFFECTS, effect_params
        self._debuff_tick -= delta_time
        if self._debuff_tick <= 0:
            self._debuff_tick = 0.5  # 每0.5秒结算一次持续伤害
            for d in list(self.debuffs):
                effect = effect_params(d["id"], d.get("level", 1))
                dmg = effect.get("value", 0)
                if dmg > 0:
                    # 中毒/燃烧：持续掉血（死亡回调在 take_damage 内处理）
                    self.take_damage(dmg)
        # 持续时间递减并清理过期效果
        for d in list(self.debuffs):
            d["duration"] -= delta_time
            if d["duration"] <= 0:
                self.debuffs.remove(d)
        # 重新计算减速倍率与眩晕状态
        self._recalc_debuffs()

    def set_on_death(self, cb):
        self._on_death_cb = cb

    def update(self, player_x: float, player_y: float, delta_time: float):
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
        dx = player_x - self.center_x
        dy = player_y - self.center_y
        dist = math.hypot(dx, dy)
        # 超出索敌距离则不追击
        if dist > SKELETON_AGGRO_RANGE or dist == 0:
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
            if _can_move_to(new_x, new_y, SKELETON_SIZE, self._walls):
                self.center_x = new_x
                self.center_y = new_y
        self._attack_timer = max(0, self._attack_timer - delta_time)

    def try_attack(self, player) -> Projectile | None:
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
            return None
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < SKELETON_AGGRO_RANGE and self._attack_timer <= 0:
            self._attack_timer = SKELETON_ATTACK_DELAY
            return Projectile(
                self.center_x, self.center_y,
                player.center_x, player.center_y,
                PROJECTILE_SPEED, self.damage,
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

    @property
    def alive(self) -> bool:
        return self.hp > 0


# ── 沙漠荒地新怪物（木乃伊 / 骆驼 / BOSS）──

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

    def update(self, player_x: float, player_y: float, delta_time: float):
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

    def try_attack(self, player) -> bool:
        if not self.alive:
            return False
        if self._stunned:
            # 眩晕状态下无法攻击
            return False
        dist = math.hypot(player.center_x - self.center_x, player.center_y - self.center_y)
        if dist < self._size + 20 and self._attack_timer <= 0:
            self._attack_timer = self._attack_delay
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

    def update(self, player_x: float, player_y: float, delta_time: float):
        if not self.alive:
            return
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)
        # 附加效果结算
        self._update_debuffs(delta_time)
        if self._stunned:
            self._attack_timer = max(0, self._attack_timer - delta_time)
            return
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

    def try_attack(self, player) -> Projectile | None:
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
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


class MummyMelee(_MeleeMonsterBase):
    """木乃伊（近战）：血厚攻高，攻击附加中毒"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            MUMMY_SIZE, MUMMY_COLOR, MUMMY_HP, MUMMY_DAMAGE, MUMMY_SPEED,
            MUMMY_ATTACK_DELAY, MUMMY_AGGRO_RANGE,
            debuff_id=MUMMY_DEBUFF_ID,
        )


class MummyRanged(_RangedMonsterBase):
    """木乃伊（远程）：发射附中毒的弹丸"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            MUMMY_RANGED_SIZE, MUMMY_RANGED_COLOR, MUMMY_RANGED_HP, MUMMY_RANGED_DAMAGE,
            MUMMY_RANGED_SPEED, MUMMY_RANGED_ATTACK_DELAY, MUMMY_RANGED_AGGRO_RANGE,
            MUMMY_RANGED_PROJECTILE_SPEED, MUMMY_RANGED_PROJECTILE_SIZE, MUMMY_RANGED_COLOR,
            debuff_id=MUMMY_RANGED_DEBUFF_ID,
        )


class Camel(_RangedMonsterBase):
    """骆驼：高血量高移速，吐口水远程攻击（弹丸略慢可躲避）"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            CAMEL_SIZE, CAMEL_COLOR, CAMEL_HP, CAMEL_DAMAGE, CAMEL_SPEED,
            CAMEL_ATTACK_DELAY, CAMEL_AGGRO_RANGE,
            CAMEL_PROJECTILE_SPEED, CAMEL_PROJECTILE_SIZE, CAMEL_COLOR,
        )


class BossZombie(_MeleeMonsterBase):
    """BOSS 僵尸：血厚攻高移速慢，攻击附加燃烧，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            ZOMBIE_SIZE, ZOMBIE_COLOR,
            ZOMBIE_HP * BOSS_HP_MULT, ZOMBIE_DAMAGE * BOSS_DAMAGE_MULT,
            ZOMBIE_SPEED * BOSS_SPEED_MULT,
            ZOMBIE_ATTACK_DELAY, ZOMBIE_AGGRO_RANGE,
            debuff_id=BOSS_ZOMBIE_DEBUFF_ID,
            is_boss=True, required_weapon_level=BOSS_REQUIRED_LEVEL,
        )


class BossSkeleton(_RangedMonsterBase):
    """BOSS 骷髅：远程弹丸附加冰冻，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            SKELETON_SIZE, SKELETON_COLOR,
            SKELETON_HP * BOSS_HP_MULT, SKELETON_DAMAGE * BOSS_DAMAGE_MULT,
            SKELETON_SPEED * BOSS_SPEED_MULT,
            SKELETON_ATTACK_DELAY, SKELETON_AGGRO_RANGE,
            PROJECTILE_SPEED, PROJECTILE_SIZE, SKELETON_COLOR,
            debuff_id=BOSS_SKELETON_DEBUFF_ID,
            is_boss=True, required_weapon_level=BOSS_REQUIRED_LEVEL,
        )


class BossMummy(_MeleeMonsterBase):
    """木乃伊 BOSS（金字塔守护者）：血厚攻高移速慢，攻击附加中毒，需要 Lv5+ 武器/装备"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            MUMMY_SIZE, MUMMY_COLOR,
            MUMMY_HP * BOSS_HP_MULT, MUMMY_DAMAGE * BOSS_DAMAGE_MULT,
            MUMMY_SPEED * BOSS_SPEED_MULT,
            MUMMY_ATTACK_DELAY, MUMMY_AGGRO_RANGE,
            debuff_id=BOSS_MUMMY_DEBUFF_ID,
            is_boss=True, required_weapon_level=BOSS_REQUIRED_LEVEL,
        )


# ── 航天基地新怪物（狙击兵 / 突击兵 / 土匪 / 火箭兵）──

class Sniper(_RangedMonsterBase):
    """狙击兵（远程型，高伤低血）：使用狙击枪，索敌距离超远"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            SNIPER_SIZE, SNIPER_COLOR, SNIPER_HP, SNIPER_DAMAGE, SNIPER_SPEED,
            SNIPER_ATTACK_DELAY, SNIPER_AGGRO_RANGE,
            SNIPER_PROJECTILE_SPEED, SNIPER_PROJECTILE_SIZE, SNIPER_COLOR,
        )


class Assault(_MeleeMonsterBase):
    """突击兵（近战型，高血高甲）：使用步枪近距离作战"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            ASSAULT_SIZE, ASSAULT_COLOR, ASSAULT_HP, ASSAULT_DAMAGE, ASSAULT_SPEED,
            ASSAULT_ATTACK_DELAY, ASSAULT_AGGRO_RANGE,
        )


class Bandit(_RangedMonsterBase):
    """土匪（远程型，低血低甲，成群刷新）：使用手枪或石锤"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            BANDIT_SIZE, BANDIT_COLOR, BANDIT_HP, BANDIT_DAMAGE, BANDIT_SPEED,
            BANDIT_ATTACK_DELAY, BANDIT_AGGRO_RANGE,
            BANDIT_PROJECTILE_SPEED, BANDIT_PROJECTILE_SIZE, BANDIT_COLOR,
        )


class RocketTroop(_RangedMonsterBase):
    """火箭兵（远程型，高血高甲，AOE 弹丸）：使用火箭筒"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(
            center_x, center_y,
            ROCKET_TROOP_SIZE, ROCKET_TROOP_COLOR, ROCKET_TROOP_HP, ROCKET_TROOP_DAMAGE,
            ROCKET_TROOP_SPEED, ROCKET_TROOP_ATTACK_DELAY, ROCKET_TROOP_AGGRO_RANGE,
            ROCKET_TROOP_PROJECTILE_SPEED, ROCKET_TROOP_PROJECTILE_SIZE, ROCKET_TROOP_COLOR,
        )

    def try_attack(self, player) -> Projectile | None:
        """火箭兵攻击：发射带爆炸属性的弹丸（命中玩家时触发 AOE 范围伤害）"""
        if not self.alive:
            return None
        if self._stunned:
            # 眩晕状态下无法攻击
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
        super().__init__(
            center_x, center_y,
            BOSS_SPACE_SIZE, BOSS_SPACE_COLOR,
            BOSS_SPACE_HP * BOSS_HP_MULT, BOSS_SPACE_DAMAGE * BOSS_DAMAGE_MULT,
            BOSS_SPACE_SPEED * BOSS_SPEED_MULT,
            BOSS_SPACE_ATTACK_DELAY, BOSS_SPACE_AGGRO_RANGE,
            # 航天兵使用激光枪弹丸（高速+穿透效果由 combat.py 处理）
            500, 5, (255, 80, 30),  # proj_speed, proj_size, proj_color
            debuff_id=BOSS_SPACE_DEBUFF_ID,
            is_boss=True, required_weapon_level=BOSS_REQUIRED_LEVEL,
        )
