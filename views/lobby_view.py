"""局域网联机大厅（建房 / 加入）—— Todo 21 落地

职责：
- 建房（host）：创建 NetServer + NetBridge，展示房间状态与已加入玩家，
  点击「开始游戏」广播 ROOM_START（含 seed/theme/全房出生点），主机进入 GameView；
- 加入（client）：自动搜索局域网房间，选择房间加入，NetClient 连接后自动发 HELLO + JOIN 握手，
  收到 JOIN_ACCEPT 记录身份，收到 ROOM_START 按种子重建地图进入 GameView；
- 返回：安全停止 server/client，清理 GameState 联机字段，回 StartView。

状态机（self.mode）：
  menu       选择 建房/加入/返回
  host_wait  建房等待：显示房间号 / 地图主题选择 / 玩家列表 / 开始游戏 / 返回
  join       加入：自动搜索房间列表 + 手动输入IP + 连接 / 返回
  client_wait 连接已建立：显示等待主机开始 / 取消（断线自动回 menu 并提示）

线程模型：NetServer/NetClient 的网络线程收发不阻塞 arcade 主线程；
主线程每帧 poll 收消息（host 读 server.player_info 显示列表，client 读 client.poll）。
"""

import arcade
import random
from config import WINDOW_WIDTH, WINDOW_HEIGHT, NET_PORT, NET_SPAWN_OFFSET
from entities.character_defs import CHARACTERS, CHARACTER_ORDER  # 房间内选角（联机开局前必选）
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from views.lobby_tutorial import LobbyTutorial  # 教程辅助模块（页面构建 + 覆盖层绘制）
from game.sound_manager import sound_manager


