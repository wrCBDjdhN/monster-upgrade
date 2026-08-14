"""NetClient 自检脚本：真实 WebSocket echo 服务器 + 断线感知 + 友好连接失败。

流程（三个阶段）：
1. 连接真实服务器（本脚本自建临时 asyncio echo 服务器，daemon 线程运行），
   发送 HELLO 消息并轮询 poll() 收回应，验证 connect -> send -> recv -> poll 全链路；
2. 关闭服务器（模拟服务器关闭）-> 客户端应感知断线：状态置 disconnected、
   断线回调触发、断线后 send() 安全失败、stop() 优雅退出；
3. 连接不存在的端口（127.0.0.1:59999）-> connect() 应返回 False（友好失败），
   绝不抛未捕获异常。

运行方式：python net/_selftest_client.py
退出码：0 全部通过 / 1 任一阶段失败
"""

import asyncio
import socket
import threading
import time

import websockets.asyncio.server  # 临时 echo 服务器（与客户端同版本 websockets）

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

# 优先以包模块方式导入（供 pyright 静态分析 / python -m 运行时解析）
try:
    from net.client import NetClient
    from net.protocol import MsgType
except ImportError:
    # 直接运行 python net/_selftest_client.py 时，脚本所在目录
    # （net/）在 sys.path 中，此时以顶层模块方式导入
    from client import NetClient  # pyright: ignore[reportMissingImports]
    from protocol import MsgType  # pyright: ignore[reportMissingImports]

# 自检常量
READY_TIMEOUT = 5.0        # 等待 echo 服务器就绪超时（秒）
ECHO_TIMEOUT = 5.0         # 等待 echo 回显超时（秒）
DISCONNECT_TIMEOUT = 5.0   # 等待断线感知超时（秒）
STOP_JOIN_TIMEOUT = 3.0    # 关闭服务器/客户端线程 join 超时（秒）
DEAD_PORT = 59999          # 不存在服务的端口（连接应友好失败）


def _pick_free_port() -> int:
    """动态选取本机空闲端口：绑定 0 端口获取随机端口号后立即释放。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_echo_server(port: int, ready: threading.Event, stop_event: threading.Event) -> None:
    """临时 WebSocket echo 服务器（daemon 线程内运行独立事件循环）。

    - 收到任何字符串帧一律原样回显（用于验证客户端收发链路）
    - stop_event 被设置后关闭服务器 -> 活动连接随之关闭，模拟"服务器关闭"
    """

    async def _echo_handler(ws: ServerConnection) -> None:
        """echo 处理器：收一条回一条。"""
        try:
            async for raw in ws:
                await ws.send(raw)  # 原样回显
        except ConnectionClosed:
            pass  # 客户端断开/服务器关闭：正常退出

    async def _serve_until_stopped() -> None:
        """启动服务并保持运行，直到外部 stop_event 被设置。"""
        # websockets 15.x：serve 为异步上下文管理器，退出上下文即关闭全部连接
        async with websockets.asyncio.server.serve(
            _echo_handler,
            "127.0.0.1",
            port,
            ping_interval=None,  # 与客户端一致：保活交给应用层 HEARTBEAT
        ):
            ready.set()  # 通知主线程服务器已就绪
            # 轮询外部停止信号（50ms 粒度），期间保持服务运行
            while not stop_event.is_set():
                await asyncio.sleep(0.05)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve_until_stopped())
    finally:
        try:
            loop.close()
        except Exception:
            pass


def main() -> int:
    """执行三阶段自检，返回进程退出码（0 全部通过 / 1 失败）。"""
    # ── 阶段 1 + 2：真实服务器 echo 收发 + 断线感知 ──
    port = _pick_free_port()
    ready = threading.Event()
    stop_event = threading.Event()
    server_thread = threading.Thread(
        target=_run_echo_server,
        args=(port, ready, stop_event),
        daemon=True,
        name="echo-server",
    )
    server_thread.start()
    if not ready.wait(READY_TIMEOUT):
        print("FAIL: echo 服务器启动超时")
        return 1

    dc_called = False

    def _on_disconnect() -> None:
        """断线回调：仅记录触发标志（回调运行在网络线程，不触碰主线程对象）。"""
        nonlocal dc_called
        dc_called = True

    client = NetClient(on_disconnect=_on_disconnect)

    # 1) 连接真实服务器应成功
    if not client.connect("127.0.0.1", port):
        print("FAIL: connect() 对真实服务器应返回 True")
        return 1
    print("OK: connect() 返回 True")

    # 2) echo 收发：发送 (MsgType, payload) 元组，轮询 poll() 收回应
    client.send((MsgType.HELLO, {"protocol": 1, "name": "selftest"}))
    echo_received = False
    deadline = time.monotonic() + ECHO_TIMEOUT
    while time.monotonic() < deadline:
        for item in client.poll():
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and item[0] is MsgType.HELLO
                and isinstance(item[1], dict)
                and item[1].get("name") == "selftest"
            ):
                echo_received = True
        if echo_received:
            break
        time.sleep(0.005)  # 短暂让出 CPU，避免忙等
    if not echo_received:
        print("FAIL: 未收到 echo 回显消息")
        return 1
    print("OK: echo 回显收发成功")

    # 3) 模拟服务器关闭 -> 客户端应感知断线（状态 + 回调）
    stop_event.set()  # 关闭服务器（活动连接随之断开）
    deadline = time.monotonic() + DISCONNECT_TIMEOUT
    while time.monotonic() < deadline:
        if client.is_disconnected:
            break
        time.sleep(0.005)
    if not client.is_disconnected:
        print("FAIL: 服务器关闭后客户端未感知断线")
        return 1
    if not dc_called:
        print("FAIL: 断线回调未触发")
        return 1
    print("OK: 断线感知（状态 disconnected + 回调触发）")

    # 4) 断线后 send() 应安全失败（返回 False，不抛异常）
    if client.send((MsgType.HEARTBEAT, {"seq": 1, "client_time": 0.0})) is not False:
        print("FAIL: 断线后 send() 应安全失败返回 False")
        return 1
    print("OK: 断线后 send() 安全失败返回 False")

    # 5) stop() 优雅关闭（此时已断线，应为幂等安全操作）
    client.stop()
    server_thread.join(timeout=STOP_JOIN_TIMEOUT)
    if server_thread.is_alive():
        print("FAIL: echo 服务器线程未退出（挂起）")
        return 1
    print("OK: stop() 优雅关闭，服务器线程正常退出")

    # ── 阶段 3：连接不存在的端口 -> connect 返回 False（友好失败）──
    client2 = NetClient()
    result = client2.connect("127.0.0.1", DEAD_PORT)
    if result is not False:
        print(f"FAIL: connect() 对不存在端口 {DEAD_PORT} 应返回 False，实际为 {result}")
        return 1
    if not client2.is_disconnected:
        print("FAIL: 连接失败后状态应为 disconnected")
        return 1
    print(f"OK: connect() 对不存在端口 {DEAD_PORT} 友好返回 False")

    # 连接失败后 stop() 也应安全
    client2.stop()
    print("OK: 连接失败后 stop() 安全")

    print("PASS: 全部自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
