"""局域网联机连通性统一自检脚本（局域网联机 Wave 2 · 任务 7）。

单脚本串联覆盖三类场景（对应交付标准）：
1. 场景1 · 服务器 + 2 客户端收发：启动 NetServer，2 个真实 WebSocket 客户端
   完成 HELLO+JOIN 握手（JOIN_ACCEPT 含互不相同的 player_id），随后验证：
   - 客户端→服务器：客户端发 HEARTBEAT，主线程经 bridge.poll() 收到入站封装；
   - 服务器→客户端：send_to() 单播 / broadcast() 广播 / broadcast(exclude) 排除。
2. 场景2 · 满员拒绝：3 个客户端入座（槽位 0 保留给主机）后，第 4 个连接
   收到 JOIN_REJECT（原因含"满员"），房间人数保持 3。
3. 场景3 · 断线感知：客户端主动断开后触发 on_disconnect 回调、房间人数-1；
   服务器 stop() 优雅关闭，线程退出无 hang。
4. 场景4 · 协议层自检（阶段11 · Task 11.2 Step 1）：本版本新消息的 encode/decode
   往返（阶段1/3 MAP_CHANGE 新 change_type、阶段2 EVAC_POINT_STATE/ACTION、
   阶段4 EVENT_START、阶段5 PLAYER_SNAPSHOT.stats、阶段6 MISSION_PROGRESS、
   FULL_STATE 扩展）+ MESSAGE_SCHEMAS 覆盖与键名校验 + 非法帧/非法入参必须抛错。
5. 场景5 · 阶段2 撤离点请求-应答流：客户端 EVAC_POINT_ACTION → 主机入站封装 →
   主机广播 EVAC_POINT_STATE → 客户端单发单收并校验 7 个状态字段。
6. 场景6 · 阶段6 任务进度单播应用：主机单播 MISSION_PROGRESS → 客户端解码应用
   本人进度 +1；他人 player_id / 未知事件键一律忽略、进度不变（防双计铁律）。

六类场景各自独立运行（各自独立服务器实例 + 动态自由端口；场景4 为纯协议用例，
不启服务器），任一场景失败只影响自身的 PASS/FAIL 记录；
最终退出码 = 0 全部通过 / 1 任一场景失败。

net 层零耦合铁律：本脚本只 import net 包，绝不 import views/game 模块；
「客户端如何应用 MISSION_PROGRESS」以本脚本内的契约镜像函数
（_apply_mission_progress，与 game/mission_tracker.apply_remote_progress 同口径）表达。

运行方式：python net/_selftest.py
"""

import asyncio
import os
import socket
import sys
import time
from collections.abc import Awaitable, Callable

# 保证 net 包可被导入：把项目根目录（本文件上一级）加入 sys.path
# （python net/_selftest.py 直接运行时，net/ 在 sys.path，父目录不在）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from net.protocol import MESSAGE_SCHEMAS, MsgType, decode, encode
    from net.server import NetServer
    from net.thread_bridge import NetBridge
except ImportError:
    # 兜底：以顶层模块方式导入（net/ 已在 sys.path 时走此分支）
    from protocol import MESSAGE_SCHEMAS, MsgType, decode, encode  # pyright: ignore[reportMissingImports]
    from server import NetServer  # pyright: ignore[reportMissingImports]
    from thread_bridge import NetBridge  # pyright: ignore[reportMissingImports]

from websockets.asyncio.client import ClientConnection, connect  # type: ignore[attr-defined]

TEST_HOST = "127.0.0.1"   # 本机回环自检
MAX_PLAYERS = 4           # 与服务器默认槽位上限一致
MSG_TIMEOUT = 5.0         # 单条消息等待超时（秒）
POLL_INTERVAL = 0.01      # bridge 轮询间隔（秒）
QUIET_WINDOW = 0.3        # 「静默窗口」：断言一发一收时，多余消息的观察时长（秒）


