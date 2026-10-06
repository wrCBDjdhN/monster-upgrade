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
    ROOM_STATUS = "ROOM_STATUS"    # 主机→全部：房间内玩家对局状态周期广播（房间页三态显示：在局→游戏中）
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
    # ── 怪物技能 debuff（怪物技能命中/范围命中后由主机单播给被影响者本人）──
    SKILL_DEBUFF = "SKILL_DEBUFF"    # 主机→指定玩家：该玩家被怪物技能 debuff 命中（范围技能无伤害命中时唯一送达通道）
    # ── 药水 ──
    POTION_USE = "POTION_USE"        # 客户端→主机：药水使用请求
    POTION_ACK = "POTION_ACK"        # 主机→全部：药水使用确认
    # ── 拾取 ──
    PICKUP_REQUEST = "PICKUP_REQUEST"  # 客户端→主机：拾取请求（主机仲裁先到先得）
    PICKUP_RESULT = "PICKUP_RESULT"    # 主机→全部：拾取确认/拒绝
    # ── 撤离 ──
    EVAC_REQUEST = "EVAC_REQUEST"      # 客户端→主机：撤离完成请求
    EVAC_RESULT = "EVAC_RESULT"        # 主机→各端：撤离结算清单（各端据此本地入库）
    # ── 阶段2 防守式撤离点 ──
    EVAC_POINT_STATE = "EVAC_POINT_STATE"   # 主机→全部：撤离点权威状态（状态变化时 + 1s 周期广播）
    EVAC_POINT_ACTION = "EVAC_POINT_ACTION"  # 客户端→主机：撤离点激活/修复请求（主机校验距离+资源后执行）
    EVAC_POINT_RESULT = "EVAC_POINT_RESULT"  # 主机→请求者单播回执（撤离点激活/修复受理结果）
    # ── 商队（Carriage） ──
    CARRIAGE_BUY = "CARRIAGE_BUY"                # 客户端→主机：商队购买请求
    CARRIAGE_BUY_RESULT = "CARRIAGE_BUY_RESULT"  # 主机→请求者单播回执
    # ── 交互（宝箱/水井/火箭发射台）──
    INTERACTION_REQUEST = "INTERACTION_REQUEST"  # 客户端→主机：交互请求（宝箱/水井/火箭台）
    INTERACTION_RESULT = "INTERACTION_RESULT"    # 主机→请求者：交互结果（拒绝型 ACK，此前只有请求无应答）
    # ── 倒地 / 救援 ──
    PLAYER_DOWNED = "PLAYER_DOWNED"      # 主机→全部：广播某玩家倒地（可被救援）
    RESCUE_REQUEST = "RESCUE_REQUEST"    # 客户端→主机：请求救援倒地玩家
    RESCUE_RESULT = "RESCUE_RESULT"      # 主机→全部：救援结果（成功/失败）
    PLAYER_REVIVED = "PLAYER_REVIVED"    # 主机→全部：广播玩家复活成功
    SPECTATE_LEAVE = "SPECTATE_LEAVE"    # 客户端→主机：主动退出观战（视为主机判定真死）
    # ── 放弃行动 ──
    PLAYER_ABANDON = "PLAYER_ABANDON"  # 客户端→主机：放弃行动通知（主机更新状态触发全员结束判定）
    PLAYER_ABANDON_RESULT = "PLAYER_ABANDON_RESULT"  # 主机→请求者：放弃行动受理结果（拒绝型 ACK）
    # ── 运行期同步 ──
    MAP_CHANGE = "MAP_CHANGE"          # 主机→全部：运行期地图改动（宝箱/环境物/水井/火箭台/建筑/燃烧区）
    # ── 局内建造请求-应答（阶段1 建造的联机接线，此前只有 MAP_CHANGE 广播无请求应答）──
    BUILD_REQUEST = "BUILD_REQUEST"    # 客户端→主机：放置建筑请求（主机裁决并广播 MAP_CHANGE build_place）
    BUILD_RESULT = "BUILD_RESULT"      # 主机→请求者：放置结果单播（成功带 bid，失败带 reason）
    ACTION_TIME = "ACTION_TIME"        # 主机→全部：剩余行动时间周期广播（客户端 HUD 显示）
    FULL_STATE = "FULL_STATE"          # 主机→晚期加入客户端：全量状态快照
    # ── 阶段4 随机地图事件 ──
    EVENT_START = "EVENT_START"        # 主机→全部：本局随机事件抽中结果（客户端只镜像横幅/参数，不本地抽选）
    # ── 阶段6.2 任务进度 ──
    MISSION_PROGRESS = "MISSION_PROGRESS"  # 主机→指定玩家：该玩家的任务/成就进度（局内事件唯一计数端=主机）
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
        "  'protocol': int,  协议版本号（当前值见 net/server.py 的 PROTOCOL_VERSION）\n"
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
        "房间错误通知：请求者视角为单播（服务器 send_room_error 只发给触发失败的那个 peer，\n"
        "即「死消息实装」）；房间级故障仍可由主机 broadcast() 广播全房。各端据此回大厅并展示错误。\n"
        "payload: {\n"
        "  'reason': str,  错误描述（如：client_disconnect / join_validation_failed）\n"
        "}\n"
    ),
    MsgType.ROOM_STATUS: (
        "主机周期广播房间内各玩家的对局状态（NET_ROOM_STATUS_INTERVAL 节拍），供房间页\n"
        "三态显示：alive/downed（在局中）显示「游戏中」，evac/dead/left 或未登记显示准备状态。\n"
        "主机在局内或后台对局（提前退出观战回房）期间持续广播；各端写 game_state.room_player_status。\n"
        "payload: {\n"
        "  'players': list[dict], 每项：\n"
        "      {'player_id': int,  玩家 id\n"
        "       'status': str,     对局状态：alive/downed=在局中；evac/dead/left=已结束/离开\n"
        "       'name': str}       玩家名（net_roster 查询，兜底 P{id}）\n"
        "}\n"
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
        "       'alive': bool,                         是否存活\n"
        "       'dmg_count': int|None,                 伤害计数（可选，客户端→主机）：\n"
        "                 客户端已应用的本人 PLAYER_HURT 次数（damage>0 才计）；\n"
        "                 主机比对幽灵 _dmg_seq，落后即陈旧上报 → 跳过 hp/max_hp\n"
        "                 采纳（防连续受伤血量回弹）；缺省 None 按旧口径直接采纳\n"
        "       'stats': dict|None,                    含祝福的有效属性（阶段5）\n"
        "                 {'max_hp','defense','char_speed_mult','regen_per_sec',\n"
        "                  'crit_chance','lifesteal','thorns','damage_mult'}}\n"
        "             本人属性快照（客户端上报 / 主机转发幽灵）；主机按绝对值\n"
        "             套用到幽灵承伤，不做倍率叠乘（避免与 ATTACK_EVENT 重复乘伤害）\n"
        "  'blessings': list[str], 可选（客户端→主机方向使用），本局已获得的祝福 id 列表\n"
        "                 （主机据此同步各端玩家的祝福展示；缺省时按未获得任何祝福处理，\n"
        "                 旧主机不下发该字段时各端退回本地祝福状态，不会崩）\n"
        "}\n"
    ),
    MsgType.MONSTER_SNAPSHOT: (
        "怪物快照（20Hz 全量广播），客户端按 net_id 增删改 + 插值渲染。\n"
        "载荷新增字段均为向后兼容的可选消费项：客户端一律 .get() 取默认值，\n"
        "旧主机不下发时退回本地 MONSTER_CONFIGS 默认值，不会崩。\n"
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
        "       'affix': str|None,      阶段3 精英词缀 id（None=普通怪；客户端渲染词缀名前缀）\n"
        "       'is_elite': bool,       阶段3 是否精英怪（客户端小地图紫点标记）\n"
        "       'attack_anim': float,   攻击动画计时（主机内部 _attack_timer 原值，旧客户端兜底用）\n"
        "       'shield': float,        当前护盾值（阶段3 精英护盾词缀；0=无护盾，客户端据此画护盾条）\n"
        "       'max_shield': float,    护盾上限（客户端按 shield/max_shield 画护盾条比例）\n"
        "       'damage': float,        攻击力（词缀/等级修正后的实际伤害，主机权威值）\n"
        "       'aggro_range': float,   仇恨探测距离（像素，词缀/等级修正后的生效值）\n"
        "       'attack_delay': float,  攻击冷却总时长（秒）\n"
        "       'attack_cd_ratio': float,  攻击冷却剩余比例（0~1，1=刚攻击完；客户端按此\n"
        "                              线性衰减画冷却条，免去客户端硬套本地 _attack_delay）\n"
        "       'skill_prompt_text': str|None,  技能提示文字（怪物施法头顶飘字）\n"
        "       'skill_prompt_color': list|None, 技能提示颜色 [r,g,b]\n"
        "       'skill_prompt_timer': float,    技能提示剩余显示时间（秒，客户端本地递减）\n"
        "       'skill_vfx_timer': float,       技能范围圈特效剩余时间（秒，客户端本地递减）\n"
        "       'skill_vfx_duration': float,    技能范围圈总时长（秒，画进度比例用）\n"
        "       'skill_vfx_radius': float,      技能范围圈半径（像素）\n"
        "       'skill_vfx_color': list|None,   技能范围圈颜色 [r,g,b]\n"
        "       'skill_buff_type': str|None,   怪物自身 buff 类型（berserk/bone_shield/\n"
        "                              tactical_retreat，None=无；客户端据此画 buff 标记）\n"
        "       'skill_buff_timer': float}     自身 buff 剩余时间（秒，客户端本地递减）\n"
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
        "  'attack_speed': float} 可选，客户端所持武器攻速（连发/激光武器以主机时序为准时，\n"
        "                          客户端据本地武器攻速上报；缺省时主机退回武器定义默认攻速）\n"
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
        "  'debuff': str|None,  附加 debuff 名（旧主机单值字段，保留向后兼容）\n"
        "  'debuff_level': int|None,  debuff 等级（旧主机单值字段，默认 1）\n"
        "  'debuffs': list}     全量附加效果列表 [(效果ID, 等级), ...]（主机权威下发；\n"
        "                      客户端遍历逐个施加，空/缺失时回退读 debuff/debuff_level）\n"
        "}"
    ),
    MsgType.SKILL_DEBUFF: (
        "主机→指定玩家：该玩家被怪物**范围技能**的 debuff 命中，客户端据此施加效果。\n"
        "为什么需要独立消息：范围技能（沙尘暴/手雷投掷）对半径内所有玩家生效，\n"
        "但范围内玩家可能完全没被弹丸命中（没有 PLAYER_HURT），只靠 PLAYER_HURT\n"
        "会静默丢失；弹丸也只对首个命中目标结算伤害，掠过中途的玩家收不到。\n"
        "「恰好一次」的两通道分工：命中类技能 debuff 只进 PLAYER_HURT.debuffs，\n"
        "范围技能 debuff 只进本消息，两者互斥，客户端不会对同一效果施加两次。\n"
        "payload: {\n"
        "  'player_id': int,  被影响玩家 id（客户端仅在自己 id 匹配时施加）\n"
        "  'debuffs': list}   附加效果列表 [(效果ID, 等级), ...]\n"
        "}"
    ),
    MsgType.PLAYER_DEATH: (
        "主机判定玩家死亡后仅发给该玩家，该客户端走 _fail_run 并离开房间。\n"
        "payload: {\n"
        "  'player_id': int,         死亡玩家 id\n"
        "  'killer_id': int|None}    击杀者 id（怪物 net_id / 玩家 id，未知为 None）\n"
        "}"
    ),
    MsgType.PLAYER_DOWNED: (
        "主机广播某玩家倒地（HP 归零但可被救援），倒地玩家保留装备，等待队友救援。\n"
        "payload: {\n"
        "  'player_id': int,         倒地玩家 id\n"
        "  'x': float, 'y': float}  倒地位置坐标（客户端渲染用）\n"
        "}"
    ),
    MsgType.RESCUE_REQUEST: (
        "客户端请求救援倒地玩家（靠近后按 E 键触发），主机裁决距离并广播结果。\n"
        "payload: {\n"
        "  'rescuer_id': int,    救援者玩家 id\n"
        "  'target_id': int}     被救援者玩家 id\n"
        "}"
    ),
    MsgType.RESCUE_RESULT: (
        "主机广播救援结果（成功/失败），成功时被救者 HP 恢复为 10。\n"
        "payload: {\n"
        "  'target_id': int,      被救援者玩家 id\n"
        "  'rescuer_id': int,     救援者玩家 id\n"
        "  'success': bool,       是否成功\n"
        "  'hp': float}           复活后 HP（成功时为 10，失败时为 0）\n"
        "}"
    ),
    MsgType.PLAYER_REVIVED: (
        "主机广播玩家复活成功（全部端收到后恢复该玩家实体）。\n"
        "payload: {\n"
        "  'player_id': int,    复活玩家 id\n"
        "  'hp': float}         复活后 HP（固定 10）\n"
        "}"
    ),
    MsgType.SPECTATE_LEAVE: (
        "客户端通知主机主动退出观战（倒地超时/玩家选择），主机标记该玩家真死并清装备。\n"
        "payload: {\n"
        "  'player_id': int}    退出观战的玩家 id\n"
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
        "  'heal_amount': float, 实际治疗量\n"
        "  'for_peer': int}      可选，仅发给指定 player_id 的单播标识（走服务器 send_to 通道）；\n"
        "                 缺省/为 None 时维持既有广播语义（全房可见治疗数字）"
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
        "  'player_id': int,\n"
        "  'carried': dict}     可选，客户端上报**本人实际携带清单**（本端视角 run_carried）。\n"
        "                       槽结构与 EVAC_RESULT.run_carried 同口径（=\n"
        "                       commit_run_to_warehouse 入参），逐槽：\n"
        "                         'gold': int（标量槽）\n"
        "                         'resource'/'potion': {item_id: qty}\n"
        "                         'weapon'/'helmet'/'armor'/'backpack': {(item_id, level): qty}\n"
        "                       装备槽键为 (item_id, level) **元组**，JSON 无元组，\n"
        "                       线上传输前需 JSON 化（发送方定编码、收方同口径还原）。\n"
        "\n"
        "                       语义：客户端购买走 CARRIAGE_BUY 请求-回执、主机校验记账后回 CARRIAGE_BUY_RESULT。\n"
        "                       账本随本次撤离请求一并上行，由主机裁决后经 _merge_authoritative\n"
        "                       做键级差集合流（权威优先、不重复计入、金币不走合流）。\n"
        "                       缺省时行为与旧版完全一致（主机只按自己的清单结算）。\n"
        "}"
    ),
    MsgType.EVAC_RESULT: (
        "主机下发撤离结算清单：各端（含主机）按清单调用 commit_run_to_warehouse 写本地库。\n"
        "payload: {\n"
        "  'player_id': int,    撤离玩家 id\n"
        "  'run_carried': dict, 本次携带物清单（结构 = commit_run_to_warehouse 入参口径）\n"
        "  'stars': int}        可选，本次撤离评定的星级（星级评价仅主机权威计算，\n"
        "                       各端据此更新地图星级进度；缺省时按 0 星处理）\n"
    ),
    MsgType.EVAC_POINT_STATE: (
        "主机广播主撤离点权威状态（阶段2 防守式撤离）。\n"
        "客户端不跑 EvacPoint.update 与波次，只按本消息镜像并渲染倒计时/血量。\n"
        "payload: {\n"
        "  'x': float, 'y': float,   撤离点世界坐标\n"
        "  'state': str,             'dormant'/'defending'/'secured'/'destroyed'\n"
        "  'hp': float,              当前血量\n"
        "  'max_hp': float,          满血值\n"
        "  'defend_left': float,     防守剩余秒数\n"
        "  'wave_no': int}           已打波数\n"
        "}"
    ),
    MsgType.EVAC_POINT_ACTION: (
        "客户端请求激活/修复主撤离点（阶段2）；主机校验距离与资源后执行并广播 EVAC_POINT_STATE。\n"
        "payload: {\n"
        "  'player_id': int,         请求玩家 id\n"
        "  'action': str,            'activate'/'repair'\n"
        "  'x': float, 'y': float}   请求时玩家世界坐标（主机判距防作弊）\n"
    ),
    MsgType.EVAC_POINT_RESULT: (
        "主机→请求者单播回执（撤离点激活/修复受理结果）。\n"
        "payload: {\n"
        "  'player_id': int,         请求玩家 id\n"
        "  'ok': bool,               是否受理成功\n"
        "  'reason': str|None,       拒绝原因（ok=False 时有值）\n"
        "  'action': str}            'activate'/'repair'\n"
    ),
    MsgType.CARRIAGE_BUY: (
        "客户端→主机，商队购买请求。\n"
        "payload: {\n"
        "  'player_id': int,         请求玩家 id\n"
        "  'kind': str,              'potion'|'weapon'|'artifact'\n"
        "  'item_id': str,           物品 id\n"
        "  'level': int,             武器/神器词条等级，药水填 0\n"
        "  'qty': int,               购买数量\n"
        "  'gold_before': float,     购买前金币\n"
        "  'gold_after': float}      购买后金币（报文自洽校验用）\n"
    ),
    MsgType.CARRIAGE_BUY_RESULT: (
        "主机→请求者单播回执。\n"
        "payload: {\n"
        "  'player_id': int,         请求玩家 id\n"
        "  'ok': bool,               是否购买成功\n"
        "  'reason': str|None,       拒绝原因（ok=False 时有值）\n"
        "  'kind': str,              'potion'|'weapon'|'artifact'\n"
        "  'item_id': str,           物品 id\n"
        "  'qty': int}               购买数量\n"
    ),
    MsgType.INTERACTION_REQUEST: (
        "客户端请求与环境物交互（宝箱/水井/火箭发射台），主机裁决后广播 MAP_CHANGE。\n"
        "payload: {\n"
        "  'player_id': int,     请求交互的玩家 id\n"
        "  'interaction_type': str,  交互类型（'chest'/'well'/'rocket_pad'）\n"
        "  'x': float, 'y': float}  请求交互时玩家世界坐标（主机判距防作弊）\n"
        "}"
    ),
    MsgType.INTERACTION_RESULT: (
        "主机单播交互结果给请求者（拒绝型 ACK：此前 INTERACTION_REQUEST 只有请求没有应答，客户端\n"
        "分不清「被主机拒绝」与「请求在链路上丢了」，只能空等）。\n"
        "payload: {\n"
        "  'player_id': int,         请求交互的玩家 id\n"
        "  'interaction_type': str,  交互类型（'chest'/'well'/'rocket_pad'）\n"
        "  'ok': bool,               是否受理成功（False = 被主机拒绝）\n"
        "  'reason': str|None}       拒绝原因（ok=False 时有值，如 too_far/no_resource/occupied）"
    ),
    MsgType.PLAYER_ABANDON: (
        "客户端通知主机放弃行动（清装备回房），主机更新 _player_status 触发全员结束判定。\n"
        "payload: {\n"
        "  'player_id': int,    放弃行动的玩家 id\n"
        "  'reason': str}       放弃原因（如 '放弃行动'）\n"
        "}"
    ),
    MsgType.PLAYER_ABANDON_RESULT: (
        "主机单播放弃行动受理结果给请求者（拒绝型 ACK：此前 PLAYER_ABANDON 只有通知没有应答，\n"
        "客户端无法确认主机是否受理）。\n"
        "payload: {\n"
        "  'player_id': int,     放弃行动的玩家 id\n"
        "  'ok': bool,           是否受理（False = 被主机拒绝，如本局已结束）\n"
        "  'reason': str|None}   拒绝原因（ok=False 时有值）"
    ),
    MsgType.MAP_CHANGE: (
        "主机广播运行期地图改动（宝箱开启/可破坏环境物/水井/火箭台状态/建筑/燃烧区）。\n"
        "11.1 接线核查：change_type 全集为\n"
        "  chest_opened/env_destroyed/well_used/rocket_pad/env_damage/env_spawn/drop_spawn\n"
        "  + chest_spawn/caravan_point（阶段4 事件实体）\n"
        "  + build_place/build_destroy/build_hp（阶段1 局内建造：build_hp = 建筑受损，载荷含 bid+hp）\n"
        "  + fire_zone（阶段3 火墙词缀区）\n"
        "（客户端 _apply_map_change 对未列出的 change_type 显式记日志，禁静默忽略）\n"
        "payload: {\n"
        "  'obj_id': str,        地图对象网络 id（列表序号或建筑 bid/燃烧区 zid）\n"
        "  'change_type': str,   改动类型（见上全集）\n"
        "  'state': dict,        新状态数据（如宝箱内容/剩余血量/倒计时；env_damage 时含 damage 字段；\n"
        "                        build_hp 时含 {hp}，即建筑当前血量（obj_id=建筑 bid，与 build_place 同口径）；\n"
        "                        build_place 时含 {kind,x,y,hp}；fire_zone 时含 {action,zid,x,y,r,life,\n"
        "                        dps,burn_duration}，action='add'/'remove'）\n"
        "  'extra': dict}        扩展字段（如掉落物生成列表，可为空 dict）\n"
        "}"
    ),
    MsgType.BUILD_REQUEST: (
        "客户端请求放置局内建筑（阶段1 建造的联机接线：此前客户端只能收到 MAP_CHANGE 广播，\n"
        "放置请求没有任何上行通道）。主机校验资源/占用/边界后置入建造系统，广播\n"
        "MAP_CHANGE build_place（含新建筑 bid），再单播 BUILD_RESULT 给请求者。\n"
        "payload: {\n"
        "  'build_id': str,         建筑类型 id（entities/build_defs.BUILDS 的 key）\n"
        "  'x': float, 'y': float}  放置点世界坐标（主机判占用/边界用，防越界建造）"
    ),
    MsgType.BUILD_RESULT: (
        "主机单播放置结果给请求者（成功带新建筑 bid，失败带 reason）。\n"
        "payload: {\n"
        "  'build_id': str,     建筑类型 id（同 BUILD_REQUEST.build_id）\n"
        "  'ok': bool,         是否放置成功\n"
        "  'bid': str|None,    新建筑网络 id（ok=True 时有值，与 MAP_CHANGE build_place 的 obj_id 同口径）\n"
        "  'reason': str|None} 拒绝原因（ok=False 时有值，如 no_resource/occupied/out_of_range）"
    ),
    MsgType.FULL_STATE: (
        "主机发给晚期加入客户端的全量世界状态，客户端据此初始化后只收增量快照。\n"
        "payload: {\n"
        "  'monsters': list[dict],     全部存活怪物（格式同 MONSTER_SNAPSHOT 的 monsters）\n"
        "  'drops': list[dict],        当前地面掉落物（id/类型/x/y/内容）\n"
        "  'chests': list[dict],       宝箱状态（id/是否已开/坐标/is_airdrop/theme/artifact_bonus）\n"
        "  'env_objects': list[dict],  环境物状态（水井/火箭台/可破坏物）\n"
        "  'buildings': list[dict],     局内建筑（阶段1；每项 {bid, kind, x, y, hp}，\n"
        "                       bid 与 MAP_CHANGE build_place/build_destroy 的 obj_id 同口径）\n"
        "  'well_opened': bool,        水井是否已首次开启（晚加入客户端镜像）\n"
        "  'evac_point': dict|None,    主撤离点状态（格式同 EVAC_POINT_STATE；None=尚未建立）\n"
        "  'event_id': str,            阶段4 本局随机事件 id（'' = 无事件；晚加入客户端补看横幅）\n"
        "  'event_flags': dict,        阶段4 事件参数（格式同 EVENT_START 的 flags）\n"
        "  'action_time_left': float}  剩余行动时间（秒）\n"
    ),
    MsgType.ACTION_TIME: (
        "主机周期广播剩余行动时间（NET_ACTION_TIME_BCAST_SEC 间隔），客户端据此更新 HUD。\n"
        "倒计时递减只在主机（主机权威），客户端本地不递减、只以广播值为准——\n"
        "修复「客户端行动时间卡死不动」：此前只靠 FULL_STATE 初始化一次，之后从不更新。\n"
        "payload: {\n"
        "  'action_time_left': float}  剩余行动时间（秒）\n"
    ),
    MsgType.EVENT_START: (
        "主机开局广播本局随机事件抽中结果（阶段4 随机地图事件）。\n"
        "抽选只在主机/单机执行（地图种子派生，教程局不抽），客户端收到后仅镜像\n"
        "event_id + 事件参数到 view.event_flags 并显示 3 秒横幅（纯表现层，禁本地抽选）。\n"
        "事件实体（空投补给箱/商队交互点）不走本消息，由 MAP_CHANGE 的\n"
        "chest_spawn / caravan_point 增量广播（两端口径与顺序号一致）。\n"
        "payload: {\n"
        "  'event_id': str,       本局事件 id（tide/airdrop/caravan/relic；'' = 无事件）\n"
        "  'flags': dict}         事件参数 {monster_cap_mult, reward_mult, artifact_bonus}\n"
    ),
    MsgType.MISSION_PROGRESS: (
        "主机→指定玩家：该玩家的任务/成就进度增量（阶段6.2 联机双端口径）。\n"
        "防双计铁律：局内战斗事件（kill/elite_kill/harvest/chest/evac）唯一计数端=主机，\n"
        "主机按归属裁决（击杀按 last_attacker_id、采集/开箱按发起者、撤离按结算玩家）后\n"
        "单播给归属客户端，客户端据此写自己的本地库；客户端永不自计局内战斗事件。\n"
        "本消息为单向（主机→客户端）：客户端上报同名消息一律显式忽略（防伪造他人进度）。\n"
        "注意：'player_id' 是**联机传输 id**（主机 0、客户端 1~3），不是 DB 主键——\n"
        "各端独立本地库，主机无从得知客户端的 DB player_id。\n"
        "进度不纳入 FULL_STATE：各端进度落各自本地库，无需全量同步。\n"
        "payload: {\n"
        "  'player_id': int,  归属玩家的联机传输 id\n"
        "  'event': str,      事件键（kill/elite_kill/harvest/chest/evac/forge/reforge）\n"
        "  'amount': int}     本次增量次数\n"
    ),
    MsgType.HEARTBEAT: (
        "双向心跳保活与延迟测量（NET_HEARTBEAT_SEC=1.0 间隔发送）。\n"
        "payload: {\n"
        "  'seq': int,         递增序号（供对端回应确认）\n"
        "  'client_time': float} 发送方时间戳（秒，用于计算 RTT）\n"
    ),
    MsgType.DISCONNECT: (
        "断线通知（服务器广播给房内其余玩家：peer_id 是断线者，不是收件人）。\n"
        "payload: {\n"
        "  'peer_id': int,   断线玩家 id（对端，不是本端）\n"
        "  'reason': str}    断线原因（如 connection_closed）\n"
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


def build_ready_state_payload(players_info: list[tuple[int, str, int]],
                              ready_state: dict,
                              host_name: str) -> dict:
    """组装 READY_STATE 广播载荷：主机名册 + 权威就绪表 → {"players": [...]}。

    供主机两处 READY 广播共用（LobbyView._broadcast_ready_state 房间页 +
    GameView._handle_host_inbound 对局中 READY 分支，②修复），避免载荷
    结构在两处复制而漂移。

    - players_info: NetServer.player_info() 的 [(player_id, name, slot)]；
    - ready_state: GameState.net_ready_state 权威表 {player_id: bool}；
    - host_name: 主机玩家名（player_id=0 恒就绪）。
    """
    players = [{"player_id": 0, "ready": True, "name": host_name}]
    for pid, name, _ in players_info:
        players.append({"player_id": pid,
                        "ready": bool(ready_state.get(pid, False)),
                        "name": name})
    return {"players": players}
