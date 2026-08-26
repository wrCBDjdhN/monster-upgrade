"""局域网联机 WebSocket 服务器层 —— 权威主机架构的网络骨架（局域网联机 Wave 2 · 任务 5）

职责范围（本层只做连接 / 路由 / 房间管理，不含任何游戏逻辑——怪物/攻击/掉落等
是 Wave 3+ 主机模拟层的职责）：
- 基于 websockets 15.x 的 asyncio 服务端，在独立 daemon 线程中跑事件循环，
  不阻塞 arcade 主线程（60fps）；
- 房间管理：房间状态（room_id / seed / theme / 玩家槽位 0..max_players-1）、
  玩家 id 分配、玩家名 -> 连接映射；满员（人数 >= max_players，默认 4）时
  对新连接回 JOIN_REJECT 并拒绝入座；
- 消息路由：单播 send_to(player_id, ...) / 广播 broadcast(msg, payload, exclude)；
- 入站消息统一经 NetBridge.put_inbound() 交给主线程处理（队列桥，见
  net/thread_bridge.py）；出站由主线程直接调用本类方法（线程安全）；
- 断线感知：客户端断开时在服务器线程内触发 on_disconnect 回调。

入站消息封装格式（主线程每帧从 bridge.poll() 收到的一条消息）：
    {"player_id": int, "msg_type": str, "payload": dict}
- player_id：发送方玩家 id（JOIN 握手成功后分配）
- msg_type：MsgType 枚举名（与 protocol.encode 的 type 字段一致）
- payload：协议载荷 dict（字段含义见 protocol.MESSAGE_SCHEMAS）

线程模型：
- 服务器线程（daemon）：跑 asyncio 事件循环，负责连接收发与房间状态读写；
- arcade 主线程：调用 start()/stop()/send_to()/broadcast()，并轮询 bridge.poll()；
- send_to()/broadcast() 经 loop.call_soon_threadsafe() 把发送任务调度到事件
  循环线程执行，因此可安全地从主线程调用；
- 房间状态只在事件循环线程内读写，主线程只能经本类方法间接访问，故无需加锁。

依赖：websockets>=15（新版 API 使用 websockets.asyncio.server.serve）、
net/protocol.py（消息编解码，本文件不改动）、net/thread_bridge.py（队列桥）。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from net.protocol import MsgType, decode, encode
from net.thread_bridge import NetBridge

__all__ = ["NetServer", "Room", "PlayerSlot"]

# 协议版本号：与 HELLO 消息的 payload['protocol'] 比对，不一致则拒绝握手
PROTOCOL_VERSION = 1
# 握手超时（秒）：客户端必须在此期限内完成 HELLO/JOIN，防恶意连接占住不放
HANDSHAKE_TIMEOUT = 10.0
# 服务器启动就绪等待超时（秒）
START_TIMEOUT = 5.0
# 优雅关闭时等待服务器线程退出的超时（秒），超时只告警不阻塞
STOP_TIMEOUT = 5.0
# 默认监听地址与端口（供 start() 缺省参数使用）
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765


@dataclass
class PlayerSlot:
    """一个已入座玩家的运行时状态（槽位号 0..max_players-1）。"""

    player_id: int                          # 本次会话内唯一的玩家 id
    name: str                               # 玩家名（加入时校验房间内唯一）
    slot: int                               # 槽位号，用于出生点偏移
    connection: ServerConnection            # 对应的 WebSocket 连接


@dataclass
class Room:
    """房间状态：房间参数 + 玩家槽位映射。

    线程约定：仅允许在服务器（事件循环）线程内读写；主线程只经 NetServer 的
    方法间接访问，从而避免加锁。
    """

    room_id: str                                                # 房间号
    seed: int                                                   # 地图随机种子（ROOM_START 用）
    theme: str                                                  # 地图主题（forest/desert/space）
    max_players: int                                            # 玩家上限（槽位数量）
    players: dict[int, PlayerSlot] = field(default_factory=dict)        # player_id -> 槽位
    names: dict[str, ServerConnection] = field(default_factory=dict)    # 玩家名 -> 连接

    def is_full(self) -> bool:
        """房间是否已满（满员后拒绝新连接入座）。

        客户端容量 = max_players - 1：槽位 0 保留给主机（主机不在
        room.players 中），故 max_players=4 时 3 个客户端即满员
        （3 客户端 + 1 主机），与 next_slot() 的槽位分配一致。
        """
        return len(self.players) >= self.max_players - 1

    def next_slot(self) -> int:
        """取最小空闲槽位号（1..max_players-1）；已满时返回 -1。

        槽位 0 保留给主机玩家（主机不在 room.players 中，但出生点偏移约定
        主机 slot=0、客户端 slot=1..max_players-1，避免出生点重叠）。
        """
        used = {p.slot for p in self.players.values()}
        for slot in range(1, self.max_players):
            if slot not in used:
                return slot
        return -1


class NetServer:
    """局域网联机 WebSocket 服务器：连接管理 + 房间管理 + 消息路由。

    用法（主机进程内）：
        bridge = NetBridge()                        # 主线程持有的队列桥
        server = NetServer(bridge, on_disconnect=handler)
        server.start("0.0.0.0", 8765)               # 启动（daemon 线程）
        # 主线程每帧：bridge.poll() 收入站；server.send_to/broadcast 发出站
        server.stop()                               # 优雅关闭，无 hang
    """

    def __init__(
        self,
        bridge: NetBridge,
        *,
        room_id: str = "default",
        seed: int = 0,
        theme: str = "forest",
        max_players: int = 4,
        on_disconnect: Callable[[int, str], None] | None = None,
    ) -> None:
        """创建服务器实例（此时尚未监听，须调用 start()）。

        参数：
        - bridge：NetBridge 实例，入站消息的归集出口（调用方持有/注入）；
        - room_id / seed / theme：本服务器唯一房间的静态参数；
        - max_players：玩家上限（槽位数量），满员后拒绝新连接；
        - on_disconnect：玩家断线回调 (player_id, reason)，在服务器线程内调用，
          回调内应只记录数据/喂队列，禁止直接触碰 arcade 对象。
        """
        if max_players < 2:
            raise ValueError("max_players 至少为 2（双人联机下限）")
        self._bridge = bridge
        self.room = Room(
            room_id=room_id,
            seed=seed,
            theme=theme,
            max_players=max_players,
        )
        self._on_disconnect = on_disconnect
        # 玩家 id 单调递增分配：本次会话内保证唯一（不回收已断线 id）。
        # 从 1 开始分配：id=0 保留给主机玩家（主机不在 room.players 中，
        # 但 game_view/协议约定 host 固定 player_id=0），避免与首个客户端 id 撞车
        self._next_player_id = 1
        # 服务器线程 / 事件循环状态（stop() 前由服务器线程写入，主线程只读）
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_future: asyncio.Future[None] | None = None
        self._ready_event = threading.Event()   # start() 就绪信号
        self._stop_requested = False            # stop() 已请求标记（跨线程布尔量）
        self._last_error: BaseException | None = None  # 启动失败原因
        self._port: int | None = None           # 实际监听端口（start() 时记录）

    # ────────────────────────── 生命周期 ──────────────────────────

    def start(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        """启动服务器：在独立 daemon 线程中跑 asyncio 事件循环。

        阻塞等待事件循环就绪（最长 START_TIMEOUT 秒）后返回；启动失败
        （如端口被占用 / 超时）抛 RuntimeError。
        """
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("服务器已在运行，不能重复 start()")
        self._stop_requested = False
        self._ready_event.clear()
        self._last_error = None
        self._port = port
        thread = threading.Thread(
            target=self._run_server_thread,
            args=(host, port),
            name="NetServer",
            daemon=True,  # daemon 线程：即使 stop() 异常也不会阻止进程退出
        )
        self._thread = thread
        thread.start()
        if not self._ready_event.wait(timeout=START_TIMEOUT):
            err = self._last_error
            raise RuntimeError(f"服务器启动失败或超时: {err!r}") from err

    def stop(self) -> None:
        """优雅关闭服务器：中断事件循环并等待 daemon 线程退出（无 hang）。

        幂等：服务器未启动 / 已停止时调用是安全的空操作。
        """
        self._stop_requested = True
        loop = self._loop
        if loop is not None and not loop.is_closed():
            # 经 call_soon_threadsafe 调度到事件循环线程执行停止逻辑
            loop.call_soon_threadsafe(self._request_loop_stop)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=STOP_TIMEOUT)
            if thread.is_alive():
                print(
                    f"[NetServer] 警告: stop() 后线程 {thread.name} 未在 "
                    f"{STOP_TIMEOUT}s 内退出"
                )

    @property
    def is_running(self) -> bool:
        """服务器线程是否仍在运行。"""
        return self._thread is not None and self._thread.is_alive()

    @property
    def port(self) -> int:
        """实际监听端口（start() 记录，供客户端连接/自检使用）。"""
        if self._port is None:
            raise RuntimeError("服务器尚未 start()，无端口信息")
        return self._port

    @property
    def player_count(self) -> int:
        """当前房间内的玩家数量（供调用方/自检读取）。"""
        return len(self.room.players)

    @property
    def player_ids(self) -> list[int]:
        """当前房间内的玩家 id 列表（按加入顺序）。"""
        return list(self.room.players)

    def player_info(self) -> list[tuple[int, str, int]]:
        """当前房间内玩家信息快照 [(player_id, name, slot)]（供 lobby 显示）。

        读取全部玩家三元组列表返回（主线程每次调用取到当前快照，
        不在返回后继续持有 room 引用，避免跨线程读写竞态）。
        """
        return [(pid, p.name, p.slot) for pid, p in self.room.players.items()]

    def inbound_poll(self) -> list[object]:
        """主线程调用：非阻塞排空入站消息（与 NetClient.poll() 同模式）。

        返回服务器线程放入的入站消息列表，每项为
        {"player_id": int, "msg_type": str, "payload": dict}；
        无消息时返回空列表，绝不阻塞 60fps 主循环（每帧调用一次）。
        主机 GameView 据此处理客户端上报的攻击等事件。
        """
        return self._bridge.poll()

    def _request_loop_stop(self) -> None:
        """事件循环线程内执行：完成停止 future，触发 _serve 优雅退出。"""
        fut = self._stop_future
        if fut is not None and not fut.done():
            fut.set_result(None)

    def _run_server_thread(self, host: str, port: int) -> None:
        """daemon 线程入口：创建专属事件循环并运行到 stop() 为止。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve(host, port))
        except BaseException as exc:
            # 启动失败（端口占用等）或运行期异常：记录，供 start()/自检感知
            self._last_error = exc
            print(f"[NetServer] 服务器异常退出: {exc!r}")
        finally:
            try:
                # 取消残留任务（如 stop 瞬间尚未完成的发送任务），避免警告
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            finally:
                loop.close()
                self._loop = None
                self._thread = None

    async def _serve(self, host: str, port: int) -> None:
        """事件循环内的服务主体：监听端口 + UDP 广播 + 等待停止信号。"""
        loop = asyncio.get_running_loop()
        self._stop_future = loop.create_future()
        if self._stop_requested:
            # stop() 在启动完成前已被调用：不再监听，立即结束
            self._stop_future.set_result(None)
        async with serve(self._handle_connection, host, port):
            self._ready_event.set()   # 已开始监听，通知 start() 返回
            # 启动 UDP 广播任务（局域网房间发现）
            self._udp_broadcast_task = asyncio.create_task(self._udp_broadcast_loop())
            await self._stop_future
            # 停止 UDP 广播任务
            if self._udp_broadcast_task:
                self._udp_broadcast_task.cancel()
                try:
                    await self._udp_broadcast_task
                except asyncio.CancelledError:
                    pass
            # 退出 async with 时 serve 会关闭全部连接并等待处理器结束（优雅关闭）

    # ────────────────────────── 消息路由（主线程可调用，线程安全） ──────────────────────────

    def send_to(self, player_id: int, msg_type: MsgType, payload: dict) -> None:
        """单播：给指定 player_id 发送消息（线程安全，可从主线程调用）。

        服务器未运行 / 玩家不在房间时静默忽略。
        """
        loop = self._loop
        if loop is None or loop.is_closed() or self._stop_requested:
            return
        loop.call_soon_threadsafe(self._enqueue_send_to, player_id, msg_type, payload)

    def broadcast(
        self,
        msg_type: MsgType,
        payload: dict,
        exclude: int | None = None,
    ) -> None:
        """广播：给房间内全部玩家发送消息，可排除指定 player_id（线程安全）。

        exclude 用于"除发送者外广播"的场景（如伤害判定结果需要回传攻击者时）。
        """
        loop = self._loop
        if loop is None or loop.is_closed() or self._stop_requested:
            return
        loop.call_soon_threadsafe(self._enqueue_broadcast, msg_type, payload, exclude)

    # ── 以下方法只应在事件循环线程内执行（由 call_soon_threadsafe 调度）──

    def _enqueue_send_to(
        self, player_id: int, msg_type: MsgType, payload: dict
    ) -> None:
        """事件循环线程内：创建单播发送任务（玩家已离线则跳过）。"""
        player = self.room.players.get(player_id)
        if player is None:
            return
        asyncio.create_task(self._send_to_task(player, msg_type, payload))

    def _enqueue_broadcast(
        self, msg_type: MsgType, payload: dict, exclude: int | None
    ) -> None:
        """事件循环线程内：创建广播发送任务（排除 exclude 指定的玩家）。"""
        targets = [
            slot.connection
            for pid, slot in self.room.players.items()
            if pid != exclude
        ]
        if not targets:
            return
        asyncio.create_task(self._broadcast_task(targets, msg_type, payload))

    async def _send_to_task(
        self, player: PlayerSlot, msg_type: MsgType, payload: dict
    ) -> None:
        """单播发送协程。"""
        await self._safe_send(player.connection, msg_type, payload)

    async def _broadcast_task(
        self, connections: list[ServerConnection], msg_type: MsgType, payload: dict
    ) -> None:
        """广播发送协程：并发发送，单个慢客户端不拖慢整体广播。"""
        raw = encode(msg_type, payload)
        await asyncio.gather(
            *(self._send_raw(conn, raw) for conn in connections),
            return_exceptions=True,
        )

    async def _send_raw(self, connection: ServerConnection, raw: str) -> None:
        """给单个连接发送已编码帧；连接已断开时静默忽略。"""
        try:
            await connection.send(raw)
        except ConnectionClosed:
            pass  # 对端已断开，发送失败不视为错误

    async def _safe_send(
        self, connection: ServerConnection, msg_type: MsgType, payload: dict
    ) -> None:
        """给单个连接发送协议消息；连接已断开时静默忽略。"""
        await self._send_raw(connection, encode(msg_type, payload))

    # ────────────────────────── 连接处理 ──────────────────────────

    async def _handle_connection(self, connection: ServerConnection) -> None:
        """单连接入口：握手入座 -> 消息转发循环 -> 断线清理。"""
        player_id, name = await self._handshake(connection)
        if player_id is None:
            return  # 握手失败：连接随后由 websockets 正常关闭
        try:
            async for raw in connection:
                try:
                    msg_type, payload = decode(raw)
                except ValueError:
                    # 非法帧：单帧忽略，不踢人（协议层已定义抛错行为）
                    continue
                # 入站消息封装后交给主线程处理（经队列桥，永不阻塞）
                self._bridge.put_inbound({
                    "player_id": player_id,
                    "msg_type": msg_type.name,
                    "payload": payload,
                })
        except ConnectionClosed:
            pass  # 对端关闭 / 服务器关闭 / 网络异常，均视为断线
        finally:
            self._remove_player(player_id, name)
            if self._on_disconnect is not None:
                try:
                    self._on_disconnect(player_id, "connection_closed")
                except Exception:
                    pass  # 回调异常不得影响服务器线程

    async def _handshake(
        self, connection: ServerConnection
    ) -> tuple[int | None, str | None]:
        """完成加入房间握手：成功返回 (player_id, name)，失败返回 (None, None)。

        流程：可选 HELLO（校验协议版本）-> JOIN -> 校验 ->
        分配 player_id/槽位 -> 回 JOIN_ACCEPT。
        满员 / 重名 / 协议不兼容 / 房间不存在 -> 回 JOIN_REJECT 并关闭连接。
        """
        # 读第一帧（HELLO 或 JOIN），带超时防恶意连接占住
        try:
            raw = await asyncio.wait_for(
                connection.recv(), timeout=HANDSHAKE_TIMEOUT
            )
        except (TimeoutError, ConnectionClosed):
            return None, None
        try:
            msg_type, payload = decode(raw)
        except ValueError:
            return None, None
        # 客户端可先发 HELLO 身份握手（校验协议版本），再发 JOIN
        if msg_type == MsgType.HELLO:
            if payload.get("protocol") != PROTOCOL_VERSION:
                await self._reject(connection, "协议版本不兼容")
                return None, None
            try:
                raw = await asyncio.wait_for(
                    connection.recv(), timeout=HANDSHAKE_TIMEOUT
                )
            except (TimeoutError, ConnectionClosed):
                return None, None
            try:
                msg_type, payload = decode(raw)
            except ValueError:
                return None, None
        if msg_type != MsgType.JOIN:
            await self._reject(connection, "非法加入流程")
            return None, None
        # JOIN 载荷校验：玩家名 / 目标房间
        name = payload.get("name")
        room_id = payload.get("room_id", self.room.room_id)
        if not isinstance(name, str) or not name.strip():
            await self._reject(connection, "玩家名无效")
            return None, None
        name = name.strip()
        if room_id != self.room.room_id:
            await self._reject(connection, "房间不存在")
            return None, None
        if name in self.room.names:
            await self._reject(connection, "玩家名已存在")
            return None, None
        # 满员检查：人数 >= max_players 时拒绝新连接入座
        if self.room.is_full():
            await self._reject(connection, "房间已满员")
            return None, None
        slot = self.room.next_slot()
        if slot < 0:  # 理论不可达（is_full 已拦），防御性保留
            await self._reject(connection, "房间已满员")
            return None, None
        # 分配玩家 id 与槽位，登记到房间映射
        player_id = self._next_player_id
        self._next_player_id += 1
        self.room.players[player_id] = PlayerSlot(
            player_id=player_id, name=name, slot=slot, connection=connection
        )
        self.room.names[name] = connection
        await self._safe_send(connection, MsgType.JOIN_ACCEPT, {
            "player_id": player_id,
            "slot": slot,
            "room_id": self.room.room_id,
        })
        print(
            f"[NetServer] 玩家加入: id={player_id} name={name!r} slot={slot} "
            f"room={self.room.room_id} "
            f"（{len(self.room.players)}/{self.room.max_players}）"
        )
        return player_id, name

    async def _reject(self, connection: ServerConnection, reason: str) -> None:
        """回 JOIN_REJECT 并关闭连接（握手失败统一出口）。"""
        await self._safe_send(connection, MsgType.JOIN_REJECT, {"reason": reason})
        try:
            await connection.close()
        except ConnectionClosed:
            pass

    def _remove_player(self, player_id: int, name: str) -> None:
        """把玩家从房间中移除（断线/关闭时调用，仅事件循环线程）。"""
        self.room.players.pop(player_id, None)
        self.room.names.pop(name, None)

    async def _udp_broadcast_loop(self) -> None:
        """UDP 广播循环：每 2 秒向局域网广播房间信息（局域网房间发现）。
        
        使用 UDP 广播地址 255.255.255.255，端口与 WebSocket 服务端口相同+1。
        客户端监听此端口即可发现局域网内的房间。
        """
        import socket
        import json
        
        broadcast_port = (self._port or DEFAULT_PORT) + 1
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        
        try:
            while True:
                # 构建房间广播信息
                player_count = len(self.room.players) + 1  # +1 包含主机
                broadcast_msg = json.dumps({
                    "type": "ROOM_BROADCAST",
                    "payload": {
                        "room_id": self.room.room_id,
                        "host_name": "主机",  # 主机名可从 GameState 获取，此处简化
                        "theme": self.room.theme,
                        "player_count": player_count,
                        "max_players": self.room.max_players,
                        "port": self._port or DEFAULT_PORT,
                    }
                }, ensure_ascii=False)
                
                # 发送 UDP 广播
                try:
                    sock.sendto(
                        broadcast_msg.encode("utf-8"),
                        ("255.255.255.255", broadcast_port)
                    )
                except OSError:
                    pass  # 广播失败不阻塞服务器
                
                # 同时监听是否有客户端的 ROOM_QUERY 请求
                try:
                    data, addr = sock.recvfrom(1024)
                    if data:
                        try:
                            msg = json.loads(data.decode("utf-8"))
                            if msg.get("type") == "ROOM_QUERY":
                                # 收到查询请求，立即回复一次广播
                                sock.sendto(
                                    broadcast_msg.encode("utf-8"),
                                    addr
                                )
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                except BlockingIOError:
                    pass  # 无数据，继续
                
                await asyncio.sleep(2.0)  # 每 2 秒广播一次
        finally:
            sock.close()
