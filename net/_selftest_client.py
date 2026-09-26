"""NetClient 自检脚本：真实 WebSocket echo 服务器 + 断线感知 + 友好连接失败。

流程（三个阶段）：
1. 连接真实服务器（本脚本自建临时 asyncio echo 服务器，daemon 线程运行），
   发送 HELLO 消息并轮询 poll() 收回应，验证 connect -> send -> recv -> poll 全链路；
2. 关闭服务器（模拟服务器关闭）-> 客户端应感知断线：状态置 disconnected、
   断线回调触发、断线后 send() 安全失败、stop() 优雅退出；
3. 连接不存在的端口（127.0.0.1:59999）-> connect() 应返回 False（友好失败），
   绝不抛未捕获异常。
4. 阶段11 新消息的客户端侧行为（Task 11.2 Step 2）：另起一个「脚本化主机」
   （daemon 线程 + 独立事件循环），验证
   - EVAC_POINT_ACTION → EVAC_POINT_STATE 请求-应答链路在客户端可解码、7 字段完整；
   - MISSION_PROGRESS 的归属过滤（防双计铁律）：本人进度写入 +1，
     他人 player_id 与未知事件键一律忽略、进度不变。

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
    from net.protocol import MsgType, decode, encode
except ImportError:
    # 直接运行 python net/_selftest_client.py 时，脚本所在目录
    # （net/）在 sys.path 中，此时以顶层模块方式导入
    from client import NetClient  # pyright: ignore[reportMissingImports]
    from protocol import MsgType, decode, encode  # pyright: ignore[reportMissingImports]

# 自检常量
READY_TIMEOUT = 5.0        # 等待 echo 服务器就绪超时（秒）
ECHO_TIMEOUT = 5.0         # 等待 echo 回显超时（秒）
DISCONNECT_TIMEOUT = 5.0   # 等待断线感知超时（秒）
STOP_JOIN_TIMEOUT = 3.0    # 关闭服务器/客户端线程 join 超时（秒）
DEAD_PORT = 59999          # 不存在服务的端口（连接应友好失败）
PUSH_TIMEOUT = 5.0         # 阶段4 等待脚本化主机推送消息的超时（秒）
SCRIPT_OWN_ID = 2          # 阶段4 脚本化主机分配给客户端的 player_id（联机传输 id）
SCRIPT_OTHER_ID = 1        # 阶段4 他人（主机自身）传输 id
EVAC_STATE_KEYS = ("x", "y", "state", "hp", "max_hp", "defend_left", "wave_no")
# 任务/成就事件键全集：镜像 entities/mission_defs.MISSION_EVENT_KEYS
# （net 自检不 import entities/game，避免与游戏层耦合；键集变更需同步此处）
MISSION_EVENT_KEYS = (
    "kill", "elite_kill", "harvest", "chest", "evac", "forge", "reforge",
)


def _apply_mission_progress(
    progress: dict[str, int], payload: dict, own_net_id: int
) -> bool:
    """客户端应用 MISSION_PROGRESS 的**契约镜像**（测试内实现，禁 import game 层）。

    与 game/mission_tracker.apply_remote_progress 同口径：仅本人
    （payload.player_id == 本端联机传输 id）才写本地进度；他人进度与
    未知事件键一律忽略。返回是否真的写入（False = 被忽略）。
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


