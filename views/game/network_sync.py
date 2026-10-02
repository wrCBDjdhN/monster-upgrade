"""NetworkSyncManager：GameView 联机同步逻辑独立模块（网络同步管理器）

从 views/game_view.py 提取全部联机同步方法（主机广播/客户端应用/请求仲裁/快照序列化），
GameView 通过 self.net_sync 持有本管理器；本模块只读 GameView 状态（self.gv），
不反向修改 GameView 结构（Phase 1：仅提取，不改动 game_view.py）。
"""

import math
import random
import time
import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT,
    NET_SNAPSHOT_HZ,  # 状态快照广播频率（怪物快照 20Hz）
    NET_HEARTBEAT_SEC,  # 心跳间隔（客户端 RTT 测量与保活）
    NET_ACTION_TIME_BCAST_SEC,  # 行动时间广播间隔（主机每秒广播剩余行动时间）
    NET_ATTACK_SPEED_TOLERANCE,  # 主机攻速仲裁容差（倍数）：攻击间隔窗口 = (1/攻速) × 容差
    SNAPSHOT_HEAL_JUMP_RATIO,  # 快照回血跳变上限比例（相对 max_hp）：抑制缓慢回血作弊
    DAMAGE_RESULT_QUEUE_TIMEOUT,  # DAMAGE_RESULT 目标缺失时的排队等待上限（秒）
    PROJECTILE_SIZE,  # 标准弹丸边长（客户端远端弹丸纯表现层渲染尺寸）
    DROP_PICKUP_RADIUS,  # 掉落物拾取半径（主机拾取仲裁距离阈值，Todo 18）
    LIFESTEAL_DEFAULT, SPREAD_COUNT_DEFAULT, SPREAD_ANGLE_DEFAULT,  # 武器扩展机制默认值（吸血/散射）
    AURA_SLOW_TICK, AURA_SLOW_LEVEL,  # 攻速光环：减速结算周期与效果等级
    # 等级系统：击杀/撤离经验常量（客户端击杀结算用）
    EXP_KILL_BASE, EXP_BOSS_MULT, EXP_EVAC,
    ELITE_EXP_MULT,  # 阶段3 精英词缀怪经验倍率（客户端击杀结算与主机 on_monster_death 同口径）
    # 倒地/救援系统
    DOWNED_TIMEOUT, RESCUE_DISTANCE, RESCUE_DURATION, REVIVE_HP,
    CACTUS_THORN_DAMAGE,  # 仙人掌反伤：客户端近战攻击环境物命中时作用于攻击者（幽灵）
    ROCKET_PAD_INTERACT_RANGE,  # 火箭发射台交互距离
    CHEST_WELL_INTERACT_RANGE,  # 宝箱/水井交互距离
    EVAC_INTERACT_RANGE,  # 撤离点激活/修复交互距离（主机校验客户端撤离请求位置同口径）
    # 商队购买主机权威记账：单局限购上限 / 价目表 / 神器专属定价与可购池
    # （数值与可购范围一律取自 config，禁在协议处理层硬编码价格与限购）
    EVENT_CARAVAN_LIMITS, EVENT_CARAVAN_PRICES,
    EVENT_CARAVAN_ARTIFACT_PRICE, EVENT_CARAVAN_ARTIFACT_POOL,
    HIT_FLASH_DURATION,  # 受击闪白时长（远端怪物受击反馈）
    EVENT_BANNER_SEC,  # 阶段4 事件横幅时长（客户端镜像横幅计时）
)
from net.protocol import MsgType  # 联机消息类型枚举（MONSTER_SNAPSHOT 等）
from game.player import Player
from game.loot import DropItem
from game.harvestable import HarvestableEntity
from game.chest import AirdropChest  # 阶段4 空投补给箱（客户端按 MAP_CHANGE 追加重建）
from game.evac import (commit_run_to_warehouse, clear_run, rollback_run,
                        _merge_authoritative)
from game.map_events import CaravanPoint  # 阶段4 商队交互点（非 Sprite，仅坐标）
from game.build_system import Building  # 阶段1 局内建筑实体（客户端按广播只还原视觉对象）
from game.sound_manager import sound_manager
from game.effects import particle_system, floating_texts
from game.entity_callbacks import (
    on_harvestable_destroyed,
    _award_exp,  # 等级经验发放（客户端击杀/撤离经验共用，各端本地结算）
)
import game.monsters as monsters
from game.monsters import Zombie  # 远端怪物实例化兜底（未知类型回退用）
# 注意：_RemoteLaser / _lookup_weapon_by_name 在函数内延迟导入，避免与 game_view.py 构成模块级循环导入


