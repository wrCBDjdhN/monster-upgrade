"""联机消息协议层 —— JSON 帧编解码与消息类型定义（局域网联机 Wave 1 · 任务 2）

帧格式严格统一为：{"type": "<MsgType.name>", "payload": {...}}
- encode(msg_type, payload) -> str：编码消息为 JSON 帧字符串
- decode(raw) -> tuple[MsgType, dict]：解码并校验，非法/未知类型一律抛 ValueError
- MESSAGE_SCHEMAS：每类消息的载荷 schema（键名 + 含义），中文说明

设计约定：
- type 字段 = MsgType 枚举名，与枚举成员一一对应；
- payload 一律为 dict，键名与含义见 MESSAGE_SCHEMAS；
- 本层只负责序列化/反序列化与消息类型校验，不包含任何网络收发逻辑
  （网络收发由 net/server.py / net/client.py 负责）。
"""

from __future__ import annotations

import json
from enum import Enum

__all__ = ["MsgType", "MESSAGE_SCHEMAS", "encode", "decode"]


class MsgType(Enum):
    """联机消息类型枚举：成员名 = 线上传输的 type 字段值"""

    # ── 握手 / 加入 ──
    HELLO = "HELLO"                # 客户端→主机：连接建立后的身份握手
    JOIN = "JOIN"                  # 客户端→主机：加入房间请求
    JOIN_ACCEPT = "JOIN_ACCEPT"    # 主机→客户端：同意加入（含分配 id / 槽位）
    JOIN_REJECT = "JOIN_REJECT"    # 主机→客户端：拒绝加入（满员 / 房间不存在等）
    # ── 房间状态机 ──
    ROOM_START = "ROOM_START"      # 主机→全部：房间开始（含 seed/theme，客户端据此重建地图）
    ROOM_ENDED = "ROOM_ENDED"      # 主机→全部：房间结束（含原因，各端回大厅）
    ROOM_ERROR = "ROOM_ERROR"      # 主机→全部：房间错误（断线 / 校验失败等）
    # ── 玩家准备 ──
    READY = "READY"                # 客户端→主机：准备/取消准备（开始游戏前全员就绪）
    READY_STATE = "READY_STATE"    # 主机→全部：全员准备状态广播（大厅/房间内同步显示）
    # ── 角色（角色系统：房间内选角 + 开局同步 + 局内技能）──
    SET_CHARACTER = "SET_CHARACTER"  # 客户端→主机：上报选择的角色（开局前，主机记入玩家槽位）
    SKILL_USE = "SKILL_USE"          # 客户端→主机：上报角色技能释放（主机裁决效果并广播）
    # ── 快照（20Hz 全量广播）──
    PLAYER_SNAPSHOT = "PLAYER_SNAPSHOT"              # 玩家实体快照
    MONSTER_SNAPSHOT = "MONSTER_SNAPSHOT"            # 怪物快照
    PROJECTILE_SNAPSHOT = "PROJECTILE_SNAPSHOT"      # 弹丸快照
    # ── 攻击 / 伤害 ──
    ATTACK_EVENT = "ATTACK_EVENT"    # 客户端→主机：上报攻击动作（主机收敛判定）
    DAMAGE_RESULT = "DAMAGE_RESULT"  # 主机→全部：伤害判定结果
    PLAYER_HURT = "PLAYER_HURT"      # 主机→全部：玩家受伤（HP 主机权威）
    PLAYER_DEATH = "PLAYER_DEATH"    # 主机→该玩家：玩家死亡
    # ── 药水 ──
    POTION_USE = "POTION_USE"        # 客户端→主机：药水使用请求
    POTION_ACK = "POTION_ACK"        # 主机→全部：药水使用确认
    # ── 拾取 ──
    PICKUP_REQUEST = "PICKUP_REQUEST"  # 客户端→主机：拾取请求（主机仲裁先到先得）
    PICKUP_RESULT = "PICKUP_RESULT"    # 主机→全部：拾取确认/拒绝
    # ── 撤离 ──
    EVAC_REQUEST = "EVAC_REQUEST"      # 客户端→主机：撤离完成请求
    EVAC_RESULT = "EVAC_RESULT"        # 主机→各端：撤离结算清单（各端据此本地入库）
    # ── 交互（宝箱/水井/火箭发射台）──
    INTERACTION_REQUEST = "INTERACTION_REQUEST"  # 客户端→主机：交互请求（宝箱/水井/火箭台）
    # ── 放弃行动 ──
    PLAYER_ABANDON = "PLAYER_ABANDON"  # 客户端→主机：放弃行动通知（主机更新状态触发全员结束判定）
    # ── 运行期同步 ──
    MAP_CHANGE = "MAP_CHANGE"          # 主机→全部：运行期地图改动（宝箱/环境物/水井/火箭台）
    ACTION_TIME = "ACTION_TIME"        # 主机→全部：剩余行动时间周期广播（客户端 HUD 显示）
    FULL_STATE = "FULL_STATE"          # 主机→晚期加入客户端：全量状态快照
    # ── 保活 / 断线 ──
    HEARTBEAT = "HEARTBEAT"            # 双向：心跳保活 + 延迟测量
    DISCONNECT = "DISCONNECT"          # 双向：主动断线通知
    # ── 局域网房间发现 ──
    ROOM_BROADCAST = "ROOM_BROADCAST"  # 主机→广播：房间信息（UDP 广播，客户端搜索用）
    ROOM_QUERY = "ROOM_QUERY"          # 客户端→广播：请求房间信息（UDP 广播，触发主机回复）