def _pick_free_port() -> int:
    """探测一个系统分配的可用端口（绑定 0 端口获取随机端口号后立即释放，供本测试监听）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((TEST_HOST, 0))
        return int(sock.getsockname()[1])


async def _handshake_join(port: int, name: str) -> tuple[ClientConnection, int]:
    """连接并完成 HELLO+JOIN 握手：返回 (连接, player_id)。失败抛断言。"""
    ws = await connect(f"ws://{TEST_HOST}:{port}")
    await ws.send(encode(MsgType.HELLO, {"protocol": 1, "name": name}))
    await ws.send(encode(MsgType.JOIN, {"name": name}))
    msg_type, payload = decode(
        await asyncio.wait_for(ws.recv(), timeout=MSG_TIMEOUT)
    )
    assert msg_type == MsgType.JOIN_ACCEPT, f"{name} 应收到 JOIN_ACCEPT，实际 {msg_type} {payload}"
    player_id = payload["player_id"]
    assert isinstance(player_id, int), f"JOIN_ACCEPT 缺少整数 player_id: {payload}"
    assert 0 <= payload["slot"] < MAX_PLAYERS, f"槽位越界: {payload}"
    return ws, player_id


async def _handshake_reject(port: int, name: str) -> str:
    """连接一个期望被拒绝的客户端（房间已满）：返回拒绝原因。"""
    ws = await connect(f"ws://{TEST_HOST}:{port}")
    await ws.send(encode(MsgType.HELLO, {"protocol": 1, "name": name}))
    await ws.send(encode(MsgType.JOIN, {"name": name}))
    msg_type, payload = decode(
        await asyncio.wait_for(ws.recv(), timeout=MSG_TIMEOUT)
    )
    assert msg_type == MsgType.JOIN_REJECT, f"{name} 应收到 JOIN_REJECT，实际 {msg_type} {payload}"
    await ws.close()
    return payload["reason"]


async def _wait_inbound(
    bridge: NetBridge,
    predicate: Callable[[dict], bool],
    timeout: float = MSG_TIMEOUT,
) -> dict:
    """轮询 bridge.poll() 直到出现满足 predicate 的入站消息并返回它。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for msg in bridge.poll():
            if isinstance(msg, dict) and predicate(msg):
                return msg
        await asyncio.sleep(POLL_INTERVAL)
    raise AssertionError(f"超时 {timeout}s 未收到满足条件的入站消息")


