"""线程安全队列桥：arcade 主线程与网络线程之间的唯一数据通道。

设计约定：
- 主线程（arcade on_update，60fps）绝不能被阻塞，因此 poll() 必须非阻塞排空
- 网络线程（asyncio 事件循环）通过 take_outbound() 阻塞取消息，超时循环保证
  stop() 之后能及时退出，不会被不可中断的阻塞等待卡死
- 消息内容本层不关心：可以是 protocol.py encode 好的 dict/str 或直接 dict，
  本桥一律按 object 透传，不解析不校验

仅依赖标准库（queue / threading / time），无第三方依赖。
"""

from __future__ import annotations

import queue
import threading
import time


class NetBridge:
    """连接 arcade 主线程与网络线程的双向线程安全消息桥。

    方向约定：
    - outbound：主线程 -> 网络线程（主线程 send()，网络线程 take_outbound()）
    - inbound： 网络线程 -> 主线程（网络线程 put_inbound()，主线程 poll()）

    线程安全：queue.Queue 本身线程安全，无需额外加锁；停止标志用
    threading.Event，可在多线程间安全读写。
    """

    def __init__(self) -> None:
        """创建两条 FIFO 队列与停止标志。

        outbound：主线程 send 写入、网络线程 take_outbound 取走
        inbound： 网络线程 put_inbound 写入、主线程 poll 取走
        """
        self._outbound: queue.Queue[object] = queue.Queue()  # 主线程 -> 网络线程
        self._inbound: queue.Queue[object] = queue.Queue()   # 网络线程 -> 主线程
        self._stop_event = threading.Event()                 # 停止标志（线程安全）

    def send(self, obj: object) -> None:
        """主线程调用：向 outbound 放入一条待发送消息。

        Queue 为无界队列，put 永不等待、不会阻塞主线程。
        """
        self._outbound.put(obj)

    def poll(self) -> list[object]:
        """主线程调用：非阻塞排空 inbound，返回全部已收到的消息。

        应在 arcade on_update 每帧调用一次；无消息时返回空列表，
        不会阻塞 60fps 主循环。
        """
        messages: list[object] = []
        while True:
            try:
                messages.append(self._inbound.get_nowait())
            except queue.Empty:
                # inbound 已排空，结束本轮收集
                break
        return messages

    def put_inbound(self, obj: object) -> None:
        """网络线程回调调用：向 inbound 放入一条收到的消息（永不阻塞）。"""
        self._inbound.put(obj)

    def take_outbound(self, timeout: float = 0.1) -> object | None:
        """网络线程调用：从 outbound 取一条待发送消息（阻塞但可超时）。

        采用短超时循环轮询 is_stopped()：每次 get 最多等待到剩余时间，
        超时后回到循环顶部复查停止标志，确保 stop() 之后能及时返回 None，
        不会被不可中断的阻塞等待卡死。未取到消息或已停止时返回 None。
        """
        deadline = time.monotonic() + timeout
        while not self.is_stopped():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 总超时耗尽，本次取消息失败
                return None
            try:
                return self._outbound.get(timeout=remaining)
            except queue.Empty:
                # 本次短超时未取到消息，继续循环并再次检查停止标志
                continue
        return None

    def stop(self) -> None:
        """设置停止标志：通知阻塞中的 take_outbound() 及时退出循环。"""
        self._stop_event.set()

    def is_stopped(self) -> bool:
        """查询是否已调用 stop()。"""
        return self._stop_event.is_set()