def _run_scripted_server(port: int, ready: threading.Event, stop_event: threading.Event) -> None:
    """阶段4 脚本化主机（daemon 线程内运行独立事件循环）。

    只表达**协议请求-应答链路**，不复制 game/ 层的撤离点/任务判定逻辑：
    - 收到 HELLO             → 回 JOIN_ACCEPT {player_id=SCRIPT_OWN_ID, slot, room_id}
                               （客户端据此得知自己的联机传输 id，作为归属过滤基准）
    - 收到 EVAC_POINT_ACTION → 按 action 推导权威状态并回 EVAC_POINT_STATE；
      非法 action 也照回状态（保持原状态），对应「拒绝也回广播，客户端不空等」
    - 随后连推 3 条 MISSION_PROGRESS：本人 / 他人 / 未知事件键，
      供客户端验证归属过滤（防双计铁律）
    """
    point_state: dict = {
        "x": 0.0, "y": 0.0, "state": "destroyed",
        "hp": 0.0, "max_hp": 1000.0, "defend_left": 0.0, "wave_no": 0,
    }

    def _state_payload(action: str, x: float, y: float) -> dict:
        """按请求动作推导权威撤离点状态（最小状态机：activate→defending / repair→dormant）。"""
        if action == "activate":
            point_state.update({
                "x": x, "y": y, "state": "defending",
                "hp": 1000.0, "defend_left": 60.0, "wave_no": 1,
            })
        elif action == "repair":
            point_state.update({"x": x, "y": y, "state": "dormant"})
        return dict(point_state)  # 副本：避免后续 update 影响已发出的载荷

    async def _scripted_handler(ws: ServerConnection) -> None:
        """脚本化主机处理器：按消息类型回帧。"""
        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                try:
                    msg_type, payload = decode(raw)
                except ValueError:
                    continue  # 非法帧忽略（本脚本只会发合法帧）
                if msg_type is MsgType.HELLO:
                    await ws.send(encode(MsgType.JOIN_ACCEPT, {
                        "player_id": SCRIPT_OWN_ID, "slot": 1, "room_id": "scripted",
                    }))
                elif msg_type is MsgType.EVAC_POINT_ACTION:
                    action = str(payload.get("action", ""))
                    x = float(payload.get("x", 0.0))
                    y = float(payload.get("y", 0.0))
                    await ws.send(encode(MsgType.EVAC_POINT_STATE, _state_payload(action, x, y)))
                    for progress_payload in (
                        {"player_id": SCRIPT_OWN_ID, "event": "kill", "amount": 1},
                        {"player_id": SCRIPT_OTHER_ID, "event": "kill", "amount": 3},
                        {"player_id": SCRIPT_OWN_ID, "event": "not_a_mission_key", "amount": 5},
                    ):
                        await ws.send(encode(MsgType.MISSION_PROGRESS, progress_payload))
        except ConnectionClosed:
            pass  # 客户端断开/服务器关闭：正常退出

    async def _serve_until_stopped() -> None:
        """启动服务并保持运行，直到外部 stop_event 被设置。"""
        async with websockets.asyncio.server.serve(
            _scripted_handler,
            "127.0.0.1",
            port,
            ping_interval=None,
        ):
            ready.set()
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


