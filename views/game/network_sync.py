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
    PROJECTILE_SIZE,  # 标准弹丸边长（客户端远端弹丸纯表现层渲染尺寸）
    DROP_PICKUP_RADIUS,  # 掉落物拾取半径（主机拾取仲裁距离阈值，Todo 18）
    LIFESTEAL_DEFAULT, SPREAD_COUNT_DEFAULT, SPREAD_ANGLE_DEFAULT,  # 武器扩展机制默认值（吸血/散射）
    AURA_SLOW_TICK, AURA_SLOW_LEVEL,  # 攻速光环：减速结算周期与效果等级
    # 等级系统：击杀/撤离经验常量（客户端击杀结算用）
    EXP_KILL_BASE, EXP_BOSS_MULT, EXP_EVAC,
    # 倒地/救援系统
    DOWNED_TIMEOUT, RESCUE_DISTANCE, RESCUE_DURATION, REVIVE_HP,
    CACTUS_THORN_DAMAGE,  # 仙人掌反伤：客户端近战攻击环境物命中时作用于攻击者（幽灵）
    ROCKET_PAD_INTERACT_RANGE,  # 火箭发射台交互距离
    CHEST_WELL_INTERACT_RANGE,  # 宝箱/水井交互距离
    HIT_FLASH_DURATION,  # 受击闪白时长（远端怪物受击反馈）
)
from net.protocol import MsgType  # 联机消息类型枚举（MONSTER_SNAPSHOT 等）
from game.player import Player
from game.loot import DropItem
from game.harvestable import HarvestableEntity
from game.evac import commit_run_to_warehouse, clear_run
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
        attacker_id = payload.get("attacker_id", sender_id)
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
        # 伤害优先采用客户端上报的实际伤害（修复联机假伤害 8/1）：主机模板 damage 是
        # 基础值，客户端武器可能已升级（gs.weapon_damage 更高），直接按模板裁决会让
        # 升级武器在联机时伤害退回基础值；客户端是本人武器的权威源，与幽灵 HP 采纳同口径。
        damage = float(payload.get("damage") or wdef.get("damage", 8))
        speed = wdef.get("attack_speed", 1.0)
        wrange = wdef.get("range", 40)
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
                            on_harvestable_destroyed(self.gv, h, award_exp=False)
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
        caster_id = payload.get("player_id", sender_id)
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
        from game.character_skills import use_skill
        use_skill(self.gv, caster,
                  float(payload.get("mouse_x") or 0),
                  float(payload.get("mouse_y") or 0),
                  damage, broadcast=True)

    def _apply_damage_result(self, payload: dict) -> None:
        """客户端应用主机下发的 DAMAGE_RESULT：远端怪物按 net_id 扣血并显示命中反馈

        - 目标缺失（怪物已被击杀/快照尚未到达）时静默忽略；
        - 主机权威血量以 MONSTER_SNAPSHOT 为准，此处扣血仅做即时显示，下一帧快照校准。
        """
        target_id = payload.get("target_id")
        if target_id is None:
            return
        rm = self.gv.remote_monsters.get(target_id)
        if rm is None:
            return  # 目标缺失（远端怪物已删除/快照未达）：忽略
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
                _award_exp(self.gv, amount)
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
            ghost.hp = min(ghost.max_hp, round(entry.get("hp", ghost.hp), 2))
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
            # 校验并扣减该玩家的 run 药水库存：
            # - 本人（主机）：gs.run_potions；
            # - 客户端：_players_run_carried[sender_id]["potion"]（主机记录的拾取清单）。
            if sender_id == getattr(gs, "net_player_id", None):
                run_potions = getattr(gs, "run_potions", None) or {}
                if run_potions.get(item_id, 0) <= 0:
                    self.gv._reject_potion(sender_id, potion_id)
                    return
                run_potions[item_id] -= 1
                if run_potions[item_id] <= 0:
                    del run_potions[item_id]
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
            return  # 非法请求：无掉落物网络 id
        # 在主机权威掉落列表中查找目标掉落物
        target = next((d for d in self.gv.drops if d.net_id == net_id), None)
        if target is None:
            # 已不存在：被其他玩家先拾取/已过期/从未生成 → 拒绝
            gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
                "player_id": sender_id, "item_id": net_id,
                "accepted": False, "reason": "already_taken",
            })
            return
        # 距离校验：优先用客户端上报的拾取瞬间坐标（与客户端本地 try_pickup 判定口径一致，
        # 避免幽灵位置 20Hz 快照滞后导致正常范围内的拾取被误判为 too_far）；
        # 未携带坐标（旧协议/异常）时回退幽灵位置兜底
        req_x, req_y = payload.get("x"), payload.get("y")
        if req_x is not None and req_y is not None:
            dist = math.hypot(req_x - target.center_x,
                              req_y - target.center_y)
        else:
            ghost = self._ensure_ghost(sender_id)
            dist = math.hypot(ghost.center_x - target.center_x,
                              ghost.center_y - target.center_y)
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
        gs.net_server.broadcast(MsgType.PICKUP_RESULT, {
            "player_id": sender_id, "item_id": net_id,
            "accepted": True, "reason": None,
        })

    def _handle_evac_request(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端撤离请求（B12）：下发主机权威携带物清单，广播 EVAC_RESULT

        - 数据源 = _players_run_carried[sender_id]（主机拾取仲裁成功时累加，口径与
          GameState.run_carried 一致，包含 tuple 键的装备类条目）；
        - 广播 EVAC_RESULT 含 "player_id" + "run_carried"，各端（含请求客户端）据此
          调用 commit_run_to_warehouse 写各自本地库（每端写自己的库）；
        - run_carried 的 tuple 键先经 _serialize_evac_carried 转为 "id|level" 字符串
          （json 序列化安全），客户端 _apply_evac_result 内 _deserialize_evac_carried 还原。
        """
        gs = self.gv.window.game_state
        if gs.net_server is None:
            return  # 非主机：理论不可达（inbound 仅主机消费），防御性返回
        carried = self.gv._players_run_carried.get(sender_id, {})
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

    def _apply_map_change(self, payload: dict) -> None:
        """客户端应用主机 MAP_CHANGE：运行期地图改动（掉落生成/宝箱/环境物/水井/火箭台）

        - drop_spawn（Todo 18）：掉落物生成广播 → 本地创建视觉掉落物（带主机 net_id）；
        - chest_opened / env_destroyed / well_used / rocket_pad（Todo 20，D2）：
          obj_id = 列表序号（与 FULL_STATE 的 chests/env_objects id 同口径，客户端确定性
          重建的地图对象顺序与主机一致），直接把本地对象与主机权威状态对齐——
          宝箱标记已开（渲染/碰撞跳过）、环境物标记已摧毁、水井首次开启、
          火箭台镜像状态机与撤离倒计时（客户端不本地推进，纯表现层）。
        """
        change_type = payload.get("change_type")
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
                # 兼容旧版单值快照（仅 run_carried）与新版 (carried, potions) 元组快照
                if isinstance(snap, tuple):
                    gs.run_carried, gs.run_potions = snap
                else:
                    gs.run_carried = snap
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
        # 按主机权威清单还原并入库（tuple 键还原为入参口径）
        run_carried = self.gv._deserialize_evac_carried(payload.get("run_carried") or {})
        commit_run_to_warehouse(gs.player_id, run_carried)
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

    def _apply_spectate_leave(self, payload: dict) -> None:
        """主机收到 SPECTATE_LEAVE：玩家主动退出观战 → 视为真死，清装备"""
        gs = self.gv.window.game_state
        player_id = payload.get("player_id")
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

    def _handle_rescue_request(self, sender_id: int, payload: dict) -> None:
        """主机处理 RESCUE_REQUEST：裁决距离并执行救援"""
        gs = self.gv.window.game_state
        rescuer_id = payload.get("rescuer_id")
        target_id = payload.get("target_id")
        # 校验：被救者必须处于倒地状态
        if self.gv._player_status.get(target_id) != "downed":
            return
        # 校验：救援者必须存活
        if self.gv._player_status.get(rescuer_id) != "alive":
            return
        # 获取被救者位置
        downed_info = self.gv._downed_players.get(target_id)
        if downed_info is None:
            return
        target_x, target_y = downed_info["x"], downed_info["y"]
        # 获取救援者位置
        if rescuer_id == 0:
            rescuer_x, rescuer_y = self.gv.player.center_x, self.gv.player.center_y
        else:
            ghost = self.gv.remote_players.get(rescuer_id)
            if ghost is None:
                return
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
        if self.gv.window.game_state.net_mode != "host":
            return

        player_id = payload.get("player_id", sender_id)
        interaction_type = payload.get("interaction_type", "")
        request_x = float(payload.get("x", 0))
        request_y = float(payload.get("y", 0))

        print(f"[Host] 收到交互请求: player={player_id}, type={interaction_type}, pos=({request_x},{request_y})")

        # 获取请求者幽灵（用于距离校验）
        ghost = self.gv.remote_players.get(player_id)
        if ghost is None:
            print(f"[Host] 幽灵不存在: player={player_id}")
            return  # 幽灵不存在，忽略

        # 用客户端上报的位置校验距离（与 ATTACK_EVENT 同口径）
        ghost.center_x = request_x
        ghost.center_y = request_y

        if interaction_type == "chest":
            # 找到最近的未开启宝箱
            for i, chest in enumerate(self.gv.chests):
                if chest.opened:
                    continue
                dist = math.hypot(chest.center_x - request_x, chest.center_y - request_y)
                if dist < CHEST_WELL_INTERACT_RANGE:
                    # 执行开箱
                    from game.entity_callbacks import spawn_chest_loot, _award_exp
                    from config import EXP_CHEST
                    loot = chest.open_chest()
                    spawn_chest_loot(self.gv, chest, loot)
                    _award_exp(self.gv, EXP_CHEST)
                    print(f"[GameView] 客户端 {player_id} 开启宝箱 {i}")
                    break

        elif interaction_type == "well":
            well = self.gv.map_data.get("water_well")
            if well:
                dist = math.hypot(well[0] - request_x, well[1] - request_y)
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

        elif interaction_type == "rocket_pad":
            for i, pad in enumerate(self.gv.rocket_pads):
                dist = math.hypot(pad.center_x - request_x, pad.center_y - request_y)
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
                    elif pad.state == "boss_defeated":
                        # 标记可选择状态（7/8键选择）
                        self.gv._rocket_pad_menu = pad
                        print(f"[GameView] 客户端 {player_id} 打开火箭台菜单 {i}")
                    break

    def _handle_player_abandon(self, sender_id: int, payload: dict) -> None:
        """主机处理客户端放弃行动通知：更新 _player_status → 触发全员结束判定

        客户端调用 _fail_run 后发送此消息，主机更新状态后 _check_all_finished
        可正确判定全员结束，广播 ROOM_ENDED 回房。
        """
        gs = self.gv.window.game_state
        if gs.net_mode != "host" or gs.net_server is None:
            return

        player_id = payload.get("player_id", sender_id)
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
          net_id / monster_type / x / y / hp / max_hp / weapon / debuff / attack_anim。
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
                "attack_anim": getattr(m, "_attack_timer", 0.0),
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

        - 已有 net_id → 原位更新（位置/HP/武器名/debuff/攻击动画）；
        - 新 net_id → 新建远端怪物对象：复用真实怪物类实例（仅作渲染数据容器，
          永不调用 update/try_attack，AI/碰撞由主机权威执行），不加入 self.monsters；
        - 本端存在但快照缺失的 net_id → 主机已击杀/移除，删除。
        插值渲染留待后续任务（B3），当前直接应用快照值。
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
                self.gv.remote_monsters[net_id] = rm
            # 原位更新核心表现数据（直接应用，不插值）
            rm.center_x = entry.get("x", rm.center_x)
            rm.center_y = entry.get("y", rm.center_y)
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
        # 删除本端存在但快照缺失的怪物（主机已击杀/移除）
        for net_id in list(self.gv.remote_monsters):
            if net_id not in snapshot_ids:
                del self.gv.remote_monsters[net_id]

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
        chests = [{"id": i, "opened": c.opened,
                   "x": c.center_x, "y": c.center_y}
                  for i, c in enumerate(self.gv.chests)]
        # 环境物（可采集物/水井等）：存活状态 + 资源类型
        env_objects = [{"id": i, "resource_type": getattr(h, "resource_type", ""),
                        "alive": getattr(h, "alive", True),
                        "x": h.center_x, "y": h.center_y}
                       for i, h in enumerate(self.gv.harvestables)]
        return {
            "monsters": self._serialize_monsters(),
            "drops": drops,
            "chests": chests,
            "env_objects": env_objects,
            # 水井首次开启状态（仅沙漠主题）：晚期加入客户端据此镜像 _well_opened
            "well_opened": self.gv._well_opened,
            "action_time_left": self.gv._action_time_remaining or 0.0,
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
        """
        if not isinstance(payload, dict):
            return
        self._apply_monster_snapshot({"monsters": payload.get("monsters", [])})
        t = payload.get("action_time_left")
        if t is not None:
            self.gv._action_time_remaining = t
            self.gv.window.game_state.action_time_remaining = t
        # 水井状态：主机已首次开启 → 本地镜像（渲染/提示与主机一致）
        if payload.get("well_opened"):
            self.gv._well_opened = True
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
        # 宝箱状态：已开宝箱标记 opened（渲染不绘制、不可再开）
        for entry in payload.get("chests", []) or []:
            cid = entry.get("id")
            if isinstance(cid, int) and 0 <= cid < len(self.gv.chests) and entry.get("opened"):
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
        # 保留原始列表供调试/扩展使用（后续渲染直接读本地坐标对象状态）
        self.gv._late_chests = payload.get("chests", [])
        self.gv._late_env = payload.get("env_objects", [])