# 每类消息的载荷 schema（键名 + 含义），供 server/client/game_view 联机逻辑参考
MESSAGE_SCHEMAS: dict[MsgType, str] = {
    MsgType.HELLO: (
        "客户端连接建立后发送的身份握手消息。\n"
        "payload: {\n"
        "  'protocol': int,  协议版本号（本协议版本为 1）\n"
        "  'name': str,      玩家名（用于显示与存档区分）\n"
        "}"
    ),
    MsgType.JOIN: (
        "客户端请求加入房间。\n"
        "payload: {\n"
        "  'name': str,      玩家名\n"
        "  'room_id': str,   目标房间号（加入默认房间可省略）\n"
        "}"
    ),
    MsgType.JOIN_ACCEPT: (
        "主机同意客户端加入，下发分配的玩家身份与房间参数。\n"
        "payload: {\n"
        "  'player_id': int,  分配的唯一玩家 id\n"
        "  'slot': int,       玩家槽位号（0-3，用于出生点偏移）\n"
        "  'room_id': str,    所在房间号\n"
        "}"
    ),
    MsgType.JOIN_REJECT: (
        "主机拒绝客户端加入。\n"
        "payload: {\n"
        "  'reason': str,  拒绝原因（如：房间已满员 / 房间不存在）\n"
        "}"
    ),
    MsgType.ROOM_START: (
        "主机广播房间开始：客户端据 seed/theme 本地重建静态地图并进入 game_view。\n"
        "payload: {\n"
        "  'seed': int,    地图随机种子（确定性重建地图）\n"
        "  'theme': str,   地图主题（forest/desert/space）\n"
        "  'players': list[dict], 全部玩家出生信息，每项：\n"
        "      {'player_id': int, 'name': str, 'slot': int, 'x': float, 'y': float,\n"
        "       'character_id': str}  角色 id（initial/mage/knight/assassin，开局前选定，幽灵创建用）\n"
        "}"
    ),
    MsgType.ROOM_ENDED: (
        "主机广播房间结束：各端（含主机）回到大厅。\n"
        "payload: {\n"
        "  'reason': str,  结束原因：\n"
        "      host_evac/host_evac_fail/host_fail  主机撤离/死亡/超时（本局结束，房间保留）\n"
        "      all_finished  全员结束（主机+全部客户端均已撤离/死亡，回房等待再次开局）\n"
        "      room_closed   主机选择关闭房间（服务器停止，全员回主菜单）\n"
        "}"
    ),
    MsgType.READY: (
        "客户端上报准备状态（开始游戏前的全员就绪机制）。\n"
        "payload: {\n"
        "  'player_id': int,  上报玩家 id\n"
        "  'ready': bool}     是否已准备\n"
    ),
    MsgType.READY_STATE: (
        "主机广播全员准备状态（房间内各端同步显示，供大厅开始游戏条件判定）。\n"
        "payload: {\n"
        "  'players': list[dict], 每项：\n"
        "      {'player_id': int, 'ready': bool, 'name': str}  玩家 id / 是否已准备 / 玩家名\n"
        "}"
    ),
    MsgType.SET_CHARACTER: (
        "客户端上报选择的角色（房间内开局前选定；主机记入该玩家槽位，ROOM_START 下发全房）。\n"
        "payload: {\n"
        "  'player_id': int,     上报玩家 id\n"
        "  'character_id': str}  角色 id（initial/mage/knight/assassin，须为已解锁角色）\n"
    ),
    MsgType.SKILL_USE: (
        "客户端上报角色技能释放（F 键），主机裁决技能效果（伤害/位移/护盾）并广播。\n"
        "payload: {\n"
        "  'player_id': int,   施放技能玩家 id\n"
        "  'x': float, 'y': float,  施放瞬间玩家世界坐标（主机据此修正幽灵位置）\n"
        "  'mouse_x': float, 'mouse_y': float,  鼠标目标点世界坐标（技能朝向/落点方向）\n"
        "  'damage': float}    当前武器伤害（技能伤害以武器伤害为基数，倍率按角色定义）\n"
    ),
    MsgType.ROOM_ERROR: (
        "主机广播房间错误：各端回到大厅并展示错误。\n"
        "payload: {\n"
        "  'reason': str,  错误描述（如：client_disconnect / join_validation_failed）\n"
        "}"
    ),
    MsgType.PLAYER_SNAPSHOT: (
        "玩家实体快照（双向，可复用）：\n"
        "  主机→全部：20Hz 全量广播（主机本地玩家 + 全部客户端幽灵），客户端据此插值\n"
        "            渲染远端玩家并校准 HP（与 MONSTER_SNAPSHOT 同节拍）；\n"
        "  客户端→主机：单向上报本人（players 仅含自己一项），主机据此更新幽灵的位置/\n"
        "            HP/武器/朝向（Todo 23 玩家实体同步，报文格式完全一致）。\n"
        "payload: {\n"
        "  'players': list[dict]，每项：\n"
        "      {'player_id': int, 'x': float, 'y': float,\n"
        "       'hp': float, 'max_hp': float,          玩家当前/最大 HP\n"
        "       'weapon': str|None,                    当前武器名\n"
        "       'facing': float,                       朝向角度（弧度）\n"
        "       'alive': bool}                         是否存活\n"
        "}"
    ),
    MsgType.MONSTER_SNAPSHOT: (
        "怪物快照（20Hz 全量广播），客户端按 net_id 增删改 + 插值渲染。\n"
        "payload: {\n"
        "  'monsters': list[dict]，每项：\n"
        "      {'net_id': int,          主机单调分配的怪物网络 id\n"
        "       'monster_type': str,    怪物类型名\n"
        "       'x': float, 'y': float, 位置\n"
        "       'hp': float, 'max_hp': float,  当前/最大 HP\n"
        "       'weapon': str|None,     携带武器名\n"
        "       'weapon_color': list|None, 武器颜色 [r,g,b]（客户端渲染用，修复装备不同步）\n"
        "       'weapon_level': int|None,  武器等级（客户端标签显示）\n"
        "       'armor': str|None,      护甲名（客户端渲染护甲层，修复装备不同步）\n"
        "       'armor_color': list|None, 护甲颜色 [r,g,b]\n"
        "       'helmet': str|None,     头盔名（客户端渲染头盔层）\n"
        "       'helmet_color': list|None, 头盔颜色 [r,g,b]\n"
        "       'debuff': str|None,     当前 debuff 名\n"
        "       'attack_anim': float}   攻击动画计时\n"
        "}"
    ),
    MsgType.PROJECTILE_SNAPSHOT: (
        "弹丸快照：客户端纯表现层渲染（不计算碰撞）。怪物弹丸 + 玩家弹丸 + 玩家激光。\n"
        "payload: {\n"
        "  'projectiles': list[dict]，每项：\n"
        "      {'proj_id': int,         弹丸网络 id\n"
        "       'owner_id': int,        发射者 id（怪物 net_id 或玩家 id）\n"
        "       'x': float, 'y': float, 当前位置\n"
        "       'vx': float, 'vy': float, 速度分量\n"
        "       'damage': float,        伤害值\n"
        "       'debuff': str|None,     弹丸附带 debuff 名\n"
        "       'color': list|None}     弹丸颜色 [r,g,b]（修复弹丸颜色不同步）\n"
        "  'lasers': list[dict]，每项（玩家激光）：\n"
        "      {'proj_id': int,         激光网络 id\n"
        "       'owner_id': int,        发射者玩家 id（客户端跳过自己的，本地已有表现）\n"
        "       'x': float, 'y': float, 发射点位置\n"
        "       'angle': float,         朝向角（弧度）\n"
        "       'damage': float,        伤害值\n"
        "       'length': float,        长度\n"
        "       'width': float,         宽度\n"
        "       'duration': float}      剩余存在时长\n"
        "}"
    ),
    MsgType.ATTACK_EVENT: (
        "客户端上报本地攻击动作，主机负责实际命中判定（攻击判定收敛主机）。\n"
        "payload: {\n"
        "  'attacker_id': int,   攻击者玩家 id\n"
        "  'weapon': str,        使用武器名\n"
        "  'angle': float,       攻击方向角度（弧度）\n"
        "  'x': float, 'y': float, 攻击瞬间玩家世界坐标（主机据此修正幽灵位置，\n"
        "                          避免 20Hz 快照滞后导致近战/远程判定 miss）\n"
        "  'damage': float,      实际伤害（升级武器以客户端为准，主机据此裁决）\n"
        "  'debuffs': list,      客户端装备/武器附加 debuff 列表 [(效果ID, 等级), ...]\n"
        "  'timestamp': float,   客户端时间戳（毫秒，用于去重/延迟测量）\n"
        "}"
    ),
    MsgType.DAMAGE_RESULT: (
        "主机广播伤害判定结果，各端据此更新怪物血量显示。\n"
        "payload: {\n"
        "  'target_id': int,    受击目标 id（怪物 net_id 或玩家 id）\n"
        "  'damage': float,     实际伤害值\n"
        "  'hit': bool,         是否命中\n"
        "  'crit': bool,        是否暴击\n"
        "  'debuffs': list}     附加 debuff 列表 [(效果ID, 等级), ...]（修复客户端特殊效果不全生效）\n"
        "}"
    ),
    MsgType.PLAYER_HURT: (
        "主机广播玩家受伤事件（HP 主机权威），客户端本地扣血 + 受击反馈。\n"
        "payload: {\n"
        "  'player_id': int,    受伤玩家 id\n"
        "  'damage': float,     伤害量\n"
        "  'debuff': str|None,  附加 debuff 名\n"
        "  'debuff_level': int|None}  debuff 等级（默认 1，修复 debuff 等级不同步）\n"
        "}"
    ),
    MsgType.PLAYER_DEATH: (
        "主机判定玩家死亡后仅发给该玩家，该客户端走 _fail_run 并离开房间。\n"
        "payload: {\n"
        "  'player_id': int,         死亡玩家 id\n"
        "  'killer_id': int|None}    击杀者 id（怪物 net_id / 玩家 id，未知为 None）\n"
        "}"
    ),
    MsgType.POTION_USE: (
        "客户端请求使用药水，主机确认后生效并广播（治疗数字全端可见）。\n"
        "payload: {\n"
        "  'player_id': int,    使用药水的玩家 id\n"
        "  'potion_id': str,    药水 item_id\n"
        "  'potion_name': str}  药水名（用于显示）\n"
        "}"
    ),
    MsgType.POTION_ACK: (
        "主机广播药水使用确认结果。\n"
        "payload: {\n"
        "  'player_id': int,    使用药水的玩家 id\n"
        "  'potion_id': str,    药水 item_id\n"
        "  'accepted': bool,    是否生效（False = 血量已满/库存不足被拒）\n"
        "  'heal_amount': float} 实际治疗量\n"
        "}"
    ),
    MsgType.PICKUP_REQUEST: (
        "客户端请求拾取地面掉落物，主机仲裁（距离 < 拾取半径 且 先到先得）。\n"
        "payload: {\n"
        "  'player_id': int,   请求拾取的玩家 id\n"
        "  'item_id': str,     掉落物网络 id\n"
        "  'x': float,         拾取瞬间玩家世界坐标 x（主机据此判距，避免幽灵快照滞后误判）\n"
        "  'y': float,         拾取瞬间玩家世界坐标 y\n"
        "  'item_type': str}   掉落物类型（resource/gold/weapon/equipment）\n"
        "}"
    ),
    MsgType.PICKUP_RESULT: (
        "主机广播拾取确认/拒绝：客户端乐观更新 run_carried，收到拒绝时回滚纠正。\n"
        "payload: {\n"
        "  'player_id': int,     拾取玩家 id\n"
        "  'item_id': str,       掉落物网络 id\n"
        "  'accepted': bool,     是否拾取成功\n"
        "  'reason': str|None}   拒绝原因（accepted=False 时有值，如 too_far/already_taken）\n"
        "}"
    ),
    MsgType.EVAC_REQUEST: (
        "客户端读条完成后请求撤离结算（read 条由客户端本地播放）。\n"
        "payload: {\n"
        "  'player_id': int}  请求撤离的玩家 id\n"
        "}"
    ),
    MsgType.EVAC_RESULT: (
        "主机下发撤离结算清单：各端（含主机）按清单调用 commit_run_to_warehouse 写本地库。\n"
        "payload: {\n"
        "  'player_id': int,    撤离玩家 id\n"
        "  'run_carried': dict} 本次携带物清单（结构 = commit_run_to_warehouse 入参口径）\n"
        "}"
    ),
    MsgType.INTERACTION_REQUEST: (
        "客户端请求与环境物交互（宝箱/水井/火箭发射台），主机裁决后广播 MAP_CHANGE。\n"
        "payload: {\n"
        "  'player_id': int,     请求交互的玩家 id\n"
        "  'interaction_type': str,  交互类型（'chest'/'well'/'rocket_pad'）\n"
        "  'x': float, 'y': float}  请求交互时玩家世界坐标（主机判距防作弊）\n"
        "}"
    ),
    MsgType.PLAYER_ABANDON: (
        "客户端通知主机放弃行动（清装备回房），主机更新 _player_status 触发全员结束判定。\n"
        "payload: {\n"
        "  'player_id': int,    放弃行动的玩家 id\n"
        "  'reason': str}       放弃原因（如 '放弃行动'）\n"
        "}"
    ),
    MsgType.MAP_CHANGE: (
        "主机广播运行期地图改动（宝箱开启/可破坏环境物/水井/火箭台状态）。\n"
        "payload: {\n"
        "  'obj_id': str,        地图对象网络 id\n"
        "  'change_type': str,   改动类型（chest_opened/env_destroyed/well_used/rocket_pad/env_damage）\n"
        "  'state': dict,        新状态数据（如宝箱内容/剩余血量/倒计时；env_damage 时含 damage 字段）\n"
        "  'extra': dict}        扩展字段（如掉落物生成列表，可为空 dict）\n"
        "}"
    ),
    MsgType.FULL_STATE: (
        "主机发给晚期加入客户端的全量世界状态，客户端据此初始化后只收增量快照。\n"
        "payload: {\n"
        "  'monsters': list[dict],     全部存活怪物（格式同 MONSTER_SNAPSHOT 的 monsters）\n"
        "  'drops': list[dict],        当前地面掉落物（id/类型/x/y/内容）\n"
        "  'chests': list[dict],       宝箱状态（id/是否已开）\n"
        "  'env_objects': list[dict],  环境物状态（水井/火箭台/可破坏物）\n"
        "  'well_opened': bool,        水井是否已首次开启（晚加入客户端镜像）\n"
        "  'action_time_left': float}  剩余行动时间（秒）\n"
    ),
    MsgType.ACTION_TIME: (
        "主机周期广播剩余行动时间（NET_ACTION_TIME_BCAST_SEC 间隔），客户端据此更新 HUD。\n"
        "倒计时递减只在主机（主机权威），客户端本地不递减、只以广播值为准——\n"
        "修复「客户端行动时间卡死不动」：此前只靠 FULL_STATE 初始化一次，之后从不更新。\n"
        "payload: {\n"
        "  'action_time_left': float}  剩余行动时间（秒）\n"
    ),
    MsgType.HEARTBEAT: (
        "双向心跳保活与延迟测量（NET_HEARTBEAT_SEC=1.0 间隔发送）。\n"
        "payload: {\n"
        "  'seq': int,         递增序号（供对端回应确认）\n"
        "  'client_time': float} 发送方时间戳（秒，用于计算 RTT）\n"
        "}"
    ),
    MsgType.DISCONNECT: (
        "主动断线通知（退出/被踢/死亡离开房间）。\n"
        "payload: {\n"
        "  'player_id': int,   断线玩家 id\n"
        "  'reason': str}      断线原因\n"
        "}"
    ),
    MsgType.ROOM_BROADCAST: (
        "主机 UDP 广播房间信息（局域网房间发现）。\n"
        "payload: {\n"
        "  'room_id': str,        房间号\n"
        "  'host_name': str,      主机玩家名\n"
        "  'theme': str,          地图主题（forest/desert/space）\n"
        "  'player_count': int,   当前玩家数（含主机）\n"
        "  'max_players': int,    最大玩家数\n"
        "  'port': int}           WebSocket 服务端口\n"
        "}"
    ),
    MsgType.ROOM_QUERY: (
        "客户端 UDP 广播请求房间信息（触发主机回复 ROOM_BROADCAST）。\n"
        "payload: {\n"
        "  'query': str}  固定值 'discover'（预留扩展）\n"
        "}"
    ),
}


