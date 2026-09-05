# net/ - 局域网联机网络层

**Updated:** 2026-09-05 | **Files:** 9 | **Lines:** ~1,726

## OVERVIEW
局域网联机（最多 4 人）：WebSocket 异步协议 + 主机权威仲裁。主机裁决伤害/拾取/撤离并广播快照；客户端确定性重建同一地图、禁本地仲裁。纯 asyncio，经 thread_bridge 汇入主循环，不阻塞主线程。支持房间自动发现（UDP 广播）。

## WHERE TO LOOK
| 任务 | 文件 |
|------|------|
| 加联机协议消息 | protocol.py（MsgType 枚举 + 消息 schema，唯一消息定义处） |
| 主机服务端 | server.py（建房/广播/仲裁/观战管理/房间自动发现） |
| 客户端 | client.py（连接/快照应用/事件上报/房间发现） |
| 异步→主线程桥 | thread_bridge.py（回调经队列汇入主循环） |
| 网络自检 | _selftest.py / _selftest_client.py / _selftest_server.py / _selftest_threadbridge.py（`python net/_selftest*.py`，返回码 0=通过） |

## CONVENTIONS
- 新消息：protocol.py 加 MsgType + schema，server/client 各加处理分支；未知消息必须显式处理或记录（protocol.py:358 铁律）
- 主机权威：怪物/弹丸/掉落/玩家位置 20Hz 快照；伤害/拾取/撤离由主机裁决广播；客户端禁本地仲裁
- 回调禁阻塞主线程、禁碰 arcade 对象（渲染非线程安全）；一律经 thread_bridge 队列汇入主循环
- 观战模式：V 键切换视角；房间等待全员结束后回房再战
- 每局新地图：同房多次开局自动重新随机种子
- 房间自动发现：UDP 广播发现局域网内主机，客户端可自动扫描加入

## ANTI-PATTERNS
- 禁静默忽略未知消息（必须显式处理或记录日志）
- 禁在回调中 sleep/等待、禁直接操作 arcade 对象
- 禁客户端本地仲裁战斗结果（以主机广播为准）
- 禁绕过 protocol.py 手拼消息（编解码唯一入口）