class NetworkSyncManager:
    """联机同步管理器：承载 GameView 的全部网络同步逻辑（Phase 1 提取）

    - 持有 GameView 引用（self.gv），所有 GameView 状态经 self.gv 读写；
    - 方法签名与 game_view.py 原实现完全一致（仅 self → self.gv 重定向）；
    - 本类方法间互调保持 self.xxx（如 _broadcast_damage/_ensure_ghost），
      GameView 独有方法（_back_to_lobby/_reject_potion 等）经 self.gv.xxx 调用。
    """

    def __init__(self, game_view):
        """构造：绑定 GameView 实例（self.gv 为唯一外部状态入口）"""
        self.gv = game_view
        # 旧主机 MONSTER_SNAPSHOT 缺新字段（shield/attack_cd_ratio 等）时的告警去重标记：
        # net 层铁律「禁静默丢字段」——缺字段走 .get() 默认值兜底，但必须显式告警一次，
        # 不能悄悄按本地默认值渲染出与主机不一致的表现。
        self._warned_old_monster_snapshot = False
        # ── 主机权威仲裁状态（本次联机安全收口新增，全部按 player_id 键控）──
        # 攻速仲裁：_last_attack_at[pid] = 上次**已受理**攻击的主机墙钟时间；
        # 客户端可在容差窗口内略微提前/滞后，但不能按帧连发。
        self._last_attack_at: dict = {}
        # 攻击事件去重：_last_attack_ts[pid] = 上次受理的客户端 timestamp，
        # 重复重发同一 timestamp 的攻击事件直接丢弃（防重放放大伤害）。
        self._last_attack_ts: dict = {}
        # 技能冷却：_skill_cd_until[pid] = 该玩家技能就绪的主机墙钟时间戳。
        # 不用 ghost.skill_cd —— 幽灵不跑 Player.update，冷却永不衰减。
        self._skill_cd_until: dict = {}
        # 被拾取掉落身份缓存：net_id -> {item_type,item_id,level,quantity}。
        # 供 PICKUP_RESULT(already_taken) 携带物品身份，客户端据此精确回滚携带物。
        self._taken_drop_info: dict = {}
        # 目标缺失的 DAMAGE_RESULT 排队（等 MONSTER_SNAPSHOT 补对象）：
        # 元素 = {"target_id", "payload", "deadline", "x", "y"}
        self._pending_damage_results: list = []
        # 限频告警去重集合（幽灵缺失/坐标回退等，正常联机也会偶发，禁逐条刷屏）
        self._warned_keys: set = set()
        # ── 商队购买主机权威记账（本局限购镜像）────────────────────────────
        # 键控 player_id（禁信任 payload.player_id，一律取连接 sender_id），
        # 值 = {item_id: 本局已购件数}；限购上限取 config.EVENT_CARAVAN_LIMITS[kind]，
        # 与单机侧 gs.caravan_bought 同口径（按 item_id 计数，非按 kind 总数）。
        # 为什么主机要维护镜像：联机客户端的购买走 CARRIAGE_BUY 请求-回执，
        # 主机是唯一记账方；主机的 _players_run_carried 是携带物账本，
        # 本局限购计数没有随携带物下发的通道，故主机侧独立按 player_id 镜像一份。
        self._carriage_bought: dict = {}
        # 换局重置标记：记录上次记账时的地图种子（GameState.current_map_seed），
        # 种子变化即视为新一局，惰性清空限购镜像（同一 GameView 实例会跨局复用 setup()，
        # 禁依赖 game_view 侧额外钩子复位——本任务不改 game_view.py）。
        self._carriage_bought_seed = None

    def _broadcast_damage(self, monster: "arcade.Sprite", damage: float, hit: bool = True,
                          crit: bool = False, debuffs: list = None) -> None:
        """主机广播单条 DAMAGE_RESULT（攻击判定收敛主机：命中→广播，各端同步血量显示）

        target_id 取怪物 net_id（Todo 10 分配；未分配 net_id 的怪物无法被客户端定位，跳过）。
        debuffs：命中施加的附加效果列表 [(效果ID, 等级), ...]（修复客户端特殊效果不全生效：
        之前只广播单个无等级 debuff，客户端只能施加 1 级且多效果丢失）。
        """
        net_id = getattr(monster, "net_id", None)
        if net_id is None:
            return  # 无 net_id（solo 模式怪物无网络 id），不广播
        gs = self.gv.window.game_state
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.broadcast(MsgType.DAMAGE_RESULT, {
                "target_id": net_id,
                "damage": damage,
                "hit": hit,
                "crit": crit,
                "debuffs": [list(d) if isinstance(d, tuple) else d for d in (debuffs or [])],
            })

    def _resolve_attack_event(self, sender_id: int, payload: dict) -> None:
        """主机权威裁决客户端的攻击动作（ATTACK_EVENT）：命中判定收敛主机

        - 攻击者用客户端幽灵（remote_players，懒创建于地图出生点）；若该 id 是主机本地
          玩家（gs.net_player_id，大厅接入后由 todo 21 设置）则直接用 self.player；
        - 按武器名查本地武器表取数值（kind/damage/range/attack_speed/special/debuff），
          近战即时判定并广播 DAMAGE_RESULT；远程生成弹丸/激光（幽灵位置为发射原点），
          命中由 on_update 的 check_monster_hits/check_laser_hits 统一判定并广播；
        - 幽灵缺失时按出生点懒创建最小幽灵（完整玩家实体同步是 todo 23，此处保持最小实现）。
        """
        gs = self.gv.window.game_state
        # 身份收口：攻击者一律取连接 sender_id，禁信任 payload.attacker_id
        # （伪造他人 id 可借别人幽灵的位置裁决攻击，并污染他人冷却/任务归属）
        attacker_id = sender_id
        weapon_name = payload.get("weapon", "")
        angle = float(payload.get("angle", 0.0))
        # 攻击者实体：防御性支持主机本地玩家（正常路径主机本地攻击不经此入口）
        if getattr(gs, "net_player_id", None) is not None and attacker_id == gs.net_player_id:
            attacker = self.gv.player
        else:
            ghost = self._ensure_ghost(attacker_id)
            attacker = ghost
        # 用客户端上报的攻击瞬间世界坐标修正幽灵位置：幽灵位置由 20Hz 快照更新存在
        # 滞后，近战扇形/远程弹丸发射点按滞后位置裁决会导致 miss（修复客户端攻击
        # 打不中怪物/资源）。仅修正幽灵（self.player 是主机本地实体，位置本就准确）。
        report_x = payload.get("x")
        report_y = payload.get("y")
        if report_x is not None and report_y is not None and attacker is not self.gv.player:
            attacker.center_x = float(report_x)
            attacker.center_y = float(report_y)
        # 按武器名查定义（主机权威数值；未知武器回退拳头）
        from views.game_view import _lookup_weapon_by_name
        wdef = _lookup_weapon_by_name(weapon_name)
        kind = wdef.get("kind", "melee")
        # 射程回退统一走 lookup_weapon_range（entities/weapon_defs 单一数值来源，
        # 近战 50 / 远程 250），禁在本层硬编码 40 之类的魔法数
        from game.monster_utils import lookup_weapon_range
        wrange = float(wdef.get("range") or lookup_weapon_range(kind, weapon_name))
        # 攻速仲裁（主机权威）：客户端上报 attack_speed 仅作为**限速下界**，
        # 防「按帧连发」无限刷伤害；窗口 = (1/attack_speed) × config.NET_ATTACK_SPEED_TOLERANCE。
        # 缺失/非法时回退武器模板攻速（数值口径仍取主机武器表，本项不改伤害/射程）。
        try:
            reported_speed = float(payload.get("attack_speed") or 0)
        except (TypeError, ValueError):
            reported_speed = 0.0
        if reported_speed <= 0:
            reported_speed = float(wdef.get("attack_speed", 1.0) or 1.0)
        now = time.time()
        min_gap = (1.0 / max(0.05, reported_speed)) * NET_ATTACK_SPEED_TOLERANCE
        last_at = self._last_attack_at.get(attacker_id)
        if last_at is not None and (now - last_at) < min_gap:
            # 超频攻击：丢弃本次事件（不扣血、不生成弹丸），显式告警一次
            print(f"[Host] 玩家 {attacker_id} 攻击超频（间隔 {now - last_at:.3f}s < "
                  f"窗口 {min_gap:.3f}s，攻速 {reported_speed:.2f}），已丢弃")
            return
        # timestamp 去重：同一 timestamp 重复重发（重放）直接丢弃
        ts = payload.get("timestamp")
        if ts is not None and ts == self._last_attack_ts.get(attacker_id):
            print(f"[Host] 玩家 {attacker_id} 重放同一 timestamp={ts!r} 的攻击事件，已丢弃")
            return
        self._last_attack_at[attacker_id] = now
        if ts is not None:
            self._last_attack_ts[attacker_id] = ts
        # 伤害优先采用客户端上报的实际伤害（修复联机假伤害 8/1）：主机模板 damage 是
        # 基础值，客户端武器可能已升级（gs.weapon_damage 更高），直接按模板裁决会让
        # 升级武器在联机时伤害退回基础值；客户端是本人武器的权威源，与幽灵 HP 采纳同口径。
        damage = float(payload.get("damage") or wdef.get("damage", 8))
        speed = wdef.get("attack_speed", 1.0)
        # 攻击目标点：由方向角（弧度）换算（combat 用世界坐标目标点计算朝向）
        tx = attacker.center_x + math.cos(angle) * 100.0
        ty = attacker.center_y + math.sin(angle) * 100.0
        # 客户端上报的装备/武器附加 debuff 列表（元素为 (效果ID, 效果等级) 元组）：
        # 客户端 _attack_debuffs 来自头盔/护甲/背包 effects 与武器 debuff，命中时一并施加
        # （修复「客户端装备特殊效果没起作用」——之前主机只按武器名施加 wdef 自带 debuff）
        raw_debuffs = payload.get("debuffs") or []
        debuffs = []
        for d in raw_debuffs:
            # 兼容 (id, lvl) 元组/列表两种载荷形态（json 序列化后元组变列表）
            if isinstance(d, (list, tuple)) and len(d) >= 2:
                debuffs.append((d[0], int(d[1])))
        # 客户端上报的装备暴击率和吸血（幽灵无此属性，需从客户端同步）：
        # 修复「客户端暴击/装备吸血不生效」——幽灵是最小实体，不继承装备 passive 效果
        if attacker is not self.gv.player:
            attacker.crit_chance = float(payload.get("crit_chance", 0.0))
            attacker.equip_lifesteal = float(payload.get("equip_lifesteal", 0.0))
        # 存活怪物列表（主机权威怪物，客户端攻击由主机裁决命中）
        monsters = [m for m in self.gv.monsters if hasattr(m, "alive") and m.alive]
        if kind == "melee":
            # 近战即时判定（按攻击者 id 独立冷却），逐条广播命中结果
            # 汇总武器自带 debuff + 客户端装备附加 debuff，一并传入 melee_attack 施加
            wdebuff = wdef.get("debuff")
            combined_debuffs = []
            if wdebuff:
                combined_debuffs.append((wdebuff, 1))
            combined_debuffs.extend(debuffs)
            hit = self.gv.combat.melee_attack(
                attacker, monsters, damage, wrange, tx, ty, speed,
                attacker_id=attacker_id,
                lifesteal=wdef.get("lifesteal", LIFESTEAL_DEFAULT),
                debuffs=combined_debuffs,
            )
            for m, actual in hit:
                self._broadcast_damage(m, actual, hit=True, crit=False, debuffs=combined_debuffs)
            # 近战命中环境物（修复「客户端近战打资源不同步」）：客户端近战攻击收敛主机后，
            # 环境物受击/摧毁/掉落也必须在主机裁决并广播（客户端本地不裁决环境物伤害）。
            # 判定口径与 handle_harvestable_combat 完全一致（范围 wrange+30、扇形 60°）
            for i, h in enumerate(self.gv.harvestables):
                if not h.alive:
                    continue
                dist = math.hypot(h.center_x - attacker.center_x,
                                  h.center_y - attacker.center_y)
                if dist <= (wrange + 30):
                    # 扇形角度检查：与本地路径一致（鼠标方向 = 攻击角 angle，弧度转度）
                    dx = h.center_x - attacker.center_x
                    dy = h.center_y - attacker.center_y
                    ang = math.degrees(math.atan2(dy, dx))
                    mouse_angle = math.degrees(angle)
                    diff = abs((ang - mouse_angle + 180) % 360 - 180)
                    if diff <= 60:  # 在攻击扇形内
                        dmg = int(damage)
                        h.take_damage(dmg)
                        # 主机广播环境物单次受击（Bug2 修复：客户端近战打资源时
                        # 客户端也能看到受击反馈，否则只有最终 env_destroyed 才同步）
                        self._broadcast_env_damage(i, dmg)
                        floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                        particle_system.emit(h.center_x, h.center_y, 5,
                                             (150, 150, 150), speed=60, life=0.3, size=3)
                        # 仙人掌反伤：攻击者（客户端幽灵）受到反弹伤害（受防御减免）
                        if h.resource_type == "cactus":
                            # 修复：用 take_damage 返回的实际伤害显示（护盾/防御减免后真实扣血量）
                            actual = attacker.take_damage(CACTUS_THORN_DAMAGE)
                            floating_texts.add_damage(attacker.center_x,
                                                      attacker.center_y + 30,
                                                      actual)
                        # 环境物被击杀：掉落 + 移出障碍（env_destroyed/drop_spawn
                        # 由 _broadcast_map_changes 与 drop_spawn 广播统一同步到客户端）
                        if not h.alive:
                            # 客户端近战裁决路径：采集经验归属客户端，不发给本端主机
                            # 阶段6.2 任务 harvest 同样按攻击者归属，主机单播给该客户端
                            on_harvestable_destroyed(self.gv, h, award_exp=False,
                                                     credit_net_id=attacker_id)
        elif wdef.get("special") == "laser":
            # 陨星炮：主机生成激光（起点=幽灵位置），命中由 check_laser_hits 统一广播；
            # 客户端装备附加 debuff 挂到激光上，命中即施加
            self.gv.combat.spawn_laser(
                attacker, damage, tx, ty,
                length=wrange, width=24, duration=3.0,
                attacker_id=attacker_id,
                debuffs=debuffs,
            )
        else:
            # 远程：生成弹丸（发射原点=幽灵位置），命中由 check_monster_hits 统一判定广播；
            # debuff 按武器定义生成（权杖随机 debuff 与单人本地路径保持一致），
            # 客户端装备附加 debuff 一并挂到弹丸上（命中时全部施加）
            debuff_id = None
            if wdef.get("random_debuff"):
                from entities.effects_defs import DEBUFF_POOL
                debuff_id = random.choice(DEBUFF_POOL)
            elif wdef.get("debuff"):
                debuff_id = wdef["debuff"]
            self.gv.combat.ranged_attack(
                attacker, damage,
                wdef.get("projectile_speed", 400), tx, ty,
                wdef.get("special", ""), debuff_id, speed,
                attacker_id=attacker_id,
                debuffs=debuffs,
                # 散射/吸血（主机裁决弹丸命中时生效）：散射按武器定义生成多发弹丸
                lifesteal=wdef.get("lifesteal", LIFESTEAL_DEFAULT),
                spread_count=wdef.get("spread_count", SPREAD_COUNT_DEFAULT),
                spread_angle=wdef.get("spread_angle", SPREAD_ANGLE_DEFAULT),
            )

    def _resolve_skill_use(self, sender_id: int, payload: dict) -> None:
        """主机权威裁决客户端的技能释放（SKILL_USE）：技能效果收敛主机

        - 施放者用客户端幽灵（remote_players，懒创建于地图出生点，角色 id 来自
          ROOM_START 名册）；若该 id 是主机本地玩家则直接用 self.player；
        - 用客户端上报的施放瞬间世界坐标修正幽灵位置（与 ATTACK_EVENT 同口径，
          20Hz 快照存在滞后，技能弹丸发射点/影袭落点按滞后位置裁决会 miss）；
        - use_skill(broadcast=True)：奥术爆发弹丸经 PROJECTILE_SNAPSHOT 同步、
          影袭落点命中经 _broadcast_damage 广播、圣盾减伤在幽灵 take_damage 结算，
          全部与客户端本地纯表现（input_handler F 键）保持一致。
        """
        gs = self.gv.window.game_state
        # 身份收口：施放者一律取连接 sender_id，禁信任 payload.player_id
        # （伪造他人 id 可用别人幽灵的名义放技能，并污染他人冷却/任务归属）
        caster_id = sender_id
        # 施放者实体：防御性支持主机本地玩家（正常路径主机本地技能不经此入口）
        if getattr(gs, "net_player_id", None) is not None and caster_id == gs.net_player_id:
            caster = self.gv.player
        else:
            caster = self._ensure_ghost(caster_id)
        # 用客户端上报的施放瞬间世界坐标修正幽灵位置（仅幽灵；主机本地玩家位置本就准确）
        report_x = payload.get("x")
        report_y = payload.get("y")
        if report_x is not None and report_y is not None and caster is not self.gv.player:
            caster.center_x = float(report_x)
            caster.center_y = float(report_y)
        # 技能伤害基数 = 客户端上报的当前武器伤害（与 ATTACK_EVENT 采纳客户端伤害同口径）
        damage = float(payload.get("damage") or 0)
        from game.character_skills import use_skill, get_skill
        # 技能冷却（主机墙钟权威）：幽灵不跑 Player.update → ghost.skill_cd 永不衰减，
        # 直接复用会让该玩家技能只能用一次；这里按主机墙钟记录就绪时刻，
        # 冷却窗口取 get_skill(caster)["cooldown"]（entities/character_defs 单一来源）
        skill_def = get_skill(caster)
        if skill_def is None:
            print(f"[Host] 玩家 {caster_id} 无角色技能定义，忽略 SKILL_USE")
            return
        cooldown = float(skill_def.get("cooldown", 0.0) or 0.0)
        now = time.time()
        ready_at = self._skill_cd_until.get(caster_id)
        if ready_at is not None and now < ready_at:
            print(f"[Host] 玩家 {caster_id} 技能冷却中（剩余 {ready_at - now:.2f}s），"
                  f"忽略本次 SKILL_USE")
            return
        if getattr(caster, "_stunned", False):
            print(f"[Host] 玩家 {caster_id} 处于眩晕，忽略本次 SKILL_USE")
            return
        self._skill_cd_until[caster_id] = now + cooldown
        # 幽灵 skill_cd 交给主机墙钟接管（每次放技能前清零，避免 can_use_skill 误拦）
        caster.skill_cd = 0.0
        use_skill(self.gv, caster,
                  float(payload.get("mouse_x") or 0),
                  float(payload.get("mouse_y") or 0),
                  damage, broadcast=True)

    def _apply_damage_result(self, payload: dict) -> None:
        """客户端应用主机下发的 DAMAGE_RESULT：远端怪物按 net_id 扣血并显示命中反馈

        - 目标缺失（怪物尚未进快照）时按 deadline 排队，等 MONSTER_SNAPSHOT 补到再重放；
      超过 0.5s 仍未出现则丢弃并限频告警（怪物大概率真被击杀，丢弃是对的）；
      丢失代价 = 客户端一次命中反馈/伤害数字缺失，下一帧 MONSTER_SNAPSHOT 会校准血量；
    - 主机权威血量以 MONSTER_SNAPSHOT 为准，此处扣血仅做即时显示，下一帧快照校准。
        """
        target_id = payload.get("target_id")
        if target_id is None:
            return
        rm = self.gv.remote_monsters.get(target_id)
        if rm is None:
            # 目标缺失：入队等快照补对象（攻击判定先于快照到达是正常时序，不是异常）
            self._pending_damage_results.append({
                "target_id": target_id,
                "payload": dict(payload),
                "deadline": time.time() + DAMAGE_RESULT_QUEUE_TIMEOUT,
                "x": payload.get("x"),
                "y": payload.get("y"),
            })
            return
        damage = payload.get("damage", 0)
        if payload.get("hit"):
            # 扣血（不小于 0）+ 命中音效 + 漂浮伤害文字 + 受击闪白
            rm.hp = max(0.0, rm.hp - damage)
            sound_manager.play_monster_hit()
            floating_texts.add_damage(rm.center_x, rm.center_y + 25, damage)
            if hasattr(rm, "_hit_flash"):
                rm._hit_flash = HIT_FLASH_DURATION
            # 等级系统：客户端击杀经验（各端本地结算）
            # 主机在 on_monster_death 按 last_attacker_id 归属发放；客户端无归属广播，
            # 简化：DAMAGE_RESULT 使远端怪物血量归零即视为本端参与击杀（host 本地玩家击杀
            # 也走本路径，net_mode 非 client 时跳过避免与 on_monster_death 重复发放）。
            if (rm.hp <= 0 and not getattr(rm, "_exp_awarded", False)
                    and getattr(self.gv.window.game_state, "net_mode", "solo") == "client"):
                rm._exp_awarded = True  # 防重复：同一怪物只发一次击杀经验
                amount = EXP_KILL_BASE * (EXP_BOSS_MULT if getattr(rm, "is_boss", False) else 1)
                # 阶段3 精英词缀怪：客户端补上主机侧的精英经验倍率（快照字段 net_is_elite），
                # 否则「客户端补刀精英」只发单倍经验，与主机/solo 口径不一致
                if getattr(rm, "is_elite", False) or getattr(rm, "net_is_elite", False):
                    amount = int(amount * ELITE_EXP_MULT)
                _award_exp(self.gv, amount)
                # 阶段4 尸潮事件：客户端补上 reward_mult 的额外经验（与主机
                # _on_monster_death → award_event_kill_exp 同口径；客户端不跑
                # 怪物死亡回调，故必须在此补，否则两端经验不一致）
                from game.map_events import event_exp_bonus
                extra = event_exp_bonus(self.gv, amount)
                if extra > 0:
                    _award_exp(self.gv, extra)
        # 附加 debuff 列表（表现层记录，快照会校准覆盖）：逐条施加，含效果等级
        # （修复客户端特殊效果不全生效：旧版只广播单个无等级 debuff，多效果丢失）
        debuffs = payload.get("debuffs") or []
        if not debuffs and payload.get("debuff"):
            debuffs = [[payload.get("debuff"), 1]]  # 兼容旧单字段载荷
        for entry in debuffs:
            if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                eid = entry[0]
                lvl = int(entry[1]) if len(entry) >= 2 and entry[1] else 1
            else:
                eid, lvl = entry, 1
            if eid and hasattr(rm, "apply_debuff"):
                try:
                    rm.apply_debuff(eid, lvl)
                except Exception:
                    pass  # 未知效果：忽略，快照校准

    def _drain_pending_damage_results(self) -> None:
        """重放排队的 DAMAGE_RESULT：MONSTER_SNAPSHOT 补到目标后即时补扣血

        调用点：客户端每次应用 MONSTER_SNAPSHOT 之后（快照是「目标已存在」的信号）。
        超时（> config.DAMAGE_RESULT_QUEUE_TIMEOUT）仍未出现的条目直接丢弃——
        怪物多半已被击杀（死亡条目会从 remote_monsters 删除），丢弃才是正确行为；
        丢弃时按 target_id 限频告警一次，禁静默（否则「一直不显示命中」无从排查）。
        """
        if not self._pending_damage_results:
            return
        now = time.time()
        still_pending = []
        for entry in self._pending_damage_results:
            target_id = entry["target_id"]
            if target_id in self.gv.remote_monsters:
                # 目标已出现 → 重放（转交 _apply_damage_result 走同一扣血/反馈路径）
                self._apply_damage_result(entry["payload"])
            elif now > entry["deadline"]:
                warn_key = ("damage_drop", target_id)
                if warn_key not in self._warned_keys:
                    self._warned_keys.add(warn_key)
                    print(f"[Client] DAMAGE_RESULT 目标 {target_id} 超时未出现在快照中，"
                          f"已丢弃（血量以下一帧快照为准）")
            else:
                still_pending.append(entry)
        self._pending_damage_results = still_pending

    def _ensure_ghost(self, player_id: int) -> Player:
        """主机/客户端按需创建远端玩家幽灵（懒创建），并注册主机侧受击广播钩子

        - 幽灵 = Player 实例，持全量 HP（主机权威）；完整实体同步是 todo 23，
          此处按地图出生点生成最小幽灵（攻击/受击坐标足够）；
        - 主机模式：幽灵注册 on_take_damage 钩子 → 扣血即广播 PLAYER_HURT
          （HP 主机权威，见 player.py on_take_damage 注释）；
        - 客户端模式：只创建幽灵供 HP 显示（钩子恒 None，不广播）。
        """
        ghost = self.gv.remote_players.get(player_id)
        if ghost is not None:
            return ghost
        gs = self.gv.window.game_state
        # 幽灵出生点：优先取 ROOM_START 下发的全房出生点（gs.net_spawns 含主机 slot=0），
        # 未知玩家（晚期加入等）回退到首个房间中心
        spawn = (gs.net_spawns or {}).get(player_id)
        if spawn is not None:
            sx, sy = spawn
        else:
            sx, sy = (self.gv.map_data["rooms"][0].center
                      if self.gv.map_data.get("rooms") else (400, 400))
        # 幽灵名册：玩家名 + 角色 id（名册驱动，供渲染头顶名称、状态条与技能裁决用）
        roster_entry = (gs.net_roster or {}).get(player_id)
        ghost = Player(
            center_x=sx, center_y=sy,
            character_id=roster_entry.get("character_id", "initial") if roster_entry else "initial",
        )
        ghost.net_name = roster_entry.get("name", f"玩家{player_id}") if roster_entry else f"玩家{player_id}"
        # 幽灵联机身份：技能弹丸归属（_arcane_blast 取 owner_net_id=net_player_id，
        # 客户端快照跳过 owner_id==my_id 去重，见 _apply_projectile_snapshot 注释）
        ghost.net_player_id = player_id
        # 幽灵朝向（Entity 同步 todo 23 快照字段，默认朝右）
        ghost.facing = 0.0
        # 主机权威：幽灵受击（怪物近战/弹丸）→ 广播 PLAYER_HURT（各端扣血显示）。
        # debuff 从幽灵 _pending_debuff 读取：怪物攻击路径先设置再 take_damage，
        # 钩子广播时随伤害一并下发（修复客户端玩家被怪物攻击时特殊效果未生效）
        if gs.net_mode == "host":
            ghost.on_take_damage = lambda actual, pid=player_id: self._broadcast_player_hurt(
                pid, actual,
                getattr(ghost, "_pending_debuff", None),
                getattr(ghost, "_pending_debuff_level", 1),
                getattr(ghost, "_pending_debuff_effects", None),
            )
        self.gv.remote_players[player_id] = ghost
        print(f"[GameView] 远端玩家幽灵懒创建: player_id={player_id} @({sx:.0f},{sy:.0f})")
        return ghost

    def _broadcast_player_hurt(self, player_id: int, damage: float,
                               debuff: dict = None, debuff_level: int = 1,
                               debuffs: list = None) -> None:
        """主机广播 PLAYER_HURT：玩家受伤（HP 主机权威），各端据此本地扣血 + 受击反馈

        debuff/debuff_level：怪物攻击（近战/弹丸）附带的附加效果与等级，随广播下发
        客户端，修复「客户端玩家被怪物攻击时特殊效果未生效」（旧版钩子恒传 None）。
        debuffs：全部效果列表 [(效果ID, 效果等级), ...]（联机多效果同步用）
        """
        gs = self.gv.window.game_state
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.broadcast(MsgType.PLAYER_HURT, {
                "player_id": player_id,
                "damage": damage,
                "debuff": debuff,
                "debuff_level": debuff_level,
                "debuffs": debuffs or [],
            })

    def _serialize_players(self) -> list:
        """序列化全部玩家为 PLAYER_SNAPSHOT 的 players 列表（主机权威）

        - 主机本地玩家 = gs.net_player_id（未接入大厅时为 0 约定值）；
        - 客户端幽灵 = remote_players（HP 权威值，位置暂为出生点，todo 23 补实体同步）；
        - 载荷键名严格遵循 net/protocol.py MESSAGE_SCHEMAS["PLAYER_SNAPSHOT"]。
        - 倒地玩家：alive=False（客户端侧幽灵消失），但 _player_status 为 "downed"。
        """
        gs = self.gv.window.game_state
        host_id = getattr(gs, "net_player_id", None)
        if host_id is None:
            host_id = 0  # 大厅未接入（todo 21）前的约定：主机固定玩家 id=0
        players = [{
            "player_id": host_id,
            "x": self.gv.player.center_x, "y": self.gv.player.center_y,
            "hp": self.gv.player.hp, "max_hp": self.gv.player.max_hp,
            "weapon": self.gv._current_weapon_name(),
            "facing": getattr(self.gv.player, "facing", 0.0),
            # 倒地/观战/死亡：alive=False（客户端侧幽灵消失）
            "alive": self.gv.player.alive and not self.gv._spectating and not self.gv.player.downed,
            # 阶段5 祝福：把含祝福的有效属性一并广播，客户端可据此校准幽灵承伤显示
            "stats": self.gv.player.blessing_stats(),
        }]
        for pid, ghost in self.gv.remote_players.items():
            # 倒地玩家：alive=False（客户端侧幽灵消失）
            is_downed = self.gv._player_status.get(pid) == "downed"
            players.append({
                "player_id": pid,
                "x": ghost.center_x, "y": ghost.center_y,
                "hp": getattr(ghost, "hp", 0), "max_hp": getattr(ghost, "max_hp", 0),
                "weapon": None, "facing": 0.0,
                "alive": getattr(ghost, "alive", True) and not is_downed,
                # 幽灵属性（主机此前从该客户端上报的 stats 套用），转发供其他端渲染参考
                "stats": ghost.blessing_stats(),
            })
        return players

    def _apply_player_hurt(self, payload: dict) -> None:
        """客户端应用主机 PLAYER_HURT：本地扣血 + 受击反馈；远端玩家幽灵同步 HP

        - player_id == 本地玩家 id → 本地扣血条 + 屏幕红闪 + 音效（本地触发，
          不依赖额外网络往返）；HP 权威值以 PLAYER_SNAPSHOT 校准为准；
        - player_id 为他人 → 对应幽灵 hp 扣减（显示用，快照校准）。
        """
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        damage = payload.get("damage", 0)
        if player_id is None:
            return
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id == my_id:
            # 本地玩家受伤：扣血 + 红闪 + 音效（受击反馈本地即时触发）
            self.gv.player.hp = max(0, round(self.gv.player.hp - damage, 2))
            self.gv._player_hit_flash = 0.3
            sound_manager.play_hurt()
            floating_texts.add_damage(self.gv.player.center_x, self.gv.player.center_y + 20, damage)
            debuff = payload.get("debuff")
            debuff_level = payload.get("debuff_level") or 1  # 附带效果等级（默认 1 级）
            if debuff and hasattr(self.gv.player, "apply_debuff"):
                try:
                    self.gv.player.apply_debuff(debuff, debuff_level)
                except Exception:
                    pass  # 未知效果：忽略，快照校准
        else:
            # 远端玩家（幽灵）受伤：同步 HP 显示（扣血，快照校准）
            ghost = self.gv.remote_players.get(player_id)
            if ghost is not None:
                ghost.hp = max(0, round(getattr(ghost, "hp", 0) - damage, 2))

    def _apply_player_snapshot(self, payload: dict) -> None:
        """客户端应用主机 PLAYER_SNAPSHOT：校准本地与幽灵 HP（防漂移）"""
        gs = self.gv.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        for entry in payload.get("players", []):
            pid = entry.get("player_id")
            hp = entry.get("hp", 0)
            max_hp = entry.get("max_hp", 0)
            if my_id is not None and pid == my_id:
                # 观战期跳过自身 HP 校准：主机快照中已撤离玩家幽灵 alive=False → hp=0，
                # 若仍校准会把本地 player.hp 置 0（触发 Player.alive=False 派生副作用），
                # 观战渲染已由 _spectating 守卫隐藏本体，HP 值无需再校准（修复观战崩溃）
                if self.gv._spectating:
                    continue
                # 本地玩家：权威 HP 校准（含 max_hp；round 避免浮点长小数）
                self.gv.player.max_hp = max(1, int(max_hp or self.gv.player.max_hp))
                self.gv.player.hp = min(self.gv.player.max_hp, round(hp, 2))
            else:
                ghost = self.gv.remote_players.get(pid)
                if ghost is None:
                    ghost = self._ensure_ghost(pid)
                ghost.max_hp = max(1, int(max_hp or getattr(ghost, "max_hp", 0)))
                ghost.hp = min(ghost.max_hp, round(hp, 2))
                # 远端实体同步：主机快照为权威位置/朝向/存活（todo 23 玩家实体同步）
                ghost.center_x = entry.get("x", ghost.center_x)
                ghost.center_y = entry.get("y", ghost.center_y)
                ghost.facing = entry.get("facing", getattr(ghost, "facing", 0.0))
                if not entry.get("alive", True):
                    ghost.hp = 0  # 存活标记为假：HP 归零（Player.alive 由 hp>0 派生）

    def _send_player_snapshot(self) -> None:
        """客户端 20Hz 上报本人实体为 PLAYER_SNAPSHOT（单播，主机据此更新本端幽灵）

        - 客户端是本人位置/朝向/HP 的权威源：主机快照（_serialize_players）以
          ghost 转发给其他端，实现全房实体同步（todo 23 玩家实体同步）；
        - facing 用鼠标世界坐标计算（玩家本地无独立 facing 字段）。
        """
        gs = self.gv.window.game_state
        if gs.net_client is None or gs.net_player_id is None:
            return
        my_id = gs.net_player_id
        # 鼠标世界坐标（沿用 on_mouse_motion 里的计算；坐标系为逻辑分辨率，
        # 窗口最大化/全屏时由 main.GameWindow 统一换算，见 input_handler 注释）
        cam = self.gv.controller.camera.position if self.gv.controller else (0, 0)
        world_mx = self.gv._mouse_x + cam[0] - WINDOW_WIDTH / 2
        world_my = self.gv._mouse_y + cam[1] - WINDOW_HEIGHT / 2
        import math
        facing = math.atan2(world_my - self.gv.player.center_y,
                            world_mx - self.gv.player.center_x)
        gs.net_client.send((MsgType.PLAYER_SNAPSHOT, {
            "players": [{
                "player_id": my_id,
                "x": self.gv.player.center_x, "y": self.gv.player.center_y,
                "hp": self.gv.player.hp, "max_hp": self.gv.player.max_hp,
                "weapon": self.gv._current_weapon_name(),
                "facing": facing,
                # 观战时已撤离/阵亡：上报 alive=False → 主机侧幽灵 HP 归零、渲染消失，
                # 不会被主机观战跟随（修复：撤离幽灵停在撤离点不再移动，跟随即视角卡死）
                "alive": self.gv.player.alive and not getattr(self.gv, "_spectating", False),
                # 阶段5 祝福：上报本人「含祝福」的有效属性，主机据此套用到该玩家幽灵，
                # 使主机裁决该玩家承伤（ghost.take_damage 走 defense）时与本人一致
                "stats": self.gv.player.blessing_stats(),
            }],
        }))

    def _apply_client_snapshot(self, sender_id: int, payload: dict) -> None:
        """主机应用客户端上报的 PLAYER_SNAPSHOT：更新该玩家幽灵的位置/朝向/存活

        - 只采纳 sender_id 本人条目（防伪造他人坐标）；幽灵 HP/max_hp 采纳客户端上报值：
          客户端本地应用了装备被动效果（max_hp/regen 加成），是本人 HP/max_hp 的权威源，
          若不采纳，主机 _serialize_players 会把无加成的默认 max_hp 广播回客户端，
          客户端 _apply_player_snapshot 校准后会把装备 max_hp 加成覆盖掉（B1 装备效果不全生效）；
        - 阵亡时 HP 归零（主机随后 PLAYER_DEATH 单播通知）；
        - 幽灵经 _ensure_ghost 懒创建（客户端幽灵在主机侧必然已存在）。
        """
        gs = self.gv.window.game_state
        ghost = self._ensure_ghost(sender_id)
        for entry in payload.get("players", []):
            if entry.get("player_id") != sender_id:
                continue  # 只同步本人上报条目
            ghost.center_x = entry.get("x", ghost.center_x)
            ghost.center_y = entry.get("y", ghost.center_y)
            ghost.facing = entry.get("facing", getattr(ghost, "facing", 0.0))
            # 采纳客户端上报的 HP/max_hp（含装备被动加成），保持幽灵与客户端本人一致，
            # 避免快照校准覆盖装备效果（详见函数 docstring）
            ghost.max_hp = max(1, int(entry.get("max_hp") or ghost.max_hp))
            reported_hp = min(ghost.max_hp, round(entry.get("hp", ghost.hp), 2))
            # 回血跳变限制：单帧上报的回血量 ≤ max_hp × config.SNAPSHOT_HEAL_JUMP_RATIO，
            # 超出部分按上限截断（扣血方向不限制：伤害本就应立即生效）。
            # 残余风险：客户端按接近上限的恒定速率持续回血仍可缓慢作弊——
            # 限幅只挡「一次性跳变」，彻底防作弊需主机侧独立结算回血来源。
            max_heal_step = ghost.max_hp * SNAPSHOT_HEAL_JUMP_RATIO
            if reported_hp > ghost.hp and (reported_hp - ghost.hp) > max_heal_step:
                reported_hp = ghost.hp + max_heal_step
            ghost.hp = reported_hp
            # 阶段5 祝福：套用客户端上报的「含祝福有效属性」——主机裁决该玩家承伤时
            # 走 ghost.take_damage（读 defense/shield），不同步会让主机按无祝福数值扣血。
            # 全部按绝对值覆盖（不是倍率叠乘），与客户端本地重算结果一致、不会双倍加成。
            stats = entry.get("stats")
            if isinstance(stats, dict):
                if "defense" in stats:
                    ghost.defense = float(stats["defense"])
                if "regen_per_sec" in stats:
                    ghost.regen_per_sec = float(stats["regen_per_sec"])
                if "char_speed_mult" in stats:
                    ghost.char_speed_mult = float(stats["char_speed_mult"])
                if "crit_chance" in stats:
                    ghost.crit_chance = float(stats["crit_chance"])
                if "lifesteal" in stats:
                    ghost.equip_lifesteal = float(stats["lifesteal"])
                if "thorns" in stats:
                    ghost.equip_thorns = float(stats["thorns"])
                if "damage_mult" in stats:
                    ghost.equip_damage_mult = float(stats["damage_mult"])
            if not entry.get("alive", True):
                ghost.hp = 0  # 客户端阵亡：HP 归零（主机随后 PLAYER_DEATH 单播通知）

    def _broadcast_room_ended(self, reason: str, close_room: bool = False) -> None:
        """主机广播 ROOM_ENDED 并收口本局房间生命周期（重构：房间生命周期与单局解耦）

        - reason：room_closed（点「关闭房间」按钮）/ all_finished（全员结束，回房等待再次开局）
          / host_evac / host_evac_fail / host_fail（旧路径保留兼容）；
        - close_room=True（reason=room_closed）：广播后停止 net_server 并清理联机字段，
          主机回 StartView（房间解散）。否则（close_room=False）房间保留：服务器继续监听、
          端口不释放，主机回 LobbyView host_wait 等待玩家再次准备开局。
        - 只在主机且尚未广播过时发送（_room_ended_handled 单次闸门，防止撤离+死亡交织重复）；
        - 广播已入队（call_soon_threadsafe），stop() join 期间事件循环会完成发送。
        """
        if self.gv._room_ended_handled:
            return
        self.gv._room_ended_handled = True
        gs = self.gv.window.game_state
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.broadcast(MsgType.ROOM_ENDED, {"reason": reason})
            print(f"[GameView] 主机广播房间结束: {reason} close_room={close_room}")
            if close_room:
                # 关闭房间：停止服务器释放端口，保证下一次建房可重新监听
                gs.net_server.stop()
                gs.net_server = None
                gs.net_mode = "solo"
                from views.start_view import StartView
                self.gv.window.show_view(StartView(self.gv.window_ref))
            else:
                # 本局结束但房间保留：主机回 LobbyView host_wait 等待再次开局（服务器不停止）
                self.gv._back_to_lobby("本局结束，等待再次开局")

    def _apply_room_ended(self, payload: dict) -> None:
        """客户端应用主机 ROOM_ENDED：按 reason 收口房间生命周期

        - room_closed（主机关闭房间）：房间解散 → 停止客户端连接回 StartView；
        - 其他（all_finished 全员结束等）：本局结束但房间保留 → 客户端不断开连接，
          回 LobbyView client_wait 等待主机再次开局（复用 gs.net_client）。
        - 未撤离即失败语义由主机侧统一收口；_room_ended_handled 防止重复处理。
        """
        if self.gv._room_ended_handled:
            return
        self.gv._room_ended_handled = True
        gs = self.gv.window.game_state
        reason = payload.get("reason", "host_end")
        if reason == "room_closed":
            # 房间解散：停止客户端连接回主菜单（与 _leave 同口径，保留 net_room_id 供展示）
            if gs.net_client is not None:
                gs.net_client.stop()
            gs.net_client = None
            gs.net_mode = "solo"
            gs.run_carried = {}  # 房间结束未撤离：本次携带物不结算清除
            gs.run_potions = {}  # 本局药水槽一并清空
            from views.start_view import StartView
            self.gv.window.show_view(StartView(self.gv.window_ref))
            return
        # 本局结束但房间保留：回房等待（连接保持，等待主机再次开局）
        gs.run_carried = {}
        gs.run_potions = {}  # 本局药水槽一并清空
        self.gv._back_to_lobby(f"本局结束：{reason}")

    def _handle_potion_use(self, sender_id: int, payload: dict) -> None:
        """主机确认客户端药水使用请求：查库存 → 应用到对应玩家/幽灵 → 广播 POTION_ACK

        - potion_id 支持两种来源：
          * "run:item_id"：本局药水槽（不占背包容量），从 equipment_defs.POTIONS 取效果，
            无 DB 扣减；主机校验该玩家 run 药水库存并扣减；
          * 普通药水 id：查库验证 → use_potion 扣减。
        - 效果统一经 Player.apply_potion_effect（含瞬回修复/护盾/狂暴），主机权威：
          各端 HP 经 PLAYER_SNAPSHOT 校准；治疗数字随 POTION_ACK 广播，全端可见。
        - ACK 携带 effect/value/duration：客户端对本人本地即时生效（buff 不随快照同步）。
        """
        gs = self.gv.window.game_state
        potion_id = payload.get("potion_id", "")
        from entities.equipment_defs import POTIONS
        # 本局药水槽药水（run: 前缀，仅字符串）：效果来自 POTIONS 定义，扣减对应库存
        if isinstance(potion_id, str) and potion_id.startswith("run:"):
            item_id = potion_id[4:]
            pdef = POTIONS.get(item_id)
            if pdef is None:
                self.gv._reject_potion(sender_id, potion_id)
                return
            effect = pdef.get("effect", "heal")
            value = pdef.get("value", 0)
            duration = pdef.get("duration", 0)
            # 药水：同时处理 run_carried["potion"] 与 run_potions（本局携带药水两处口径）
            # 主机本地：gs.run_potions；远程玩家：_players_run_carried[sender_id]["potion"]
            if sender_id == getattr(gs, "net_player_id", None):
                run_potions = getattr(gs, "run_potions", None) or {}
                if run_potions.get(item_id, 0) <= 0:
                    self.gv._reject_potion(sender_id, potion_id)
                    return
                run_potions[item_id] -= 1
                if run_potions[item_id] <= 0:
                    del run_potions[item_id]
                carried = self.gv._players_run_carried.setdefault(sender_id, {})
                pslot = carried.setdefault("potion", {})
                if pslot.get(item_id, 0) > 0:
                    pslot[item_id] -= 1
                    if pslot[item_id] <= 0:
                        del pslot[item_id]
            else:
                carried = self.gv._players_run_carried.setdefault(sender_id, {})
                potion_slot = carried.setdefault("potion", {})
                if potion_slot.get(item_id, 0) <= 0:
                    self.gv._reject_potion(sender_id, potion_id)
                    return
                potion_slot[item_id] -= 1
                if potion_slot[item_id] <= 0:
                    del potion_slot[item_id]
        else:
            # 仓库药水：查库验证 → use_potion 扣减
            # 修复：sender_id 是网络 player_id（主机=0，客户端=1/2/3），
            # DB 查询需要 DB player_id（数据库自增 ID）。
            # 主机用 gs.player_id；客户端通过玩家名反查 DB ID。
            from db.database import get_potions, use_potion
            db_pid = sender_id
            if sender_id == getattr(gs, "net_player_id", None):
                db_pid = gs.player_id
            elif gs.net_server is not None:
                for pid, pname, _slot in gs.net_server.player_info():
                    if pid == sender_id:
                        from db.database import get_or_create_player
                        db_pid = get_or_create_player(pname)
                        break
            potions = get_potions(db_pid)
            pot = next((p for p in potions if p["id"] == potion_id), None)
            if pot is None:
                self.gv._reject_potion(sender_id, potion_id)
                return
            effect_info = use_potion(db_pid, potion_id)
            if not effect_info:
                self.gv._reject_potion(sender_id, potion_id)
                return
            effect = effect_info["effect"]
            value = effect_info.get("value", 0)
            duration = effect_info.get("duration", 0)
        # 目标玩家实体：主机本地玩家或客户端幽灵
        target = self.gv.player if getattr(gs, "net_player_id", None) == sender_id \
            else self._ensure_ghost(sender_id)
        # 统一应用药水效果（heal 无 duration 时瞬回，见 Player.apply_potion_effect）
        heal_amount = target.apply_potion_effect(effect, value, duration)
        # 广播确认（含效果信息，客户端本地即时生效 + 全端可见治疗数字）
        gs.net_server.broadcast(MsgType.POTION_ACK, {
            "player_id": sender_id, "potion_id": potion_id,
            "accepted": True, "heal_amount": heal_amount,
            "effect": effect, "value": value, "duration": duration,
        })

    def _apply_potion_ack(self, payload: dict) -> None:
        """客户端应用主机 POTION_ACK：本地即时生效 + 治疗数字漂浮文字

        - 本人：按 ACK 的 effect/value/duration 本地应用（speed/shield/power buff
          不随快照同步，须本地即时生效；heal 瞬回/持续），HP 权威值以
          PLAYER_SNAPSHOT 校准为准；
        - 他人（幽灵）：效果已在主机侧应用到幽灵实体，仅显示治疗数字；
        - accepted=False（库存不足/无效）时仅提示，不做本地扣减。
        """
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        heal_amount = payload.get("heal_amount", 0)
        accepted = payload.get("accepted", False)
        my_id = getattr(gs, "net_player_id", None)
        if not accepted:
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 20,
                              "药水无效", arcade.color.RED)
            return
        # 本人：本地即时应用效果（与主机对本人实体应用同口径）
        if my_id is not None and player_id == my_id:
            effect = payload.get("effect", "heal")
            value = payload.get("value", 0)
            duration = payload.get("duration", 0)
            heal_amount = self.gv.player.apply_potion_effect(effect, value, duration)
            # 本局药水（run: 前缀）：本地同步扣减 run_potions（主机权威扣减后广播，
            # 客户端据此保持本地槽位与主机一致；仓库药水已由主机 use_potion 扣减，无需处理）
            potion_id = payload.get("potion_id", "")
            if isinstance(potion_id, str) and potion_id.startswith("run:"):
                item_id = potion_id[4:]
                run_potions = getattr(gs, "run_potions", None)
                if run_potions and run_potions.get(item_id, 0) > 0:
                    run_potions[item_id] -= 1
                    if run_potions[item_id] <= 0:
                        del run_potions[item_id]
                # 双账本口径（修复 desync）：本局药水在 run_carried["potion"] 里**另有
                # 一份同源账本**（联机客户端商队购入药水按 game/map_events 的客户端
                # 双写保底写这里；主机侧 POTION_USE 也校验 _players_run_carried["potion"]）。
                # 只扣 run_potions 会让 run_carried["potion"] 残留一瓶已用掉的药水，
                # 撤离时按权威清单入库 → 凭空多入账一瓶（客户端撤离结算对不上账）。
                # 故此处与 run_potions 同步扣减；键缺失（该药水不来自 run_carried）时安全跳过。
                run_carried = getattr(gs, "run_carried", None)
                if isinstance(run_carried, dict):
                    carried_potions = run_carried.get("potion")
                    if isinstance(carried_potions, dict) \
                            and carried_potions.get(item_id, 0) > 0:
                        carried_potions[item_id] -= 1
                        if carried_potions[item_id] <= 0:
                            del carried_potions[item_id]
            # 浮动文字：按效果显示对应文案
            if heal_amount > 0:
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y,
                                   f"+{int(heal_amount)} HP", arcade.color.GREEN)
            elif effect == "speed":
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y,
                                   "加速!", arcade.color.CYAN)
            elif effect == "shield":
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y,
                                   f"护盾 +{int(value)}", (120, 160, 255))
            elif effect == "power":
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y,
                                   f"狂暴! +{int((value - 1) * 100)}% 伤害", (255, 120, 40))
            elif effect == "fruit":
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y,
                                   "果实加速!", arcade.color.GREEN)
        else:
            ghost = self.gv.remote_players.get(player_id)
            if ghost is not None and heal_amount > 0:
                floating_texts.add(ghost.center_x, ghost.center_y,
                                   f"+{int(heal_amount)} HP", arcade.color.GREEN)

    def _handle_pickup_request(self, sender_id: int, payload: dict) -> None:
        """主机仲裁客户端拾取请求：先到先得 + 距离校验，广播 PICKUP_RESULT（Todo 18）

        仲裁规则（对应协议 PICKUP_REQUEST/RESULT 设计）：
        - 按掉落物网络 id 在主机掉落列表中查找（掉落在主机权威生成，客户端视觉为副本）；
        - 距离校验：用该玩家幽灵位置（远程玩家实体）与掉落物距离 < DROP_PICKUP_RADIUS；
        - 先到先得：匹配成功即从主机掉落列表移除该掉落物并广播 accepted=True，
          后续同 id 请求必然失败（already_taken）——双客户端抢同一掉落物只有一人获得。
        """
        gs = self.gv.window.game_state
        if gs.net_server is None:
            return  # 非主机：理论不可达（inbound 仅主机消费），防御性返回
        net_id = payload.get("item_id")
        if not net_id:
            # 非法请求：无掉落物网络 id（禁静默——回执与日志至少要有一个）
            print(f"[Host] 玩家 {sender_id} 的拾取请求缺 item_id，已拒绝")
            return
        # 在主机权威掉落列表中查找目标掉落物
        target = next((d for d in self.gv.drops if d.net_id == net_id), None)
        if target is None:
            # 已不存在：被其他玩家先拾取/已过期/从未生成 → 拒绝
            # 携带该掉落物身份（取自主机拾取成功时的缓存）：客户端被拒后据此精确
            # 撤销乐观拾取写入的携带槽位（无 drop 时只能退化为整槽回滚）
            result = {
                "player_id": sender_id, "item_id": net_id,
                "accepted": False, "reason": "already_taken",
            }
            taken = self._taken_drop_info.get(net_id)
            if taken:
                result["drop"] = dict(taken)
            gs.net_server.broadcast(MsgType.PICKUP_RESULT, result)
            return
        # 距离校验优先用幽灵实测坐标（PLAYER_SNAPSHOT 主机权威）：直接采信上报坐标等于
        # 「隔空捡物」，客户端可伪造 x/y 在全图任意位置拾取。
        # 幽灵缺失（异常）→ 限频告警，并优先用本次上报坐标兜底判定；上报坐标也缺失时
        # 才退到出生点（懒创建幽灵的初始位置），而不是 (0,0)（地图左上角会误判成「很近」）。
        req_x, req_y = payload.get("x"), payload.get("y")
        ghost = self.gv.remote_players.get(sender_id)
        if ghost is None:
            ghost = self._ensure_ghost(sender_id)
            warn_key = ("ghost_missing", "pickup", sender_id)
            if warn_key not in self._warned_keys:
                self._warned_keys.add(warn_key)
                if req_x is not None and req_y is not None:
                    print(f"[Host] 玩家 {sender_id} 拾取时幽灵缺失，"
                          f"已按上报坐标 ({float(req_x)},{float(req_y)}) 兜底（限频告警一次）")
                    check_x, check_y = float(req_x), float(req_y)
                else:
                    print(f"[Host] 玩家 {sender_id} 拾取时幽灵缺失且无上报坐标，"
                          f"已按出生点 ({ghost.center_x},{ghost.center_y}) 兜底（限频告警一次）")
                    check_x, check_y = ghost.center_x, ghost.center_y
        else:
            # 幽灵在位：距离判据吃实测坐标，客户端上报的 x/y 仅用于日志/告警，不参与判定
            check_x, check_y = ghost.center_x, ghost.center_y
        dist = math.hypot(check_x - target.center_x, check_y - target.center_y)
        if dist >= DROP_PICKUP_RADIUS:
            # 拒绝：物品仍在主机掉落列表，广播掉落物详情供客户端恢复视觉（乐观拾取回滚）
            gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
                "player_id": sender_id, "item_id": net_id,
                "accepted": False, "reason": "too_far",
                "drop": {
                    "item_type": target.item_type, "item_id": target.item_id,
                    "x": target.center_x, "y": target.center_y,
                    "quantity": target.quantity, "level": target.level,
                },
            })
            return
        # 先到先得：从主机掉落列表移除（后续请求必失败）并记录该玩家携带物
        self.gv.drops.remove(target)
        self._record_player_pickup(sender_id, target)
        # 缓存该掉落物身份（供后续 already_taken 拒绝时下发 drop，客户端精确回滚）
        self._remember_taken_drop(target)
        # 阶段6.2 任务 harvest：远程玩家拾取资源按拾取者归属补计数
        #（主机唯一计数端；本人本地拾取走 game_view 的 solo 路径，不在此重复计）
        if sender_id != getattr(gs, "net_player_id", None) \
                and getattr(target, "item_type", "") == "resource":
            from game.mission_tracker import on_event as mission_on_event
            mission_on_event(self.gv, "harvest", 1, sender_id)
        gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
            "player_id": sender_id, "item_id": net_id,
            "accepted": True, "reason": None,
        })

    def _remember_taken_drop(self, drop) -> None:
        """缓存已发放掉落物的身份（net_id → 物品描述），供 already_taken 拒绝时下发

        背景：客户端乐观拾取（try_pickup 立即写 run_carried/run_potions），若主机拒绝
        （已被他人先拿），客户端需知道「被拒的是哪个物品/槽位」才能精确回滚。主机侧
        该掉落物已从 drops 移除，只能靠这份有界缓存还原身份。
        """
        net_id = getattr(drop, "net_id", None)
        if net_id is None:
            return
        # 有界缓存：只保留最近若干条，按插入顺序淘汰最旧（防长局内存无上限增长）
        if len(self._taken_drop_info) >= 64 and net_id not in self._taken_drop_info:
            oldest = next(iter(self._taken_drop_info))
            self._taken_drop_info.pop(oldest, None)
        self._taken_drop_info[net_id] = {
            "item_type": getattr(drop, "item_type", ""),
            "item_id": getattr(drop, "item_id", ""),
            "quantity": getattr(drop, "quantity", 1),
            "level": getattr(drop, "level", 1),
        }

    def _handle_evac_request(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端撤离请求（B12）：下发主机权威携带物清单，广播 EVAC_RESULT

        - 数据源 = _players_run_carried[sender_id]（主机拾取仲裁成功时累加，口径与
          GameState.run_carried 一致，包含 tuple 键的装备类条目）；
        - 广播 EVAC_RESULT 含 "player_id" + "run_carried"，各端（含请求客户端）据此
          调用 commit_run_to_warehouse 写各自本地库（每端写自己的库）；
        - 协议可选键 carried：客户端上报自身实际携带清单，与主机权威清单按
          主机优先合并（仅补入主机未记到的条目），缺失时纯用主机记录；
        - run_carried 的 tuple 键先经 _serialize_evac_carried 转为 "id|level" 字符串
          （json 序列化安全），客户端 _apply_evac_result 内 _deserialize_evac_carried 还原；
        - 受理前的三重主机权威校验（防伪造撤离/凭空结算）：请求方幽灵仍在场且存活 →
          与撤离点距离 ≤ EVAC_INTERACT_RANGE → 撤离点存在且状态允许撤离（secured）。
          任一不满足即回 EVAC_RESULT{success:False, reason} 单播拒绝并记中文日志，
          不计任务进度、不标记已撤离（客户端据此保持局内状态可重试）。
        """
        gs = self.gv.window.game_state
        if gs.net_server is None:
            return  # 非主机：理论不可达（inbound 仅主机消费），防御性返回
        # ── 三重主机权威校验（拒绝时必须回执 + 记日志，禁静默丢弃）──
        # ① 请求方幽灵缺失或已阵亡/已撤离：HOST 本地玩家（sender_id == net_player_id）
        #    用 self.player 判活，其余用幽灵（PLAYER_SNAPSHOT 主机权威坐标）。
        #    缺幽灵时按 _ensure_ghost 兜底出生点并在 is_alive 判据上直接判否——
        #    「拿不出一个活着的实体」即不可受理，避免凭空结算一笔撤离。
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and sender_id == my_id:
            actor = self.gv.player
        else:
            actor = self.gv.remote_players.get(sender_id)
        if actor is None:
            self._reject_evac_request(sender_id, "ghost_not_found", "请求方幽灵不在位")
            return
        if not getattr(actor, "alive", False):
            self._reject_evac_request(sender_id, "ghost_not_alive", "请求方已阵亡/不在场")
            return
        # ② 距离校验一律用幽灵实测坐标（禁采信上报坐标，客户端可伪造 x/y 隔空撤离）。
        #    判定目标 = 主撤离点（阶段2 防守式撤离的权威坐标）。
        point = getattr(self.gv, "evac_point", None)
        if point is None:
            self._reject_evac_request(sender_id, "no_evac_point", "撤离点尚未建立")
            return
        dist = math.hypot(actor.center_x - point.x, actor.center_y - point.y)
        if dist > EVAC_INTERACT_RANGE:
            self._reject_evac_request(
                sender_id, "too_far",
                f"距离撤离点 {dist:.0f}px > {EVAC_INTERACT_RANGE:.0f}px")
            return
        # ③ 撤离点状态必须允许读条撤离：secured（已守住）才可发起撤离结算。
        #    defending/dormant/destroyed 一律拒绝——否则客户端可提前结算带跑战利品。
        if getattr(point, "state", "") != "secured":
            self._reject_evac_request(
                sender_id, "evac_not_secured",
                f"撤离点状态 {getattr(point, 'state', '?')} 不允许撤离")
            return
        # 阶段6.2 防双计闸门：同一玩家本局只结算一次撤离。客户端侧有 _evac_request_sent
        # 单次标志，但重复帧/异常重发仍可能二次到达主机——若不在此拦一道，evac 进度会
        # 被重复计数（EVAC_RESULT 清单也会重复广播）。口径对齐 _handle_player_abandon
        # 的 _player_status 判定：已是 evac/left 视为已结算，显式忽略并记日志（禁静默）。
        if self.gv._player_status.get(sender_id) in ("evac", "left"):
            print(f"[GameView] 忽略玩家 {sender_id} 重复的撤离请求（已结算，不再计数）")
            return
        # 阶段6.2 任务/成就进度：撤离成功按「结算玩家」归属（唯一 evac 计数口径），
        # 客户端经 MISSION_PROGRESS 写自己的本地库（防双计铁律：客户端永不自计）
        from game.mission_tracker import on_event as mission_on_event
        mission_on_event(self.gv, "evac", 1, sender_id)
        carried = self.gv._players_run_carried.get(sender_id, {})
        # 协议可选键 carried：客户端上报自身实际携带清单（拾取乐观更新/事件倍率缩放
        # 等只在客户端侧体现的部分）。反序列化（"id|level" 字符串 → tuple 键）后交
        # _merge_authoritative：主机账本权威优先，客户端载荷**仅用于打对账日志**
        # （不采信任何客户端独有键/条目，防凭空上报资源/武器骗结算）。
        # 传 player_id=sender_id 供对账日志标注玩家（不传则日志显示「未知」）。
        client_carried = self.gv._deserialize_evac_carried(payload.get("carried") or {})
        if client_carried:
            merged = _merge_authoritative(carried, client_carried, player_id=sender_id)
            if merged != carried:
                warn_key = ("evac_carried_diff", sender_id)
                if warn_key not in self._warned_keys:
                    self._warned_keys.add(warn_key)
                    print(f"[Host] 玩家 {sender_id} 上报携带清单与主机账本不一致，"
                          f"已按主机账本为准结算（明细见下方[撤离对账]日志）")
            carried = merged
        gs.net_server.broadcast(MsgType.EVAC_RESULT, {
            "player_id": sender_id,
            "run_carried": self.gv._serialize_evac_carried(carried),
        })
        # 记录该玩家本局已撤离（观战期间全员结束判定用；客户端撤离后回房等待）
        self.gv._player_status[sender_id] = "evac"
        # 修复：设置幽灵 HP=0 + alive=False，使小地图不再绘制该玩家点
        # （与 _handle_player_abandon 对齐，此前遗漏导致撤离后小地图幽灵残留）
        ghost = self.gv.remote_players.get(sender_id)
        if ghost is not None:
            ghost.hp = 0
            ghost.alive = False
        print(f"[GameView] 主机响应玩家 {sender_id} 撤离请求：广播 EVAC_RESULT 结算清单")

    def _reject_evac_request(self, sender_id: int, reason: str, detail: str) -> None:
        """主机拒绝客户端撤离请求：单播 EVAC_RESULT{success:False} + 中文日志

        为什么必须有回执（net 层铁律：禁静默忽略）：EVAC_REQUEST 此前只有请求没有应答，
        客户端无法区分「被主机拒绝」与「请求在链路上丢了」，只能空等或按本地状态瞎猜
        是否已结算。回执 success=False 让客户端 _apply_evac_result 走拒绝分支：不入库、
        不清装、不进观战，玩家仍留在局内可重试。

        载荷在既有 EVAC_RESULT schema（player_id/run_carried/stars）之外**不新增**字段
        以外的消息类型：success/reason 属拒绝型 ACK 的附加说明（与 INTERACTION_RESULT /
        PLAYER_ABANDON_RESULT 同一模式），net/protocol.py 本任务不改。
        """
        gs = self.gv.window.game_state
        print(f"[Host] 拒绝玩家 {sender_id} 的撤离请求：{detail}（reason={reason}）")
        if gs.net_server is None:
            return  # 防御性：主机无服务端连接时无从回执（已记日志）
        gs.net_server.send_to(sender_id, MsgType.EVAC_RESULT, {
            "player_id": sender_id,
            "success": False,
            "reason": detail,
        })

    # ── 商队购买：客户端 CARRIAGE_BUY 请求-回执（主机权威记账）─────────────
    def _carriage_unit_price(self, kind: str, item_id: str) -> int | None:
        """取商队某货品的**单价**（账号金币），未知货品返回 None

        价格与可购范围一律查既有定义，禁在协议层硬编码（数值调整只改 config/entities）：
        - potion  → config.EVENT_CARAVAN_PRICES（键须是 entities.equipment_defs.POTIONS
          的 item_id，防客户端拿资源/武器 id 冒充药水走低单价通道）；
        - weapon  → entities.weapon_defs.ALL_WEAPONS[item_id].price（price<=0 表示非卖品，
          与 game/map_events.caravan_stock 的可购过滤同口径）；
        - artifact→ config.EVENT_CARAVAN_ARTIFACT_PRICE（神器 def price=0），
          且 item_id 必须在 config.EVENT_CARAVAN_ARTIFACT_POOL 内（排除锻造坊专属配方
          codex_monster_blade，与单机侧货单池一致）。
        """
        from entities.equipment_defs import POTIONS
        from entities.weapon_defs import ALL_WEAPONS
        if kind == "potion":
            if item_id not in POTIONS or item_id not in EVENT_CARAVAN_PRICES:
                return None
            return int(EVENT_CARAVAN_PRICES[item_id])
        if kind == "weapon":
            wdef = ALL_WEAPONS.get(item_id)
            if not wdef or wdef.get("artifact"):
                return None
            price = int(wdef.get("price", 0) or 0)
            return price if price > 0 else None  # 非卖品：单机货单同样不入池
        if kind == "artifact":
            if item_id not in EVENT_CARAVAN_ARTIFACT_POOL:
                return None
            return int(EVENT_CARAVAN_ARTIFACT_PRICE)
        return None  # 未知 kind

    def _carriage_ledger(self, sender_id: int) -> dict:
        """取（并按需惰性初始化）某玩家本局商队已购计数镜像

        换局重置口径：地图种子变化即视为新一局，整份镜像清零。同一 GameView 实例会
        跨局复用（setup() 重跑），本任务不改 game_view.py，故用 GameState 的
        current_map_seed 做惰性判局，避免上一局的限购额度泄漏到下一局。
        """
        gs = self.gv.window.game_state
        seed = getattr(gs, "current_map_seed", None)
        if self._carriage_bought_seed != seed:
            self._carriage_bought.clear()
            self._carriage_bought_seed = seed
        return self._carriage_bought.setdefault(sender_id, {})

    def _reject_carriage_buy(self, sender_id: int, payload: dict,
                             reason: str, detail: str) -> None:
        """主机拒绝商队购买：单播 CARRIAGE_BUY_RESULT{ok:False} + 中文日志（禁静默）"""
        gs = self.gv.window.game_state
        print(f"[Host] 拒绝玩家 {sender_id} 的商队购买：{detail}"
              f"（item={payload.get('item_id')!r} kind={payload.get('kind')!r} "
              f"reason={reason}）")
        if gs.net_server is None:
            return  # 防御性：无服务端连接时无从回执（已记日志）
        gs.net_server.send_to(sender_id, MsgType.CARRIAGE_BUY_RESULT, {
            "player_id": sender_id,
            "ok": False,
            "reason": detail,
            "kind": str(payload.get("kind") or ""),
            "item_id": str(payload.get("item_id") or ""),
            "qty": int(payload.get("qty", 0) or 0),
        })

    def _handle_carriage_buy(self, sender_id: int, payload: dict) -> None:
        """主机裁决客户端 CARRIAGE_BUY：校验 → 写权威账本 → 单播 CARRIAGE_BUY_RESULT

        主机权威铁律：联机客户端的商队购买**不走本地结算**，一律请求-回执由主机记账。
        原因：客户端本地购买写入的 run_carried 从不上报（EVAC_REQUEST 只带 carried 快照，
        而主机账本才是结算唯一权威），本地购买既进不了主机账本 → 撤离丢失，
        又能让客户端凭空上报骗结算（旧 _merge_authoritative 漏洞的根因之一）。

        校验链（任一不满足即单播拒绝，**不扣任何账**）：
        ① 载荷合法：kind ∈ {potion, weapon, artifact}、item_id 非空、qty ≥ 1；
        ② 货品可购且可定价（_carriage_unit_price 能查到单价，武器等级 ≥ 1）；
        ③ 报文金币自洽：gold_before - gold_after == 单价 × qty，且 gold_after ≥ 0
           （防客户端随手报一个「恰好够钱」的假差额空买）；
        ④ 本局限购：按 item_id 计已购数 + 本次 qty 不超 config.EVENT_CARAVAN_LIMITS[kind]。

        入账口径与 game/map_events._caravan_add_to_carried / _players_run_carried 完全一致：
        药水 → "potion"[item_id]（item_id 键）；武器/神器 → "weapon"[(item_id, level)]（元组键）。
        账号金币**不在主机扣**：各端账号金币由各自 SQLite 持有，主机无从也不应替客户端扣款
        （主机只在自己端结算时用 db.spend_gold）；这里只保证「货一定进主机账本」。
        """
        gs = self.gv.window.game_state
        if gs.net_server is None or gs.net_mode != "host":
            return  # 非主机：理论不可达（inbound 仅主机消费），防御性返回
        # 身份收口：购买者一律取连接 sender_id，禁信任 payload.player_id
        # （否则可伪造成他人名义购买，白嫖他人限购额度）
        kind = str(payload.get("kind") or "")
        item_id = str(payload.get("item_id") or "")
        qty = int(payload.get("qty", 0) or 0)
        level = int(payload.get("level", 0) or 0)
        # ① 载荷合法性
        if kind not in ("potion", "weapon", "artifact"):
            self._reject_carriage_buy(sender_id, payload, "bad_kind", "购买类型非法")
            return
        if not item_id:
            self._reject_carriage_buy(sender_id, payload, "empty_item", "缺少物品 id")
            return
        if qty <= 0:
            self._reject_carriage_buy(sender_id, payload, "bad_qty", "购买数量非法")
            return
        # ② 货品可购且可定价（价格/可购范围查 config + entities，禁硬编码）
        unit_price = self._carriage_unit_price(kind, item_id)
        if unit_price is None:
            self._reject_carriage_buy(sender_id, payload, "unknown_item",
                                      "货品不存在或不可购买")
            return
        if kind != "potion" and level < 1:
            self._reject_carriage_buy(sender_id, payload, "bad_level", "武器/神器等级非法")
            return
        # ③ 报文金币自洽（各端金币是各端自己的账，主机只能校验「差額算得对不对」）
        gold_before = payload.get("gold_before")
        gold_after = payload.get("gold_after")
        try:
            expect_cost = unit_price * qty
            diff = float(gold_before) - float(gold_after)
        except (TypeError, ValueError):
            self._reject_carriage_buy(sender_id, payload, "bad_gold", "金币字段非法")
            return
        if abs(diff - expect_cost) > 0.001 or float(gold_after) < 0:
            self._reject_carriage_buy(
                sender_id, payload, "gold_mismatch",
                f"金币差额 {diff:.0f} 与应扣 {expect_cost} 不符")
            return
        # ④ 本局限购（按 item_id 计数，与 config.EVENT_CARAVAN_LIMITS[kind] 同口径）
        limit = int(EVENT_CARAVAN_LIMITS.get(kind, 1))
        ledger = self._carriage_ledger(sender_id)
        already = int(ledger.get(item_id, 0) or 0)
        if already + qty > limit:
            self._reject_carriage_buy(
                sender_id, payload, "purchase_limit",
                f"本局限购（{kind} 上限 {limit} 件，已购 {already} 件）")
            return
        # ── 全部校验通过：写主机权威账本（原地改字典，保 _players_run_carried 引用）──
        carried = self.gv._players_run_carried.setdefault(sender_id, {})
        if kind == "potion":
            slot = carried.setdefault("potion", {})
            slot[item_id] = int(slot.get(item_id, 0) or 0) + qty
        else:
            slot = carried.setdefault("weapon", {})
            key = (item_id, level)
            slot[key] = int(slot.get(key, 0) or 0) + qty
        ledger[item_id] = already + qty
        gs.net_server.send_to(sender_id, MsgType.CARRIAGE_BUY_RESULT, {
            "player_id": sender_id,
            "ok": True,
            "reason": None,
            "kind": kind,
            "item_id": item_id,
            "qty": qty,
        })
        print(f"[Host] 玩家 {sender_id} 商队购入 {kind}/{item_id}×{qty} 成功"
              f"（扣账 {expect_cost} 账号金币由本端结算），"
              f"权威携带物={ {k: v for k, v in carried.items()} }")



    def _apply_map_change(self, payload: dict) -> None:
        """客户端应用主机 MAP_CHANGE：运行期地图改动（掉落生成/宝箱/环境物/水井/火箭台）

        - drop_spawn（Todo 18）：掉落物生成广播 → 本地创建视觉掉落物（带主机 net_id）；
        - chest_opened / env_destroyed / well_used / rocket_pad（Todo 20，D2）：
          obj_id = 列表序号（与 FULL_STATE 的 chests/env_objects id 同口径，客户端确定性
          重建的地图对象顺序与主机一致），直接把本地对象与主机权威状态对齐——
          宝箱标记已开（渲染/碰撞跳过）、环境物标记已摧毁、水井首次开启、
          火箭台镜像状态机与撤离倒计时（客户端不本地推进，纯表现层）。
        - 11.1 接线核查补齐三个新 change_type：build_place / build_destroy（阶段1 局内建造，
          obj_id = 建筑 bid）/ fire_zone（阶段3 火墙词缀燃烧区，obj_id = 区域 zid，state.action
          区分 add/remove）；未知 change_type 一律显式记日志（禁静默忽略——net 层铁律）。
        """
        change_type = payload.get("change_type")
        _KNOWN_CHANGE_TYPES = (
            "drop_spawn", "chest_opened", "env_destroyed", "env_damage", "env_spawn",
            "well_used", "rocket_pad", "chest_spawn", "caravan_point",
            "build_place", "build_destroy", "fire_zone",
        )
        if change_type not in _KNOWN_CHANGE_TYPES:
            # 未知改动类型：显式记录而非静默忽略（net 层铁律）
            print(f"[Client] MAP_CHANGE 未知 change_type={change_type!r}，忽略")
            return
        if change_type == "drop_spawn":
            for entry in payload.get("extra", {}).get("drops", []):
                net_id = entry.get("net_id")
                if not net_id:
                    continue  # 非法条目：无网络 id
                if any(d.net_id == net_id for d in self.gv.drops):
                    continue  # 已存在（重复广播）：跳过
                drop = DropItem(
                    entry.get("x", 0.0), entry.get("y", 0.0),
                    entry.get("item_type", "gold"), entry.get("item_id", "gold"),
                    quantity=entry.get("quantity", 1), level=entry.get("level", 1),
                    net_id=net_id,
                )
                self.gv.drops.append(drop)
            return
        # 运行期地图对象状态对齐（坐标对象均已在 setup 确定性重建，按序号直接对齐）
        obj_id = payload.get("obj_id")
        if change_type == "chest_opened":
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.gv.chests):
                self.gv.chests[obj_id].opened = True
        elif change_type == "env_destroyed":
            # 注意：alive 是只读 property（hp > 0），不能直接赋值；
            # 置 hp=0 + 移出障碍物，与主机 on_harvestable_destroyed 一致
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.gv.harvestables):
                h = self.gv.harvestables[obj_id]
                h.hp = 0
                if h in self.gv.obstacle_list:
                    self.gv.obstacle_list.remove(h)
        elif change_type == "env_damage":
            # 主机对资源造成的逐次伤害（Bug2 修复）：本地血条扣减 + 伤害数字 + 粒子，
            # 与 _client_visual_collisions 同口径 clamp 最低 1（避免本地提前"死亡"，
            # 最终归零以 env_destroyed 广播为准）
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.gv.harvestables):
                h = self.gv.harvestables[obj_id]
                if h.alive:
                    dmg = payload.get("state", {}).get("damage", 0)
                    h.hp = max(1, h.hp - dmg)
                    floating_texts.add_damage(h.center_x, h.center_y + 20, dmg)
                    particle_system.emit(h.center_x, h.center_y, 5,
                                         (150, 150, 150), speed=60, life=0.3, size=3)
        elif change_type == "env_spawn":
            # 主机运行期补刷的环境物（修复「二次刷新资源客户端不可见」）：
            # 主机 respawn_harvestables 追加在列表末尾，客户端同样**追加**以保持
            # 两端列表长度/序号同步（obstacle_list 由 _sync_obstacles 每帧重建自动纳入）
            if isinstance(obj_id, int) and obj_id == len(self.gv.harvestables):
                st = payload.get("state", {})
                h = HarvestableEntity(
                    st.get("x", 0.0), st.get("y", 0.0),
                    st.get("resource_type", "tree"),
                )
                self.gv.harvestables.append(h)
        elif change_type == "well_used":
            # 水井首次开启：镜像 _well_opened（与主机交互/提示口径一致）
            self.gv._well_opened = True
        elif change_type == "rocket_pad":
            # 火箭台：镜像状态机与撤离倒计时（客户端渲染读取 pad.state/_countdown_timer）
            if isinstance(obj_id, int) and 0 <= obj_id < len(self.gv.rocket_pads):
                pad = self.gv.rocket_pads[obj_id]
                state = payload.get("state", {})
                if "pad_state" in state:
                    pad.state = state["pad_state"]
                if "countdown" in state:
                    pad._countdown_timer = float(state["countdown"])
        elif change_type == "chest_spawn":
            # 阶段4 空投补给箱：主机运行期追加到 chests 末尾（obj_id = 追加位置），
            # 客户端同样**追加**保持两端列表长度/序号同步，因此后续 chest_opened
            # 的 obj_id 与主机对得上。已存在同序号则跳过（重复广播幂等）。
            if isinstance(obj_id, int) and obj_id > len(self.gv.chests):
                return
            if isinstance(obj_id, int) and obj_id == len(self.gv.chests):
                st = payload.get("state", {})
                chest = AirdropChest(st.get("x", 0.0), st.get("y", 0.0),
                                     theme=st.get("theme", "forest"),
                                     artifact_bonus=st.get("artifact_bonus", 0.0))
                self.gv.chests.append(chest)
                self.gv.obstacle_list.append(chest)
        elif change_type == "caravan_point":
            # 阶段4 商队交互点：镜像主机权威坐标（客户端只渲染招牌/弹层，不本地生成）
            st = payload.get("state", {})
            if self.gv.caravan_point is None:
                self.gv.caravan_point = CaravanPoint(st.get("x", 0.0), st.get("y", 0.0))
        elif change_type == "build_place":
            # 阶段1 局内建筑落成：obj_id = 建筑 bid（主机 BuildSystem._next_bid 单调分配）。
            # 客户端只建视觉对象（渲染血条/方块），**不注册 monster_grid 碰撞**——
            # 客户端无怪物 AI，怪物位置由 MONSTER_SNAPSHOT 权威下发。
            self._client_add_building(
                payload.get("obj_id"), payload.get("state") or {})
        elif change_type == "build_destroy":
            # 阶段1 建筑被摧毁/拆除：按 bid 从本地列表移除（幂等：不存在则跳过）
            bid = payload.get("obj_id")
            buildings = self._building_list()
            for index, building in enumerate(buildings):
                if building.bid == bid:
                    del buildings[index]
                    break
        elif change_type == "fire_zone":
            # 阶段3 火墙词缀燃烧区：state.action = add/remove，obj_id = 区域 zid。
            # 区域本体完全由主机建/删广播驱动，客户端不本地生成（禁本地仲裁）。
            st = payload.get("state", {})
            action = st.get("action", "add")
            zid = st.get("zid", payload.get("obj_id"))
            if action == "remove":
                self.gv.fire_zones[:] = [z for z in self.gv.fire_zones
                                         if z.get("zid") != zid]
            else:
                if any(z.get("zid") == zid for z in self.gv.fire_zones):
                    return  # 已存在（重复广播）：幂等跳过
                self.gv.fire_zones.append({
                    "zid": zid,
                    "x": float(st.get("x", 0.0)), "y": float(st.get("y", 0.0)),
                    # 半径口径兼容 r / radius 两种键名（协议 fixture 与主机广播口径）
                    "r": float(st.get("r", st.get("radius", 0.0)) or 0.0),
                    "life": float(st.get("life", 0.0)),
                    "dps": float(st.get("dps", 0.0)),
                    "burn_duration": float(st.get("burn_duration", 0.0)),
                    "tick": float(st.get("tick", 0.0)),
                })

    def _building_list(self) -> list:
        """取本局建筑共享列表（主机广播与客户端还原、渲染读同一份 list 对象）

        GameState.buildings 与 BuildSystem.buildings 是同一个 list 对象，
        这里统一经 build_system 取，避免渲染与联机还原各读一份。
        """
        build_system = getattr(self.gv, "build_system", None)
        if build_system is not None:
            return build_system.buildings
        return self.gv.window.game_state.buildings

    def _client_add_building(self, bid, state: dict) -> None:
        """客户端按主机广播还原一个建筑视觉对象（幂等：同 bid 已存在则只校准血量）

        不注册 monster_grid：客户端无怪物 AI（怪物位置由快照权威下发），
        注册碰撞网格只会在客户端造出永不参与计算的碰撞体。
        """
        from entities.build_defs import BUILDS
        buildings = self._building_list()
        # bid 口径：主机用 BuildSystem._next_bid 分配的 int；协议 fixture 用字符串 id。
        # 两种都按不透明标识处理（增删两侧同口径即可），但必须存在。
        if not isinstance(bid, (int, str)) or isinstance(bid, bool):
            print(f"[Client] build_place 非法 obj_id={bid!r}，忽略")
            return
        kind = state.get("kind", "")
        if kind not in BUILDS:
            # 未知建筑类型：显式记录而非静默忽略（跨版本建筑定义不同步的防御）
            print(f"[Client] build_place 未知 kind={kind!r}（bid={bid}），忽略")
            return
        for building in buildings:
            if building.bid == bid:
                building.hp = float(state.get("hp", building.hp))
                return
        building = Building(bid, kind,
                            float(state.get("x", 0.0)), float(state.get("y", 0.0)))
        building.hp = float(state.get("hp", building.max_hp))
        buildings.append(building)

    def _apply_event_start(self, payload: dict) -> None:
        """客户端应用主机 EVENT_START：镜像本局事件 id/参数 + 显示横幅（纯表现层）

        - 客户端禁本地抽选（主机权威），因此 event_id 与 event_flags 完全以广播值为准；
        - 横幅计时 3 秒由本端递减（表现层），因此客户端同样调用 map_events.update_event；
        - 事件实体（空投箱/商队点）不在本消息内，由 MAP_CHANGE 增量广播（见 _apply_map_change）。
        """
        from game.map_events import apply_event
        event_id = payload.get("event_id") or ""
        flags = payload.get("flags") or {}
        self.gv.event_id = event_id
        # 客户端同样反算 _monster_cap：保证客户端野外刷新上限表现与主机一致
        # （客户端不跑 respawn，此处仅为参数镜像，无副作用）
        apply_event(self.gv, event_id)
        if flags:
            self.gv.event_flags.update(flags)
        self.gv._event_banner_timer = EVENT_BANNER_SEC

    def _broadcast_event_start(self) -> None:
        """联机主机：开局广播 EVENT_START（抽中事件后立即调用一次）

        - 只有 host 广播（solo 无网；客户端由 _apply_event_start 消费）；
        - flags 走 view.event_flags 副本，客户端 apply_event 会再按 MAP_EVENTS 覆盖，
          广播值用于显式对齐（禁把 view 直接塞进载荷）。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        event_id = getattr(self.gv, "event_id", "")
        if not event_id:
            return
        gs.net_server.broadcast(MsgType.EVENT_START, {
            "event_id": event_id,
            "flags": dict(getattr(self.gv, "event_flags", {}) or {}),
        })

    def _broadcast_event_entities(self) -> None:
        """联机主机：广播阶段4 事件实体（空投补给箱追加 / 商队交互点），各只广播一次

        - obj_id 与 env_spawn 同口径（= 追加位置），客户端据此保持两端列表序号同步；
        - 用 gv._event_spawned 集合去重，避免每帧 _broadcast_map_changes 重复广播。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        spawned = self.gv._event_spawned
        # 空投补给箱：运行时追加到 chests 末尾，按追加序号广播
        for i, chest in enumerate(self.gv.chests):
            if not getattr(chest, "is_airdrop", False) or i in spawned:
                continue
            spawned.add(i)
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": i, "change_type": "chest_spawn",
                "state": {"x": chest.center_x, "y": chest.center_y,
                          "theme": getattr(chest, "theme", "forest"),
                          "artifact_bonus": getattr(chest, "artifact_bonus", 0.0)},
                "extra": {},
            })
        # 商队交互点：单点，只广播一次
        point = getattr(self.gv, "caravan_point", None)
        if point is not None and "caravan" not in spawned:
            spawned.add("caravan")
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": 0, "change_type": "caravan_point",
                "state": {"x": point.center_x, "y": point.center_y},
                "extra": {},
            })

    def _broadcast_map_changes(self) -> None:
        """联机主机：检测并广播运行期地图改动（宝箱开启/环境物摧毁/水井/火箭台状态）

        - 变更按列表序号 obj_id 广播（与 FULL_STATE 的 chests/env_objects id 同口径，
          客户端确定性重建的地图对象顺序一致，按序号直接对齐）；
        - 已广播跟踪（_broadcasted_chests/_broadcasted_env/_well_broadcasted/
          _broadcasted_pads）：只在状态首次变化时广播，避免对相同状态重复刷屏；
        - 火箭台撤离倒计时按整数秒重播（客户端纯表现层镜像倒计时，不本地推进）。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        # 宝箱：首次被打开 → 广播 opened（客户端据此跳过渲染/碰撞）
        for i, chest in enumerate(self.gv.chests):
            if chest.opened and i not in self.gv._broadcasted_chests:
                self.gv._broadcasted_chests.add(i)
                gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                    "obj_id": i, "change_type": "chest_opened",
                    "state": {"opened": True}, "extra": {},
                })
        # 环境物：首次被摧毁 → 广播 alive=False（客户端渲染/碰撞跳过）
        for i, h in enumerate(self.gv.harvestables):
            if not h.alive and i not in self.gv._broadcasted_env:
                self.gv._broadcasted_env.add(i)
                gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                    "obj_id": i, "change_type": "env_destroyed",
                    "state": {"alive": False}, "extra": {},
                })
        # 水井：首次开启 → 广播一次（客户端镜像 _well_opened，提示口径一致）
        if self.gv._well_opened and not self.gv._well_broadcasted:
            self.gv._well_broadcasted = True
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": 0, "change_type": "well_used",
                "state": {"opened": True}, "extra": {},
            })
        # 火箭台：状态变化（撤离中按整数秒计数）→ 广播状态机镜像
        for i, pad in enumerate(self.gv.rocket_pads):
            pad_state = getattr(pad, "state", None)
            if pad_state is None:
                continue
            if pad_state == "evacuating":
                key = ("evacuating", int(pad.get_countdown()))
            else:
                key = (pad_state,)
            if self.gv._broadcasted_pads.get(i) != key:
                self.gv._broadcasted_pads[i] = key
                gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                    "obj_id": i, "change_type": "rocket_pad",
                    "state": {"pad_state": pad_state,
                              "countdown": pad.get_countdown()},
                    "extra": {},
                })
        # 阶段4 事件实体：空投补给箱（运行期追加到 chests 末尾）/ 商队交互点，
        # 内部用 gv._event_spawned 去重，各只广播一次
        self._broadcast_event_entities()
        # 11.1 接线核查：阶段1 局内建筑 + 阶段3 火墙词缀燃烧区的建/删增量广播
        self._broadcast_buildings()
        self._broadcast_fire_zones()

    def _broadcast_buildings(self) -> None:
        """联机主机：增量广播局内建筑的落成与摧毁（11.1 接线核查，change_type=build_place/build_destroy）

        - obj_id = 建筑 bid（BuildSystem._next_bid 单调分配，两端同口径，客户端据此还原/移除）；
        - 去重跟踪 gv._broadcasted_buildings（已广播的 bid 集合）：新建筑只广播一次；
        - 摧毁判定走「已广播但本地已不在列表」——BuildSystem.remove 是唯一移除入口
          （怪物拆塔 take_damage 与局末 clear 共用），因此该差集即等于被摧毁的建筑；
        - 客户端收到后只建/删视觉对象，不注册怪物碰撞（客户端无 AI）。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        buildings = self._building_list()
        alive_bids = {b.bid for b in buildings}
        # 新落成的建筑 → 广播 build_place（含当前 hp，客户端据此画血条）
        for building in buildings:
            if building.bid in self.gv._broadcasted_buildings:
                continue
            self.gv._broadcasted_buildings.add(building.bid)
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": building.bid, "change_type": "build_place",
                "state": {"kind": building.kind, "x": building.x, "y": building.y,
                          "hp": building.hp},
                "extra": {},
            })
        # 已广播但本地已消失 → 广播 build_destroy（差集按 bid 逐条发，幂等）
        for bid in list(self.gv._broadcasted_buildings - alive_bids):
            self.gv._broadcasted_buildings.discard(bid)
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": bid, "change_type": "build_destroy",
                "state": {}, "extra": {},
            })

    def _broadcast_fire_zones(self) -> None:
        """联机主机：增量广播火墙词缀燃烧区的生成与消失（11.1 接线核查，change_type=fire_zone）

        - obj_id = 区域 zid（主机在首次见到该区域时分配，客户端按 zid 还原/移除）；
        - 区域本体由 game/monster_affixes.py 的 on_affix_death 生成、update_fire_zones 按
          life 到期移除，本方法只做「主机列表 → 客户端列表」的差量同步（禁客户端本地生成）；
        - 去重跟踪 gv._fire_zone_broadcasted（已广播的 zid 集合）+ 消失差集发 remove。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        zones = getattr(self.gv, "fire_zones", None) or []
        alive_zids = set()
        for zone in zones:
            zid = zone.get("zid")
            if zid is None:
                # 首次见到：主机分配单调 zid 后再广播（保证两端同口径）
                zid = f"z{self.gv._next_fire_zone_zid}"
                self.gv._next_fire_zone_zid += 1
                zone["zid"] = zid
            alive_zids.add(zid)
            if zid in self.gv._fire_zone_broadcasted:
                continue
            self.gv._fire_zone_broadcasted.add(zid)
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": zid, "change_type": "fire_zone",
                "state": {
                    "action": "add", "zid": zid,
                    "x": zone.get("x", 0.0), "y": zone.get("y", 0.0),
                    "r": zone.get("r", 0.0), "life": zone.get("life", 0.0),
                    "dps": zone.get("dps", 0.0),
                    "burn_duration": zone.get("burn_duration", 0.0),
                    "tick": zone.get("tick", 0.0),
                },
                "extra": {},
            })
        # 已广播但本地已消失（life 耗尽被 update_fire_zones 移除）→ 广播 remove
        for zid in list(self.gv._fire_zone_broadcasted - alive_zids):
            self.gv._fire_zone_broadcasted.discard(zid)
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": zid, "change_type": "fire_zone",
                "state": {"action": "remove", "zid": zid},
                "extra": {},
            })

    def _broadcast_env_damage(self, obj_id: int, damage: float) -> None:
        """联机主机：广播环境物单次受击（Bug2 修复：主机对资源的伤害同步到客户端）

        - 主机本地近战/弹丸/激光命中环境物时逐次广播（handle_harvestable_combat 与
          _resolve_attack_event 近战环境物路径均调用），客户端据此本地扣血条 +
          显示伤害数字/粒子，不再只有最终 env_destroyed 才同步；
        - obj_id = harvestables 列表序号（与 env_destroyed 同口径，客户端确定性重建
          的地图对象顺序一致）；solo/客户端模式不广播（solo 无网）。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        gs.net_server.broadcast(MsgType.MAP_CHANGE, {
            "obj_id": obj_id, "change_type": "env_damage",
            "state": {"damage": damage}, "extra": {},
        })

    def _broadcast_env_spawn(self, obj_id: int, x: float, y: float, resource_type: str) -> None:
        """联机主机：广播运行期补刷的环境物（修复「二次刷新资源客户端不可见」）

        - 主机 respawn_harvestables 补刷新资源时调用（game/respawn.py），客户端据此
          在本地 harvestables 列表**追加**新对象（obj_id = 追加位置，与 env_destroyed/
          env_damage 同口径，两端列表长度同步增长，序号保持一致）；
        - 客户端不再本地生成补刷资源（respawn 只在主机跑），完全由本广播驱动；
        - solo/客户端模式不广播（solo 无网；客户端不跑 respawn）。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        gs.net_server.broadcast(MsgType.MAP_CHANGE, {
            "obj_id": obj_id, "change_type": "env_spawn",
            "state": {"x": x, "y": y, "resource_type": resource_type}, "extra": {},
        })

    @staticmethod
    def _pickup_drop_spec(drop_info: dict) -> dict:
        """把主机下发的掉落物身份翻译成 rollback_run 的 drop 规格 {slot: {key}}

        槽结构契约（与 game/loot.py try_pickup 写入口径一致）：
        - gold      : int（标量槽，禁进 drop → 另行按数量扣回）
        - resource  : {item_id: qty}
        - potion    : {item_id: qty}（同时写 run_potions，故带出 run_potions 槽）
        - weapon/helmet/armor/backpack: {(item_id, level): qty}（tuple 键仅存内存，
          json 往返已由 _serialize_evac_carried / _deserialize_evac_carried 处理过）
        """
        if not drop_info:
            return {}
        item_type = drop_info.get("item_type", "")
        item_id = drop_info.get("item_id", "")
        qty = int(drop_info.get("quantity", 1) or 1)
        if item_type in ("resource", "potion"):
            spec = {item_type: {item_id: qty}}
            if item_type == "potion":
                # 药水同时写 run_carried["potion"] 与 run_potions，两处都要撤销
                spec["run_potions"] = {item_id: qty}
            return spec
        if item_type in ("weapon", "helmet", "armor", "backpack"):
            level = int(drop_info.get("level", 1) or 1)
            return {item_type: {(item_id, level): qty}}
        return {}  # gold（标量槽）与未知类型：不走 drop

    def _rollback_optimistic_pickup(self, snap, drop_info: dict) -> None:
        """被主机拒绝的乐观拾取做**增量回滚**（保留回滚窗口期的合法并发写入）

        旧实现是 `gs.run_carried = snap` 整体重绑，会把窗口期的合法写入一起吞掉
        （典型：本地商队/市场购买已扣本端账号金币、只写本端 run_carried、不广播）。
        这里改走 game.evac.rollback_run：基线键还原 + drop 精确剔除被拒物品，
        原地改字典（保持 HUD / 背包 / BuildSystem 持有的引用）。
        """
        gs = self.gv.window.game_state
        if snap is None:
            return  # 无快照（理论上不会发生）：保持静默，不做猜测性改动
        baseline, potions_baseline = snap if isinstance(snap, tuple) else (snap, None)
        baseline = baseline or {}  # 基线可能为空/None：后续用 `"gold" not in baseline` 判据
        spec = self._pickup_drop_spec(drop_info)
        if not spec and drop_info.get("item_type") == "gold":
            # gold 是标量槽：rollback_run 的 baseline merge 只能恢复「基线里已存在」的键。
            # 分两种情况，禁无脑相减（否则基线已含 gold 时会二次扣减、凭空少钱）：
            #   a) 基线含 gold → merge 已把本次乐观收入抹掉，取基线值即完成回滚；
            #   b) 基线不含 gold（本次是新获得的金币槽）→ merge 无从撤销，按数量精确扣回。
            rollback_run(gs.run_carried, baseline=baseline,
                         run_potions=gs.run_potions,
                         potions_baseline=potions_baseline)
            if "gold" not in baseline:
                current_gold = gs.run_carried.get("gold")
                if isinstance(current_gold, (int, float)):
                    gs.run_carried["gold"] = max(
                        0, current_gold - int(drop_info.get("quantity", 0) or 0))
            return
        if not spec:
            # 主机未下发物品身份（旧协议 / 异常）：无法精确剔除，退化为整槽基线还原
            # （= 旧行为；此路径不静默，调用方已限频告警）
            if isinstance(snap, tuple):
                gs.run_carried, gs.run_potions = snap
            else:
                gs.run_carried = snap
            return
        rollback_run(gs.run_carried, baseline=baseline,
                     run_potions=gs.run_potions,
                     potions_baseline=potions_baseline,
                     drop=spec)

    def _apply_pickup_result(self, payload: dict) -> None:
        """客户端应用主机 PICKUP_RESULT：成功保持乐观状态 / 被拒回滚 run_carried

        - accepted=True（本人/他人）：该掉落物已被某人拿走 → 移除本地视觉（物品消失）；
        - accepted=False + too_far（本人）：物品仍在主机掉落列表 → 回滚 run_carried
          + 用广播的 drop 详情重建本地视觉（乐观拾取时 try_pickup 已从 self.drops 移除）；
        - accepted=False + already_taken（本人）：物品已被他人拿走 → 回滚 run_carried
          + 移除本地视觉（物品已消失）。
        """
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        net_id = payload.get("item_id")
        accepted = payload.get("accepted", False)
        reason = payload.get("reason")
        my_id = getattr(gs, "net_player_id", None)
        # 本人请求被拒：回滚乐观拾取（恢复拾取前 run_carried + run_potions 快照）
        if not accepted and my_id is not None and player_id == my_id:
            if self.gv._pickup_snapshot is not None:
                snap = self.gv._pickup_snapshot
                drop_info = payload.get("drop") or {}
                self._rollback_optimistic_pickup(snap, drop_info)
            if reason == "too_far":
                # 物品仍在主机上：重建本地视觉掉落物（带主机权威 net_id/位置）
                self.gv._remove_drop_visual(net_id)
                drop_info = payload.get("drop") or {}
                if drop_info and not any(d.net_id == net_id for d in self.gv.drops):
                    self.gv.drops.append(DropItem(
                        drop_info.get("x", 0.0), drop_info.get("y", 0.0),
                        drop_info.get("item_type", "gold"), drop_info.get("item_id", "gold"),
                        quantity=drop_info.get("quantity", 1),
                        level=drop_info.get("level", 1), net_id=net_id,
                    ))
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 20,
                                  "距离太远，拾取被拒绝!", arcade.color.RED)
            else:  # already_taken：物品已被他人拿走 → 移除视觉
                self.gv._remove_drop_visual(net_id)
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 20,
                                  "物品已被其他玩家拾取!", arcade.color.RED)
        elif accepted:
            # 成功（本人乐观拾取已移除视觉；他人成功 → 移除本地视觉，物品消失）
            self.gv._remove_drop_visual(net_id)
        # 本人请求结果（无论成败）：扣减本批待确认计数；本批全部确认后才清空快照，
        # 保证同批多个被拒请求都能回滚到同一拾取前状态（快照不逐帧覆盖，见 on_update）
        if my_id is not None and player_id == my_id and self.gv._pending_pickup_count > 0:
            self.gv._pending_pickup_count -= 1
            if self.gv._pending_pickup_count == 0:
                self.gv._pickup_snapshot = None

    def _apply_evac_result(self, payload: dict) -> None:
        """客户端应用主机 EVAC_RESULT（本人撤离成功）：按权威清单本地入库 + 进入观战

        - 仅处理本人（player_id == 本端 net_player_id）；他人撤离结果忽略（其余玩家继续）；
        - 按主机权威清单 commit_run_to_warehouse 写本地库（与单机撤离同一入库口径），
          随后 clear_run 清空携带；
        - 修复：客户端撤离成功不再回房等待，而是进入观战模式（跟随主机幽灵继续观看）——
          房间保留、连接保留，由主机 ROOM_ENDED(all_finished) 广播统一收口回房。
        """
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id != my_id:
            return  # 他人撤离：本端无动作（其余玩家继续游戏）
        # 拒绝型回执（success=False）：主机校验未通过，**不入库、不清装、不进观战**，
        # 玩家仍留在局内可重试。必须在反序列化/入库之前拦，否则空 run_carried 会被
        # commit_run_to_warehouse 当成「本局零战利品」清空账本（凭空丢货 desync）。
        if payload.get("success") is False:
            reason = payload.get("reason") or "撤离请求未被主机受理"
            print(f"[Client] 撤离请求被主机拒绝：{reason}")
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                               f"撤离失败：{reason}", arcade.color.RED,
                               life=2.0, font_size=16)
            return
        # 按主机权威清单还原并入库（tuple 键还原为入参口径）
        run_carried = self.gv._deserialize_evac_carried(payload.get("run_carried") or {})
        # 仓库有容量上限 → 主机清单也可能装不下；commit_run_to_warehouse 返回未入仓报告
        # 并自行挂到 game_state（与单机撤离同一通道），此处无需再转发
        commit_run_to_warehouse(gs.player_id, run_carried,
                                        client_carried=getattr(gs, "run_carried", None))
        clear_run(gs.run_carried)
        # 客户端本局药水已由主机记录进 EVAC_RESULT 载荷（run_carried 含 potion 键），
        # 本地 run_potions 不再重复入库，直接清空即可
        gs.run_potions = {}
        # 等级系统：客户端撤离成功经验（各端本地结算，客户端经 EVAC_RESULT 发放）
        _award_exp(self.gv, EXP_EVAC)
        # 客户端撤离成功 → 进入观战模式（跟随主机幽灵继续观看）而非回房等待：
        # - _spectating=True：渲染隐藏本体/武器/读条，input_handler 屏蔽操作，相机跟随观战目标；
        # - _spectate_target_id=None：由 _spectate_camera_target 自动回退第一个存活幽灵（主机）；
        # - 连接保留、poll 继续运行，主机 ROOM_ENDED(all_finished) 广播时经 _apply_room_ended 回房。
        gs.net_wait_reason = "evac"
        self.gv._spectating = True
        # 观战期 input_handler 早退（handle_key_release/handle_mouse_release 被观战守卫拦截）
        # 导致 _left_mouse_held/_chest_key_pressed 无法复位，这里手动清零：
        # 双保险防止幽灵持续攻击/拾取（修复客户端观战崩溃，配合 on_update 的观战守卫）
        self.gv._left_mouse_held = False
        self.gv._chest_key_pressed = False
        # 观战模式：相机改由观战段控制跟随幽灵，禁止 controller.update() 每帧拉回
        # 已撤离/阵亡的静止玩家（否则观战视角卡死在撤离点，修复场景2）
        self.gv.controller.follow_player = False
        self.gv._spectate_target_id = None
        # 观战提示（与主机 _enter_spectate 分流文案一致）
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           "你已撤离，进入观战模式（V 键切换视角）",
                           arcade.color.GOLD, life=3.0, font_size=16)

    def _apply_player_death(self, payload: dict) -> None:
        """客户端收到主机 PLAYER_DEATH（本人死亡）：清装备 + 进入观战模式

        - 其余玩家/怪物不受影响继续游戏（死亡 = 单人事件，B9）；
        - 与放弃行动同路径：进入观战模式（连接保留、等待全员结束回房），
          而非直接回 LobbyView（避免 SettingsView 阻挡视图切换）。
        """
        # 已在观战模式（放弃行动/已死亡）：不再重复处理 PLAYER_DEATH，
        # 避免"你已阵亡"红字与"你已放弃行动"红字同时出现
        if self.gv._spectating:
            return
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id != my_id:
            return  # 非本人死亡事件：忽略（幽灵同步由快照处理）
        # 死亡丢失装备（与单机死亡同口径），进入观战模式
        gs.net_wait_reason = "dead"
        self.gv._clear_run_equipment(gs)
        self.gv._spectating = True
        self.gv._left_mouse_held = False
        self.gv._chest_key_pressed = False
        self.gv._attack_debuffs = []
        self.gv._attack_this_frame = False
        self.gv.controller.follow_player = False
        self.gv._spectate_target_id = None
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           "你已阵亡，进入观战模式（V 键切换视角）",
                           arcade.color.RED, life=3.0, font_size=16)

    def _apply_player_downed(self, payload: dict) -> None:
        """客户端收到 PLAYER_DOWNED：标记远端玩家倒地（显示倒地标记）"""
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id == my_id:
            # 本人倒地：进入倒地状态
            if not self.gv.player.downed:
                self.gv._player_downed()
            return
        # 远端玩家倒地：记录倒地信息（渲染用）
        self.gv._downed_players[player_id] = {
            "x": payload.get("x", 0), "y": payload.get("y", 0),
            "timer": DOWNED_TIMEOUT,
        }
        self.gv._player_status[player_id] = "downed"

    def _apply_rescue_result(self, payload: dict) -> None:
        """客户端收到 RESCUE_RESULT：救援成功 → 恢复被救者 HP；失败 → 保持倒地"""
        gs = self.gv.window.game_state
        target_id = payload.get("target_id")
        success = payload.get("success", False)
        hp = payload.get("hp", 0)
        my_id = getattr(gs, "net_player_id", None)
        if success:
            if my_id is not None and target_id == my_id:
                # 本人被救：恢复 HP，退出倒地状态
                self.gv.player.hp = hp
                self.gv.player.downed = False
                self.gv.player.downed_timer = 0.0
                self.gv._spectating = False
                self.gv.controller.follow_player = True
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                                   f"你已被救！HP 恢复为 {int(hp)}",
                                   arcade.color.GREEN, life=2.0, font_size=16)
            else:
                # 远端玩家被救：移除倒地标记
                self.gv._downed_players.pop(target_id, None)
                self.gv._player_status[target_id] = "alive"
            # 救援者提示
            rescuer_id = payload.get("rescuer_id")
            if my_id is not None and rescuer_id == my_id:
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                                   "救援成功！",
                                   arcade.color.GREEN, life=1.5, font_size=16)
        else:
            # 救援失败（被救者已超时或离开）
            self.gv._downed_players.pop(target_id, None)

    def _apply_player_revived(self, payload: dict) -> None:
        """客户端收到 PLAYER_REVIVED：广播复活成功（所有端恢复该玩家实体）"""
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
        hp = payload.get("hp", REVIVE_HP)
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id == my_id:
            # 本人复活：恢复 HP
            self.gv.player.hp = hp
            self.gv.player.downed = False
            self.gv.player.downed_timer = 0.0
            self.gv._spectating = False
            self.gv.controller.follow_player = True
        else:
            # 远端玩家复活：恢复幽灵 HP，移除倒地标记
            ghost = self.gv.remote_players.get(player_id)
            if ghost is not None:
                ghost.hp = hp
            self.gv._downed_players.pop(player_id, None)
            self.gv._player_status[player_id] = "alive"

    def _apply_spectate_leave(self, payload: dict, sender_id=None) -> None:
        """主机收到 SPECTATE_LEAVE：玩家主动退出观战 → 视为真死，清装备

        身份收口：sender_id 传入时（连接侧已知来源）一律以它为准，禁采信
        payload.player_id —— 否则客户端可伪造他人 id 把别人标记 dead，
        也会伪造 player_id=0 让主机自己清装备进观战。
        sender_id 为 None 时（历史调用方未提供来源）才回退 payload.player_id。
        """
        gs = self.gv.window.game_state
        # 身份收口：优先用连接 sender_id（禁信任 payload.player_id）
        player_id = sender_id if sender_id is not None else payload.get("player_id")
        if player_id is None:
            print("[Host] SPECTATE_LEAVE 缺少玩家身份，忽略本次请求")
            return
        if player_id == 0:
            # 主机自己退出观战：清装备 + 观战
            self.gv._clear_run_equipment(gs)
            self.gv._enter_spectate("dead")
        else:
            # 客户端退出观战：标记真死
            self.gv._downed_players.pop(player_id, None)
            self.gv._player_status[player_id] = "dead"
            # 通知该客户端真死
            gs.net_server.send_to(player_id, MsgType.PLAYER_DEATH, {
                "player_id": player_id,
                "killer_id": None,
            })

    def handle_evac_point_result(self, payload: dict) -> None:
        """客户端应用主机 EVAC_POINT_RESULT：撤离点激活/修复的权威回执

        双扣规避（核心风险点）——依据 game_view.py 实测：
        - views/game_view.py:1460-1489 `_client_request_evac_point`：客户端发
          EVAC_POINT_ACTION 时，1481 消费按键后直接 send，不调用 `_pay_resource_cost`，
          **未对 gs.run_carried 做任何扣减**（单机路径 1446 才调用 `_pay_resource_cost` 扣料）。
        - 因此客户端**未乐观扣料**。本方法在 ok=True 时**负责按主机权威 action 扣对应资源**，
          成本从 config.evac_activate_cost(theme) 读取（禁硬编码），避免双扣。
        - player_id 校验：只处理发给自己的回执（他人回执本端无动作）。
        - action 非法：显式告警并 return（net 层铁律：禁静默忽略）。
        - ok=False：仅提示，不改任何本地账本。
        """
        gs = self.gv.window.game_state
        if not isinstance(payload, dict):
            return
        player_id = payload.get("player_id")
        my_id = getattr(gs, "net_player_id", None)
        if my_id is not None and player_id != my_id:
            return  # 他人的回执：本端无动作
        action = str(payload.get("action") or "")
        known_actions = ("activate", "repair")
        if action not in known_actions:
            print(f"[Client] EVAC_POINT_RESULT action 非法：{action!r} payload={payload!r}")
            return  # 禁静默忽略未知载荷
        ok = bool(payload.get("ok"))
        reason = payload.get("reason") or "撤离点操作被拒绝"
        if not ok:
            print(f"[Client] 撤离点操作被主机拒绝：action={action!r} reason={reason}")
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                               f"操作失败：{reason}", arcade.color.RED,
                               life=2.0, font_size=16)
            return
        # ok=True：客户端未乐观扣料（依据 game_view.py:1460-1489），按主机权威 action 扣资源
        # 扣料走唯一口径 GameView._pay_resource_cost（资源嵌套在 carried["resource"] 子字典，
        # 直接写顶层键会漏扣/错位——这是本次修复的 bug 根因）
        theme = None
        try:
            if getattr(self, "gv", None) and getattr(self.gv, "map_data", None):
                theme = self.gv.map_data.get("theme")
        except Exception:
            theme = None
        if not theme:
            print("[Client] EVAC_POINT_RESULT：无法获取地图主题，放弃扣料并返回")
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                               "操作失败：地图主题未知", arcade.color.RED,
                               life=2.0, font_size=16)
            return
        try:
            from config import evac_activate_cost
            cost = evac_activate_cost(theme)
        except Exception as exc:
            print(f"[Client] EVAC_POINT_RESULT：evac_activate_cost 导入/调用失败：{exc}")
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                               "操作失败：配置读取失败", arcade.color.RED,
                               life=2.0, font_size=16)
            return
        # 复用唯一口径 _pay_resource_cost（校验+扣减，资源结构 carried["resource"]）
        paid = False
        try:
            paid = self.gv._pay_resource_cost(gs, cost)
        except Exception as exc:
            print(f"[Client] EVAC_POINT_RESULT：_pay_resource_cost 调用失败：{exc}")
            paid = False
        if not paid:
            print(f"[Client] EVAC_POINT_RESULT：资源扣减失败（theme={theme!r} cost={cost}）")
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                               "操作失败：资源不足或账本异常", arcade.color.RED,
                               life=2.0, font_size=16)
            return
        # 成功提示（按 action 区分）
        if action == "activate":
            tip = "撤离点已激活"
        else:
            tip = "撤离点已修复"
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           tip, (140, 240, 180),
                           life=2.0, font_size=16)

    def _send_rescue_reject(self, rescuer_id: int, target_id) -> None:
        """主机单播救援拒绝回执 RESCUE_RESULT{success:False}（禁静默拒绝）

        与距离过远分支（_handle_rescue_request 内已内联同构回执）口径一致：
        救援请求此前在「被救者非倒地 / 救援者非存活 / 倒地位置账本缺失」三条路径上
        直接 return，请求方永远等不到任何回执——分不清是被拒还是请求丢了，
        读条会卡在原地。此处补齐拒绝型 ACK，hp=0 表示未复活。
        """
        gs = self.gv.window.game_state
        if gs.net_server is None:
            return  # 防御性：无服务端连接时无从回执（调用方已记日志）
        gs.net_server.send_to(rescuer_id, MsgType.RESCUE_RESULT, {
            "target_id": target_id, "rescuer_id": rescuer_id,
            "success": False, "hp": 0,
        })

    def _handle_rescue_request(self, sender_id: int, payload: dict) -> None:
        """主机处理 RESCUE_REQUEST：裁决距离并执行救援"""
        gs = self.gv.window.game_state
        # 身份收口：救援者一律取连接 sender_id，禁信任 payload.rescuer_id
        # （否则可伪造成他人名义发起救援，白嫖他人倒地位置的救援权）
        rescuer_id = sender_id
        target_id = payload.get("target_id")
        # 校验：被救者必须处于倒地状态（禁静默——目标不是倒地态时对方永远等不到回执）
        if self.gv._player_status.get(target_id) != "downed":
            reason = ("被救者不存在" if target_id is None
                      else f"被救者 {target_id} 当前状态 "
                           f"{self.gv._player_status.get(target_id)!r} 非倒地")
            print(f"[Host] 拒绝玩家 {rescuer_id} 的救援请求：{reason}")
            self._send_rescue_reject(rescuer_id, target_id)
            return
        # 校验：救援者必须存活（自身已阵亡/撤离/断线者不得发起救援）
        if self.gv._player_status.get(rescuer_id) != "alive":
            reason = (f"救援者 {rescuer_id} 当前状态 "
                      f"{self.gv._player_status.get(rescuer_id)!r} 非存活")
            print(f"[Host] 拒绝玩家 {rescuer_id} 的救援请求：{reason}")
            self._send_rescue_reject(rescuer_id, target_id)
            return
        # 获取被救者位置
        downed_info = self.gv._downed_players.get(target_id)
        if downed_info is None:
            print(f"[Host] 拒绝玩家 {rescuer_id} 的救援请求："
                  f"被救者 {target_id} 无倒地位置记录（状态与位置账本不一致）")
            self._send_rescue_reject(rescuer_id, target_id)
            return
        target_x, target_y = downed_info["x"], downed_info["y"]
        # 获取救援者位置
        if rescuer_id == 0:
            rescuer_x, rescuer_y = self.gv.player.center_x, self.gv.player.center_y
        else:
            # 救援者位置一律取幽灵实测坐标（与交互/拾取同口径，禁采信上报坐标）：
            # 幽灵缺失时兜底懒创建（出生点为主机权威坐标，客户端无从伪造）并限频告警
            ghost = self.gv.remote_players.get(rescuer_id)
            if ghost is None:
                ghost = self._ensure_ghost(rescuer_id)
                warn_key = ("ghost_missing", "rescue", rescuer_id)
                if warn_key not in self._warned_keys:
                    self._warned_keys.add(warn_key)
                    print(f"[Host] 玩家 {rescuer_id} 救援时幽灵缺失，"
                          f"已按出生点 ({ghost.center_x},{ghost.center_y}) 兜底（限频告警一次）")
            rescuer_x, rescuer_y = ghost.center_x, ghost.center_y
        # 距离校验
        dist = math.hypot(rescuer_x - target_x, rescuer_y - target_y)
        if dist > RESCUE_DISTANCE:
            # 距离太远：发送失败结果
            gs.net_server.send_to(rescuer_id, MsgType.RESCUE_RESULT, {
                "target_id": target_id, "rescuer_id": rescuer_id,
                "success": False, "hp": 0,
            })
            return
        # 执行救援：被救者 HP 恢复为 REVIVE_HP
        del self.gv._downed_players[target_id]
        self.gv._player_status[target_id] = "alive"
        # 广播救援成功
        gs.net_server.broadcast(MsgType.RESCUE_RESULT, {
            "target_id": target_id, "rescuer_id": rescuer_id,
            "success": True, "hp": REVIVE_HP,
        })
        gs.net_server.broadcast(MsgType.PLAYER_REVIVED, {
            "player_id": target_id, "hp": REVIVE_HP,
        })
        # 如果被救者是幽灵：恢复其 HP
        ghost = self.gv.remote_players.get(target_id)
        if ghost is not None:
            ghost.hp = REVIVE_HP
            ghost.downed = False
        print(f"[GameView] 玩家 {rescuer_id} 成功救援玩家 {target_id}，HP 恢复为 {REVIVE_HP}")

    def _handle_interaction_request(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端交互请求：验证距离 → 执行交互 → 广播结果

        安全校验：验证请求者位置与交互物距离，防止作弊。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host":
            return

        player_id = sender_id
        interaction_type = payload.get("interaction_type", "")
        request_x = float(payload.get("x", 0))
        request_y = float(payload.get("y", 0))

        print(f"[Host] 收到交互请求: player={player_id}, type={interaction_type}, pos=({request_x},{request_y})")

        # 距离校验一律用幽灵实测坐标（PLAYER_SNAPSHOT 主机权威）：直接采信上报坐标
        # 会让客户端隔空开箱/回血/启火箭台。幽灵缺失时优先用本次上报坐标兜底
        # （拿不到上报坐标才退到出生点）并限频告警。
        ghost = self.gv.remote_players.get(player_id)
        if ghost is None:
            ghost = self._ensure_ghost(player_id)
            warn_key = ("ghost_missing", "interaction", player_id)
            if warn_key not in self._warned_keys:
                self._warned_keys.add(warn_key)
                print(f"[Host] 玩家 {player_id} 交互时幽灵缺失，"
                      f"已按上报坐标 ({request_x},{request_y}) 兜底（限频告警一次）")
            # 上报坐标缺失（None）才退到出生点：ghost 是刚懒创建的，坐标即出生点
            if payload.get("x") is None or payload.get("y") is None:
                request_x, request_y = ghost.center_x, ghost.center_y
        check_x, check_y = ghost.center_x, ghost.center_y
        # 校验完成后才用上报坐标刷新幽灵位置（交互处理器沿用请求点语义）
        ghost.center_x = request_x
        ghost.center_y = request_y

        # handled 标记：是否真的受理并执行了交互。
        # 此前各分支在「范围内无目标」（宝箱全已开/水井不存在/发射台超距/发射台状态不允许）
        # 时直接落空 return，**无回执无日志**——客户端分不清「被主机拒绝」与「请求丢了」，
        # 按 E 只会毫无反应。这里统一收口：未受理即显式记中文日志。
        handled = False

        if interaction_type == "chest":
            # 找到最近的未开启宝箱
            for i, chest in enumerate(self.gv.chests):
                if chest.opened:
                    continue
                dist = math.hypot(chest.center_x - check_x, chest.center_y - check_y)
                if dist < CHEST_WELL_INTERACT_RANGE:
                    # 执行开箱
                    from game.entity_callbacks import spawn_chest_loot, _award_exp
                    from config import EXP_CHEST
                    loot = chest.open_chest()
                    spawn_chest_loot(self.gv, chest, loot)
                    _award_exp(self.gv, EXP_CHEST)
                    # 阶段6.2 任务 chest 按开箱发起者归属（主机裁决，单播给该客户端）
                    from game.mission_tracker import on_event as mission_on_event
                    mission_on_event(self.gv, "chest", 1, player_id)
                    print(f"[GameView] 客户端 {player_id} 开启宝箱 {i}")
                    handled = True
                    break

        elif interaction_type == "well":
            well = self.gv.map_data.get("water_well")
            if well:
                dist = math.hypot(well[0] - check_x, well[1] - check_y)
                if dist < CHEST_WELL_INTERACT_RANGE:
                    # 执行水井交互
                    from game.entity_callbacks import handle_well_interaction
                    # 临时设置 _chest_key_pressed 和 player 为幽灵
                    old_player = self.gv.player
                    old_key = self.gv._chest_key_pressed
                    self.gv.player = ghost
                    self.gv._chest_key_pressed = True
                    # 修复：记录治疗前 HP，治疗后广播 POTION_ACK 给客户端
                    hp_before = ghost.hp
                    handle_well_interaction(self.gv)
                    heal_amount = max(0, ghost.hp - hp_before)
                    self.gv.player = old_player
                    self.gv._chest_key_pressed = old_key
                    # 向客户端单播水井回血结果，客户端收到后更新本地玩家 HP
                    if heal_amount > 0:
                        gs.net_server.send_to(sender_id, MsgType.POTION_ACK, {
                            "player_id": sender_id,
                            "potion_id": "well",
                            "accepted": True,
                            "heal_amount": heal_amount,
                            "effect": "heal", "value": heal_amount, "duration": 0,
                        })
                    print(f"[GameView] 客户端 {player_id} 使用水井，回血 {heal_amount}")
                    handled = True

        elif interaction_type == "rocket_pad":
            for i, pad in enumerate(self.gv.rocket_pads):
                dist = math.hypot(pad.center_x - check_x, pad.center_y - check_y)
                if dist < ROCKET_PAD_INTERACT_RANGE:
                    if pad.state == "idle":
                        # 激活火箭台
                        from game.entity_callbacks import handle_rocket_pad_interaction
                        old_player = self.gv.player
                        old_key = self.gv._chest_key_pressed
                        self.gv.player = ghost
                        self.gv._chest_key_pressed = True
                        handle_rocket_pad_interaction(self.gv)
                        self.gv.player = old_player
                        self.gv._chest_key_pressed = old_key
                        print(f"[GameView] 客户端 {player_id} 激活火箭台 {i}")
                        handled = True
                    elif pad.state == "boss_defeated":
                        # 标记可选择状态（7/8键选择）
                        self.gv._rocket_pad_menu = pad
                        print(f"[GameView] 客户端 {player_id} 打开火箭台菜单 {i}")
                        handled = True
                    else:
                        # 范围内但状态不允许（倒计时中/已启动/已摧毁）：显式记日志（禁静默）
                        print(f"[Host] 玩家 {player_id} 交互火箭台被拒："
                              f"第 {i} 个发射台当前状态 {pad.state!r} 不可交互")
                    break
        else:
            # 未知交互类型：显式告警而非静默忽略（net 层铁律：禁静默丢弃请求）
            print(f"[Host] 玩家 {player_id} 请求未知交互类型 "
                  f"interaction_type={interaction_type!r}，已忽略")
            return

        if not handled:
            # 范围内无可交互目标：显式记日志（禁静默，让「按 E 没反应」可追因）
            print(f"[Host] 玩家 {player_id} 的 {interaction_type} 交互未被受理："
                  f"坐标 ({check_x:.0f},{check_y:.0f}) 范围内无合法目标")

    def _handle_player_abandon(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端放弃行动通知：更新 _player_status → 触发全员结束判定

        客户端调用 _fail_run 后发送此消息，主机更新状态后 _check_all_finished
        可正确判定全员结束，广播 ROOM_ENDED 回房。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return

        # 身份收口：放弃者一律取连接 sender_id，禁信任 payload.player_id
        # （否则可伪造他人 id 把别人标记 dead，提前触发全员结束判定）
        player_id = sender_id
        reason = payload.get("reason", "放弃行动")

        # 更新玩家状态为 dead（与死亡同待遇，触发全员结束判定）
        if self.gv._player_status.get(player_id) not in ("evac", "dead", "left"):
            self.gv._player_status[player_id] = "dead"
            print(f"[GameView] 客户端 {player_id} 放弃行动: {reason}，已标记 dead")

        # 标记幽灵为不存活（设置 hp=0，alive 是只读 property：hp > 0）
        ghost = self.gv.remote_players.get(player_id)
        if ghost is not None:
            ghost.hp = 0

    def _serialize_monsters(self) -> list:
        """序列化全部存活怪物为 MONSTER_SNAPSHOT 的 monsters 列表（主机权威）

        - 已死亡怪物（alive=False）跳过：客户端以「快照缺失即删除」感知怪物被击杀/移除；
        - 运行期补刷的新怪物在此惰性分配 net_id（存储在 m.net_id 上，存活期不变），
          与 setup() 中初始怪物的分配共用同一个单调计数器，保证全房唯一；
        - 载荷键名严格遵循 net/protocol.py MESSAGE_SCHEMAS["MONSTER_SNAPSHOT"]：
          net_id / monster_type / x / y / hp / max_hp / weapon / debuff / attack_anim，
          以及 shield / max_shield / damage / aggro_range / attack_delay / attack_cd_ratio
          （客户端护盾条、冷却条、表现层战斗数值均读这批字段）。
        """
        snapshot = []
        for m in self.gv.monsters:
            # 跳过已死亡怪物（不广播，客户端据此删除远端怪物）
            if hasattr(m, "alive") and not m.alive:
                continue
            net_id = getattr(m, "net_id", None)
            if net_id is None:
                # 补刷怪物首次序列化时分配 net_id（单调递增，不回收）
                net_id = self.gv._next_monster_net_id
                self.gv._next_monster_net_id += 1
                m.net_id = net_id
                self.gv.net_id_to_monster[net_id] = m
            # 当前生效中的首个 debuff id（无则 None）；debuffs 元素为 dict（含 id 键）
            debuff_id = None
            if getattr(m, "debuffs", None):
                debuff_id = m.debuffs[0].get("id") if isinstance(m.debuffs[0], dict) else None
            # 装备快照字段：客户端渲染远端怪物护甲/头盔/武器颜色与等级（修复装备不同步）
            weapon = m.weapon if isinstance(getattr(m, "weapon", None), dict) else {}
            armor = m.armor if isinstance(getattr(m, "armor", None), dict) else {}
            helmet = m.helmet if isinstance(getattr(m, "helmet", None), dict) else {}
            # 攻击冷却：主机发「剩余比例 + 总时长」而非裸计时器——客户端不跑 AI，
            # 无从知道本怪真实 _attack_delay（词缀/等级可能改过），按比例线性衰减
            # 即可还原同一条冷却曲线，避免用本地默认值算出的脏进度条。
            atk_timer = float(getattr(m, "_attack_timer", 0.0))
            atk_delay = max(float(getattr(m, "_attack_delay", 1.0)), 1e-6)
            atk_cd_ratio = max(0.0, min(1.0, atk_timer / atk_delay))
            snapshot.append({
                "net_id": net_id,
                "monster_type": type(m).__name__,  # 类名即 game.monsters 模块属性名，客户端据此实例化
                "x": m.center_x,
                "y": m.center_y,
                "hp": m.hp,
                "max_hp": m.max_hp,
                "weapon": weapon.get("name"),
                "weapon_color": list(weapon.get("color", (255, 255, 255))[:3]) if weapon else None,
                "weapon_level": weapon.get("level"),
                "armor": armor.get("name"),
                "armor_color": list(armor.get("color", (200, 200, 200))[:3]) if armor else None,
                "helmet": helmet.get("name"),
                "helmet_color": list(helmet.get("color", (200, 200, 200))[:3]) if helmet else None,
                "debuff": debuff_id,
                "attack_anim": atk_timer,
                # 阶段3 精英词缀：affix=词缀 id（None=普通怪），is_elite=是否精英
                # （客户端据此显示头顶词缀名前缀、小地图紫点）
                "affix": getattr(m, "affix", None),
                "is_elite": bool(getattr(m, "is_elite", False)),
                # 精英护盾词缀的实际值（monster_affixes 写入 shield/max_shield）：
                # 客户端幽灵只有 MONSTER_CONFIGS 默认值（shield=0），不广播则
                # 护盾条在客户端永远不显示（修复客户端精英护盾条缺失）
                "shield": float(getattr(m, "shield", 0.0)),
                "max_shield": float(getattr(m, "max_shield", 0.0)),
                # 主机权威战斗数值（词缀/等级修正后的生效值），供客户端幽灵
                # 表现层与后续消费方读取（不参与客户端本地仲裁）
                "damage": float(getattr(m, "damage", 0.0)),
                "aggro_range": float(getattr(m, "_aggro_range", 0.0)),
                "attack_delay": atk_delay,
                "attack_cd_ratio": atk_cd_ratio,
            })
        return snapshot

    def _serialize_projectiles(self) -> dict:
        """序列化全部存活弹丸与激光为 PROJECTILE_SNAPSHOT（主机权威）

        - 怪物弹丸（skeleton_projectiles）与玩家弹丸（combat.projectiles）统一进
          projectiles 列表：修复「主机子弹客户端不可见」——之前只序列化怪物弹丸，
          玩家弹丸（含客户端上报远程攻击后主机生成的权威弹丸）从不进快照，
          客户端永远看不到主机发射的子弹；
        - 激光（combat.lasers，陨星炮）：进 lasers 列表，客户端按快照渲染远端激光
          （起点/角度/伤害/长度/宽度/剩余时长），修复「主机激光客户端不可见」；
        - 弹丸/激光首次序列化时惰性分配单调 proj_id（存储在对象上，存活期不变），
          与 setup() 初始弹丸共用同一个单调计数器，保证全房唯一；消亡后 id 不回收；
        - 载荷键名严格遵循 net/protocol.py MESSAGE_SCHEMAS["PROJECTILE_SNAPSHOT"]：
          proj_id / owner_id / x / y / vx / vy / damage / debuff（弹丸）；
          proj_id / owner_id / x / y / angle / damage / length / width / duration（激光）。
        """
        snapshot = []
        # 怪物弹丸（骷髅等远程怪物的权威弹丸）
        for p in self.gv.skeleton_projectiles:
            proj_id = getattr(p, "proj_id", None)
            if proj_id is None:
                # 新弹丸首次序列化时分配 proj_id（单调递增，不回收）
                proj_id = self.gv._next_proj_id
                self.gv._next_proj_id += 1
                p.proj_id = proj_id
            snapshot.append({
                "proj_id": proj_id,
                "owner_id": getattr(p, "owner_net_id", 0),
                "x": p.center_x,
                "y": p.center_y,
                "vx": p.change_x,
                "vy": p.change_y,
                "damage": p.damage,
                "debuff": getattr(p, "debuff_id", None),
                "color": list(getattr(p, "color", (255, 100, 50))[:3]),
            })
        # 玩家弹丸（修复主机子弹客户端不可见：主机本地攻击与客户端上报攻击生成的
        # 权威弹丸都进快照；客户端按 owner_id==自己 跳过本地表现弹丸，其余照常渲染）
        for p in self.gv.combat.projectiles:
            proj_id = getattr(p, "proj_id", None)
            if proj_id is None:
                proj_id = self.gv._next_proj_id
                self.gv._next_proj_id += 1
                p.proj_id = proj_id
            snapshot.append({
                "proj_id": proj_id,
                "owner_id": getattr(p, "owner_net_id", 0),
                "x": p.center_x,
                "y": p.center_y,
                "vx": p.change_x,
                "vy": p.change_y,
                "damage": p.damage,
                "debuff": getattr(p, "debuff_id", None),
                "color": list(getattr(p, "color", (255, 100, 50))[:3]),
            })
        # 激光（陨星炮）：起点=发射者当前位置、角度实时跟随，客户端按快照重绘
        lasers = []
        for b in self.gv.combat.lasers:
            proj_id = getattr(b, "proj_id", None)
            if proj_id is None:
                proj_id = self.gv._next_proj_id
                self.gv._next_proj_id += 1
                b.proj_id = proj_id
            sx, sy = b.start_point
            lasers.append({
                "proj_id": proj_id,
                "owner_id": getattr(b, "owner_net_id", 0),
                "x": sx,
                "y": sy,
                "angle": b.angle,
                "damage": b.damage,
                "length": b.length,
                "width": b.width,
                "duration": max(0.0, b.duration),
            })
        return {"projectiles": snapshot, "lasers": lasers}

    def _apply_monster_snapshot(self, payload: dict) -> None:
        """应用主机下发的 MONSTER_SNAPSHOT：按 net_id 增/改/删维护 self.remote_monsters

        - 已有 net_id → 原位更新（位置/HP/武器名/debuff/攻击冷却）；
        - 新 net_id → 新建远端怪物对象：复用真实怪物类实例（仅作渲染数据容器，
          永不调用 update/try_attack，AI/碰撞由主机权威执行），不加入 self.monsters；
          实例化后立即打 net_ghost=True 防呆标记（消费侧 monster_base 的提前 return
          判据，语义同 Building/RocketPad 的非 Sprite 防呆）；
        - 本端存在但快照缺失的 net_id → 主机已击杀/移除，删除。
        位置插值（阶段B3）：只对**绘制坐标**插值——应用时把「上一快照位置 → 本快照
        位置 + 到达时间戳」记到 net_prev_*/net_next_*/net_interp_t0，由 game_view
        客户端块每帧推进 net_interp_alpha（alpha=(now-t0)/快照间隔），rendering.py
        据此 lerp 出 render 坐标再绘制。**center_x/center_y 恒等于最新快照**（逻辑
        坐标，客户端弹丸命中判定 arcade.check_for_collision 读 rm.center_x，改它会
        直接改变手感），绘制完必须还原。
        """
        monster_list = payload.get("monsters", []) if isinstance(payload, dict) else []
        snapshot_ids = set()
        for entry in monster_list:
            net_id = entry.get("net_id")
            if net_id is None:
                continue  # 非法条目：无 net_id 无法同步，跳过
            snapshot_ids.add(net_id)
            rm = self.gv.remote_monsters.get(net_id)
            if rm is None:
                # 新建：按 monster_type（类名）实例化真实怪物类，未知类型回退 Zombie
                cls = getattr(monsters, entry.get("monster_type", ""), Zombie)
                rm = cls(center_x=entry.get("x", 0.0), center_y=entry.get("y", 0.0))
                rm.net_id = net_id
                # 防呆：远端幽灵标记（客户端不跑 AI/碰撞，消费侧据此提前 return）
                rm.net_ghost = True
                self.gv.remote_monsters[net_id] = rm
            # 位置插值：先留旧坐标作插值起点，再落最新快照（逻辑坐标）
            prev_x, prev_y = rm.center_x, rm.center_y
            rm.center_x = entry.get("x", rm.center_x)
            rm.center_y = entry.get("y", rm.center_y)
            # 插值两端 + 本快照到达时间戳（首帧 prev==next，天然原地起步不跳变）
            rm.net_prev_x, rm.net_prev_y = prev_x, prev_y
            rm.net_next_x, rm.net_next_y = rm.center_x, rm.center_y
            rm.net_interp_t0 = time.time()
            rm.net_interp_alpha = 0.0
            rm.hp = entry.get("hp", rm.hp)
            rm.max_hp = entry.get("max_hp", rm.max_hp)
            # 武器名/debuff/攻击动画：以 net_* 前缀保存快照值供渲染任务使用，
            # 不覆盖怪物自身的 weapon dict（那是主机掉落逻辑用的结构）
            rm.net_weapon = entry.get("weapon")
            rm.net_debuff = entry.get("debuff")
            rm.net_attack_anim = entry.get("attack_anim", 0.0)
            # 装备颜色/等级快照（修复远端怪物装备与主机不一致）：
            # 客户端渲染护甲/头盔/武器颜色时优先用这些 net_* 值
            rm.net_weapon_color = entry.get("weapon_color")
            rm.net_weapon_level = entry.get("weapon_level")
            rm.net_armor = entry.get("armor")
            rm.net_armor_color = entry.get("armor_color")
            rm.net_helmet = entry.get("helmet")
            rm.net_helmet_color = entry.get("helmet_color")
            # 阶段3 精英词缀快照：客户端不跑 apply_affix（词缀逻辑主机权威），
            # 改为以 net_* 前缀保存 affix / is_elite 供渲染消费：
            # 头顶词缀名前缀（rendering_hud._draw_monster）与小地图紫点读这两个字段
            rm.net_affix = entry.get("affix")
            rm.net_is_elite = bool(entry.get("is_elite", False))
            # ── 精英护盾 + 主机权威战斗数值（全部 .get() 默认 None 向后兼容）──
            # 默认 None（而非 0）是有意为之：消费侧（rendering_hud 护盾条）据此
            # 区分「主机没下发」与「主机下发 0」，前者回退本地 MONSTER_CONFIGS
            # 默认值而非误画/误隐藏；旧主机缺字段时不会崩、也不会显示脏值。
            rm.net_shield = entry.get("shield")
            rm.net_max_shield = entry.get("max_shield")
            # damage / aggro_range / attack_delay：主机权威生效值，客户端幽灵不跑 AI
            # 用不到，留给表现层与后续消费方（禁在此处改写本地 damage/_aggro_range）
            rm.net_damage = entry.get("damage")
            rm.net_aggro_range = entry.get("aggro_range")
            rm.net_attack_delay = entry.get("attack_delay")
            # 攻击冷却剩余比例（0~1）：客户端按此线性衰减（game_view 客户端块），
            # 免去用本地 _attack_delay 反推主机冷却长度导致的脏进度条
            rm.net_attack_cd = entry.get("attack_cd_ratio")
        if monster_list and not self._warned_old_monster_snapshot:
            # net 层铁律：禁静默丢字段——主机快照缺新字段时显式告警一次（非每帧刷屏）
            if "attack_cd_ratio" not in monster_list[0] or "shield" not in monster_list[0]:
                print("[NetSync] 主机 MONSTER_SNAPSHOT 缺 shield/attack_cd_ratio 等新字段"
                      "（旧主机？），已按本地默认值兜底渲染")
                self._warned_old_monster_snapshot = True
        # 删除本端存在但快照缺失的怪物（主机已击杀/移除）
        for net_id in list(self.gv.remote_monsters):
            if net_id not in snapshot_ids:
                del self.gv.remote_monsters[net_id]
        # 快照是「目标已存在」的信号 → 重放此前因目标缺失而排队的 DAMAGE_RESULT
        # （放在删除之后：本快照未出现的怪物已从表里移除，重放时会被正确判为缺失）
        self._drain_pending_damage_results()

    def _apply_projectile_snapshot(self, payload: dict) -> None:
        """应用主机下发的 PROJECTILE_SNAPSHOT：按 proj_id 增/改/删维护 remote_projectiles/remote_lasers

        - 新 proj_id → 新建轻量表现弹丸（arcade.SpriteSolidColor，标准弹丸尺寸），
          加入渲染列表并登记 _remote_proj_map 供后续 O(1) 查找；
        - 已有 proj_id → 原位更新位置（主机权威，纯视觉跟随，不做客户端物理/插值）；
        - 本端存在但快照缺失的 proj_id → 弹丸已在主机消亡（超时/碰墙/命中玩家），
          删除即命中消失。vx/vy/damage/debuff 以 net_* 前缀保存在弹丸对象上供后续任务扩展。
        - owner_id == 本端玩家 id 的弹丸跳过：那是本端自己发射的纯表现弹丸（本地已渲染），
          重复渲染会造成双重弹丸（修复主机子弹同步后引入的双重渲染问题）；
        - lasers 列表：维护远端激光表现对象（owner_id==自己 的激光同样跳过，本地已有表现）。
        """
        gs = self.gv.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        proj_list = payload.get("projectiles", []) if isinstance(payload, dict) else []
        snapshot_ids = set()
        for entry in proj_list:
            proj_id = entry.get("proj_id")
            if proj_id is None:
                continue  # 非法条目：无 proj_id 无法同步，跳过
            # 自己发射的弹丸：本地已有纯表现弹丸（input_handler 本地生成），跳过避免双重渲染
            if my_id is not None and entry.get("owner_id") == my_id:
                continue
            snapshot_ids.add(proj_id)
            rp = self.gv._remote_proj_map.get(proj_id)
            if rp is None:
                # 新建轻量表现弹丸：颜色取快照 color（修复主机弹丸颜色与本地不一致），
                # 缺省回退怪物弹丸默认橙红；尺寸取配置 PROJECTILE_SIZE
                snap_color = entry.get("color") or [255, 100, 50]
                rp = arcade.SpriteSolidColor(PROJECTILE_SIZE, PROJECTILE_SIZE,
                                             color=(int(snap_color[0]), int(snap_color[1]), int(snap_color[2])))
                rp.proj_id = proj_id
                self.gv.remote_projectiles.append(rp)
                self.gv._remote_proj_map[proj_id] = rp
            # 原位更新位置（主机权威），速度/伤害/debuff 快照值以 net_* 前缀保存
            rp.center_x = entry.get("x", rp.center_x)
            rp.center_y = entry.get("y", rp.center_y)
            rp.net_vx = entry.get("vx", 0.0)
            rp.net_vy = entry.get("vy", 0.0)
            rp.net_damage = entry.get("damage", 0.0)
            rp.net_debuff = entry.get("debuff")
        # 删除本端存在但快照缺失的弹丸（主机已消亡：超时/碰墙/命中玩家 = 命中消失）
        for proj_id in list(self.gv._remote_proj_map):
            if proj_id not in snapshot_ids:
                rp = self.gv._remote_proj_map.pop(proj_id)
                rp.remove_from_sprite_lists()
        # 远端激光同步（陨星炮）：按 proj_id 增/改/删维护 remote_lasers（纯表现层）
        laser_list = payload.get("lasers", []) if isinstance(payload, dict) else []
        laser_ids = set()
        for entry in laser_list:
            laser_id = entry.get("proj_id")
            if laser_id is None:
                continue
            # 自己发射的激光：本地已有纯表现激光（input_handler 本地 spawn_laser），跳过
            if my_id is not None and entry.get("owner_id") == my_id:
                continue
            laser_ids.add(laser_id)
            beam = self.gv.remote_lasers.get(laser_id)
            if beam is None:
                from views.game_view import _RemoteLaser
                beam = _RemoteLaser(laser_id)
                self.gv.remote_lasers[laser_id] = beam
            # 原位更新（主机权威位置/角度/剩余时长，20Hz 快照足以平滑跟随）
            beam.x = entry.get("x", beam.x)
            beam.y = entry.get("y", beam.y)
            beam.angle = entry.get("angle", beam.angle)
            beam.length = entry.get("length", beam.length)
            beam.width = entry.get("width", beam.width)
            beam.duration = entry.get("duration", beam.duration)
        # 删除快照缺失的激光（主机激光已消失：duration 耗尽）
        for laser_id in list(self.gv.remote_lasers):
            if laser_id not in laser_ids:
                del self.gv.remote_lasers[laser_id]

    def _serialize_full_state(self) -> dict:
        """主机序列化全量世界状态（FULL_STATE）：发给晚期加入客户端的初始化数据

        - monsters 复用 _serialize_monsters 格式（存活怪物 id/位置/hp/装备/debuff）；
        - drops/chests/env_objects 序列化当前地面掉落、宝箱开启状态与环境物存活状态；
        - 11.1 接线核查：buildings（阶段1 局内建筑 bid/kind/x/y/hp）与 event_flags
          （阶段4 事件参数）一并进全量，解析端 _apply_full_state 对称还原；
          blessings 不进全量——只同步活跃玩家 stats（见 PLAYER_SNAPSHOT）；
        - action_time_left 同步剩余行动时间，客户端据此初始化后只收增量快照（B13）。
        """
        gs = self.gv.window.game_state
        # 地面掉落物（与 drop_spawn 广播格式一致：net_id/item_type/item_id/x/y/quantity/level）
        drops = []
        for d in self.gv.drops:
            # 兜底分配网络 id（正常情况生命周期段已分配；此处防御晚期加入时遗漏）
            if getattr(d, "net_id", None) is None:
                d.net_id = f"drop_{self.gv._next_drop_net_id}"
                self.gv._next_drop_net_id += 1
            drops.append({
                "net_id": d.net_id,
                "item_type": getattr(d, "item_type", ""),
                "item_id": getattr(d, "item_id", ""),
                "x": d.center_x, "y": d.center_y,
                "quantity": getattr(d, "quantity", 1),
                "level": getattr(d, "level", 1),
            })
        # 宝箱：按列表序号作为稳定 id（运行期改动同步是 todo 20）
        # 阶段4 空投箱额外带 is_airdrop/theme/artifact_bonus：晚期加入客户端据此
        # 重建同类型箱体（否则晚期加入者看不到空投箱、序号也对不上后续 chest_opened）
        chests = [{"id": i, "opened": c.opened,
                   "x": c.center_x, "y": c.center_y,
                   "is_airdrop": bool(getattr(c, "is_airdrop", False)),
                   "theme": getattr(c, "theme", "forest"),
                   "artifact_bonus": float(getattr(c, "artifact_bonus", 0.0) or 0.0)}
                  for i, c in enumerate(self.gv.chests)]
        # 环境物（可采集物/水井等）：存活状态 + 资源类型
        env_objects = [{"id": i, "resource_type": getattr(h, "resource_type", ""),
                        "alive": getattr(h, "alive", True),
                        "x": h.center_x, "y": h.center_y}
                       for i, h in enumerate(self.gv.harvestables)]
        # 11.1 接线核查：局内建筑（阶段1）进全量，bid 与 build_place/build_destroy 同口径，
        # 晚期加入客户端据此补建视觉对象（否则看不到他人已建的路障/箭塔）
        buildings = [{"bid": b.bid, "kind": b.kind, "x": b.x, "y": b.y, "hp": b.hp}
                     for b in self._building_list()]
        # ── 地图元信息：先算一次，原键与别名键共用同一份权威值 ──────────────────
        # 主题口径：gs.map_theme 是本局地图主题（game_view.setup() 生成地图读的就是它，
        # lobby_view 建房时由 server.room.theme 写入），**不是** chests 条目里那只
        # 空投箱自己的 theme（后者只是单箱皮肤，两者同名易混，勿取错）。
        map_seed = getattr(gs, "current_map_seed", None)
        map_theme = getattr(gs, "map_theme", "")
        roster_state = dict(getattr(gs, "net_roster", {}) or {})
        # 出生点 tuple 不可 json 序列化 → 降为 [x, y]；json 的 object 键一律字符串
        spawns_state = {str(pid): [float(pos[0]), float(pos[1])]
                        for pid, pos in (getattr(gs, "net_spawns", {}) or {}).items()
                        if isinstance(pos, (tuple, list)) and len(pos) >= 2}
        max_players_state = getattr(gs, "net_max_players", None)
        return {
            "monsters": self._serialize_monsters(),
            "drops": drops,
            "chests": chests,
            "env_objects": env_objects,
            "buildings": buildings,
            # 水井首次开启状态（仅沙漠主题）：晚期加入客户端据此镜像 _well_opened
            "well_opened": self.gv._well_opened,
            # 阶段2 主撤离点权威状态（格式同 EVAC_POINT_STATE；None=尚未建立，
            # 航天图为击败 BOSS 后才建点）——晚期加入客户端据此还原防守进度
            "evac_point": self.gv._serialize_evac_point(),
            # 阶段4 本局随机事件 id（''=无事件；教程局恒为空）——晚期加入客户端
            # 镜像事件横幅与参数，避免错过开局 3 秒横幅看不到事件名
            "event_id": getattr(self.gv, "event_id", ""),
            # 阶段4 事件参数（与 _apply_event_start 的 flags 同口径；apply_event 会按
            # MAP_EVENTS 重算，此处为显式对齐，让两端 event_flags 完全同值）
            "event_flags": dict(getattr(self.gv, "event_flags", {}) or {}),
            "action_time_left": self.gv._action_time_remaining or 0.0,
            # 地图元信息（补齐项）：种子/主题/花名册/出生点/人数上限
            # 晚期加入客户端据此对齐地图布局与出生位置，避免按本地默认值跑偏
            "current_map_seed": map_seed,
            "map_theme": map_theme,
            # 名册/出生点均为 {player_id: ...} 映射（见 _ensure_ghost 取值口径）：
            # 花名册值原样下发；出生点已在上方降为 [x, y] 列表，
            # 客户端 _apply_full_state 再还原为 tuple 布局。
            "net_roster": roster_state,
            "net_spawns": spawns_state,
            "net_max_players": max_players_state,
            # 别名键：与 views/lobby_view.py FULL_STATE 消费端字段名对齐
            # （seed/roster/spawns/max_players/theme），值与上方原键同源。
            # 起因：两路消费端键名不一致 → 晚加入客户端 seed 缺省回落 1、theme 回落
            # forest，用本地默认种子重建地图，与主机随机种子不同 ⇒ 全图实体/掉落/撤离点
            # 全部不同步（P0）。追加别名而非改消费端，对已跑通的老消费端零风险。
            "seed": map_seed,
            "theme": map_theme,
            "roster": dict(roster_state),
            "spawns": dict(spawns_state),
            "max_players": max_players_state,
        }

    def _apply_full_state(self, payload: dict) -> None:
        """客户端应用 FULL_STATE：晚期加入时初始化远端世界，之后只收增量快照

        - monsters：复用 _apply_monster_snapshot 的增/改/删逻辑初始化 remote_monsters；
        - 行动时间：以主机权威值校准本地倒计时（HUD 同步）；
        - drops：转为视觉 DropItem 加入 self.drops（Todo 18，晚期加入客户端即可见可拾取，
          与主机 drop_spawn 广播共用 net_id 口径）；
        - chests/env_objects（todo 20 收口）：按 id（列表序号，与绘制本地对象一致的口径）
          把已开宝箱 / 已摧毁环境物状态应用到本地坐标对象，渲染与碰撞据此跳过；
        - well_opened（todo 20 收口）：晚期加入客户端镜像主机水井首次开启状态，
          与主机交互/提示口径一致（此前只有运行期 well_used 增量广播，晚期加入者会漏）。
        - 11.1 接线核查：buildings 逐条走 _client_add_building 还原（与 build_place 同入口）；
          event_flags 与 event_id 一并镜像（否则 flags 只能靠 apply_event 本地重算）。
        """
        if not isinstance(payload, dict):
            return
        self._apply_monster_snapshot({"monsters": payload.get("monsters", [])})
        t = payload.get("action_time_left")
        if t is not None:
            self.gv._action_time_remaining = t
            self.gv.window.game_state.action_time_remaining = t
        # 地图元信息（补齐项）：种子/主题/花名册/出生点/人数上限
        # 缺字段一律保留本地默认（不做 None 覆盖，避免旧主机把客户端清空）
        gs = self.gv.window.game_state
        seed = payload.get("current_map_seed")
        if seed is not None:
            gs.current_map_seed = seed
        theme = payload.get("map_theme")
        if theme:
            gs.map_theme = theme
        roster = payload.get("net_roster")
        if isinstance(roster, dict) and roster:
            # json 的 object 键一律是字符串 → 按 int 还原 player_id（_ensure_ghost 按 id 取名册）
            gs.net_roster = {int(k): v for k, v in roster.items() if str(k).lstrip("-").isdigit()}
        spawns = payload.get("net_spawns")
        if isinstance(spawns, dict) and spawns:
            # 主机下发 {player_id: [x, y]} → 还原为 {player_id: (x, y)}（与本地出生点口径一致）
            gs.net_spawns = {
                int(k): (float(v[0]), float(v[1]))
                for k, v in spawns.items()
                if str(k).lstrip("-").isdigit() and isinstance(v, (list, tuple)) and len(v) >= 2
            }
        max_players = payload.get("net_max_players")
        if max_players:
            gs.net_max_players = max_players
        # 水井状态：主机已首次开启 → 本地镜像（渲染/提示与主机一致）
        if payload.get("well_opened"):
            self.gv._well_opened = True
        # 阶段2 主撤离点：按主机权威状态镜像（None/缺字段=尚未建立，保持 None）
        # 客户端不跑 EvacPoint.update 与波次，只还原字段供渲染与倒计时显示
        evac_data = payload.get("evac_point")
        if isinstance(evac_data, dict):
            self.gv._apply_evac_point_state(evac_data)
        # 阶段4 随机事件：镜像本局 event_id/参数（晚期加入者补看横幅；
        # 事件实体本身由 chests 列表序号 + caravan_point 增量广播还原）
        event_id = payload.get("event_id")
        if event_id:
            self._apply_event_start({"event_id": event_id,
                                     "flags": payload.get("event_flags") or {}})
        # FULL_STATE 掉落物转为视觉 DropItem（带主机权威 net_id；已存在则跳过）
        for entry in payload.get("drops", []) or []:
            net_id = entry.get("net_id")
            if not net_id or any(d.net_id == net_id for d in self.gv.drops):
                continue
            self.gv.drops.append(DropItem(
                entry.get("x", 0.0), entry.get("y", 0.0),
                entry.get("item_type", "gold"), entry.get("item_id", "gold"),
                quantity=entry.get("quantity", 1),
                level=entry.get("level", 1), net_id=net_id,
            ))
        # 宝箱状态：已开宝箱标记 opened（渲染不绘制、不可再开）；
        # 阶段4 空投补给箱（运行期追加、序号超出本地列表）在此重建，
        # 保证晚期加入客户端的 chests 序号与主机一致（后续 chest_opened 可正确对齐）
        for entry in payload.get("chests", []) or []:
            cid = entry.get("id")
            if not isinstance(cid, int):
                continue
            if entry.get("is_airdrop") and cid == len(self.gv.chests):
                chest = AirdropChest(entry.get("x", 0.0), entry.get("y", 0.0),
                                     theme=entry.get("theme", "forest"),
                                     artifact_bonus=entry.get("artifact_bonus", 0.0))
                self.gv.chests.append(chest)
                self.gv.obstacle_list.append(chest)
            if 0 <= cid < len(self.gv.chests) and entry.get("opened"):
                self.gv.chests[cid].opened = True
        # 环境物状态：已摧毁环境物置 hp=0（alive 是只读 property）+ 移出障碍物（渲染跳过、无碰撞）
        for entry in payload.get("env_objects", []) or []:
            eid = entry.get("id")
            if isinstance(eid, int) and 0 <= eid < len(self.gv.harvestables) \
                    and not entry.get("alive", True):
                h = self.gv.harvestables[eid]
                h.hp = 0
                if h in self.gv.obstacle_list:
                    self.gv.obstacle_list.remove(h)
        # 11.1 接线核查：局内建筑对称还原（与 build_place 同一入口 _client_add_building，
        # bid 同口径；客户端只渲染、不注册 monster_grid 碰撞）
        for entry in payload.get("buildings", []) or []:
            self._client_add_building(entry.get("bid"), entry)
        # 保留原始列表供调试/扩展使用（后续渲染直接读本地坐标对象状态）
        self.gv._late_chests = payload.get("chests", [])
        self.gv._late_env = payload.get("env_objects", [])

    # ── 建造请求-应答（阶段1 局内建造的联机接线）────────────────────────────
    def handle_build_request(self, sender_id: int, payload: dict) -> None:
        """主机仲裁客户端 BUILD_REQUEST：主机权威 place + 单播 BUILD_RESULT

        - 联机建造铁律：资源扣减与建筑落位只由主机执行（BuildSystem.place 在
          net_mode=="client" 直接返回 None）；客户端只发请求、看回执；
        - 判定复用 BuildSystem.can_place（边界/距离/墙体/占用/资源同一口径，不重复实现），
          但 can_place 读的是 view.player 与 game_state.run_carried——主机本地玩家的
          坐标与携带物。故请求期间把 view.player 临时换成请求者幽灵、run_carried 临时
          指向该玩家的权威清单，用 try/finally 保证异常/提前 return 也能复原；
        - 位置校验用幽灵实测坐标（与交互/拾取同口径）：客户端可改上报坐标伪造建造点，
          但 can_place 的距离判据吃的是临时绑定的幽灵坐标，故越界建造会被拒。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return
        from entities.build_defs import BUILDS
        build_system = getattr(self.gv, "build_system", None)
        if build_system is None:
            # 无建造系统（教程局/未初始化）：显式拒绝，禁静默
            gs.net_server.send_to(sender_id, MsgType.BUILD_RESULT, {
                "build_id": payload.get("build_id"), "ok": False,
                "bid": None, "reason": "build_unavailable",
            })
            return
        build_id = payload.get("build_id")
        if build_id not in BUILDS:
            # 未知建筑类型：显式拒绝并告警（禁静默，跨版本建筑表不同步的防御）
            print(f"[Host] 玩家 {sender_id} 请求未知建筑 {build_id!r}，已拒绝")
            gs.net_server.send_to(sender_id, MsgType.BUILD_RESULT, {
                "build_id": build_id, "ok": False, "bid": None, "reason": "unknown_build",
            })
            return
        ghost = self._ensure_ghost(sender_id)
        x = float(payload.get("x") or 0.0)
        y = float(payload.get("y") or 0.0)
        # 临时绑定请求者幽灵与该玩家的权威携带物，判据与扣减都走主机侧账本
        real_player = self.gv.player
        real_carried = gs.run_carried
        building = None
        self.gv.player = ghost
        gs.run_carried = self.gv._players_run_carried.setdefault(sender_id, {})
        try:
            ok, reason = build_system.can_place(x, y, build_id)
            if ok:
                # 用幽灵实测坐标放置（禁采信上报坐标）：越界/隔空建造在此被 can_place 拒
                building = build_system.place(ghost.center_x, ghost.center_y, build_id)
                if building is None:
                    ok, reason = False, "place_failed"
        finally:
            # 复原主机本地玩家与携带物（异常路径也必须复原，否则后续主机本地建造/撤离全错）
            self.gv.player = real_player
            gs.run_carried = real_carried
        bid = building.bid if ok and building is not None else None
        if ok and building is not None:
            # 广播 build_place（含 hp，客户端据此还原视觉 + 血条）
            self.gv._broadcasted_buildings.add(bid)
            gs.net_server.broadcast(MsgType.MAP_CHANGE, {
                "obj_id": bid, "change_type": "build_place",
                "state": {"kind": building.kind, "x": building.x, "y": building.y,
                          "hp": building.hp},
                "extra": {},
            })
            # 该玩家的权威携带物已被 place 扣减（游戏背包/结算口径均读它），
            # 故记一笔显式日志便于对账（携带清单无独立消息类型，禁改 net/protocol.py，
            # 因此不在此发明新消息；客户端携带显示仍以本端为准，撤离时才按主机清单入库）
            print(f"[Host] 玩家 {sender_id} 建造 {build_id} 成功：bid={bid}，"
                  f"携带清单={self.gv._players_run_carried.get(sender_id) or {}}")
        gs.net_server.send_to(sender_id, MsgType.BUILD_RESULT, {
            "build_id": build_id, "ok": ok, "bid": bid, "reason": reason or None,
        })

    def apply_build_result(self, payload: dict) -> None:
        """客户端应用主机 BUILD_RESULT：成功扣本端携带资源 / 失败时提示原因

        - ok=True：建筑已由 MAP_CHANGE build_place 在本地还原，这里不再重复建；
          但**必须按 entities/build_defs.BUILDS 的 cost 扣本端 run_carried["resource"]**。
          修复 desync：联机建造资源扣减只发生在主机 _players_run_carried（权威账本），
          客户端本端账本此前毫发无损 → HUD 资源数与实际不符，且玩家能反复「凭空造塔」
          （本端看着还有料，实际主机早已扣空）。此处按同一 cost 表镜像扣减；
        - ok=False：显示主机拒绝原因（材料不足/位置占用/越界/距离过远等），
          让玩家知道为什么「按了没反应」——此前只有请求没有回执，只能空等；
          失败**不扣**任何资源（主机未受理）。
        """
        gs = self.gv.window.game_state
        if payload.get("ok"):
            # 成功：资源按 BUILDS cost 镜像扣减（视觉/血条由 MAP_CHANGE build_place 还原）
            build_id = payload.get("build_id")
            from entities.build_defs import BUILDS
            cost = dict((BUILDS.get(build_id) or {}).get("cost") or {})
            carried = getattr(gs, "run_carried", None)
            if not cost or not isinstance(carried, dict):
                # 载荷缺 build_id / 建筑表无 cost 变更：显式告警，不静默吞掉
                print(f"[Client] BUILD_RESULT 成功但 cost 缺失：build_id={build_id!r}")
                return
            resource = carried.get("resource")
            if not isinstance(resource, dict):
                resource = {}
            # 先整体校验：本端资源不足说明两端账本已漂移，此时不硬扣成负数，
            # 改按权威值对齐到 0 并限频告警（禁静默——漂移必须留痕）
            drift = [iid for iid, need in cost.items()
                     if int(resource.get(iid, 0) or 0) < int(need)]
            if drift:
                warn_key = ("build_cost_drift", tuple(sorted(drift)), build_id)
                if warn_key not in self._warned_keys:
                    self._warned_keys.add(warn_key)
                    print(f"[Client] 建造 {build_id} 成功但本端资源不足于 cost"
                          f"（缺 {drift}），已按 0 对齐并记限频告警一次")
            for item_id, need in cost.items():
                left = int(resource.get(item_id, 0) or 0) - int(need)
                if left > 0:
                    resource[item_id] = left
                else:
                    resource.pop(item_id, None)
            carried["resource"] = resource
            return
        build_id = payload.get("build_id")
        reason = payload.get("reason") or "无法建造"
        # 主机拒绝原因为 BuildSystem.can_place 的中文文案（边界/距离/占用/资源不足），
        # 直接展示即可；未知 reason 前缀（防御分支）也照原样显示，不静默吞掉
        print(f"[Client] 建造请求被主机拒绝：build_id={build_id!r} reason={reason}")
        from entities.build_defs import BUILDS
        build_name = BUILDS.get(build_id, {}).get("name", str(build_id))
        gs.build_mode = False  # 退出建造模式：拒绝后别留在原地反复按（build_mode 在 game_state 上）
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           f"{build_name}建造失败：{reason}", arcade.color.RED,
                           life=2.0, font_size=16)

    # ── 拒绝型 ACK：交互 / 放弃 ────────────────────────────────────────────
    def handle_interaction_result(self, payload: dict) -> None:
        """客户端应用主机 INTERACTION_RESULT（拒绝型 ACK）：仅提示被拒原因

        - ok=True：交互已生效，主机经 MAP_CHANGE 广播结果（宝箱开启/水井用过/发射台启动），
          客户端据此镜像状态，此处不重复处理；
        - ok=False：显示主机拒绝原因（距离太远/无资源/被占用），让玩家知道为何无响应
          ——此前只有请求没有应答，玩家只能空等。
        """
        gs = self.gv.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        player_id = payload.get("player_id")
        if my_id is not None and player_id != my_id:
            return  # 他人的交互结果：本端无动作（禁按他人结果改自己状态）
        if payload.get("ok"):
            return  # 成功：效果已由 MAP_CHANGE 广播落地
        reason = payload.get("reason") or "无法交互"
        print(f"[Client] 交互请求被主机拒绝：type={payload.get('interaction_type')!r} "
              f"reason={reason}")
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           f"交互失败：{reason}", arcade.color.RED, life=2.0, font_size=16)

    def handle_player_abandon_result(self, payload: dict) -> None:
        """客户端应用主机 PLAYER_ABANDON_RESULT（拒绝型 ACK）

        - ok=True：主机已受理放弃（状态标记 dead，清装备回房由主机 ROOM_ENDED 收口），
          本端无额外动作（此前无回执，玩家不知道主机是否受理，只会干等）；
        - ok=False：本局已结束/状态非法等原因被拒 → 提示原因，玩家可继续游戏或返回大厅。
        """
        gs = self.gv.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        player_id = payload.get("player_id")
        if my_id is not None and player_id != my_id:
            return  # 他人的受理结果：本端无动作
        if payload.get("ok"):
            return  # 已受理：清装备/回房由主机 ROOM_ENDED 统一收口
        reason = payload.get("reason") or "放弃请求未被受理"
        print(f"[Client] 放弃行动被主机拒绝：{reason}")
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           f"放弃失败：{reason}", arcade.color.RED, life=2.0, font_size=16)

    # ── 连接/房间级事件 ───────────────────────────────────────────────────
    def handle_disconnect(self, payload: dict) -> None:
        """任一端收到 DISCONNECT：清理该 peer 的幽灵并提示

        - peer_id 是断线者（不是收件人，见 net/protocol.py DISCONNECT schema）；
        - 忽略 peer_id == 本端 id（主机自己的连接关闭也会广播，若按对端处理会误清自己）；
        - 断线玩家从 remote_players / _downed_players / _player_status 一并清理，
          否则残留幽灵会继续被怪物 AI 追击、且卡在 _check_all_finished 的存活判定上。
        """
        gs = self.gv.window.game_state
        peer_id = payload.get("peer_id")
        my_id = getattr(gs, "net_player_id", None)
        if peer_id is None:
            return  # 非法载荷：缺 peer_id，显式返回（无 peer 可清）
        if my_id is not None and peer_id == my_id:
            return  # 本端连接关闭通知：不是「他人断线」，不做对端清理
        if self.gv.remote_players.pop(peer_id, None) is not None:
            self.gv._downed_players.pop(peer_id, None)
            self.gv._player_status[peer_id] = "left"
            reason = payload.get("reason") or "connection_closed"
            print(f"[NetSync] 玩家 {peer_id} 断连（{reason}），已清理其幽灵与状态")
            if peer_id != 0:
                # 断线幽灵消失需要提示：玩家可能正与对方交战中突然对手不见了
                floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                                   f"玩家 {peer_id} 已断线", arcade.color.YELLOW,
                                   life=2.0, font_size=16)

    def handle_room_error(self, payload: dict) -> None:
        """任一端收到 ROOM_ERROR：提示原因后回大厅等待（连接保留/断开由 _back_to_lobby 收口）

        reason 为主机给出的错误描述（如 client_disconnect / join_validation_failed）；
        显式走 _back_to_lobby 而不是静默停在半死的游戏视图里。
        """
        reason = payload.get("reason") or "房间错误"
        print(f"[NetSync] 收到 ROOM_ERROR：{reason}")
        self.gv._back_to_lobby(f"房间错误：{reason}")