def _wait_until(predicate: Callable[[], bool], timeout: float = MSG_TIMEOUT) -> bool:
    """同步轮询直到 predicate 为 True 或超时（用于检查回调副作用）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_INTERVAL)
    return False


# ── 阶段11 · Task 11.2：新增消息的协议契约常量（纯数据，照 net/protocol.py 的 schema 抄写）──
EVAC_POINT_STATES = ("dormant", "defending", "secured", "destroyed")   # 撤离点 4 态
EVAC_POINT_ACTIONS = ("activate", "repair")                           # 撤离点 2 动作
# 任务/成就事件键全集：镜像 entities/mission_defs.MISSION_EVENT_KEYS
# （net 自检不 import entities/game，避免与游戏层耦合；键集变更需同步此处）
MISSION_EVENT_KEYS = (
    "kill", "elite_kill", "harvest", "chest", "evac", "forge", "reforge",
)

# 新消息载荷样例（encode/decode 往返用例）：覆盖本版本新增/扩展的消息
_ROUNDTRIP_CASES: tuple[tuple[str, MsgType, dict], ...] = (
    # 阶段2 防守式撤离：状态广播（defending 中态，7 字段齐全）
    ("EVAC_POINT_STATE/defending", MsgType.EVAC_POINT_STATE, {
        "x": 640.0, "y": 360.0, "state": "defending",
        "hp": 1000.0, "max_hp": 1000.0, "defend_left": 60.0, "wave_no": 1,
    }),
    # 阶段2：撤离点被拆（客户端只镜像，不本地跑 update 与波次）
    ("EVAC_POINT_STATE/destroyed", MsgType.EVAC_POINT_STATE, {
        "x": 640.0, "y": 360.0, "state": "destroyed",
        "hp": 0.0, "max_hp": 1000.0, "defend_left": 0.0, "wave_no": 3,
    }),
    # 阶段2：激活请求（客户端→主机，主机判距 + 校验资源后执行并广播状态）
    ("EVAC_POINT_ACTION/activate", MsgType.EVAC_POINT_ACTION, {
        "player_id": 1, "action": "activate", "x": 641.5, "y": 358.25,
    }),
    # 阶段2：修复请求
    ("EVAC_POINT_ACTION/repair", MsgType.EVAC_POINT_ACTION, {
        "player_id": 2, "action": "repair", "x": 700.0, "y": 400.0,
    }),
    # 阶段4 随机事件：开局广播抽中结果（含事件参数）
    ("EVENT_START/tide", MsgType.EVENT_START, {
        "event_id": "tide",
        "flags": {"monster_cap_mult": 1.5, "reward_mult": 1.2, "artifact_bonus": 0.1},
    }),
    # 阶段4：无事件（event_id='' → 客户端不显示横幅）
    ("EVENT_START/无事件", MsgType.EVENT_START, {"event_id": "", "flags": {}}),
    # 阶段6 任务进度：主机单播给归属客户端（event 取自 MISSION_EVENT_KEYS）
    ("MISSION_PROGRESS/kill", MsgType.MISSION_PROGRESS, {
        "player_id": 1, "event": "kill", "amount": 1,
    }),
    ("MISSION_PROGRESS/elite_kill", MsgType.MISSION_PROGRESS, {
        "player_id": 3, "event": "elite_kill", "amount": 2,
    }),
    # 阶段1/3 MAP_CHANGE 新 change_type：协议层按不透明字符串透传（枚举由 game 层校验）
    ("MAP_CHANGE/build_place", MsgType.MAP_CHANGE, {
        "obj_id": "b1", "change_type": "build_place",
        "state": {"kind": "barricade", "x": 300.0, "y": 200.0, "hp": 120.0}, "extra": {},
    }),
    ("MAP_CHANGE/build_destroy", MsgType.MAP_CHANGE, {
        "obj_id": "b1", "change_type": "build_destroy", "state": {}, "extra": {},
    }),
    ("MAP_CHANGE/fire_zone", MsgType.MAP_CHANGE, {
        "obj_id": "z1", "change_type": "fire_zone",
        "state": {"x": 500.0, "y": 500.0, "radius": 90.0, "dps": 12.0}, "extra": {},
    }),
    ("MAP_CHANGE/env_damage", MsgType.MAP_CHANGE, {
        "obj_id": "w1", "change_type": "env_damage",
        "state": {"hp": 40.0}, "extra": {"damage": 15.0},
    }),
    # 阶段5 祝福：PLAYER_SNAPSHOT 扩展 stats（客户端上报本人 / 主机转发幽灵）
    ("PLAYER_SNAPSHOT/stats", MsgType.PLAYER_SNAPSHOT, {
        "players": [{
            "player_id": 1, "x": 100.0, "y": 200.0, "hp": 90.0, "max_hp": 120.0,
            "weapon": "铁剑", "facing": 0.5, "alive": True,
            "stats": {
                "max_hp": 120.0, "defense": 12.0, "char_speed_mult": 1.1,
                "regen_per_sec": 0.5, "crit_chance": 0.15, "lifesteal": 0.05,
                "thorns": 3.0, "damage_mult": 1.2,
            },
        }],
    }),
    # 阶段2/4 FULL_STATE 扩展：evac_point + event_id（晚期加入客户端初始化）
    ("FULL_STATE/evac_point+event_id", MsgType.FULL_STATE, {
        "monsters": [], "drops": [], "chests": [], "env_objects": [],
        "well_opened": True,
        "evac_point": {
            "x": 640.0, "y": 360.0, "state": "dormant",
            "hp": 1000.0, "max_hp": 1000.0, "defend_left": 0.0, "wave_no": 0,
        },
        "event_id": "caravan", "action_time_left": 240.0,
    }),
)

# 各新消息 schema 必须写清的载荷键名（联机「四接线」依赖 schema 描述，故纳入断言）
_SCHEMA_KEY_EXPECT: tuple[tuple[MsgType, tuple[str, ...]], ...] = (
    (MsgType.MISSION_PROGRESS, ("player_id", "event", "amount")),
    (MsgType.EVAC_POINT_STATE, ("x", "y", "state", "hp", "max_hp", "defend_left", "wave_no")),
    (MsgType.EVAC_POINT_ACTION, ("player_id", "action", "x", "y")),
    (MsgType.EVENT_START, ("event_id", "flags")),
    (MsgType.MAP_CHANGE, ("obj_id", "change_type", "state", "extra")),
    (MsgType.PLAYER_SNAPSHOT, ("players", "stats")),
    (MsgType.FULL_STATE, ("evac_point", "event_id")),
)

# 非法帧样本：(说明, 原始 JSON 帧) —— decode 必须一律抛 ValueError，禁止静默忽略
_INVALID_FRAMES: tuple[tuple[str, str], ...] = (
    ("未知 MsgType", '{"type": "NO_SUCH_MSG", "payload": {}}'),
    ("空 type", '{"type": "", "payload": {}}'),
    ("缺 type 字段", '{"payload": {}}'),
    ("type 类型错（int）", '{"type": 123, "payload": {}}'),
    ("缺 payload 字段", '{"type": "MISSION_PROGRESS"}'),
    ("payload 类型错（list）", '{"type": "MISSION_PROGRESS", "payload": [1, 2]}'),
    ("payload 为 null", '{"type": "MISSION_PROGRESS", "payload": null}'),
    ("帧类型错（顶层数组）", "[1, 2, 3]"),
    ("JSON 不可解析", '{"type": "MISSION_PROGRESS", '),
)


def _expect_value_error(label: str, fn: Callable[[], object]) -> None:
    """执行 fn 并断言其抛 ValueError（非法帧/非法入参必须显式抛错，不得静默接受）。"""
    try:
        fn()
    except ValueError:
        return
    except Exception as exc:  # noqa: BLE001 —— 抛了别的异常也算不符合契约
        raise AssertionError(
            f"{label} 应抛 ValueError，实际抛 {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"{label} 未抛 ValueError（被静默接受，违反协议层铁律）")


async def _recv_until(
    ws: ClientConnection,
    predicate: Callable[[MsgType, dict], bool],
    timeout: float = MSG_TIMEOUT,
) -> dict:
    """接收并跳过无关消息，直到出现满足 predicate 的 (MsgType, payload) 并返回其载荷。"""
    deadline = time.monotonic() + timeout
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remain)
        except asyncio.TimeoutError:
            break
        msg_type, payload = decode(raw)
        if predicate(msg_type, payload):
            return payload
    raise AssertionError(f"超时 {timeout}s 未收到满足条件的消息")


async def _drain_quiet(
    ws: ClientConnection, window: float = QUIET_WINDOW
) -> list[tuple[MsgType, dict]]:
    """在 window 秒静默窗口内收尽其余消息（用于断言「不多发」：一发一收语义）。"""
    out: list[tuple[MsgType, dict]] = []
    while True:
        try:
            out.append(decode(await asyncio.wait_for(ws.recv(), timeout=window)))
        except asyncio.TimeoutError:
            return out


def _apply_mission_progress(
    progress: dict[str, int], payload: dict, own_net_id: int
) -> bool:
    """客户端应用 MISSION_PROGRESS 的**契约镜像**（测试内实现，禁 import game 层）。

    与 game/mission_tracker.apply_remote_progress 同口径：
    - 仅本人（payload.player_id == 本端联机传输 id）才写本地进度，他人进度忽略；
    - 事件键不在 MISSION_EVENT_KEYS 全集内则忽略。

    返回是否真的写入（False = 被忽略），供自检断言。
    """
    event = payload.get("event")
    if event not in MISSION_EVENT_KEYS:
        return False
    player_id = payload.get("player_id")
    if player_id is not None and int(player_id) != own_net_id:
        return False  # 他人进度：本端无动作（防双计 + 防伪造他人进度）
    amount = int(payload.get("amount", 1))
    progress[str(event)] = progress.get(str(event), 0) + amount
    return True


async def _scenario_1_connectivity() -> tuple[bool, str]:
    """场景1 · 服务器 + 2 客户端收发：握手 / 入站 / 单播 / 广播 / 广播排除。"""
    bridge = NetBridge()
    server = NetServer(
        bridge,
        room_id="test-room",
        seed=20260811,
        theme="forest",
        max_players=MAX_PLAYERS,
    )
    ws_a: ClientConnection | None = None
    ws_b: ClientConnection | None = None
    try:
        # 1) 启动服务器（动态自由端口）+ 2 个客户端完成 HELLO+JOIN 握手
        server.start(TEST_HOST, _pick_free_port())
        assert server.is_running, "start() 后服务器线程应存活"
        ws_a, pid_a = await _handshake_join(server.port, "玩家A")
        ws_b, pid_b = await _handshake_join(server.port, "玩家B")
        assert pid_a != pid_b, "两次握手分配的 player_id 必须不同"

        # 2a) 客户端→服务器：入站消息应出现在主线程的 bridge.poll() 里
        await ws_a.send(encode(MsgType.HEARTBEAT, {"seq": 1, "client_time": 0.0}))
        env = await _wait_inbound(
            bridge,
            lambda e: e.get("player_id") == pid_a
            and e.get("msg_type") == "HEARTBEAT",
        )
        assert env["payload"]["seq"] == 1, f"入站封装 payload 不符: {env}"

        # 2b) 服务器→客户端：单播 send_to 只发给指定玩家
        server.send_to(pid_b, MsgType.HEARTBEAT, {"seq": 2, "client_time": 0.0})
        msg_type, payload = decode(
            await asyncio.wait_for(ws_b.recv(), timeout=MSG_TIMEOUT)
        )
        assert msg_type == MsgType.HEARTBEAT and payload["seq"] == 2, payload

        # 2c) 服务器→客户端：广播 broadcast 全员收到
        server.broadcast(MsgType.HEARTBEAT, {"seq": 3, "client_time": 0.0})
        m_a = decode(await asyncio.wait_for(ws_a.recv(), timeout=MSG_TIMEOUT))
        m_b = decode(await asyncio.wait_for(ws_b.recv(), timeout=MSG_TIMEOUT))
        assert m_a[0] == MsgType.HEARTBEAT and m_a[1]["seq"] == 3, m_a
        assert m_b[0] == MsgType.HEARTBEAT and m_b[1]["seq"] == 3, m_b

        # 2d) 广播 exclude：排除 B，仅 A 收到
        server.broadcast(MsgType.HEARTBEAT, {"seq": 4, "client_time": 0.0}, exclude=pid_b)
        m_a = decode(await asyncio.wait_for(ws_a.recv(), timeout=MSG_TIMEOUT))
        assert m_a[1]["seq"] == 4, m_a
        try:
            await asyncio.wait_for(ws_b.recv(), timeout=0.5)
            raise AssertionError("exclude 未生效：B 不应收到 seq=4 广播")
        except asyncio.TimeoutError:
            pass  # B 确实没收到，符合预期

        return True, (
            f"服务器已启动（动态端口 {server.port}）；A/B 握手成功，"
            f"player_id={pid_a}/{pid_b} 互不相同；"
            f"客户端→服务器 bridge.poll() 收到入站封装（HEARTBEAT seq=1，含 player_id/msg_type/payload）；"
            f"服务器→客户端 send_to 单播（B 收到 seq=2）、broadcast 广播（A/B 均收 seq=3）、"
            f"broadcast exclude（排除 B，仅 A 收到 seq=4）"
        )
    except Exception as exc:  # noqa: BLE001 —— 自检脚本需汇总所有失败
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        # 清理客户端连接后优雅关闭服务器（失败路径同样确保不残留线程）
        for ws in (ws_a, ws_b):
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
        server.stop()


async def _scenario_2_full_room() -> tuple[bool, str]:
    """场景2 · 满员拒绝：补满 4 人后第 5 个连接收到 JOIN_REJECT（含"满员"）。"""
    bridge = NetBridge()
    server = NetServer(
        bridge,
        room_id="test-room",
        seed=20260811,
        theme="forest",
        max_players=MAX_PLAYERS,
    )
    connections: list[ClientConnection] = []
    try:
        server.start(TEST_HOST, _pick_free_port())
        # 补满到 3 个客户端（槽位 0 保留给主机，客户端容量 = max_players-1）
        for name in ("玩家A", "玩家B", "玩家C"):
            ws, _ = await _handshake_join(server.port, name)
            connections.append(ws)
        assert server.player_count == MAX_PLAYERS - 1, (
            f"满员后客户端人数应为 {MAX_PLAYERS - 1}"
        )
        # 第 4 个客户端（主机外的第 4 个）被拒：JOIN_REJECT 原因含"满员"
        reason = await _handshake_reject(server.port, "玩家D")
        assert "满员" in reason, f"拒绝原因应含满员字样，实际: {reason}"
        assert server.player_count == MAX_PLAYERS - 1, "被拒后房间人数应保持 3"

        return True, (
            f"房间补满 {MAX_PLAYERS - 1} 个客户端（+1 主机槽位）后，"
            f"第 4 个客户端收到 JOIN_REJECT"
            f"（原因：{reason}，含「满员」字样），房间人数保持 {MAX_PLAYERS - 1}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        for ws in connections:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        server.stop()


async def _scenario_3_disconnect() -> tuple[bool, str]:
    """场景3 · 断线感知：客户端断开触发 on_disconnect 回调、人数-1；stop() 优雅关闭。"""
    bridge = NetBridge()
    disconnected: list[tuple[int, str]] = []
    server = NetServer(
        bridge,
        room_id="test-room",
        seed=20260811,
        theme="forest",
        max_players=MAX_PLAYERS,
        on_disconnect=lambda pid, reason: disconnected.append((pid, reason)),
    )
    ws_a: ClientConnection | None = None
    try:
        server.start(TEST_HOST, _pick_free_port())
        ws_a, pid_a = await _handshake_join(server.port, "玩家A")
        assert server.player_count == 1, "单个客户端入座后人数应为 1"
        # 客户端主动断开 -> on_disconnect 回调触发 + 房间人数-1
        await ws_a.close()
        ws_a = None  # 已主动关闭，清理阶段不再重复 close
        assert _wait_until(
            lambda: any(pid == pid_a for pid, _ in disconnected)
        ), "on_disconnect 回调未触发"
        assert server.player_count == 0, "断线后房间人数应-1"
        # 服务器 stop() 优雅关闭：线程退出，无 hang
        server.stop()
        assert not server.is_running, "stop() 后服务器线程仍存活（挂起）"

        return True, (
            f"客户端（player_id={pid_a}）断开后 on_disconnect 回调触发，"
            f"房间人数 1→0；server.stop() 后线程正常退出，无 hang"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if ws_a is not None:
            try:
                await ws_a.close()
            except Exception:  # noqa: BLE001
                pass
        server.stop()


async def _scenario_4_protocol() -> tuple[bool, str]:
    """场景4 · 协议层：新消息 encode/decode 往返 + MESSAGE_SCHEMAS 校验 + 非法帧被拒（纯协议，不启服务器）。"""
    try:
        # 4a) 新消息往返：类型与载荷无损，且二次编码幂等（sort_keys 保证帧稳定）
        for label, msg_type, payload in _ROUNDTRIP_CASES:
            raw = encode(msg_type, payload)
            back_type, back_payload = decode(raw)
            assert back_type is msg_type, f"{label} 往返后类型不符: {back_type}"
            assert back_payload == payload, f"{label} 往返后载荷不符: {back_payload} != {payload}"
            assert encode(back_type, back_payload) == raw, f"{label} 二次编码不幂等"
        # 4b) 中文不转义（ensure_ascii=False，抓包可读）
        cn_raw = encode(MsgType.JOIN_REJECT, {"reason": "房间已满员"})
        assert "房间已满员" in cn_raw, f"中文应原样输出，实际: {cn_raw}"
        assert decode(cn_raw) == (MsgType.JOIN_REJECT, {"reason": "房间已满员"}), "中文载荷往返失败"
        # 4c) MESSAGE_SCHEMAS 覆盖：每个 MsgType 都有非空 schema，且无孤儿条目
        missing = [t.name for t in MsgType if not MESSAGE_SCHEMAS.get(t)]
        assert not missing, f"以下 MsgType 缺 MESSAGE_SCHEMAS 条目: {missing}"
        all_types = set(MsgType)
        orphan = [k.name for k in MESSAGE_SCHEMAS if k not in all_types]
        assert not orphan, f"MESSAGE_SCHEMAS 存在非 MsgType 的孤儿键: {orphan}"
        # 4d) 新消息 schema 必须写清载荷键名
        for msg_type, keys in _SCHEMA_KEY_EXPECT:
            schema = MESSAGE_SCHEMAS[msg_type]
            lack = [k for k in keys if f"'{k}'" not in schema]
            assert not lack, f"{msg_type.name} 的 schema 未描述键: {lack}"
        # 4e) 枚举取值写进 schema：撤离点 4 态 / 2 动作 / 7 个任务事件键
        state_schema = MESSAGE_SCHEMAS[MsgType.EVAC_POINT_STATE]
        lack_states = [s for s in EVAC_POINT_STATES if s not in state_schema]
        assert not lack_states, f"EVAC_POINT_STATE schema 未列出状态: {lack_states}"
        action_schema = MESSAGE_SCHEMAS[MsgType.EVAC_POINT_ACTION]
        lack_actions = [a for a in EVAC_POINT_ACTIONS if a not in action_schema]
        assert not lack_actions, f"EVAC_POINT_ACTION schema 未列出动作: {lack_actions}"
        mp_schema = MESSAGE_SCHEMAS[MsgType.MISSION_PROGRESS]
        lack_events = [e for e in MISSION_EVENT_KEYS if e not in mp_schema]
        assert not lack_events, f"MISSION_PROGRESS schema 未列出事件键: {lack_events}"
        # 4f) 非法帧必须显式抛错（禁静默忽略——protocol.py 铁律）
        for label, raw in _INVALID_FRAMES:
            _expect_value_error(f"decode({label})", lambda r=raw: decode(r))
        _expect_value_error("decode(非 str 入参)", lambda: decode(b'{"type": "HEARTBEAT", "payload": {}}'))
        # encode 的入参守卫：非 MsgType / payload 非 dict 必须抛错（故意传非法类型）
        _expect_value_error("encode(非 MsgType 入参)", lambda: encode("MISSION_PROGRESS", {}))
        _expect_value_error(
            "encode(payload 非 dict)", lambda: encode(MsgType.MISSION_PROGRESS, ["kill"])
        )
        return True, (
            f"新消息 encode/decode 往返 {len(_ROUNDTRIP_CASES)} 例全部无损且幂等"
            f"（EVAC_POINT_STATE×2 / EVAC_POINT_ACTION×2 / EVENT_START×2 / MISSION_PROGRESS×2 / "
            f"MAP_CHANGE 新 change_type build_place·build_destroy·fire_zone·env_damage / "
            f"PLAYER_SNAPSHOT.stats / FULL_STATE.evac_point+event_id）；"
            f"中文载荷不转义；MESSAGE_SCHEMAS 覆盖全部 {len(MESSAGE_SCHEMAS)} 条消息"
            f"（无缺项无孤儿），新消息键名与枚举取值均写进 schema；"
            f"非法帧/非法入参 {len(_INVALID_FRAMES) + 3} 例全部抛 ValueError，无一静默"
        )
    except Exception as exc:  # noqa: BLE001 —— 自检脚本需汇总所有失败
        return False, f"{type(exc).__name__}: {exc}"


async def _scenario_5_evac_point_flow() -> tuple[bool, str]:
    """场景5 · 阶段2 撤离点请求-应答流：EVAC_POINT_ACTION → 主机 → EVAC_POINT_STATE（一发一收 + 字段断言）。"""
    bridge = NetBridge()
    server = NetServer(
        bridge,
        room_id="test-room",
        seed=20260811,
        theme="forest",
        max_players=MAX_PLAYERS,
    )
    ws_a: ClientConnection | None = None
    try:
        server.start(TEST_HOST, _pick_free_port())
        ws_a, pid_a = await _handshake_join(server.port, "玩家A")

        # 5a) 客户端发激活请求 → 主机侧入站封装（原样透传 + 服务器认定发送者为权威）
        req = {"player_id": pid_a, "action": "activate", "x": 640.0, "y": 360.0}
        await ws_a.send(encode(MsgType.EVAC_POINT_ACTION, req))
        env = await _wait_inbound(
            bridge,
            lambda e: e.get("player_id") == pid_a
            and e.get("msg_type") == "EVAC_POINT_ACTION",
        )
        assert env["payload"] == req, f"入站 EVAC_POINT_ACTION 载荷应原样透传: {env}"
        assert env["player_id"] == pid_a, "主机认定发送者应等于握手分配的 player_id（防伪造）"

        # 5b) 主机校验通过 → 广播权威状态；客户端单发单收 + 7 字段逐项断言
        defending = {
            "x": 640.0, "y": 360.0, "state": "defending",
            "hp": 1000.0, "max_hp": 1000.0, "defend_left": 60.0, "wave_no": 1,
        }
        server.broadcast(MsgType.EVAC_POINT_STATE, defending)
        got = await _recv_until(ws_a, lambda t, _p: t is MsgType.EVAC_POINT_STATE)
        assert got == defending, f"撤离点状态字段不符: {got}"
        extra = [t.name for t, _ in await _drain_quiet(ws_a)]
        assert not extra, f"一次请求只应回一条 EVAC_POINT_STATE，多余消息: {extra}"

        # 5c) 修复请求 → 主机拒绝时同样回广播状态（game_view 口径：拒绝也回状态，客户端不空等）
        await ws_a.send(encode(MsgType.EVAC_POINT_ACTION, {
            "player_id": pid_a, "action": "repair", "x": 900.0, "y": 900.0,
        }))
        env2 = await _wait_inbound(
            bridge,
            lambda e: e.get("player_id") == pid_a
            and e.get("msg_type") == "EVAC_POINT_ACTION"
            and e.get("payload", {}).get("action") == "repair",
        )
        assert env2["payload"]["x"] == 900.0, f"修复请求坐标未透传: {env2}"
        damaged = {**defending, "hp": 620.0, "defend_left": 31.5, "wave_no": 2}
        server.broadcast(MsgType.EVAC_POINT_STATE, damaged)
        got2 = await _recv_until(ws_a, lambda t, _p: t is MsgType.EVAC_POINT_STATE)
        for key in ("x", "y", "state", "hp", "max_hp", "defend_left", "wave_no"):
            assert key in got2, f"EVAC_POINT_STATE 缺字段 {key}: {got2}"
        assert got2["state"] in EVAC_POINT_STATES, f"state 非法: {got2['state']}"
        assert (got2["hp"], got2["defend_left"], got2["wave_no"]) == (620.0, 31.5, 2), (
            f"状态镜像数值不符: {got2}"
        )
        extra2 = [t.name for t, _ in await _drain_quiet(ws_a)]
        assert not extra2, f"修复请求只应回一条 EVAC_POINT_STATE，多余消息: {extra2}"

        return True, (
            f"EVAC_POINT_ACTION(activate) → 主机入站封装（player_id={pid_a} 与握手一致、载荷原样）"
            f"→ 广播 EVAC_POINT_STATE(state=defending, 7 字段全等) → 客户端 1 请求 1 应答无多余消息；"
            f"EVAC_POINT_ACTION(repair) → 主机回广播 EVAC_POINT_STATE(hp=620/defend_left=31.5/wave_no=2)，"
            f"state 属 {len(EVAC_POINT_STATES)} 态枚举，同样 1 请求 1 应答"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if ws_a is not None:
            try:
                await ws_a.close()
            except Exception:  # noqa: BLE001
                pass
        server.stop()


async def _scenario_6_mission_progress() -> tuple[bool, str]:
    """场景6 · 阶段6 任务进度：主机单播 → 客户端解码应用（本人 +1）；他人 player_id / 未知事件键忽略。"""
    bridge = NetBridge()
    server = NetServer(
        bridge,
        room_id="test-room",
        seed=20260811,
        theme="forest",
        max_players=MAX_PLAYERS,
    )
    ws_a: ClientConnection | None = None
    ws_b: ClientConnection | None = None
    try:
        server.start(TEST_HOST, _pick_free_port())
        ws_a, pid_a = await _handshake_join(server.port, "玩家A")
        ws_b, pid_b = await _handshake_join(server.port, "玩家B")
        assert pid_a != pid_b, "两次握手分配的 player_id 必须不同"
        progress_a: dict[str, int] = {}
        progress_b: dict[str, int] = {}

        # 6a) 主机单播本人进度 → 客户端解码后本地进度 +1
        server.send_to(pid_b, MsgType.MISSION_PROGRESS, {
            "player_id": pid_b, "event": "kill", "amount": 1,
        })
        mine = await _recv_until(ws_b, lambda t, _p: t is MsgType.MISSION_PROGRESS)
        assert _apply_mission_progress(progress_b, mine, own_net_id=pid_b), (
            f"本人进度应被应用: {mine}"
        )
        assert progress_b == {"kill": 1}, f"本人进度应 +1，实际 {progress_b}"

        # 6b) 单播语义：非归属客户端 A 收不到（防双计的传输层前提）
        leaked = [p for t, p in await _drain_quiet(ws_a) if t is MsgType.MISSION_PROGRESS]
        assert not leaked, f"单播不应触达非归属客户端 A: {leaked}"

        # 6c) 归属校验：payload.player_id ≠ 本人 → 忽略，进度不变
        server.send_to(pid_b, MsgType.MISSION_PROGRESS, {
            "player_id": pid_a, "event": "kill", "amount": 3,
        })
        other = await _recv_until(ws_b, lambda t, _p: t is MsgType.MISSION_PROGRESS)
        assert not _apply_mission_progress(progress_b, other, own_net_id=pid_b), (
            f"他人 player_id 的进度必须被忽略: {other}"
        )
        assert progress_b == {"kill": 1}, f"忽略后进度应不变，实际 {progress_b}"

        # 6d) 未知事件键同样被拒（合法键全集见 MISSION_EVENT_KEYS）
        server.send_to(pid_b, MsgType.MISSION_PROGRESS, {
            "player_id": pid_b, "event": "not_a_mission_key", "amount": 5,
        })
        bad_key = await _recv_until(ws_b, lambda t, _p: t is MsgType.MISSION_PROGRESS)
        assert not _apply_mission_progress(progress_b, bad_key, own_net_id=pid_b), (
            f"未知事件键应被忽略: {bad_key}"
        )
        assert progress_b == {"kill": 1}, f"忽略后进度应不变，实际 {progress_b}"

        # 6e) 主机误用广播时，各端仍只应用自己那份（防双计最后一道防线）
        server.broadcast(MsgType.MISSION_PROGRESS, {
            "player_id": pid_a, "event": "harvest", "amount": 2,
        })
        fa = await _recv_until(ws_a, lambda t, _p: t is MsgType.MISSION_PROGRESS)
        fb = await _recv_until(ws_b, lambda t, _p: t is MsgType.MISSION_PROGRESS)
        assert _apply_mission_progress(progress_a, fa, own_net_id=pid_a), "A 应应用自己那份"
        assert not _apply_mission_progress(progress_b, fb, own_net_id=pid_b), "B 不应应用他人那份"
        assert progress_a == {"harvest": 2}, f"A 进度应 +2，实际 {progress_a}"
        assert progress_b == {"kill": 1}, f"B 进度不应变，实际 {progress_b}"

        return True, (
            f"主机单播 MISSION_PROGRESS(player_id={pid_b}, kill+1) → B 解码应用后 kill=1，"
            f"A 未收到（单播不外泄）；player_id={pid_a}（他人）与未知事件键各 1 条均被忽略、"
            f"B 进度保持 kill=1；误用 broadcast 时 A 应用 harvest=2、B 保持不变（各端只写自己那份）"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        for ws in (ws_a, ws_b):
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
        server.stop()


async def _run_scenario(
    label: str, scenario: Callable[[], Awaitable[tuple[bool, str]]]
) -> int:
    """运行单个场景并打印 PASS/FAIL 记录，返回该场景的失败数（0/1）。"""
    passed, detail = await scenario()
    if passed:
        print(f"PASS {label}: {detail}")
        return 0
    print(f"FAIL {label}: {detail}")
    return 1


async def main() -> int:
    """执行六类场景自检，返回进程退出码（0 全部通过 / 1 任一场景失败）。"""
    failed = 0
    failed += await _run_scenario("场景1（服务器+2客户端收发）", _scenario_1_connectivity)
    failed += await _run_scenario("场景2（满员拒绝）", _scenario_2_full_room)
    failed += await _run_scenario("场景3（断线感知）", _scenario_3_disconnect)
    failed += await _run_scenario("场景4（协议：新消息往返+schema+非法帧）", _scenario_4_protocol)
    failed += await _run_scenario(
        "场景5（阶段2 撤离点请求-应答流）", _scenario_5_evac_point_flow
    )
    failed += await _run_scenario(
        "场景6（阶段6 任务进度单播应用）", _scenario_6_mission_progress
    )
    if failed == 0:
        print(
            "PASS: 六类场景全部通过（server + 2 client 收发 / 满员拒绝 / 断线感知 / "
            "协议往返+schema+非法帧 / EVAC_POINT_ACTION→EVAC_POINT_STATE / MISSION_PROGRESS 单播应用）"
        )
        return 0
    print(f"FAIL: {failed} 个场景未通过")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
