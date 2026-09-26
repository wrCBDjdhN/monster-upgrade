"""任务/成就进度事件追踪（阶段6.2，联机双端口径）

唯一计数铁律（计划 Task 6.2 硬性要求）：
- **局内战斗事件**（kill / elite_kill / harvest / chest / evac）唯一计数端 = 主机
  （单机 solo 时「主机」即本地）。主机按归属裁决后：
    - 归属本端玩家 → 写本端本地库（bump_mission / bump_achievement）；
    - 归属其它玩家 → 单播 MISSION_PROGRESS {player_id, event, amount}，
      目标客户端经 apply_remote_progress 写自己的本地库；
- **客户端永不自计局内战斗事件**：客户端即使本地感知到击杀/开箱/撤离（补经验路径），
  也在此直接返回，等主机广播到达后再写库——这是防双计的唯一实现点。

局外事件（forge / reforge）：锻造坊与市场本就是各端本地 UI、各端本地 DB，
故三端（solo/host/client）都在本地 bump，player_id 参数不参与。

player_id 参数语义（重要）：是**联机传输 id**（`gs.net_player_id`，主机约定 0、
客户端 1~3），**不是** DB 主键。主机无法得知客户端的 DB player_id（各端独立库），
故归属判定与 MISSION_PROGRESS 载荷一律用传输 id；客户端收到后与本端
`gs.net_player_id` 比对，命中才写自己的库。缺省 None/缺省 = 本端玩家。
"""

from entities.mission_defs import (
    MISSION_EVENT_KEYS,
    DAILY_BY_ID,
    ACHIEVEMENT_BY_ID,
)

# 局内战斗事件（唯一计数端 = 主机；客户端永不自计）
IN_RUN_EVENTS = frozenset({"kill", "elite_kill", "harvest", "chest", "evac"})
# 局外事件（各端本地 UI 触发，各端本地计数）
OUT_RUN_EVENTS = frozenset({"forge", "reforge"})

# 达成提示色（金色，与 missions UI 口径一致）
_GOLD = (255, 215, 0)


def _game_state(view):
    """取 GameState：兼容 arcade.View 的 self.window 与本仓部分视图的 self.window_ref"""
    window = getattr(view, "window", None) or getattr(view, "window_ref", None)
    return getattr(window, "game_state", None) if window is not None else None


def _local_net_id(gs) -> int:
    """本端联机传输 id（未接入大厅时约定 0，与 game_view 一致）"""
    value = getattr(gs, "net_player_id", None)
    return 0 if value is None else int(value)


def on_event(view, event: str, amount: int = 1, player_id: int | None = None) -> None:
    """任务/成就进度事件入口（阶段6.2 唯一计数口径）

    view：GameView / ForgeView / MarketView 任一（只需能取到 game_state，
           另有 player 精灵时走世界飘字，否则回落到视图自身 toast）。
    event：MISSION_EVENT_KEYS 七种之一；未知键显式告警后忽略（禁静默）。
    amount：本次次数（连杀可传 N；进度按 target 封顶由 db 层保证）。
    player_id：归属玩家的**联机传输 id**；缺省 = 本端玩家。
    """
    if event not in MISSION_EVENT_KEYS:
        print(f"[任务] on_event 收到未知事件键 {event!r}，已忽略（合法键：{MISSION_EVENT_KEYS}）")
        return
    gs = _game_state(view)
    if gs is None or not getattr(gs, "player_id", None):
        return  # 未登录本地库（无 player_id）：无进度可写
    mode = getattr(gs, "net_mode", "solo") or "solo"
    # 归属玩家：缺省即本端（联机下即主机自身传输 id 0）
    target = _local_net_id(gs) if player_id is None else int(player_id)

    if event in IN_RUN_EVENTS:
        if mode == "client":
            # 防双计铁律：客户端永不自计局内战斗事件，只等主机 MISSION_PROGRESS 广播
            return
        if mode == "host" and target != _local_net_id(gs):
            # 归属其它客户端：主机裁决归属后单播，本端不写他人进度
            _send_progress(gs, target, event, amount)
            return
    bump_local(gs, view, event, amount)


def apply_remote_progress(view, player_id, event: str, amount: int = 1) -> None:
    """客户端应用主机 MISSION_PROGRESS 广播：归属校验后写本端本地库

    - 仅本人（payload.player_id == 本端 gs.net_player_id）才写库，他人进度忽略；
    - 局内/局外事件都走这里（局外事件客户端本已本地计过，主机不会就局外事件广播，
      双保险下即便收到也按同一 bump 口径写库，不会出现同端重复计数路径）。
    """
    if event not in MISSION_EVENT_KEYS:
        print(f"[任务] MISSION_PROGRESS 收到未知事件键 {event!r}，已忽略")
        return
    gs = _game_state(view)
    if gs is None or not getattr(gs, "player_id", None):
        return
    mine = _local_net_id(gs)
    if player_id is not None and int(player_id) != mine:
        return  # 他人进度：本端无动作
    bump_local(gs, view, event, amount)


def _send_progress(gs, target: int, event: str, amount: int) -> None:
    """主机单播 MISSION_PROGRESS 给归属客户端（延迟 import 避免 game→net 顶层耦合）"""
    server = getattr(gs, "net_server", None)
    if server is None:
        return  # 理论不可达（host 模式必有 net_server），防御性返回
    from net.protocol import MsgType
    server.send_to(int(target), MsgType.MISSION_PROGRESS, {
        "player_id": int(target),
        "event": event,
        "amount": int(amount),
    })


def bump_local(gs, view, event: str, amount: int = 1) -> None:
    """本地写库：每日任务 + 成就各 bump 一次，新达成的 id 走提示"""
    pid = gs.player_id
    from db.database import bump_achievement, bump_mission
    done = list(bump_mission(pid, event, amount))
    done += list(bump_achievement(pid, event, amount))
    if done:
        _announce(view, done)


def _desc_of(mission_id: str) -> str:
    """任务/成就 id → 中文描述（任务板 UI 同一份 mission_defs 数据）"""
    defn = DAILY_BY_ID.get(mission_id) or ACHIEVEMENT_BY_ID.get(mission_id)
    return str(defn["desc"]) if defn else mission_id


def _announce(view, ids: list) -> None:
    """达成提示：局内走世界飘字 + 音效，局外走视图自身 toast（禁重复弹出）"""
    from game.sound_manager import sound_manager
    texts = [f"任务达成: {_desc_of(str(i))}" for i in ids]
    player = getattr(view, "player", None)
    if player is not None and hasattr(player, "center_x"):
        from game.effects import floating_texts
        for i, text in enumerate(texts):
            floating_texts.add(player.center_x, player.center_y + 60 + i * 24,
                               text, _GOLD, life=2.5, font_size=16, vy=60)
    else:
        toast = getattr(view, "show_toast", None)          # 市场：show_toast(text, duration)
        if callable(toast):
            toast(texts[0], 3.0)
        else:
            fallback = getattr(view, "_toast", None)       # 锻造坊：_toast(text, color)
            if callable(fallback):
                fallback(texts[0], _GOLD)
    sound_manager.play_level_up()
