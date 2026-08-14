"""NetServer 自检脚本：用真实 WebSocket 客户端验证服务器层（局域网联机 Wave 2 · 任务 5）。

覆盖场景（对应交付标准）：
1. 2 个客户端完成 HELLO+JOIN 握手，成功入座（JOIN_ACCEPT 含 player_id/slot）
2. 收发验证：
   - 客户端 -> 服务器：客户端发 HEARTBEAT，主线程经 bridge.poll() 收到入站封装
   - 服务器 -> 客户端：send_to() 单播 / broadcast() 广播（含 exclude 排除）
3. 满员拒绝：房间补满到 4 人后，第 5 个连接收到 JOIN_REJECT（原因含"满员"）
4. 断线感知：客户端主动断开后触发 on_disconnect 回调，房间人数-1
5. stop() 优雅关闭：服务器线程退出，无 hang

运行方式：python net/_selftest_server.py
"""

import asyncio
import os
import socket
import sys
import time
from collections.abc import Callable

# 保证 net 包可被导入：把项目根目录（本文件上一级）加入 sys.path
# （python net/_selftest_server.py 直接运行时，net/ 在 sys.path，父目录不在）
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
    """探测一个系统分配的可用端口（绑定后立即释放，供本测试监听）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((TEST_HOST, 0))
        return sock.getsockname()[1]


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


async def main() -> int:
    """执行服务器层自检，返回进程退出码（0 成功 / 1 失败）。"""
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
    ws_a = ws_b = ws_c = ws_d = None
    try:
        # ── 1) 两个客户端成功握手入座 ──
        server.start(TEST_HOST, _pick_free_port())
        print(f"  [0] 服务器已启动（线程存活={server.is_running}）")
        ws_a, pid_a = await _handshake_join(server.port, "玩家A")
        ws_b, pid_b = await _handshake_join(server.port, "玩家B")
        assert pid_a != pid_b, "两次握手分配的 player_id 必须不同"
        print(f"  [1] 握手成功: A=id{pid_a} B=id{pid_b}，房间人数={server.player_count}")

        # ── 2) 收发验证 ──
        # 2a 客户端 -> 服务器：入站消息应出现在主线程的 bridge.poll() 里
        await ws_a.send(encode(MsgType.HEARTBEAT, {"seq": 1, "client_time": 0.0}))
        env = await _wait_inbound(
            bridge,
            lambda e: e.get("player_id") == pid_a
            and e.get("msg_type") == "HEARTBEAT",
        )
        assert env["payload"]["seq"] == 1, f"入站封装 payload 不符: {env}"
        print(f"  [2a] 客户端→服务器: bridge 收到入站封装 {env}")

        # 2b 服务器 -> 客户端：单播 send_to 只发给指定玩家
        server.send_to(pid_b, MsgType.HEARTBEAT, {"seq": 2, "client_time": 0.0})
        msg_type, payload = decode(
            await asyncio.wait_for(ws_b.recv(), timeout=MSG_TIMEOUT)
        )
        assert msg_type == MsgType.HEARTBEAT and payload["seq"] == 2, payload
        print("  [2b] 单播: B 收到 send_to 消息")

        # 2c 服务器 -> 客户端：广播 broadcast 全员收到
        server.broadcast(MsgType.HEARTBEAT, {"seq": 3, "client_time": 0.0})
        m_a = decode(await asyncio.wait_for(ws_a.recv(), timeout=MSG_TIMEOUT))
        m_b = decode(await asyncio.wait_for(ws_b.recv(), timeout=MSG_TIMEOUT))
        assert m_a[0] == MsgType.HEARTBEAT and m_a[1]["seq"] == 3, m_a
        assert m_b[0] == MsgType.HEARTBEAT and m_b[1]["seq"] == 3, m_b
        print("  [2c] 广播: A/B 均收到")

        # 2d 广播 exclude：排除 B，仅 A 收到
        server.broadcast(MsgType.HEARTBEAT, {"seq": 4, "client_time": 0.0}, exclude=pid_b)
        m_a = decode(await asyncio.wait_for(ws_a.recv(), timeout=MSG_TIMEOUT))
        assert m_a[1]["seq"] == 4, m_a
        try:
            await asyncio.wait_for(ws_b.recv(), timeout=0.5)
            raise AssertionError("exclude 未生效：B 不应收到 seq=4 广播")
        except asyncio.TimeoutError:
            pass  # B 确实没收到，符合预期
        print("  [2d] 广播 exclude 生效: 仅 A 收到")

        # ── 3) 满员拒绝：补满到 4 人，第 5 个连接被拒 ──
        ws_c, pid_c = await _handshake_join(server.port, "玩家C")
        ws_d, pid_d = await _handshake_join(server.port, "玩家D")
        reason = await _handshake_reject(server.port, "玩家E")
        assert "满员" in reason, f"拒绝原因应含满员字样，实际: {reason}"
        assert server.player_count == MAX_PLAYERS, f"满员后人数应为 {MAX_PLAYERS}"
        print(f"  [3] 满员拒绝: 第 5 个连接收到 JOIN_REJECT（{reason}）")

        # ── 4) 断线感知 ──
        await ws_a.close()
        assert _wait_until(
            lambda: any(pid == pid_a for pid, _ in disconnected)
        ), "on_disconnect 回调未触发"
        assert server.player_count == MAX_PLAYERS - 1, "断线后房间人数应-1"
        print("  [4] 断线感知: A 断开后 on_disconnect 触发，房间人数-1")

        print("PASS: 服务器层自检全部通过")
        return 0
    except Exception as exc:  # noqa: BLE001 —— 自检脚本需汇总所有失败
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        # 清理全部客户端连接，随后优雅关闭服务器
        for ws in (ws_a, ws_b, ws_c, ws_d):
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
        server.stop()
        if server.is_running:
            print("FAIL: stop() 后服务器线程仍存活（挂起）")
        else:
            print(f"  [5] stop(): 服务器线程正常退出，无 hang（端口 {server.port}）")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