def encode(msg_type: MsgType, payload: dict) -> str:
    """把消息编码为 JSON 帧字符串。

    帧格式：{"type": "<MsgType.name>", "payload": {...}}
    """
    if not isinstance(msg_type, MsgType):
        raise ValueError(f"非法消息类型: {msg_type!r}")
    if not isinstance(payload, dict):
        raise ValueError(f"payload 必须是 dict，实际为 {type(payload).__name__}")
    return json.dumps(
        {"type": msg_type.name, "payload": payload},
        ensure_ascii=False,  # 玩家名等中文原样输出，便于抓包调试
        sort_keys=True,      # 键排序使相同载荷编码结果稳定（encode→decode→encode 幂等）
    )


def decode(raw: str) -> tuple[MsgType, dict]:
    """把 JSON 帧字符串解码为 (MsgType, payload)。

    校验规则（任一不满足均抛 ValueError）：
    - raw 必须是 str，且 JSON 可解析为 dict；
    - 必须包含 'type' 与 'payload' 两个键；
    - 'type' 必须是 MsgType 的合法枚举名——未知类型绝不静默忽略。
    """
    if not isinstance(raw, str):
        raise ValueError(f"消息必须是 str，实际为 {type(raw).__name__}")
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(frame, dict):
        raise ValueError(f"帧必须是 JSON 对象，实际为 {type(frame).__name__}")
    type_name = frame.get("type")
    payload = frame.get("payload")
    if not isinstance(type_name, str):
        raise ValueError(f"帧缺少字符串字段 'type'，实际为 {type_name!r}")
    try:
        msg_type = MsgType[type_name]
    except KeyError:
        # 未知消息类型必须显式抛错，禁止静默忽略（协议层硬性约定）
        raise ValueError(f"未知消息类型: {type_name!r}") from None
    if not isinstance(payload, dict):
        raise ValueError(f"payload 必须是 dict，实际为 {type(payload).__name__}")
    return msg_type, payload
