"""NetBridge 自检脚本：双线程 ping-pong 收发测试（仅标准库）。

流程：
- 线程 A（发送方）：经 send() 依次发送 100 条带序号消息 {"seq": i}
- 线程 B（回显方）：经 take_outbound() 收取后立即 put_inbound() 回显
- 主线程：poll() 收集全部回显，断言 100 条无丢失、按序号 0..99 无乱序
- 最后 stop()，join 两个线程并确认退出（无 hang）

运行方式：python net/_selftest_threadbridge.py
"""

import threading
import time

# 优先以包模块方式导入（供 pyright 静态分析 / python -m 运行时解析）
try:
    from net.thread_bridge import NetBridge
except ImportError:
    # 直接运行 python net/_selftest_threadbridge.py 时，脚本所在目录
    # （net/）在 sys.path 中，此时以顶层模块方式导入
    from thread_bridge import NetBridge  # pyright: ignore[reportMissingImports]

MESSAGE_COUNT = 100   # 测试消息条数
ECHO_TIMEOUT = 0.1    # 回显线程 take_outbound 超时（秒）
COLLECT_TIMEOUT = 5.0  # 主线程收集总超时（秒），防止无限挂起


def _seq_of(msg: object) -> int:
    """从消息对象提取序号（自检消息固定为 {"seq": int}）。"""
    if not isinstance(msg, dict):
        raise AssertionError(f"消息类型错误: {type(msg)}")
    seq = msg.get("seq")
    if not isinstance(seq, int):
        raise AssertionError(f"消息缺少整数 seq 字段: {msg!r}")
    return seq


def _sender(bridge: NetBridge) -> None:
    """线程 A：按序号 0..99 依次 send() 到 outbound。"""
    for i in range(MESSAGE_COUNT):
        bridge.send({"seq": i})


def _echo_worker(bridge: NetBridge) -> None:
    """线程 B：从 outbound 取消息，原样 put_inbound() 回显。

    以短超时循环轮询 is_stopped()，stop() 后可正常退出。
    """
    while not bridge.is_stopped():
        msg = bridge.take_outbound(timeout=ECHO_TIMEOUT)
        if msg is not None:
            bridge.put_inbound(msg)


def main() -> int:
    """执行 ping-pong 自检，返回进程退出码（0 成功 / 1 失败）。"""
    bridge = NetBridge()

    # 启动线程 A（发送）与线程 B（回显）
    thread_a = threading.Thread(target=_sender, args=(bridge,), name="sender")
    thread_b = threading.Thread(target=_echo_worker, args=(bridge,), name="echo")
    thread_a.start()
    thread_b.start()

    # 主线程 poll() 收集回显消息，直到收满或超时（带截止时间，绝不无限挂起）
    received: list[object] = []
    deadline = time.monotonic() + COLLECT_TIMEOUT
    while len(received) < MESSAGE_COUNT and time.monotonic() < deadline:
        received.extend(bridge.poll())
        time.sleep(0.001)  # 短暂让出 CPU，避免忙等空转

    # 最后再排空一次，确保队列中不残留消息
    received.extend(bridge.poll())

    # 断言 1：无丢失
    if len(received) != MESSAGE_COUNT:
        print(
            f"FAIL: 丢失消息 {MESSAGE_COUNT - len(received)} 条"
            f"（收到 {len(received)}/{MESSAGE_COUNT}）"
        )
        return 1

    # 断言 2：按序号无乱序（回显经两条 FIFO 队列往返，序号应严格为 0..99）
    seqs = [_seq_of(m) for m in received]
    if seqs != list(range(MESSAGE_COUNT)):
        print(
            f"FAIL: 消息乱序"
            f"（期望 0..{MESSAGE_COUNT - 1}，实际前 10 条 {seqs[:10]}...）"
        )
        return 1

    # 关闭桥，确认两个线程都能正常退出（无 hang）
    bridge.stop()
    thread_a.join(timeout=2.0)
    thread_b.join(timeout=2.0)
    if thread_b.is_alive():
        print("FAIL: stop() 后回显线程仍未退出（挂起）")
        return 1

    print(f"PASS: {MESSAGE_COUNT}/{MESSAGE_COUNT} messages, ordered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