class LobbyView(arcade.View):
    """局域网联机大厅视图"""

    # 可建房主题（与 MapSelectView 的 theme 口径一致）
    THEMES = {
        "forest": ("幽暗森林", (40, 80, 40)),
        "desert": ("沙漠荒地", (150, 120, 50)),
        "space": ("航天基地", (50, 60, 100)),
    }

    def __init__(self, window):
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        # 状态机
        self.mode = "menu"
        self.selected_theme = "forest"
        # 按钮
        cx = WINDOW_WIDTH // 2
        self.host_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 + 20, 260, 50)
        self.join_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 - 50, 260, 50)
        self.back_rect = arcade.XYWH(80, 40, 100, 36)
        self.start_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 - 60, 220, 50)
        self.ip_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 + 60, 300, 40)
        self.connect_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 - 10, 220, 50)
        # 房间内功能按钮：关闭房间（host_wait）/ 准备（client_wait）/ 市场 / 仓库
        self.close_rect = arcade.XYWH(140, WINDOW_HEIGHT - 60, 150, 36)
        self.ready_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 - 90, 200, 44)
        self.warehouse_rect = arcade.XYWH(cx - 130, WINDOW_HEIGHT // 2 - 150, 220, 40)
        self.market_rect = arcade.XYWH(cx + 130, WINDOW_HEIGHT // 2 - 150, 220, 40)
        # 主题按钮（建房模式选择）
        self.theme_rects = {}
        for i, (tid, (_, _)) in enumerate(self.THEMES.items()):
            # 地图主题按钮：放在角色选择区下方、市场/仓库按钮上方（host_wait 模式）
            self.theme_rects[tid] = arcade.XYWH(cx - 160 + i * 160, WINDOW_HEIGHT - 370, 140, 36)
        # hover 状态
        self.host_hover = False
        self.join_hover = False
        self.back_hover = False
        self.start_hover = False
        self.connect_hover = False
        self.ip_editing = False
        self.close_hover = False
        self.ready_hover = False
        self.warehouse_hover = False
        self.market_hover = False
        self.theme_hover = ""  # host_wait 模式地图主题 hover 状态
        # 房间内角色选择（host_wait/client_wait 共用）：4 个角色按钮横排
        self.char_rects = {}
        self.char_hover = ""
        char_w, char_gap = 130, 12
        char_total = 4 * char_w + 3 * char_gap
        for i, cid in enumerate(CHARACTER_ORDER):
            self.char_rects[cid] = arcade.XYWH(
                int(cx - char_total / 2 + char_w / 2 + i * (char_w + char_gap)),
                WINDOW_HEIGHT - 240, char_w, 40)
        # 联机对象（host/client 各自非 None）
        self.server = None
        self.bridge = None
        self.client = None
        # 加入输入与状态
        self._ip_buffer = "127.0.0.1"  # 默认本机回环（局域网联机测试）
        self._error = ""
        self._status = ""
        # HELLO+JOIN 握手已发送标志（client_wait 轮询 state=connected 后发一次，防每帧重发）
        self._handshake_sent = False
        # 已加入玩家信息 [(player_id, name, slot)]（host_wait 显示）
        self._players_info: list = []
        # 全员准备状态：host 维护 {player_id: bool}（含 host=0 恒就绪）；
        # client 收到 READY_STATE 后更新 _ready_display（[{player_id, ready, name}] 供显示）
        self._ready_state: dict = {0: True}
        self._ready_display: list = []
        # 房间结束提示（GameView ROOM_ENDED 回大厅时设置，menu 模式顶部显示）
        self._notice = ""
        # 局域网房间发现
        from net.client import RoomDiscovery
        self._discovery = RoomDiscovery()
        self._discovered_rooms: list = []  # 发现的房间列表
        self._discovery_hover_idx = -1  # hover 的房间索引
        self._discovery_scroll_offset = 0  # 房间列表滚动偏移
        self._manual_ip_mode = False  # 是否切换到手动输入IP模式
        self._client_theme = "forest"  # 客户端加入房间时的地图主题（用于战备检查）
        # 联机教程状态
        self.tutorial = LobbyTutorial(self)  # 教程辅助对象（页面构建 + 覆盖层绘制委托）
        self._tutorial_shown = False  # 本次会话是否已显示教程
        self._tutorial_page = 0  # 教程当前页码（0-based）
        self._tutorial_pages = self._build_tutorial_pages()  # 教程页面内容
        self._tutorial_next_rect = arcade.XYWH(WINDOW_WIDTH // 2 + 100, 80, 160, 40)
        self._tutorial_prev_rect = arcade.XYWH(WINDOW_WIDTH // 2 - 100, 80, 160, 40)
        self._tutorial_close_rect = arcade.XYWH(WINDOW_WIDTH // 2, 30, 160, 36)
        self._tutorial_next_hover = False
        self._tutorial_prev_hover = False
        self._tutorial_close_hover = False
        # 从市场/仓库/锻造等房间内页面返回时：自动复用 GameState 中的联机连接（房间保持）
        self._restore_net_connection()
        # 首次进入：显示教程
        if not self._tutorial_shown and not self._restore_net_connection():
            self.mode = "tutorial"
            self._tutorial_shown = True

    def _restore_net_connection(self) -> bool:
        """复用 GameState 中已建立的联机连接进入对应等待模式。

        从市场/仓库/锻造/背包等页面返回大厅时新建 LobbyView 并进入此方法：
        - host 且 net_server 存活 → host_wait（复用服务器，房间保持）
        - client 且 net_client 存活 → client_wait（复用客户端，已连上不重复握手）
        - 单机 / 连接已清理 → 保持 menu
        返回 True 表示已恢复连接（不需要显示教程），False 表示需要显示教程。
        """
        gs = self.window.game_state
        if getattr(gs, "net_mode", "solo") == "host" and gs.net_server is not None:
            self.mode = "host_wait"
            self.server = gs.net_server
            self.bridge = getattr(gs.net_server, "bridge", None)
            self._players_info = gs.net_server.player_info()
            self._status = f"房间已创建，等待玩家加入…（{len(self._players_info) + 1}/4）"
            return True  # 已恢复连接，不需要显示教程
        elif getattr(gs, "net_mode", "solo") == "client" and gs.net_client is not None:
            self.mode = "client_wait"
            self.client = gs.net_client
            self._handshake_sent = True  # 连接已建立：不重复握手
            self._status = "已回到房间，等待主机开始…"
            return True  # 已恢复连接，不需要显示教程
        return False  # 未恢复连接，需要显示教程

    def _build_tutorial_pages(self) -> list[dict]:
        """委托 → tutorial._build_tutorial_pages"""
        return self.tutorial._build_tutorial_pages()

    def _draw_tutorial(self, cx: int):
        """委托 → tutorial._draw_tutorial"""
        return self.tutorial._draw_tutorial(cx)

    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY

    # ─────────────────────────── 状态切换 ───────────────────────────

    def _broadcast_ready_state(self):
        """主机广播 READY_STATE：全员准备状态（含主机=0 恒就绪），供各端同步显示"""
        from net.protocol import MsgType
        gs = self.window.game_state
        if self.server is None:
            return
        players = [{"player_id": 0, "ready": True, "name": gs.player_name}]
        for pid, name, _ in self.server.player_info():
            players.append({"player_id": pid, "ready": self._ready_state.get(pid, False), "name": name})
        self.server.broadcast(MsgType.READY_STATE, {"players": players})

    def _toggle_ready(self):
        """客户端准备/取消准备：战备检查 → 翻转本地 net_ready 并上报 READY 给主机"""
        from net.protocol import MsgType
        from views.map_select_view import check_battle_readiness
        gs = self.window.game_state
        my_id = getattr(gs, "net_player_id", None)
        if my_id is None or self.client is None:
            return
        # 如果是准备操作（非取消），检查战备
        if not gs.net_ready:
            # 客户端战备检查：根据房间地图主题检查装备价值
            passed, error_msg, _ = check_battle_readiness(gs.player_id, self._client_theme, gs.equipped_weapon_id)
            if not passed:
                self._status = f"战备不足：{error_msg}"
                return
        gs.net_ready = not gs.net_ready
        self.client.send((MsgType.READY, {"player_id": my_id, "ready": gs.net_ready}))
        self._status = ("已准备，等待房主开始游戏…" if gs.net_ready else "已取消准备")

    def _enter_host(self):
        """建房：创建 NetServer + NetBridge 并启动监听（进入 host_wait）"""
        from net.thread_bridge import NetBridge
        from net.server import NetServer
        gs = self.window.game_state
        # 兜底：若上一局残留未停止的 net_server（异常路径等），先停止并释放端口，
        # 否则新建服务器 start() 绑定同端口会阻塞 5 秒后失败（第二次联机建房卡死）
        if gs.net_server is not None:
            try:
                gs.net_server.stop()
            except Exception:
                pass  # 停止失败不阻塞建房，start() 会重新尝试绑定
            gs.net_server = None
        try:
            self.bridge = NetBridge()
            self.server = NetServer(
                self.bridge,
                room_id="default",
                seed=random.randint(1, 999999),  # 建房即定种子，ROOM_START 广播给全员
                theme=self.selected_theme,
                max_players=4,
            )
            self.server.start("0.0.0.0", NET_PORT)
        except Exception as exc:
            # 启动失败（端口占用等）：回 menu 显示错误，不崩溃
            self._error = f"建房失败: {exc}"
            self.server = None
            self.bridge = None
            self.mode = "menu"
            return
        # 主机身份固定：player_id=0 / slot=0（出生点偏移约定，见 server.py next_slot）
        gs.net_mode = "host"
        gs.net_server = self.server
        gs.net_player_id = 0
        gs.net_slot = 0
        gs.net_room_id = "default"
        gs.net_max_players = self.server.room.max_players
        self._error = ""
        self._status = f"房间已创建，等待玩家加入…（端口 {NET_PORT}）"
        # 新建房间：准备状态初始化（主机=0 恒就绪，客户端待加入后上报）
        self._ready_state = {0: True}
        self._ready_display = []
        gs.net_ready = False
        gs.net_wait_reason = ""
        # 新房间：角色映射重置（全员需重新选角，开局前必选，见 _host_start_game 校验）
        gs.net_characters = {}
        self.mode = "host_wait"

    def _select_character(self, cid: str):
        """房间内选角（host_wait/client_wait 共用）：
        - 校验角色已解锁（与 character_select_view 同口径，未解锁禁选）
        - 主机：本地权威映射 net_characters[0]=cid（ROOM_START 打包下发）
        - 客户端：上报 SET_CHARACTER 给主机（主机权威映射）+ 本地即时更新显示
        """
        from db.database import get_unlocked_characters
        gs = self.window.game_state
        # 解锁校验：仅"已解锁"角色可选（初始角色恒解锁）
        if gs.player_id:
            unlocked = set(get_unlocked_characters(gs.player_id))
            if cid not in unlocked:
                self._status = f"{CHARACTERS[cid]['name']} 未解锁（需 {CHARACTERS[cid]['price']} 金币），请先在单机市场购买"
                return
        gs.character_id = cid
        if self.mode == "host_wait":
            # 主机：本地权威角色映射（player_id=0）
            gs.net_characters[0] = cid
            self._status = f"已选择角色：{CHARACTERS[cid]['name']}（就绪后可开始游戏）"
        elif self.mode == "client_wait" and self.client is not None:
            # 客户端：上报主机权威映射 + 本地同步显示（主机 ROOM_START 最终下发）
            my_id = getattr(gs, "net_player_id", None)
            if my_id is not None:
                from net.protocol import MsgType
                self.client.send((MsgType.SET_CHARACTER,
                                  {"player_id": my_id, "character_id": cid}))
                gs.net_characters[my_id] = cid
                self._status = f"已选择角色：{CHARACTERS[cid]['name']}（等待主机开始游戏）"
            else:
                self._status = "正在连接主机，连接完成后即可选择角色"
        else:
            self._status = f"已选择角色：{CHARACTERS[cid]['name']}"

    def _host_start_game(self):
        """主机开始游戏：全员就绪检查 → 战备检查 → 计算全房出生点 → 广播 ROOM_START → 本地进入 GameView"""
        from net.protocol import MsgType
        from game.map_gen import generate_map
        from views.map_select_view import check_battle_readiness
        gs = self.window.game_state
        server = self.server
        if server is None or gs.net_server is None:
            return
        # 全员就绪检查：主机(0)恒就绪，所有已入座客户端必须 ready 才能开局
        unready = [pid for pid, _, _ in server.player_info() if not self._ready_state.get(pid, False)]
        if unready:
            self._status = f"还有 {len(unready)} 名玩家未准备，无法开始游戏"
            return
        # 全员角色检查：主机(0) + 所有已入座客户端都必须已选角色（联机开局前必选）
        all_pids = [0] + [pid for pid, _, _ in server.player_info()]
        no_char = [pid for pid in all_pids if gs.net_characters.get(pid) is None]
        if no_char:
            self._status = f"还有 {len(no_char)} 名玩家未选择角色，无法开始游戏"
            return
        # 主机战备检查：根据所选地图主题检查装备价值
        host_passed, host_error, _ = check_battle_readiness(gs.player_id, self.selected_theme, gs.equipped_weapon_id)
        if not host_passed:
            self._status = f"主机战备不足：{host_error}"
            return
        # 开局后重置准备状态（新一局全员重新准备，避免直接继承上一局就绪态）
        self._ready_state = {0: True}
        # 修复「同房间每局地图不变」：建房时定的种子（server.room.seed）只应约束首局，
        # 之后每局重新随机种子并写回，保证同一房间多次开局地图布局各不相同。
        # 服务器线程不读写 seed（仅主线程开局时使用），主线程直接更新无并发风险。
        server.room.seed = random.randint(1, 999999)
        # 玩家列表 = 主机(slot=0) + 已入座客户端（server 权威数据）；每项携带角色 id
        # （客户端 SET_CHARACTER 上报 + 主机本地选角，ROOM_START 下发后各端按角色建玩家）
        players = [{"player_id": 0, "name": gs.player_name, "slot": 0,
                    "character_id": gs.net_characters.get(0, "initial")}]
        for pid, name, slot in server.player_info():
            players.append({"player_id": pid, "name": name, "slot": slot,
                            "character_id": gs.net_characters.get(pid, "initial")})
        # 出生点：以首个房间中心为基准，按槽位对称展开（slot 0..max_players-1）
        map_data = generate_map(server.room.seed, theme=server.room.theme)
        cx, cy = map_data["rooms"][0].center if map_data["rooms"] else (400, 400)
        max_players = server.room.max_players
        for p in players:
            p["x"] = cx + (p["slot"] - (max_players - 1) / 2) * NET_SPAWN_OFFSET
            p["y"] = cy
        # 全房出生点/名册写入 GameState（幽灵出生与状态条用）
        gs.net_spawns = {p["player_id"]: (p["x"], p["y"]) for p in players}
        gs.net_roster = {p["player_id"]: {"name": p["name"], "slot": p["slot"]} for p in players}
        gs.net_spawn = gs.net_spawns[0]  # 主机本端出生点
        # 广播 ROOM_START（客户端据此重建地图 + 出生点）
        server.broadcast(MsgType.ROOM_START, {
            "seed": server.room.seed,
            "theme": server.room.theme,
            "players": players,
        })
        # 主机本地进入 GameView（seed/theme 与广播一致，客户端确定性重建同图）
        gs.current_map_seed = server.room.seed
        gs.map_theme = server.room.theme
        from views.game_view import GameView
        gv = GameView(self.window_ref)
        gv.setup()
        self.window.show_view(gv)

    def _enter_join(self):
        """切换到加入模式：启动房间搜索，清空错误与状态"""
        self._error = ""
        self._status = ""
        self.ip_editing = False
        self._manual_ip_mode = False
        self._discovered_rooms = []
        self._discovery_hover_idx = -1
        self._discovery_scroll_offset = 0
        # 启动局域网房间搜索
        self._discovery.start()
        self._status = "正在搜索局域网房间…"
        self.mode = "join"

    def _client_connect(self):
        """加入：非阻塞发起 NetClient 连接 → 进入 client_wait。

        注意：不在此处阻塞等待连接结果（原 connect() 会卡死主线程最长 7 秒，
        导致窗口冻结/未响应）。改为 connect_async() 立即返回，由 on_update
        每帧轮询 client.state：connecting → connected（补发握手）/ disconnected（报错）。
        """
        from net.client import NetClient
        gs = self.window.game_state
        host = self._ip_buffer.strip() or "127.0.0.1"
        self._error = ""
        self._status = "连接中…"
        client = NetClient()
        self.client = client
        self._handshake_sent = False
        self.mode = "client_wait"
        # 非阻塞发起连接（立即返回，不阻塞 60fps 主循环）；失败/成功由 on_update 轮询收敛
        if not client.connect_async(host, NET_PORT):
            self._error = f"连接失败：{host}:{NET_PORT} 不可达"
            client.stop()
            self.client = None
            self.mode = "join"
            return
        self._status = f"正在连接 {host}:{NET_PORT}…"

    def _apply_room_start(self, payload: dict):
        """客户端应用主机 ROOM_START：填充 GameState 联机字段 → 进入 GameView"""
        from views.game_view import GameView
        gs = self.window.game_state
        players = payload.get("players", [])
        gs.net_roster = {
            p["player_id"]: {"name": p.get("name", ""), "slot": p.get("slot", 0),
                             "character_id": p.get("character_id", "initial")}
            for p in players
        }
        gs.net_spawns = {
            p["player_id"]: (p.get("x", 0), p.get("y", 0))
            for p in players
        }
        # 本端出生点：名册中自己那一项
        my = next((p for p in players if p["player_id"] == gs.net_player_id), None)
        if my is not None:
            gs.net_spawn = (my.get("x", 0), my.get("y", 0))
            # 角色 id 由主机 ROOM_START 权威下发（客户端在房间内选角，见 _select_character）
            gs.character_id = my.get("character_id", "initial")
        gs.net_max_players = len(players)
        # 地图种子/主题与主机一致（客户端确定性重建同图）
        gs.current_map_seed = payload.get("seed", 1)
        gs.map_theme = payload.get("theme", "forest")
        # 新一局：重置准备状态与回房等待原因（客户端重新准备，撤离/死亡原因清零）
        gs.net_ready = False
        gs.net_wait_reason = ""
        self._ready_display = []
        gv = GameView(self.window_ref)
        gv.setup()
        self.window.show_view(gv)

    def _leave(self):
        """返回主菜单：安全停止 server/client/discovery，清理 GameState 联机字段"""
        # 停止房间搜索
        if self._discovery is not None:
            self._discovery.stop()
        gs = self.window.game_state
        if self.server is not None:
            self.server.stop()
        if self.client is not None:
            self.client.stop()
        self.server = None
        self.bridge = None
        self.client = None
        # 清理联机运行时状态（solo 恢复）
        gs.net_mode = "solo"
        gs.net_server = None
        gs.net_client = None
        gs.net_player_id = None
        gs.net_slot = 0
        gs.net_spawn = None
        gs.net_room_id = ""
        gs.net_roster = {}
        gs.net_spawns = {}
        gs.net_characters = {}  # 离开房间：清空角色映射
        from views.start_view import StartView
        self.window.show_view(StartView(self.window_ref))

    # ─────────────────────────── 更新（poll 消息） ───────────────────────────

    def on_update(self, delta_time):
        gs = self.window.game_state
        # 加入模式：更新发现的房间列表
        if self.mode == "join" and self._discovery is not None:
            self._discovered_rooms = self._discovery.get_rooms()
            if self._discovered_rooms and not self._manual_ip_mode:
                self._status = f"发现 {len(self._discovered_rooms)} 个房间，点击加入"
        # 建房等待：刷新已加入玩家列表（server 权威数据）+ 处理 READY 上报 + 新玩家就绪登记
        if self.mode == "host_wait" and self.server is not None:
            self._players_info = self.server.player_info()
            # 本局结束回房（_notice 非空）时保留提示；新局/首次建房显示等待加入
            if self._notice and not self._players_info:
                self._status = self._notice
            else:
                self._status = f"房间已创建，等待玩家加入…（{len(self._players_info) + 1}/4）"
                self._notice = ""
            # 新加入玩家：登记未就绪并广播 READY_STATE（各端同步显示准备状态）
            new_ids = [pid for pid, _, _ in self._players_info if pid not in self._ready_state]
            if new_ids:
                for pid, _, _ in self._players_info:
                    self._ready_state.setdefault(pid, False)
                self._broadcast_ready_state()
            # 处理客户端 READY 上报：更新就绪表并广播（全员就绪由 _host_start_game 判定）
            for inbound in self.server.inbound_poll():
                if not isinstance(inbound, dict):
                    continue
                if inbound.get("msg_type") == "READY":
                    pid = (inbound.get("payload") or {}).get("player_id")
                    ready = bool((inbound.get("payload") or {}).get("ready", False))
                    if pid in self._ready_state and self._ready_state.get(pid) != ready:
                        self._ready_state[pid] = ready
                        self._broadcast_ready_state()
                        print(f"[LobbyView] 玩家 {pid} 准备状态: {ready}")
                elif inbound.get("msg_type") == "SET_CHARACTER":
                    # 客户端选角上报：更新主机权威角色映射（ROOM_START 打包下发全房）
                    pid = (inbound.get("payload") or {}).get("player_id")
                    cid = (inbound.get("payload") or {}).get("character_id")
                    if pid is not None and cid:
                        gs.net_characters[pid] = cid
                        print(f"[LobbyView] 玩家 {pid} 选择角色: {cid}")
        # 加入等待：轮询连接状态推进握手 + poll 握手/房间消息
        if self.mode == "client_wait" and self.client is not None:
            st = self.client.state
            if st == "connecting":
                # 仍在连接中：保持"正在连接…"状态，主循环继续渲染（不冻结窗口）
                pass
            elif st == "connected":
                # 连接成功：首次补发 HELLO+JOIN 握手（防每帧重发）
                if not self._handshake_sent:
                    from net.protocol import MsgType
                    self._handshake_sent = True
                    gs.net_mode = "client"
                    gs.net_client = self.client
                    gs.net_room_id = "default"
                    self._status = f"已连接 {self.client.host}，正在加入房间…"
                    self.client.send((MsgType.HELLO, {"protocol": 1, "name": gs.player_name}))
                    self.client.send((MsgType.JOIN, {"name": gs.player_name, "room_id": "default"}))
            elif st == "disconnected":
                # 连接失败（拒绝/超时/不可达）：报错并回 join 模式
                if not self._error:
                    self._error = f"连接失败：{self.client.host}:{self.client.port} 不可达"
                self._cleanup_client()
                self.mode = "join"
                return
            # 连接已建立后：poll 握手/房间消息
            for msg_type, payload in self.client.poll():
                if msg_type.name == "JOIN_ACCEPT":
                    # 身份下发：记录 player_id/slot（出生点偏移用）
                    gs.net_player_id = payload.get("player_id")
                    gs.net_slot = payload.get("slot", 0)
                    gs.net_room_id = payload.get("room_id", "default")
                    self._status = "已加入房间，等待主机开始游戏…"
                elif msg_type.name == "JOIN_REJECT":
                    self._error = f"加入被拒：{payload.get('reason', '未知原因')}"
                    self._cleanup_client()
                    self.mode = "join"
                    return
                elif msg_type.name == "ROOM_START":
                    self._apply_room_start(payload)
                    return
                elif msg_type.name == "READY_STATE":
                    # 主机广播的全员准备状态：更新显示（ready_display）与本地 net_ready 基准
                    self._ready_display = payload.get("players", [])
                    ready_map = {p.get("player_id"): p.get("ready", False)
                                 for p in self._ready_display}
                    my_id = getattr(gs, "net_player_id", None)
                    if my_id is not None and my_id in ready_map:
                        gs.net_ready = ready_map[my_id]
                elif msg_type.name == "ROOM_ENDED":
                    # 本局结束回房等待：room_closed → 断开回主菜单；all_finished → 保持等待再次开局
                    reason = payload.get("reason", "host_end")
                    if reason == "room_closed":
                        self._error = "房主已关闭房间"
                        self._cleanup_client()
                        from views.start_view import StartView
                        self.window.show_view(StartView(self.window_ref))
                        return
                    # 全员结束：本局结束但房间保留，继续等待主机再次开局（连接保留）
                    gs.run_carried = {}
                    self._status = f"本局已结束，等待房主再次开局…"
                    # 新一局重新准备
                    gs.net_ready = False
            # 断线感知：连接意外中断 → 回 menu 提示
            if self.client.is_disconnected:
                if not self._error:
                    self._error = "与主机的连接已断开"
                self._cleanup_client()
                self.mode = "menu"

    def _cleanup_client(self):
        """停止并清空客户端联机对象（client_wait 失败/断线路径）"""
        gs = self.window.game_state
        if self.client is not None:
            self.client.stop()
        self.client = None
        self._handshake_sent = False  # 复位握手标志，下次连接重新握手
        gs.net_client = None
        gs.net_mode = "solo"
        gs.net_characters = {}  # 断开连接：清空角色映射（重新加入需重新选角）

    def _get_battle_readiness_status(self, theme: str) -> tuple[bool, str]:
        """检查当前玩家的战备状态，返回 (passed, status_text)"""
        from views.map_select_view import check_battle_readiness
        gs = self.window.game_state
        passed, error_msg, info = check_battle_readiness(gs.player_id, theme, gs.equipped_weapon_id)
        equip_value = info.get("equip_value", 0)
        has_artifact = info.get("has_artifact", False)
        if passed:
            return True, f"战备充足（装备价值: {equip_value}）"
        else:
            # 显示更详细的战备信息
            detail = f"装备价值: {equip_value}"
            if theme == "space":
                detail += f"，神器: {'有' if has_artifact else '无'}"
            return False, f"战备不足（{detail}）：{error_msg}"

    # ─────────────────────────── 绘制 ───────────────────────────

    def on_draw(self):
        self.clear()
        cx = WINDOW_WIDTH // 2
        # 标题
        self._tc.text("lobby_title", "局域网联机", cx, WINDOW_HEIGHT - 80,
                      arcade.color.GOLD, size=40, anchor_x="center", bold=True)
        if self.mode == "tutorial":
            self._draw_tutorial(cx)
        elif self.mode == "menu":
            self._draw_menu(cx)
        elif self.mode == "host_wait":
            self._draw_host_wait(cx)
        elif self.mode == "join":
            self._draw_join(cx)
        elif self.mode == "client_wait":
            self._draw_client_wait(cx)

    def _draw_menu(self, cx):
        """menu：建房 / 加入 / 返回"""
        # 建房按钮
        color = arcade.color.CORNFLOWER_BLUE if self.host_hover else arcade.color.STEEL_BLUE
        arcade.draw_rect_filled(self.host_rect, color)
        arcade.draw_rect_outline(self.host_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("btn_host", "建 房", self.host_rect.center_x, self.host_rect.center_y,
                      arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center")
        # 加入按钮
        jcolor = arcade.color.DARK_ORANGE if self.join_hover else (150, 100, 40)
        arcade.draw_rect_filled(self.join_rect, jcolor)
        arcade.draw_rect_outline(self.join_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("btn_join", "加 入", self.join_rect.center_x, self.join_rect.center_y,
                      arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center")
        # 返回
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("back", "返回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center")
        # 通知（房间结束回大厅等提示，无则空串不显示）
        if self._notice:
            self._tc.text("notice", self._notice, cx, WINDOW_HEIGHT // 2 + 100,
                          arcade.color.ORANGE_RED, size=14, anchor_x="center", bold=True)

    def _draw_host_wait(self, cx):
        """host_wait：房间信息 / 地图主题选择 / 角色选择 / 玩家列表（含准备状态）/ 开始游戏 / 关闭房间 / 市场仓库"""
        gs = self.window.game_state
        # 房间信息
        self._tc.text("room_info", f"房间: {gs.net_room_id}  |  主题: {self.selected_theme}",
                      cx, WINDOW_HEIGHT - 150, arcade.color.LIGHT_GRAY, size=16,
                      anchor_x="center")
        self._tc.text("room_status", self._status, cx, WINDOW_HEIGHT - 180,
                      arcade.color.CYAN, size=13, anchor_x="center")
        # 主机战备状态显示
        host_ready, host_status = self._get_battle_readiness_status(self.selected_theme)
        status_color = arcade.color.GREEN if host_ready else arcade.color.ORANGE_RED
        self._tc.text("host_readiness", f"[主机] {host_status}", cx, WINDOW_HEIGHT - 200,
                      status_color, size=12, anchor_x="center")
        # 地图主题选择（建房后主机可在此切换，开局前确认最终主题）
        self._tc.text("theme_label", "选择地图:", cx, WINDOW_HEIGHT - 325,
                      arcade.color.LIGHT_GRAY, size=13, anchor_x="center")
        for tid, (tname, tcolor) in self.THEMES.items():
            rect = self.theme_rects[tid]
            sel = tid == self.selected_theme
            hov = self.theme_hover == tid
            # 选中/悬停时提亮底色
            if sel:
                base = (min(255, tcolor[0] + 30), min(255, tcolor[1] + 30), min(255, tcolor[2] + 30))
            elif hov:
                base = (min(255, tcolor[0] + 15), min(255, tcolor[1] + 15), min(255, tcolor[2] + 15))
            else:
                base = tcolor
            arcade.draw_rect_filled(rect, base)
            arcade.draw_rect_outline(rect, arcade.color.GOLD if sel else arcade.color.WHITE,
                                     border_width=2 if sel else 1)
            self._tc.text(f"theme_{tid}", tname, rect.center_x, rect.center_y,
                          arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center")
        # 角色选择区（开局前必选，主机本人在此选角）
        self._draw_char_select(cx)
        # 玩家列表（主机 + 已加入客户端，含准备状态）
        players = [("(主机) " + gs.player_name, 0, True)] + [
            (f"玩家{pid}: {name}", slot, self._ready_state.get(pid, False))
            for pid, name, slot in self._players_info
        ]
        y = WINDOW_HEIGHT // 2 + 80
        for i, (name, slot, ready) in enumerate(players):
            self._tc.text(f"plist_{i}", f"[槽位{slot}] {name}", cx, y,
                          arcade.color.WHITE, size=15, anchor_x="center")
            self._tc.text(f"plist_ready_{i}", ("✔ 已准备" if ready else "✘ 未准备"),
                          cx + 170, y, arcade.color.GREEN if ready else arcade.color.ORANGE_RED,
                          size=13, anchor_x="center")
            y -= 28
        # 开始游戏按钮（全员就绪判定在 _host_start_game，按钮常亮便于提示）
        color = arcade.color.GREEN if self.start_hover else (40, 110, 60)
        arcade.draw_rect_filled(self.start_rect, color)
        arcade.draw_rect_outline(self.start_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("btn_start", "开 始 游 戏", self.start_rect.center_x, self.start_rect.center_y,
                      arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center")
        # 市场 / 仓库入口（房间内可补给装备，与开始菜单一致）
        wcolor = arcade.color.CORNFLOWER_BLUE if self.warehouse_hover else arcade.color.STEEL_BLUE
        arcade.draw_rect_filled(self.warehouse_rect, wcolor)
        arcade.draw_rect_outline(self.warehouse_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("btn_warehouse", "仓  库", self.warehouse_rect.center_x,
                      self.warehouse_rect.center_y, arcade.color.WHITE, size=16,
                      anchor_x="center", anchor_y="center")
        mcolor = arcade.color.DARK_ORANGE if self.market_hover else (150, 100, 40)
        arcade.draw_rect_filled(self.market_rect, mcolor)
        arcade.draw_rect_outline(self.market_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("btn_market", "市  场", self.market_rect.center_x,
                      self.market_rect.center_y, arcade.color.WHITE, size=16,
                      anchor_x="center", anchor_y="center")
        # 关闭房间（解散房间回主菜单；server 保留在 gs，_leave 统一停止）
        ccolor = arcade.color.DARK_RED if self.close_hover else (120, 40, 40)
        arcade.draw_rect_filled(self.close_rect, ccolor)
        arcade.draw_rect_outline(self.close_rect, arcade.color.WHITE, border_width=2)
        self._tc.text("btn_close", "关闭房间", self.close_rect.center_x, self.close_rect.center_y,
                      arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center")
        # 返回
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("back", "返回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center")

    def _draw_join(self, cx):
        """join：搜索到的房间列表 / 手动输入IP / 返回"""
        # 标题
        self._tc.text("join_title", "搜索局域网房间", cx, WINDOW_HEIGHT - 140,
                      arcade.color.WHITE, size=20, anchor_x="center")
        
        # 房间列表区域
        room_list_top = WINDOW_HEIGHT - 180
        room_list_bottom = WINDOW_HEIGHT // 2 + 40
        room_item_height = 60
        
        if self._discovered_rooms and not self._manual_ip_mode:
            # 显示搜索到的房间列表
            self._tc.text("room_list_title", f"发现 {len(self._discovered_rooms)} 个房间：",
                          60, room_list_top, arcade.color.LIGHT_GRAY, size=14)
            y = room_list_top - 30
            for i, room in enumerate(self._discovered_rooms):
                if y < room_list_bottom:
                    break
                # 房间条目背景
                room_rect = arcade.XYWH(cx, y, 500, room_item_height)
                is_hover = self._discovery_hover_idx == i
                bg_color = (60, 80, 60) if is_hover else (40, 50, 50)
                arcade.draw_rect_filled(room_rect, bg_color)
                arcade.draw_rect_outline(room_rect, arcade.color.WHITE if is_hover else arcade.color.GRAY, 1)
                
                # 房间信息
                theme_names = {"forest": "幽暗森林", "desert": "沙漠荒地", "space": "航天基地"}
                theme_name = theme_names.get(room.get("theme", ""), room.get("theme", ""))
                self._tc.text(f"room_{i}_info", 
                              f"{room.get('host_name', '未知')} 的房间 | {theme_name} | {room.get('player_count', 0)}/{room.get('max_players', 4)}人",
                              80, y + 15, arcade.color.WHITE, size=14)
                self._tc.text(f"room_{i}_ip", 
                              f"IP: {room.get('host_ip', '?')}:{room.get('port', 8765)}",
                              80, y - 10, arcade.color.LIGHT_GRAY, size=12)
                # "加入" 按钮
                join_btn = arcade.XYWH(cx + 200, y, 80, 30)
                arcade.draw_rect_filled(join_btn, arcade.color.DARK_GREEN)
                self._tc.text(f"room_{i}_join", "加入", join_btn.center_x, join_btn.center_y,
                              arcade.color.WHITE, 12, anchor_x="center", anchor_y="center")
                y -= room_item_height + 5
        else:
            # 无房间或手动模式
            if not self._manual_ip_mode:
                self._tc.text("no_room", "未发现房间，请稍候或手动输入IP", cx, room_list_top - 50,
                              arcade.color.GRAY, size=14, anchor_x="center")
        
        # 手动输入IP按钮
        manual_btn = arcade.XYWH(cx, room_list_bottom - 40, 200, 36)
        manual_color = arcade.color.DARK_ORANGE if not self._manual_ip_mode else (150, 100, 40)
        arcade.draw_rect_filled(manual_btn, manual_color)
        arcade.draw_rect_outline(manual_btn, arcade.color.WHITE, 2)
        self._tc.text("btn_manual", "手动输入IP", manual_btn.center_x, manual_btn.center_y,
                      arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")
        self._manual_btn_rect = manual_btn
        
        # 手动输入IP区域（仅手动模式显示）
        if self._manual_ip_mode:
            # IP 输入框
            box_color = arcade.color.DARK_GRAY if not self.ip_editing else (70, 90, 70)
            arcade.draw_rect_filled(self.ip_rect, box_color)
            arcade.draw_rect_outline(self.ip_rect,
                                     arcade.color.GOLD if self.ip_editing else arcade.color.WHITE,
                                     border_width=2)
            self._tc.text("ip_label", "主机 IP:", self.ip_rect.center_x - 120, self.ip_rect.center_y,
                          arcade.color.LIGHT_GRAY, size=14, anchor_x="center", anchor_y="center")
            self._tc.text("ip_value", self._ip_buffer, self.ip_rect.center_x + 10,
                          self.ip_rect.center_y, arcade.color.WHITE, size=16,
                          anchor_x="center", anchor_y="center")
            self._tc.text("ip_hint", "（点击输入框后直接键入，回车连接）",
                          cx, self.ip_rect.center_y - 32, arcade.color.GRAY, size=10,
                          anchor_x="center")
            # 连接按钮
            ccolor = arcade.color.CORNFLOWER_BLUE if self.connect_hover else arcade.color.STEEL_BLUE
            arcade.draw_rect_filled(self.connect_rect, ccolor)
            arcade.draw_rect_outline(self.connect_rect, arcade.color.WHITE, border_width=2)
            self._tc.text("btn_connect", "连 接", self.connect_rect.center_x, self.connect_rect.center_y,
                          arcade.color.WHITE, size=22, anchor_x="center", anchor_y="center")
        
        # 刷新按钮
        refresh_btn = arcade.XYWH(cx + 150, room_list_bottom - 40, 80, 36)
        arcade.draw_rect_filled(refresh_btn, arcade.color.STEEL_BLUE)
        self._tc.text("btn_refresh", "刷新", refresh_btn.center_x, refresh_btn.center_y,
                      arcade.color.WHITE, 12, anchor_x="center", anchor_y="center")
        self._refresh_btn_rect = refresh_btn
        
        # 返回
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("back", "返回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, size=14, anchor_x="center", anchor_y="center")
        # 状态提示
        if self._status:
            self._tc.text("status", self._status, cx, WINDOW_HEIGHT - 160,
                          arcade.color.CYAN, size=12, anchor_x="center")
        # 错误提示（红色）
        if self._error:
            self._tc.text("err", self._error, cx, WINDOW_HEIGHT - 200,
                          arcade.color.RED, size=14, anchor_x="center")

    def _draw_char_select(self, cx):
        """房间内角色选择区（host_wait/client_wait 共用）：
        - 4 角色按钮横排（CHARACTER_ORDER 顺序，位置见 __init__ char_rects）
        - 已选中：金色边框 + 原色高亮；未解锁：灰显 + 价格；hover：提亮
        - 标题提示"开局前必选"（主机 _host_start_game 全员角色校验）
        """
        gs = self.window.game_state
        # 解锁集合：仅已解锁角色可选（联机房间内禁选未购买角色，与单机选角同口径）
        from db.database import get_unlocked_characters
        unlocked = set(get_unlocked_characters(gs.player_id)) if gs.player_id else {"initial"}
        self._tc.text("char_title", "选择角色（开局前必选）", cx, WINDOW_HEIGHT - 215,
                      arcade.color.LIGHT_GRAY, size=13, anchor_x="center")
        for cid, rect in self.char_rects.items():
            char = CHARACTERS[cid]
            selected = gs.character_id == cid
            locked = cid not in unlocked
            hover = self.char_hover == cid
            if locked:
                base = (70, 70, 70)  # 未解锁：灰显
            elif selected:
                base = char["color"]  # 已选中：原色 + 金色边框
            elif hover:
                base = (min(255, char["color"][0] + 40), min(255, char["color"][1] + 40),
                        min(255, char["color"][2] + 40))
            else:
                base = (max(30, char["color"][0] - 40), max(30, char["color"][1] - 40),
                        max(30, char["color"][2] - 40))
            arcade.draw_rect_filled(rect, base)
            arcade.draw_rect_outline(rect, arcade.color.GOLD if selected else arcade.color.WHITE,
                                     border_width=2 if selected else 1)
            label = char["name"] + (f"({char.get('price', 0)}金)" if locked else "")
            self._tc.text(f"char_{cid}", label, rect.center_x, rect.center_y,
                          arcade.color.WHITE, size=13, anchor_x="center", anchor_y="center")

    def _draw_client_wait(self, cx):
        """client_wait：连接状态 / 准备按钮 / 全员准备状态 / 市场仓库 / 取消"""
        gs = self.window.game_state
        self._tc.text("cw_status", self._status, cx, WINDOW_HEIGHT // 2 + 60,
                      arcade.color.CYAN, size=18, anchor_x="center")
        # 连接尚未建立（handshake 未发）→ 显示"连接中"；已连上 → 显示等待房主
        tip = "正在连接主机，请稍候…" if not self._handshake_sent else "已加入房间，准备开始游戏…"
        self._tc.text("cw_tip", tip, cx, WINDOW_HEIGHT // 2 + 32,
                      arcade.color.LIGHT_GRAY, size=13, anchor_x="center")
        # 客户端战备状态显示（连接建立后显示）
        if self._handshake_sent:
            client_ready, client_status = self._get_battle_readiness_status(self._client_theme)
            status_color = arcade.color.GREEN if client_ready else arcade.color.ORANGE_RED
            self._tc.text("client_readiness", client_status, cx, WINDOW_HEIGHT // 2 + 15,
                          status_color, size=12, anchor_x="center")
        # 角色选择区（连接建立后可用，点击上报 SET_CHARACTER 给主机权威映射）
        if self._handshake_sent:
            self._draw_char_select(cx)
        # 全员准备状态（READY_STATE 广播驱动）
        if self._ready_display:
            y = WINDOW_HEIGHT // 2 + 8
            for p in self._ready_display:
                name = p.get("name", f"玩家{p.get('player_id')}")
                ready = p.get("ready", False)
                self._tc.text(f"cw_ready_{p.get('player_id')}", f"{name}：",
                              cx - 40, y, arcade.color.WHITE, size=14, anchor_x="center")
                self._tc.text(f"cw_ready_state_{p.get('player_id')}",
                              ("✔ 已准备" if ready else "✘ 未准备"),
                              cx + 40, y, arcade.color.GREEN if ready else arcade.color.ORANGE_RED,
                              size=14, anchor_x="center")
                y -= 22
        # 准备 / 取消准备按钮（已连接后可用）
        if self._handshake_sent:
            rcolor = arcade.color.GREEN if self.ready_hover else (40, 110, 60)
            if gs.net_ready:
                rcolor = arcade.color.DARK_ORANGE if self.ready_hover else (150, 100, 40)
            arcade.draw_rect_filled(self.ready_rect, rcolor)
            arcade.draw_rect_outline(self.ready_rect, arcade.color.WHITE, border_width=2)
            self._tc.text("btn_ready", ("取消准备" if gs.net_ready else "准  备"),
                          self.ready_rect.center_x, self.ready_rect.center_y,
                          arcade.color.WHITE, size=18, anchor_x="center", anchor_y="center")
            # 市场 / 仓库入口
            wcolor = arcade.color.CORNFLOWER_BLUE if self.warehouse_hover else arcade.color.STEEL_BLUE
            arcade.draw_rect_filled(self.warehouse_rect, wcolor)
            arcade.draw_rect_outline(self.warehouse_rect, arcade.color.WHITE, border_width=2)
            self._tc.text("btn_warehouse", "仓  库", self.warehouse_rect.center_x,
                          self.warehouse_rect.center_y, arcade.color.WHITE, size=16,
                          anchor_x="center", anchor_y="center")
            mcolor = arcade.color.DARK_ORANGE if self.market_hover else (150, 100, 40)
            arcade.draw_rect_filled(self.market_rect, mcolor)
            arcade.draw_rect_outline(self.market_rect, arcade.color.WHITE, border_width=2)
            self._tc.text("btn_market", "市  场", self.market_rect.center_x,
                          self.market_rect.center_y, arcade.color.WHITE, size=16,
                          anchor_x="center", anchor_y="center")
        # 取消（返回菜单）
        cancel_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2 - 210, 160, 40)
        arcade.draw_rect_filled(cancel_rect, arcade.color.DARK_RED)
        self._tc.text("btn_cancel", "离开房间", cancel_rect.center_x, cancel_rect.center_y,
                      arcade.color.WHITE, size=16, anchor_x="center", anchor_y="center")
        self._cancel_rect = cancel_rect
        # 错误提示
        if self._error:
            self._tc.text("err", self._error, cx, WINDOW_HEIGHT - 140,
                          arcade.color.RED, size=14, anchor_x="center")

    # ─────────────────────────── 输入 ───────────────────────────

    def on_mouse_motion(self, x, y, dx, dy):
        self.host_hover = self.host_rect.point_in_rect((x, y))
        self.join_hover = self.join_rect.point_in_rect((x, y))
        self.back_hover = self.back_rect.point_in_rect((x, y))
        self.start_hover = self.start_rect.point_in_rect((x, y))
        self.connect_hover = self.connect_rect.point_in_rect((x, y))
        self.close_hover = self.close_rect.point_in_rect((x, y))
        self.ready_hover = self.ready_rect.point_in_rect((x, y))
        self.warehouse_hover = self.warehouse_rect.point_in_rect((x, y))
        self.market_hover = self.market_rect.point_in_rect((x, y))
        # 教程按钮 hover
        self._tutorial_next_hover = self._tutorial_next_rect.point_in_rect((x, y))
        self._tutorial_prev_hover = self._tutorial_prev_rect.point_in_rect((x, y))
        self._tutorial_close_hover = self._tutorial_close_rect.point_in_rect((x, y))
        # 房间内角色选择按钮 hover（host_wait/client_wait 有效）
        self.char_hover = ""
        if self.mode in ("host_wait", "client_wait"):
            for cid, rect in self.char_rects.items():
                if rect.point_in_rect((x, y)):
                    self.char_hover = cid
                    break
        # host_wait 模式地图主题按钮 hover
        self.theme_hover = ""
        if self.mode == "host_wait":
            for tid, rect in self.theme_rects.items():
                if rect.point_in_rect((x, y)):
                    self.theme_hover = tid
                    break

    def _open_warehouse(self):
        """房间内打开仓库（联机保持连接，返回时回 LobbyView 复用连接）"""
        from views.warehouse_view import WarehouseView
        self.window.show_view(WarehouseView(self.window_ref))

    def _open_market(self):
        """房间内打开市场（联机保持连接，返回时回 LobbyView 复用连接）"""
        from views.market_view import MarketView
        self.window.show_view(MarketView(self.window_ref))

    def _close_room(self):
        """主机关闭房间：广播 ROOM_ENDED(room_closed) → 停止服务器 → 回主菜单"""
        from net.protocol import MsgType
        gs = self.window.game_state
        # 停止房间搜索
        if self._discovery is not None:
            self._discovery.stop()
        if self.server is not None:
            try:
                self.server.broadcast(MsgType.ROOM_ENDED, {"reason": "room_closed"})
            except Exception:
                pass  # 广播失败不阻塞关房，stop() 会正常释放端口
            self.server.stop()
        self.server = None
        self.bridge = None
        # 清理联机运行时状态（与 _leave 同口径，回主菜单）
        gs.net_mode = "solo"
        gs.net_server = None
        gs.net_client = None
        gs.net_player_id = None
        gs.net_slot = 0
        gs.net_spawn = None
        gs.net_room_id = ""
        gs.net_roster = {}
        gs.net_spawns = {}
        gs.net_characters = {}  # 关闭房间：清空角色映射（全员重新选角）
        gs.run_carried = {}
        from views.start_view import StartView
        self.window.show_view(StartView(self.window_ref))

    def on_mouse_press(self, x, y, button, modifiers):
        sound_manager.play_ui()
        # 教程模式：处理教程按钮点击
        if self.mode == "tutorial":
            total = len(self._tutorial_pages)
            # 下一页
            if self._tutorial_page < total - 1 and self._tutorial_next_rect.point_in_rect((x, y)):
                self._tutorial_page += 1
            # 上一页
            elif self._tutorial_page > 0 and self._tutorial_prev_rect.point_in_rect((x, y)):
                self._tutorial_page -= 1
            # 关闭教程
            elif self._tutorial_close_rect.point_in_rect((x, y)):
                self.mode = "menu"
            return
        # 返回按钮（除 join 的输入框点击外，各模式共用；host_wait 返回=关闭房间，客户端能看到"房主已关闭房间"）
        if self.mode != "join" and self.back_rect.point_in_rect((x, y)):
            if self.mode == "host_wait":
                self._close_room()
            else:
                self._leave()
            return
        if self.mode == "menu":
            if self.host_rect.point_in_rect((x, y)):
                self._enter_host()
            elif self.join_rect.point_in_rect((x, y)):
                self._enter_join()
            elif self.back_rect.point_in_rect((x, y)):
                self._leave()
        elif self.mode == "host_wait":
            if self.start_rect.point_in_rect((x, y)):
                self._host_start_game()
            elif self.warehouse_rect.point_in_rect((x, y)):
                self._open_warehouse()
            elif self.market_rect.point_in_rect((x, y)):
                self._open_market()
            elif self.close_rect.point_in_rect((x, y)):
                self._close_room()
            # 房间内选角（主机本人）
            for cid, rect in self.char_rects.items():
                if rect.point_in_rect((x, y)):
                    self._select_character(cid)
                    break
            # host_wait 模式：地图主题切换（更新本地选择 + 服务器房间主题）
            for tid, rect in self.theme_rects.items():
                if rect.point_in_rect((x, y)):
                    self.selected_theme = tid
                    # 同步更新服务器房间主题，保证 _host_start_game 使用最新选择
                    if self.server is not None:
                        self.server.room.theme = tid
                    break
        elif self.mode == "join":
            # 手动输入IP模式按钮
            if hasattr(self, '_manual_btn_rect') and self._manual_btn_rect.point_in_rect((x, y)):
                self._manual_ip_mode = not self._manual_ip_mode
                self.ip_editing = False
                self._error = ""
                return
            # 刷新按钮
            if hasattr(self, '_refresh_btn_rect') and self._refresh_btn_rect.point_in_rect((x, y)):
                self._discovered_rooms = []
                self._discovery.send_query()
                self._status = "正在搜索…"
                return
            if self._manual_ip_mode:
                # 手动输入IP模式
                if self.ip_rect.point_in_rect((x, y)):
                    # 点击输入框：进入编辑态
                    self.ip_editing = True
                    return
                if self.connect_rect.point_in_rect((x, y)):
                    self.ip_editing = False
                    self._client_connect()
            else:
                # 房间列表模式：检查是否点击了某个房间的"加入"按钮
                room_list_top = WINDOW_HEIGHT - 180
                room_item_height = 60
                y_start = room_list_top - 30
                for i, room in enumerate(self._discovered_rooms):
                    item_y = y_start - i * (room_item_height + 5)
                    join_btn = arcade.XYWH(cx + 200, item_y, 80, 30)
                    if join_btn.point_in_rect((x, y)):
                        # 点击加入按钮：使用该房间的IP连接，并存储地图主题用于战备检查
                        self._ip_buffer = room.get("host_ip", "127.0.0.1")
                        self._client_theme = room.get("theme", "forest")
                        self._client_connect()
                        return
            # 返回按钮
            if self.back_rect.point_in_rect((x, y)):
                if self._discovery is not None:
                    self._discovery.stop()
                self.ip_editing = False
                self.mode = "menu"
        elif self.mode == "client_wait":
            cancel_rect = getattr(self, "_cancel_rect", None)
            if cancel_rect is not None and cancel_rect.point_in_rect((x, y)):
                self._cleanup_client()
                self.mode = "menu"
            elif self.ready_rect.point_in_rect((x, y)) and self._handshake_sent:
                self._toggle_ready()
            elif self.warehouse_rect.point_in_rect((x, y)) and self._handshake_sent:
                self._open_warehouse()
            elif self.market_rect.point_in_rect((x, y)) and self._handshake_sent:
                self._open_market()
            # 房间内选角（客户端：上报 SET_CHARACTER，需连接已建立才有 player_id）
            if self._handshake_sent:
                for cid, rect in self.char_rects.items():
                    if rect.point_in_rect((x, y)):
                        self._select_character(cid)
                        break

    def on_key_press(self, key, modifiers):
        """IP 输入编辑（仅 join 模式 + 输入框聚焦时生效）"""
        if self.mode != "join" or not self.ip_editing:
            return
        if key == arcade.key.ENTER:
            # 回车：提交连接
            self.ip_editing = False
            self._client_connect()
            return
        if key == arcade.key.BACKSPACE:
            self._ip_buffer = self._ip_buffer[:-1]
            return
        if key == arcade.key.ESCAPE:
            self.ip_editing = False
            return
        # 数字与点（IP 地址字符）
        if arcade.key.KEY_0 <= key <= arcade.key.KEY_9:
            self._ip_buffer += chr(ord("0") + (key - arcade.key.KEY_0))
        elif key == arcade.key.PERIOD:
            self._ip_buffer += "."
