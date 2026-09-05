"""玩家精灵、WASD 移动、相机跟随

包含两个核心类：
1. Player: 玩家精灵，管理生命值、防御、背包等属性
2. PlayerController: 处理键盘输入与相机更新

移动机制：
- 使用 arcade.PhysicsEngineSimple 进行碰撞检测
- WASD 控制四方向移动
- 相机自动跟随玩家居中
"""

import arcade
from arcade.types import LBWH, LRBT
from config import (
    PLAYER_SPEED, PLAYER_HP, PLAYER_SIZE, PLAYER_COLOR,
    MAP_WIDTH, MAP_HEIGHT, WINDOW_WIDTH, WINDOW_HEIGHT,
    FRUIT_SPEED_MULT,
)
from entities.character_defs import CHARACTERS


class Player(arcade.SpriteSolidColor):
    """玩家精灵类

    继承自 SpriteSolidColor，使用纯色矩形表示玩家。
    管理玩家的生命值、防御力、背包容量等属性。

    角色系统：构造时按 character_id 应用角色基础属性
    （hp/defense/speed/color，见 entities/character_defs.py），
    默认 "initial" 与旧版数值完全一致（100 血 / 0 防 / 4.0 速 / 蓝）。
    """
    def __init__(self, center_x=0, center_y=0, character_id: str = "initial"):
        char = CHARACTERS.get(character_id, CHARACTERS["initial"])
        super().__init__(PLAYER_SIZE * 2, PLAYER_SIZE * 2, color=char["color"])
        self.center_x = center_x
        self.center_y = center_y
        self.character_id = character_id          # 角色 id（跨层契约：DB/界面/联机共用）
        self.character_def = char                  # 角色定义引用（技能/被动/数值）
        self.hp = char["hp"]                      # 当前生命值（角色基础值）
        self.max_hp = char["hp"]                  # 最大生命值（角色基础值）
        self.change_x = 0.0                      # X 轴速度（由物理引擎处理）
        self.change_y = 0.0                      # Y 轴速度（由物理引擎处理）
        # 装备属性
        self.base_defense = char["defense"]      # 角色基础防御（骑士 10，装备防御叠加其上）
        self.defense = self.base_defense         # 总防御力（角色基础 + 头盔 + 护甲）
        self.speed_mult = 1.0                    # 移速倍率（药水效果）
        self.speed_effect_timer = 0.0            # 疾跑药水剩余时间
        # 护盾药水状态：临时护盾先于防御结算吸收伤害
        self.shield = 0.0                        # 当前护盾值（0 = 无护盾）
        self.shield_effect_timer = 0.0           # 护盾剩余时间（归零护盾清空）
        # 狂暴药水状态：攻击伤害倍率（攻击结算时乘算，见 character_skills.modify_attack_damage）
        self.power_mult = 1.0                    # 伤害倍率（1.0 = 无加成）
        self.power_effect_timer = 0.0            # 狂暴剩余时间（归零恢复 1.0）
        # 角色基础移速倍率 = 角色速度 / 标准玩家速度（法师 3.6/4=0.9、骑士 3.4/4=0.85、刺客 4.8/4=1.2）
        self.char_speed_mult = char["speed"] / PLAYER_SPEED
        # 技能状态（F 键释放；无技能角色恒为不可用）
        self.skill_cd = 0.0                      # 技能剩余冷却（秒），<=0 可释放
        self.skill_active = 0.0                  # 技能生效剩余时间（骑士圣盾庇护持续秒），>0 表示技能效果激活
        # 背包
        self.backpack_capacity = 0               # 背包容量（0 = 无背包，不能拾取资源）
        # 持续回复效果（HoT）
        self.heal_per_sec = 0.0                  # 每秒回复量
        self.heal_duration = 0.0                 # 剩余持续时间
        # 装备附加效果（Lv.5+ 物品随机效果）
        self.regen_per_sec = 0.0                 # 装备持续回血（每秒回复量）
        self.gear_speed_mult = 1.0               # 装备移速倍率（与药水区分，药水乘算在其上）
        # 玩家侧 debuff（木乃伊中毒/燃烧/冰冻/减速/眩晕，参考怪物侧实现移植）
        self.debuffs = []           # 当前生效的 debuff 列表 [{"id","duration",...}]
        self._debuff_tick = 0.0     # 持续伤害 tick 计时（每0.5秒结算一次）
        self._debuff_speed_mult = 1.0  # 减速倍率（冰冻/减速效果叠乘）
        self._stunned = False       # 眩晕状态（无法移动和攻击）
        # 联机主机伤害广播钩子（默认 None，单机完全不受影响）：
        # 主机在本地玩家/客户端幽灵上注册为「扣血后回调实际伤害值」的闭包，
        # 用于广播 PLAYER_HURT（HP 主机权威，见 views/game_view.py _record_hurt）。
        # 仅当主机模式且已注入网络对象时才被设置，其余模式恒为 None。
        self.on_take_damage = None  # Callable[[float], None] | None
        # 倒地/救援系统（联机模式）：
        # downed=True 表示 HP=0 但可被队友救援（保留装备），超时未被救则真死
        self.downed = False
        self.downed_timer = 0.0    # 倒地计时器（秒），归零时真死

    def clamp_to_map(self):
        """将玩家位置限制在地图边界内"""
        half = PLAYER_SIZE
        self.center_x = max(half, min(MAP_WIDTH - half, self.center_x))
        self.center_y = max(half, min(MAP_HEIGHT - half, self.center_y))

    def take_damage(self, amount: int) -> int:
        """受到伤害，先扣防御（至少造成 1 点伤害），返回实际扣除的血量

        角色被动修正（见 entities/character_defs.py）：
        - 骑士「钢铁之躯」：受到伤害额外 -5（防御结算后再减）
        - 骑士「圣盾庇护」激活期间：伤害再乘 (1 - 减伤比例 70%)

        返回实际伤害值：护盾吸收后的最终扣血量。
        修复：仙人掌反伤等显示与血条扣血不一致——旧版调用方以原始伤害
        显示浮动文字，护盾/防御减免后实际扣血更少。现以返回值显示真实伤害。
        """
        # 圣盾庇护激活：先按比例减伤（70% 减伤 → 仅承受 30%）
        if self.skill_active > 0 and self.character_def.get("skill"):
            reduce = self.character_def["skill"].get("damage_reduce", 0.0)
            amount = int(amount * (1.0 - reduce))
        # 护盾药水：临时护盾先于防御结算吸收伤害（吸收后剩余伤害继续走防御减免）
        if self.shield > 0:
            absorbed = min(self.shield, amount)
            self.shield = round(self.shield - absorbed, 2)
            amount = int(amount - absorbed)
        # 角色被动：钢铁之躯 受到伤害额外 -5
        passive = self.character_def.get("passive") or {}
        flat_reduce = passive.get("flat_reduce", 0)
        actual = max(1, amount - self.defense - flat_reduce)
        # round 到 2 位小数：regen 回复使 hp 成为浮点，直接相减会产生二进制长小数（如 87.13-10=77.129999...）
        self.hp = max(0, round(self.hp - actual, 2))
        # 联机主机伤害广播钩子：扣血完成后以「实际伤害」回调一次（恰好一次/次伤害事件）。
        # 单机/客户端模式为 None 直接跳过，零开销，行为与旧版完全一致。
        if self.on_take_damage is not None:
            self.on_take_damage(actual)
        return actual

    def heal(self, amount: int):
        """回复生命（不超过最大生命值）"""
        self.hp = min(self.max_hp, self.hp + amount)

    def apply_hot(self, heal_per_sec: float, duration: float):
        """施加持续回复效果（新效果覆盖旧效果）"""
        self.heal_per_sec = heal_per_sec
        self.heal_duration = duration

    def apply_potion_effect(self, effect: str, value: float, duration: float) -> float:
        """应用药水效果（本地玩家/联机幽灵共用入口），返回治疗量（非治疗返回 0）

        效果分支（effect 见 entities/equipment_defs.py POTIONS）：
        - heal:   回复生命。duration>0 走持续回复（HoT）；duration<=0 瞬间回复——
                  修复：旧版无 duration 字段的治疗药水 apply_hot(value, 0) 永不生效
        - speed:  移速倍率提升（乘算在装备倍率之上）
        - fruit:  组合效果：瞬回 + 移速提升（仙人掌果实）
        - shield: 获得临时护盾（先于防御结算吸收伤害，见 take_damage）
        - power:  攻击伤害倍率提升（攻击结算乘算，见 character_skills.modify_attack_damage）
        """
        heal_amount = 0.0
        if effect == "heal":
            if duration > 0:
                self.apply_hot(value / duration, duration)
            else:
                self.heal(int(value))
                heal_amount = float(value)
        elif effect == "speed":
            self.speed_mult = value * self.gear_speed_mult
            self.speed_effect_timer = duration
        elif effect == "fruit":
            self.heal(int(value))
            self.speed_mult = FRUIT_SPEED_MULT * self.gear_speed_mult
            self.speed_effect_timer = duration
            heal_amount = float(value)
        elif effect == "shield":
            self.shield = float(value)
            self.shield_effect_timer = duration
        elif effect == "power":
            self.power_mult = float(value)
            self.power_effect_timer = duration
        return heal_amount

    def apply_debuff(self, effect_id: str, level: int = 1):
        """施加附加效果（中毒/燃烧/冰冻/减速/眩晕），同类刷新持续时间

        与怪物侧实现一致：中毒/燃烧按 tick 掉血（直接扣 hp，不受防御减免），
        冰冻/减速降速、眩晕无法行动。
        level：效果等级（联机同步修复：客户端装备附加效果按上报等级生效，
        不再固定 1 级），经 effect_params 计算等级缩放后的数值/时长。
        """
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
                    # 中毒/燃烧：持续掉血。玩家侧直接扣 hp（绕防御，与计划一致），
                    # 不使用 take_damage 以免被防御减免影响
                    self.hp = max(0, self.hp - dmg)
        # 持续时间递减并清理过期效果
        for d in list(self.debuffs):
            d["duration"] -= delta_time
            if d["duration"] <= 0:
                self.debuffs.remove(d)
        # 重新计算减速倍率与眩晕状态
        self._recalc_debuffs()

    def get_active_debuffs(self):
        """返回当前生效的 debuff id 列表（供 HUD 显示）"""
        return [d["id"] for d in self.debuffs]

    @property
    def effective_speed_mult(self) -> float:
        """最终移速倍率 = 角色基础移速倍率 × 药水/装备倍率 × debuff 减速倍率"""
        return self.char_speed_mult * self.speed_mult * self._debuff_speed_mult

    def update_skill(self, delta_time: float):
        """每帧递减技能冷却与技能生效时间（由 PlayerController.update 或联机幽灵更新路径调用）

        冷却归零后可再次释放；生效时间归零表示技能效果结束（如圣盾庇护减伤结束）。
        """
        if self.skill_cd > 0:
            self.skill_cd = max(0.0, self.skill_cd - delta_time)
        if self.skill_active > 0:
            self.skill_active = max(0.0, self.skill_active - delta_time)

    @property
    def alive(self) -> bool:
        """是否存活"""
        return self.hp > 0


