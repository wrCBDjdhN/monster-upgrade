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

三类场景各自独立运行（各自独立服务器实例 + 动态自由端口），任一场景失败只影响
自身的 PASS/FAIL 记录；最终退出码 = 0 全部通过 / 1 任一场景失败。

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
    from net.protocol import MsgType, decode, encode
    from net.server import NetServer
    from net.thread_bridge import NetBridge
except ImportError:
    # 兜底：以顶层模块方式导入（net/ 已在 sys.path 时走此分支）
    from protocol import MsgType, decode, encode  # pyright: ignore[reportMissingImports]
    from server import NetServer  # pyright: ignore[reportMissingImports]
    from thread_bridge import NetBridge  # pyright: ignore[reportMissingImports]

from websockets.asyncio.client import ClientConnection, connect  # type: ignore[attr-defined]

TEST_HOST = "127.0.0.1"   # 本机回环自检
MAX_PLAYERS = 4           # 与服务器默认槽位上限一致
MSG_TIMEOUT = 5.0         # 单条消息等待超时（秒）
POLL_INTERVAL = 0.01      # bridge 轮询间隔（秒）


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
    """执行三类场景自检，返回进程退出码（0 全部通过 / 1 任一场景失败）。"""
    failed = 0
    failed += await _run_scenario("场景1（服务器+2客户端收发）", _scenario_1_connectivity)
    failed += await _run_scenario("场景2（满员拒绝）", _scenario_2_full_room)
    failed += await _run_scenario("场景3（断线感知）", _scenario_3_disconnect)
    if failed == 0:
        print("PASS: 三类场景全部通过（server + 2 client 收发 / 满员拒绝 / 断线感知）")
        return 0
    print(f"FAIL: {failed} 个场景未通过")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
