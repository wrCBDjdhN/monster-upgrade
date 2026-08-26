"""局域网联机客户端层 —— websockets 客户端封装（局域网联机 Wave 2 · 任务 6）

职责边界（严格遵循任务约束）：
- 只做四件事：建立连接 / 收发消息 / 断线感知 / 优雅关闭
- 不做：任何游戏逻辑、消息语义处理（协议帧解码后原样经 NetBridge 上交主线程，
  心跳保活等游戏层语义由上层决定，本层不自动应答）

线程模型：
- 网络 daemon 线程持有独立的 asyncio 事件循环，连接 + 收发协程全部运行其中，
  绝不占用 arcade 主线程（60fps 主循环不受网络 I/O 影响）
- arcade 主线程与网络线程之间唯一数据通道 = net/thread_bridge.py 的 NetBridge：
    · 主线程 send() -> outbound 队列 -> 网络线程 _send_loop 发往服务器
    · 网络线程 _recv_loop 收到 -> decode -> inbound 队列 -> 主线程 poll() 取
- connect() 会阻塞等待结果（带超时），但阻塞只发生在调用方线程，网络 I/O
  本体始终在 daemon 线程内异步进行

安全约定（硬性）：
- 连接失败 / 服务器关闭 / 任意内部异常，一律转换为 disconnected 状态 + 回调通知，
  绝不向调用方抛出未捕获异常；
- 断线后 send() 安全失败（返回 False）；
- stop() 幂等可重复调用，且保证网络线程在限定时间内退出（join 带超时）。

依赖：websockets 15.x（websockets.asyncio.client）+ asyncio + 标准库 queue/threading
"""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection, connect as _ws_connect
from websockets.exceptions import ConnectionClosed

try:
    from net.protocol import MsgType, decode, encode
    from net.thread_bridge import NetBridge
except ImportError:
    # 直接运行 python net/_selftest_client.py 时，脚本目录（net/）在 sys.path，
    # 此时以顶层模块方式导入（与 _selftest_threadbridge.py 相同模式）
    from protocol import MsgType, decode, encode  # pyright: ignore[reportMissingImports]
    from thread_bridge import NetBridge  # pyright: ignore[reportMissingImports]

__all__ = ["NetClient", "RoomDiscovery"]

# 默认连接超时（秒）：超过视为连接失败，connect() 返回 False
CONNECT_TIMEOUT_DEFAULT = 5.0
# connect() 等待网络线程结果的额外余量（秒），覆盖调度抖动，绝不无限挂起
CONNECT_EVENT_MARGIN = 2.0
# 发送循环每次取 outbound 消息的阻塞超时（秒）：超时后复查停止标志，
# 保证 stop() 之后发送循环能及时退出
BRIDGE_TAKE_TIMEOUT = 0.1
# stop() 等待网络线程退出的总超时（秒）
STOP_JOIN_TIMEOUT = 2.0