def _run_phase4_new_messages() -> int:
    """阶段4：客户端侧新消息解码 + 任务进度归属过滤（返回 0 通过 / 1 失败）。"""
    port = _pick_free_port()
    ready = threading.Event()
    stop_event = threading.Event()
    server_thread = threading.Thread(
        target=_run_scripted_server,
        args=(port, ready, stop_event),
        daemon=True,
        name="scripted-server",
    )
    server_thread.start()
    if not ready.wait(READY_TIMEOUT):
        print("FAIL: 阶段4 脚本化主机启动超时")
        return 1

    client = NetClient()
    thread_hung = False
    try:
        if not client.connect("127.0.0.1", port):
            print("FAIL: 阶段4 connect() 对脚本化主机应返回 True")
            return 1

        # 4a) HELLO → JOIN_ACCEPT：客户端解码出自己的联机传输 id（归属过滤基准）
        if not client.send((MsgType.HELLO, {"protocol": 1, "name": "stage4"})):
            print("FAIL: 阶段4 send(HELLO) 应返回 True")
            return 1
        own_id = -1
        deadline = time.monotonic() + PUSH_TIMEOUT
        while time.monotonic() < deadline and own_id < 0:
            for item in client.poll():
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and item[0] is MsgType.JOIN_ACCEPT
                    and isinstance(item[1], dict)
                ):
                    own_id = int(item[1]["player_id"])
            if own_id < 0:
                time.sleep(0.005)
        if own_id != SCRIPT_OWN_ID:
            print(f"FAIL: 应收到 JOIN_ACCEPT(player_id={SCRIPT_OWN_ID})，实际 own_id={own_id}")
            return 1
        print(f"OK: 阶段4 JOIN_ACCEPT 解码成功（own player_id={own_id}）")

        # 4b) 发撤离点请求 → 收主机权威状态（客户端只镜像，不本地推演倒计时/波次）
        if not client.send((MsgType.EVAC_POINT_ACTION, {
            "player_id": own_id, "action": "activate", "x": 640.0, "y": 360.0,
        })):
            print("FAIL: 阶段4 send(EVAC_POINT_ACTION) 应返回 True")
            return 1
        states: list[dict] = []
        progresses: list[dict] = []
        deadline = time.monotonic() + PUSH_TIMEOUT
        while time.monotonic() < deadline and not (states and len(progresses) >= 3):
            for item in client.poll():
                if not (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[0], MsgType)
                    and isinstance(item[1], dict)
                ):
                    continue
                if item[0] is MsgType.EVAC_POINT_STATE:
                    states.append(item[1])
                elif item[0] is MsgType.MISSION_PROGRESS:
                    progresses.append(item[1])
            if not (states and len(progresses) >= 3):
                time.sleep(0.005)
        if len(states) != 1:
            print(f"FAIL: 应收到 1 条 EVAC_POINT_STATE，实际 {len(states)} 条")
            return 1
        lack = [k for k in EVAC_STATE_KEYS if k not in states[0]]
        if lack:
            print(f"FAIL: EVAC_POINT_STATE 缺字段 {lack}: {states[0]}")
            return 1
        if states[0]["state"] != "defending" or states[0]["x"] != 640.0 or states[0]["hp"] != 1000.0:
            print(f"FAIL: EVAC_POINT_STATE 镜像值不符: {states[0]}")
            return 1
        print(f"OK: 阶段4 EVAC_POINT_ACTION→EVAC_POINT_STATE 应答解码成功（{states[0]}）")

        # 4c) 任务进度归属过滤：本人 +1；他人 player_id / 未知事件键忽略、进度不变
        if len(progresses) != 3:
            print(f"FAIL: 应收到 3 条 MISSION_PROGRESS，实际 {len(progresses)} 条")
            return 1
        progress: dict[str, int] = {}
        if not _apply_mission_progress(progress, progresses[0], own_net_id=own_id):
            print(f"FAIL: 本人进度应被应用: {progresses[0]}")
            return 1
        if progress != {"kill": 1}:
            print(f"FAIL: 本人进度应 +1，实际 {progress}")
            return 1
        if _apply_mission_progress(progress, progresses[1], own_net_id=own_id):
            print(f"FAIL: 他人 player_id 的进度必须被忽略: {progresses[1]}")
            return 1
        if _apply_mission_progress(progress, progresses[2], own_net_id=own_id):
            print(f"FAIL: 未知事件键应被忽略: {progresses[2]}")
            return 1
        if progress != {"kill": 1}:
            print(f"FAIL: 忽略后进度应保持 kill=1，实际 {progress}")
            return 1
        print("OK: 阶段4 MISSION_PROGRESS 归属过滤正确（本人 kill=1，他人与未知键均忽略）")
        return 0
    finally:
        # 清理：先停客户端再停主机线程（禁在 finally 里 return，故用标志位）
        client.stop()
        stop_event.set()
        server_thread.join(timeout=STOP_JOIN_TIMEOUT)
        thread_hung = server_thread.is_alive()
    if thread_hung:
        print("FAIL: 阶段4 脚本化主机线程未退出（挂起）")
        return 1
    return 0


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

    # ── 阶段 4：脚本化主机下的新消息解码 + 任务进度归属过滤（Task 11.2 Step 2）──
    print("── 阶段 4：EVAC_POINT_ACTION→EVAC_POINT_STATE 应答 + MISSION_PROGRESS 归属过滤 ──")
    if _run_phase4_new_messages() != 0:
        return 1

    print("PASS: 全部自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