class PlayerController:
    """处理键盘输入与相机更新

    职责：
    1. 跟踪 WASD 按键状态
    2. 根据按键计算玩家速度
    3. 更新物理引擎和相机位置
    """
    def __init__(self, player: Player, physics_engine, key_bindings=None):
        self.player = player
        self.physics_engine = physics_engine
        self.camera = arcade.Camera2D(
            # 投影固定为逻辑分辨率（1280x720 中心对称 ±640/±360）：
            # Camera2D 默认按「构造时 viewport」生成投影，若 resize 后再进图，
            # 投影会变为 ±800/±450，与文字层 FixedLogicalProjector 的固定投影
            # (±W/2, ±H/2) 不一致 → 相机移动时文字与画面位移不同步。
            # 显式固定投影后，任意窗口尺寸下世界层/文字层 NDC 换算完全一致。
            projection=LRBT(
                -WINDOW_WIDTH / 2, WINDOW_WIDTH / 2,
                -WINDOW_HEIGHT / 2, WINDOW_HEIGHT / 2,
            ),
        )
        # Camera2D.position 是视口中心的世界坐标，直接设为玩家中心即可居中
        self.camera.position = (player.center_x, player.center_y)
        # 相机是否跟随玩家（观战模式置 False：相机改由 game_view 观战段控制，
        # 否则 update() 每帧把相机拉回已撤离/阵亡的静止玩家，观战视角卡死）
        self.follow_player = True
        # 追踪当前按下的键，解决对侧键冲突（如同时按 A 和 D）
        self._pressed = set()
        # 键位绑定：{动作名: [键码, ...]}，由 GameView 传入（默认 config.KEY_BINDINGS）。
        # 移动方向查询绑定键码，实现设置界面重绑后移动键同步生效。
        self._bindings = self._resolve_bindings(key_bindings)

    @staticmethod
    def _resolve_bindings(key_bindings):
        """把键位绑定（动作名 -> [arcade.key 属性名]）解析为 {动作名: [键码, ...]}

        传入 None 时使用 config.KEY_BINDINGS 默认值；键名解析失败（异常/未知属性）
        时跳过该键，保证配置数据损坏也不会崩溃。
        """
        from config import KEY_BINDINGS
        src = key_bindings or KEY_BINDINGS
        result = {}
        for action, names in src.items():
            codes = []
            for n in names:
                code = getattr(arcade.key, n, None)
                if code is not None:
                    codes.append(code)
            result[action] = codes
        return result

    def _has_key(self, action):
        """当前按下的键中是否包含指定动作的任一绑定键"""
        return any(k in self._pressed for k in self._bindings.get(action, []))

    def refresh_bindings(self, key_bindings=None):
        """设置界面重绑后刷新移动键绑定（无需重建 controller）

        传入新的键位绑定 dict（或 None 回退默认）；已按住的键保留，
        新绑定从下一帧 _recalc_velocity 生效。
        """
        self._bindings = self._resolve_bindings(key_bindings)

    def on_key_press(self, key):
        """按键按下"""
        self._pressed.add(key)
        self._recalc_velocity()

    def on_key_release(self, key):
        """按键释放"""
        self._pressed.discard(key)
        self._recalc_velocity()

    def clear_keys(self):
        """清空全部按键状态（视图切换时按键释放事件会发给新视图而丢失，
        返回本视图须主动复位，否则角色会残留移动方向一直走）"""
        self._pressed.clear()
        self._recalc_velocity()

    def _recalc_velocity(self):
        """根据当前按下的键重新计算速度

        逻辑：如果只按了 A，向左移动；如果同时按了 A 和 D，速度为 0。
        眩晕状态（_stunned）下无法移动。
        """
        p = self.player
        if p._stunned:
            # 眩晕：无法移动（保持静止）
            p.change_x = 0
            p.change_y = 0
            return
        # X 轴（effective_speed_mult：药水/装备倍率 × debuff 减速倍率）
        # 移动键从键位绑定查询（设置界面重绑后此处同步生效）
        if self._has_key("move_left") and not self._has_key("move_right"):
            p.change_x = -PLAYER_SPEED * p.effective_speed_mult
        elif self._has_key("move_right") and not self._has_key("move_left"):
            p.change_x = PLAYER_SPEED * p.effective_speed_mult
        else:
            p.change_x = 0
        # Y 轴
        if self._has_key("move_up") and not self._has_key("move_down"):
            p.change_y = PLAYER_SPEED * p.effective_speed_mult
        elif self._has_key("move_down") and not self._has_key("move_up"):
            p.change_y = -PLAYER_SPEED * p.effective_speed_mult
        else:
            p.change_y = 0

    def update(self, delta_time: float):
        """每帧更新：物理引擎 + 地图边界 + 相机跟随"""
        # PhysicsEngineSimple.update() 自动将 change_x/y × delta_time 并应用位移
        # 不要再调 player.update()，否则位移翻倍
        self.physics_engine.update()
        # 结算玩家 debuff（中毒掉血/减速/眩晕）
        self.player._update_debuffs(delta_time)
        # 递减技能冷却与技能生效时间（角色技能 F 键用）
        self.player.update_skill(delta_time)
        # 地图边界
        self.player.clamp_to_map()
        # 相机直接跟随玩家（居中：position 即视口中心）。
        # 观战模式（follow_player=False）不跟随：相机已由 game_view 观战段
        # 控制跟随幽灵，此处再拉回玩家会使观战视角卡死在撤离点（修复场景2）
        if self.follow_player:
            self.camera.position = (self.player.center_x, self.player.center_y)

    def use_camera(self):
        """激活相机（设置视口变换）

        Camera2D 构造时 viewport 取自 framebuffer 初始尺寸（1280x720），
        窗口 resize 后不会自动更新；若沿用旧视口，世界层 GL 视口与
        main.FixedLogicalProjector（HUD/文字层）的 letterbox 视口不一致，
        会导致相机移动时文字与画面位移不同步（文字不随人物移动）。
        因此每次激活相机前，按当前窗口逻辑尺寸重算 letterbox 视口并同步，
        保证世界层与文字层使用同一 GL 视口（修复窗口缩放后文字错位 bug）。
        """
        win = arcade.get_window()
        log_w, log_h = win.get_size()
        scale = min(log_w / WINDOW_WIDTH, log_h / WINDOW_HEIGHT)
        vw = max(1, round(WINDOW_WIDTH * scale))
        vh = max(1, round(WINDOW_HEIGHT * scale))
        vx = (log_w - vw) // 2
        vy = (log_h - vh) // 2
        self.camera.viewport = LBWH(vx, vy, vw, vh)
        self.camera.use()