class NetClient:
    """局域网联机 websocket 客户端。

    状态机：idle -> connecting -> connected -> disconnected（或 -> stopped）
    - state 取值："idle" / "connecting" / "connected" / "disconnected" / "stopped"
    - 断线回调在【网络线程】中被调用：若回调需要操作 arcade 主线程对象，
      请在回调内自行切换线程（例如仅置标志位，由主线程每帧检查）
    """

    def __init__(
        self,
        on_disconnect: Callable[[], None] | None = None,
        connect_timeout: float = CONNECT_TIMEOUT_DEFAULT,
    ) -> None:
        """创建客户端。

        :param on_disconnect: 断线回调（网络线程内触发，见类注释）
        :param connect_timeout: 连接超时（秒），超过即视为失败
        """
        self._bridge = NetBridge()                       # 主线程 <-> 网络线程消息桥
        self._on_disconnect = on_disconnect              # 断线回调（可空）
        self._connect_timeout = connect_timeout          # 连接超时（秒）
        self._lock = threading.Lock()                    # 保护状态字段

        # 网络线程 / 事件循环相关（仅网络线程内读写，stop() 经 call_soon_threadsafe 调度）
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: ClientConnection | None = None
        self._main_task: asyncio.Task[None] | None = None
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None

        # connect() 一次性同步结果（主线程等待，网络线程设置）
        self._connect_event = threading.Event()
        self._connect_result = False

        self._state = "idle"
        self._host: str | None = None
        self._port: int | None = None

    # ─────────────────────────── 对外接口 ───────────────────────────

    def connect(self, host: str, port: int) -> bool:
        """建立到服务器的连接，阻塞等待结果（带超时）。

        - 已连接：幂等返回 True
        - 网络线程仍在运行（连接中/清理中）：返回 False，拒绝重复 connect
        - 连接成功：返回 True；失败（端口不存在/拒绝/超时）：返回 False
        - 绝不抛出未捕获异常；调用方拿 False 显示友好错误即可

        注意：本方法会阻塞调用方线程直至连接结果（最长 connect_timeout +
        CONNECT_EVENT_MARGIN）。arcade 主线程内禁用（会造成窗口冻结），
        请改用 connect_async() + 逐帧轮询 state；本方法仅供 selftest 等
        非实时路径使用。
        """
        if not self.connect_async(host, port):
            return False
        # 阻塞等待网络线程完成连接（带总超时，绝不无限挂起）
        self._connect_event.wait(timeout=self._connect_timeout + CONNECT_EVENT_MARGIN)
        return self._connect_result

    def connect_async(self, host: str, port: int) -> bool:
        """非阻塞发起连接：启动网络 daemon 线程后立即返回（arcade 主线程安全）。

        - 已连接：幂等返回 True（不会重复启动线程）
        - 网络线程仍在运行（连接中/清理中）：返回 False，拒绝重复发起
        - 成功发起：返回 True，连接结果由调用方轮询 state 观察：
            connecting → connected（成功） / disconnected（失败）
        - 绝不抛出未捕获异常

        这是 arcade 主线程的推荐入口：发起后立即返回，不阻塞 60fps 主循环，
        连接结果（成功/失败/超时）由后续 on_update 逐帧读取 self.state 收敛。
        """
        thread = self._thread
        if self.state == "connected" and thread is not None and thread.is_alive():
            # 已连接：幂等返回 True
            return True
        if thread is not None and thread.is_alive():
            # 线程仍存活（正在连接或收尾中）：拒绝重复发起
            return False

        # 重置一次性状态，启动网络 daemon 线程
        self._host = host
        self._port = port
        self._connect_event.clear()
        self._connect_result = False
        self._set_state("connecting")
        self._thread = threading.Thread(
            target=self._network_worker,
            args=(host, port),
            name="net-client",
            daemon=True,  # daemon 线程：主进程退出时不阻塞
        )
        self._thread.start()
        return True

    def send(self, msg: object) -> bool:
        """发送一条消息到服务器（主线程调用，只入队不阻塞）。

        支持的入参（两种）：
        - str：protocol.encode 编码好的 JSON 帧字符串，原样发送
        - (MsgType, dict)：内部调用 protocol.encode 编码后发送
        其他类型、或未连接/已断线/已停止：返回 False（安全失败，绝不抛异常）。
        """
        if self.state != "connected":
            return False  # 未连接 / 已断线 / 已停止：安全失败
        frame: str
        if isinstance(msg, str):
            frame = msg
        elif (
            isinstance(msg, tuple)
            and len(msg) == 2
            and isinstance(msg[0], MsgType)
            and isinstance(msg[1], dict)
        ):
            try:
                frame = encode(msg[0], msg[1])
            except ValueError:
                return False
        else:
            return False
        # 入队后由网络线程异步发送
        self._bridge.send(frame)
        return True

    def poll(self) -> list[object]:
        """主线程调用：非阻塞排空 inbound 队列，返回全部已收消息。

        每条消息为 (MsgType, payload) 元组（网络线程已按 protocol.decode 解码）；
        无消息时返回空列表，绝不阻塞 60fps 主循环。建议每帧调用一次。
        """
        return self._bridge.poll()

    def stop(self) -> None:
        """优雅关闭连接与网络线程（幂等，可重复调用）。

        流程：置 NetBridge 停止标志 -> 事件循环内取消收发/连接任务 ->
        join 网络线程（带超时，不阻塞主线程过久）。
        """
        if self.state == "stopped" and (
            self._thread is None or not self._thread.is_alive()
        ):
            return  # 幂等：已停止且线程已退出

        self._bridge.stop()          # 停止标志：发送循环/取消息尽快退出
        self._set_state("stopped")
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                # 线程安全地让事件循环取消进行中的任务
                loop.call_soon_threadsafe(self._request_shutdown)
            except RuntimeError:
                pass  # 事件循环正在关闭，忽略
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=STOP_JOIN_TIMEOUT)

    # ─────────────────────────── 状态属性 ───────────────────────────

    @property
    def state(self) -> str:
        """当前状态：idle/connecting/connected/disconnected/stopped。"""
        with self._lock:
            return self._state

    @property
    def is_connected(self) -> bool:
        """是否已连接（可收发）。"""
        return self.state == "connected"

    @property
    def is_disconnected(self) -> bool:
        """是否已断线（含连接失败）。"""
        return self.state == "disconnected"

    @property
    def host(self) -> str | None:
        """最近一次 connect() 的目标主机。"""
        return self._host

    @property
    def port(self) -> int | None:
        """最近一次 connect() 的目标端口。"""
        return self._port

    # ─────────────────────────── 内部实现 ───────────────────────────

    def _set_state(self, new_state: str) -> None:
        """线程安全地更新状态。"""
        with self._lock:
            self._state = new_state

    def _trigger_disconnect_callback(self) -> None:
        """在网络线程内触发断线回调；回调自身异常不逃逸，避免拖垮网络线程。"""
        if self._on_disconnect is not None:
            try:
                self._on_disconnect()
            except Exception as exc:
                print(f"[NetClient] 断线回调异常: {exc!r}")

    def _network_worker(self, host: str, port: int) -> None:
        """网络 daemon 线程入口：创建并运行事件循环，直至连接结束或 stop()。

        任何异常都被限制在本方法内（转为断线状态并唤醒 connect()），
        绝不向调用方逃逸——这是断线/失败路径的安全底线。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._main_task = None
        try:
            self._main_task = loop.create_task(self._client_main(host, port))
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            # stop() 主动中断连接：按断线处理，不视为异常
            self._connect_result = False
            self._connect_event.set()
            self._set_state("disconnected")
            self._trigger_disconnect_callback()
        except Exception as exc:
            # 兜底：任何未捕获异常都不得逃逸，统一转为断线状态
            self._connect_result = False
            self._connect_event.set()
            self._set_state("disconnected")
            self._trigger_disconnect_callback()
            print(f"[NetClient] 网络线程异常: {exc!r}")
        finally:
            # 清理事件循环内未完成的任务，避免 loop.close() 告警
            if not loop.is_closed():
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._main_task = None
            self._send_task = None
            self._recv_task = None
            self._ws = None

    def _request_shutdown(self) -> None:
        """在事件循环线程内执行：取消进行中的任务，使主协程尽快收尾。

        已进入收发阶段 -> 取消收发任务（主协程随后自行清理并关闭连接）；
        仍处于连接阶段 -> 直接取消主协程，中断阻塞中的 wait_for(connect)。
        """
        cancelled_any = False
        for task in (self._send_task, self._recv_task):
            if task is not None and not task.done():
                task.cancel()
                cancelled_any = True
        if not cancelled_any:
            # 尚未进入收发阶段（可能正阻塞在连接中）：取消主协程
            if self._main_task is not None and not self._main_task.done():
                self._main_task.cancel()

    async def _client_main(self, host: str, port: int) -> None:
        """客户端主协程：建立连接 -> 并行收发 -> 断线统一清理（网络线程内运行）。"""
        uri = f"ws://{host}:{port}"
        try:
            # 库级 ping 关闭：协议层已定义 HEARTBEAT 负责保活，避免双保活干扰
            # （open_timeout 与 wait_for 双重兜底，保证连接阶段绝不会无限挂起）
            self._ws = await asyncio.wait_for(
                _ws_connect(
                    uri,
                    open_timeout=self._connect_timeout,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=2.0,
                ),
                timeout=self._connect_timeout,
            )
        except asyncio.CancelledError:
            raise  # stop() 中断连接：交给 worker 统一处理
        except Exception:
            # 连接失败（端口不存在/拒绝/超时）：置 disconnected 并唤醒 connect()
            self._connect_result = False
            self._set_state("disconnected")
            self._connect_event.set()
            return

        # 连接成功：唤醒阻塞中的 connect()
        self._connect_result = True
        self._set_state("connected")
        self._connect_event.set()

        # 并行启动收发循环；任一结束（断线/被关闭）即进入统一清理
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())
        await asyncio.wait(
            {self._send_task, self._recv_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 收发已结束：取消对端任务并等待其收尾（asyncio.wait 不因子任务取消抛错）
        for task in (self._send_task, self._recv_task):
            if not task.done():
                task.cancel()
        try:
            await asyncio.wait({self._send_task, self._recv_task})
        except Exception:
            pass

        # 关闭连接（尽力而为，绝不抛异常）
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

        # 断线感知：置 disconnected 并触发回调（不崩溃）
        self._set_state("disconnected")
        self._trigger_disconnect_callback()

    async def _send_loop(self) -> None:
        """发送循环：从 NetBridge outbound 取消息发往服务器（网络线程内运行）。

        take_outbound 是阻塞式取消息，用 asyncio.to_thread 包装：
        等待期间事件循环保持空闲，接收协程不受阻塞。
        """
        ws = self._ws
        if ws is None:
            return
        while not self._bridge.is_stopped():
            msg = await asyncio.to_thread(self._bridge.take_outbound, BRIDGE_TAKE_TIMEOUT)
            if msg is None:
                continue  # 超时未取到消息，或桥已停止
            if not isinstance(msg, str):
                continue  # 只发送字符串帧（protocol.encode 产物），其余丢弃
            try:
                await ws.send(msg)
            except (ConnectionClosed, OSError, RuntimeError):
                # 连接已断：退出发送循环，交由主协程统一清理
                break

    async def _recv_loop(self) -> None:
        """接收循环：持续 recv，decode 后放入 inbound 桥（网络线程内运行）。"""
        ws = self._ws
        if ws is None:
            return
        while True:
            try:
                raw = await ws.recv()
            except (ConnectionClosed, OSError, RuntimeError):
                # 连接断开/被关闭：退出接收循环（断线感知的触发点）
                break
            if not isinstance(raw, str):
                continue
            try:
                msg_type, payload = decode(raw)
            except ValueError as exc:
                # 非法帧丢弃（协议层约定），不崩溃
                print(f"[NetClient] 丢弃非法帧: {exc}")
                continue
            # 解码后的 (MsgType, payload) 交给主线程 poll() 消费
            self._bridge.put_inbound((msg_type, payload))


class RoomDiscovery:
    """局域网房间发现：UDP 广播搜索在线房间。
    
    用法：
        discovery = RoomDiscovery()
        discovery.start()  # 启动后台搜索线程
        rooms = discovery.get_rooms()  # 获取发现的房间列表
        discovery.stop()  # 停止搜索
    """
    
    def __init__(self, broadcast_port: int = 8766):
        """初始化房间发现器。
        
        Args:
            broadcast_port: UDP 广播端口（默认 8766 = 8765 + 1）
        """
        self._broadcast_port = broadcast_port
        self._rooms: dict[str, dict] = {}  # room_id -> room_info
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None
    
    def start(self) -> None:
        """启动后台搜索线程（非阻塞）。"""
        if self._thread is not None and self._thread.is_alive():
            return  # 已在运行
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._discovery_worker,
            name="room-discovery",
            daemon=True,
        )
        self._thread.start()
    
    def stop(self) -> None:
        """停止搜索线程（幂等）。"""
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
    
    def get_rooms(self) -> list[dict]:
        """获取当前发现的房间列表（线程安全）。
        
        返回列表，每项包含：
        - room_id: 房间号
        - host_name: 主机名
        - theme: 地图主题
        - player_count: 当前玩家数
        - max_players: 最大玩家数
        - port: WebSocket 端口
        - host_ip: 主机 IP 地址
        """
        with self._lock:
            # 过滤掉超过 6 秒未更新的房间（可能已关闭）
            import time
            now = time.time()
            stale = [rid for rid, info in self._rooms.items() 
                     if now - info.get("last_seen", 0) > 6.0]
            for rid in stale:
                del self._rooms[rid]
            return list(self._rooms.values())
    
    def send_query(self) -> None:
        """发送一次 ROOM_QUERY 广播（触发主机回复）。"""
        import socket
        import json
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)
            
            query_msg = json.dumps({
                "type": "ROOM_QUERY",
                "payload": {"query": "discover"}
            }, ensure_ascii=False)
            
            sock.sendto(
                query_msg.encode("utf-8"),
                ("255.255.255.255", self._broadcast_port)
            )
            sock.close()
        except OSError:
            pass
    
    def _discovery_worker(self) -> None:
        """后台搜索线程：监听 UDP 广播，收集房间信息。"""
        import socket
        import json
        import time
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        self._sock = sock
        
        try:
            sock.bind(("", self._broadcast_port))
        except OSError:
            print(f"[RoomDiscovery] 无法绑定端口 {self._broadcast_port}")
            return
        
        # 启动时立即发送一次查询
        self.send_query()
        
        while not self._stop_event.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                if data:
                    try:
                        msg = json.loads(data.decode("utf-8"))
                        if msg.get("type") == "ROOM_BROADCAST":
                            payload = msg.get("payload", {})
                            room_id = payload.get("room_id", "")
                            if room_id:
                                with self._lock:
                                    self._rooms[room_id] = {
                                        "room_id": room_id,
                                        "host_name": payload.get("host_name", "未知"),
                                        "theme": payload.get("theme", "forest"),
                                        "player_count": payload.get("player_count", 0),
                                        "max_players": payload.get("max_players", 4),
                                        "port": payload.get("port", 8765),
                                        "host_ip": addr[0],
                                        "last_seen": time.time(),
                                    }
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
            except BlockingIOError:
                pass  # 无数据
            
            # 每 1 秒发送一次查询，保持发现
            if not self._stop_event.is_set():
                self._stop_event.wait(1.0)
