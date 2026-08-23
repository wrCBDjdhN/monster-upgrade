"""Arcade 窗口入口与共享游戏状态

主入口文件，负责：
1. 创建游戏窗口
2. 初始化共享游戏状态 (GameState)
3. 启动第一个视图 (StartView)
4. 运行 Arcade 主循环

GameState 是各 View 共享的运行时状态，包含：
- player_id: 玩家数据库 ID
- player_name: 玩家名称
- run_carried: 本次运行携带的物品
- current_weapon_kind/id: 当前装备的武器
- equipped_weapon_id: 从仓库选择携带的武器 ID
- current_map_seed: 当前地图随机种子
- net_mode: 联机模式(solo=单机 / host=主机 / client=客户端)
"""

import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_TITLE,
    WINDOW_RESIZABLE, FULLSCREEN,
)
from pyglet.math import Mat4
from arcade.camera.default import ViewportProjector
from arcade.types import LBWH


class FixedLogicalProjector(ViewportProjector):
    """固定逻辑分辨率投影器

    全部视图的绘制/布局坐标都以 WINDOW_WIDTH×WINDOW_HEIGHT（逻辑分辨率）为准，
    窗口实际尺寸变化时（最大化/全屏/拖拽缩放）不能改变逻辑坐标系，否则画面会
    只占窗口一角、点击坐标全部错位。本投影器将投影固定为逻辑分辨率，视口按
    窗口实际尺寸等比缩放并居中（letterbox），保证任何尺寸下画面不拉伸、不失真。
    """

    def __init__(self, *, context=None):
        super().__init__(viewport=LBWH(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT), context=context)

    def use(self):
        """按窗口当前实际尺寸重算 letterbox 视口，并应用固定逻辑投影。"""
        # 先把自己设为当前相机，再直接改 framebuffer 的 viewport，
        # 避免经 ctx.viewport setter 触发「默认相机回调 use()」造成递归
        self._ctx.current_camera = self
        # 关键：arcade 的 DefaultFrameBuffer.viewport setter 内部会 ×get_pixel_ratio()
        # 把「逻辑像素」换算成 GL 物理像素（getter 反向 ÷），因此这里必须以「窗口逻辑像素」
        # （get_size()，本机 DPI 1.25 下为 1280×720）计算视口再赋值。
        # 若直接用 ctx.screen.size（物理像素）计算，会被二次放大 1.25 倍，
        # 导致 GL 视口超出 framebuffer、画面整体偏移、点击错位（修复前 bug）。
        log_w, log_h = self._ctx.window.get_size()
        scale = min(log_w / WINDOW_WIDTH, log_h / WINDOW_HEIGHT)
        vw = max(1, round(WINDOW_WIDTH * scale))
        vh = max(1, round(WINDOW_HEIGHT * scale))
        vx = (log_w - vw) // 2
        vy = (log_h - vh) // 2
        self._ctx.active_framebuffer.viewport = (vx, vy, vw, vh)
        self._ctx.view_matrix = Mat4()
        # 投影固定为逻辑分辨率（_projection_matrix 在基类构造时按
        # LBWH(0,0,WINDOW_WIDTH,WINDOW_HEIGHT) 生成）
        self._ctx.projection_matrix = self._projection_matrix


class GameWindow(arcade.Window):
    """游戏窗口（支持最大化 / F11 全屏切换）

    - resizable=True：标题栏最大化按钮与拖拽缩放可用
    - 默认投影器替换为 FixedLogicalProjector：渲染坐标系恒为逻辑分辨率，
      窗口实际尺寸变化由投影器等比缩放（letterbox）
    - dispatch_event 拦截：
      * 鼠标事件坐标从物理像素转换为逻辑坐标（与渲染坐标同一口径，保证点击命中）
      * F11 键全局切换全屏/窗口模式（各视图无需各自处理）
    """

    # pyglet 鼠标事件：前两个参数均为 (x, y) 窗口坐标
    _MOUSE_EVENTS = {
        "on_mouse_press",
        "on_mouse_release",
        "on_mouse_motion",
        "on_mouse_drag",
        "on_mouse_scroll",
        "on_mouse_enter",
        "on_mouse_leave",
    }

    def __init__(self):
        super().__init__(WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_TITLE,
                         resizable=WINDOW_RESIZABLE, fullscreen=FULLSCREEN)
        # 替换默认投影器为固定逻辑分辨率投影器，并立即应用（初始 1280×720 下
        # 视口恰为全窗口，行为与改动前完全一致）
        self.ctx._default_camera = FixedLogicalProjector(context=self.ctx)
        self.ctx.current_camera = self.ctx._default_camera
        self.ctx.current_camera.use()

    def _letterbox(self):
        """当前窗口的 letterbox 视口几何：返回 (vx, vy, vw, vh, scale)。

        以「窗口逻辑像素」（pyglet 鼠标事件所在空间，get_size()）为基准计算：
        - 窗口逻辑像素 = 物理像素 ÷ DPI 缩放比（本机 1.25，1280×720 → 1600×900）
        - pyglet 鼠标事件坐标已除以 _mouse_scale，落在逻辑像素空间（0..逻辑宽）
        - GL 视口必须用物理像素（FixedLogicalProjector.use() 内部换算），
          两者只差 DPI 缩放比，等比关系一致，故鼠标反变换必须用本函数而非物理视口
        """
        log_w, log_h = self.get_size()
        scale = min(log_w / WINDOW_WIDTH, log_h / WINDOW_HEIGHT)
        vw = WINDOW_WIDTH * scale
        vh = WINDOW_HEIGHT * scale
        vx = (log_w - vw) / 2.0
        vy = (log_h - vh) / 2.0
        return vx, vy, vw, vh, scale

    def _to_logical(self, x, y):
        """窗口逻辑像素坐标（pyglet 鼠标事件口径） → 逻辑 WINDOW_WIDTH×WINDOW_HEIGHT 坐标"""
        vx, vy, _vw, _vh, scale = self._letterbox()
        return (x - vx) / scale, (y - vy) / scale

    def _toggle_fullscreen(self):
        """F11：切换全屏/窗口模式（退出全屏时自动恢复原窗口尺寸）"""
        self.set_fullscreen(not self.fullscreen)

    def dispatch_event(self, event_type, *args):
        """拦截事件：鼠标坐标物理→逻辑转换 + F11 全局全屏切换。"""
        if event_type == "on_key_press" and args and args[0] == arcade.key.F11:
            # F11 全屏切换：全局拦截，不传给当前视图（各界面统一生效）
            self._toggle_fullscreen()
            return True  # pyglet EVENT_HANDLED：停止向下分发

        if event_type in self._MOUSE_EVENTS and len(args) >= 2:
            x, y = self._to_logical(args[0], args[1])
            if event_type == "on_mouse_motion":
                # pyglet win32 特例：on_mouse_motion 的 dx/dy 为「物理像素」（已 ×_mouse_scale，
                # 见 pyglet win32 _event_mousemove），而 x/y 为逻辑像素，口径不同。
                # 统一换算到游戏逻辑坐标：dx 先 ÷DPI 比（物理→逻辑），再 ÷letterbox scale。
                _vx, _vy, _vw, _vh, scale = self._letterbox()
                dpi_ratio = self.ctx.screen.size[0] / max(self.get_size()[0], 1)
                args = (x, y, args[2] / (scale * dpi_ratio), args[3] / (scale * dpi_ratio)) + args[4:]
            elif event_type == "on_mouse_drag":
                # pyglet win32：on_mouse_drag 的 dx/dy 已 ÷_mouse_scale（逻辑像素，与 x/y 同口径），
                # 只需再 ÷letterbox scale 得到游戏逻辑坐标增量。
                _vx, _vy, _vw, _vh, scale = self._letterbox()
                args = (x, y, args[2] / scale, args[3] / scale) + args[4:]
            else:
                args = (x, y) + args[2:]

        return super().dispatch_event(event_type, *args)


class TutorialState:
    """新手教程运行状态（仅首次启动启用）

    - active：教程是否激活（DB settings 未标记 tutorial_done 时首次启动为 True）
    - stage：当前阶段（0=开始界面 1=角色选择 2=地图选择 3=游戏内
         4=撤离结算+航天基地教学 5=市场教学 6=完成）
    - page：当前阶段内的向导页码
    - kill_count / pickup_count：游戏内引导的击杀/拾取计数（阶段 3 检测用）
    - minimap_taught：小地图是否已讲解（避免重复弹讲解）
    - boss_taught：BOSS房间是否已讲解（避免重复弹讲解）
    - 教程中途退出（关游戏）不写 tutorial_done，下次启动从头开始
    """

    def __init__(self, active: bool):
        self.active = active
        self.stage = 0
        self.page = 0
        self.kill_count = 0
        self.pickup_count = 0
        self.minimap_taught = False
        self.boss_taught = False


class GameState:
    """各 View 共享的运行时状态
    
    该类的实例存储在 window.game_state 中，所有视图都可以访问。
    主要用途是在不同视图之间传递玩家数据和游戏状态。
    """
    def __init__(self):
        self.player_id: int | None = None          # 玩家数据库 ID（登录后设置）
        self.player_name: str = "hero"              # 玩家名称（默认 "hero"）
        self.character_id: str = "initial"          # 当前选择角色（initial/mage/knight/assassin，单机/联机共用）
        self.run_carried: dict = {}                 # 本次携带物: {"resource": {id: qty}, "gold": int, "weapon": {id: qty}}
        self.run_potions: dict = {}                 # 本局拾取的药水: {item_id: qty}（上限 RUN_POTION_SLOTS，
                                                    # 不占背包容量、无需背包即可使用；撤离时随 run_carried 一并入库）
        self.current_weapon_kind: str = "melee"     # 当前武器类型: "melee"(近战) 或 "ranged"(远程)
        self.current_weapon_id: int | None = None   # 当前武器数据库 ID
        self.current_weapon_item_id: str | None = None  # 当前武器物品ID（如 "iron_sword"），用于渲染
        self.equipped_weapon_id: int | None = None  # 从仓库选择携带的武器 ID（进入游戏前选择）
        self.equipped_helmet_id: str | None = None  # 当前装备的头盔 ID（从数据库加载）
        self.equipped_armor_id: str | None = None   # 当前装备的护甲 ID（从数据库加载）
        self.equipped_backpack_id: str | None = None  # 当前装备的背包 item_id（从数据库加载）
        self.free_equipped_item_ids: set[str] = set()  # 局内免费拾取并装备的物品 ID（撤离时仅将这些物品入库）
        self.backpack_capacity: int = 0             # 背包容量（从数据库加载，供各 View 共享）
        self.current_map_seed: int = 1              # 当前地图随机种子（每次进入地图随机生成）
        self.map_theme: str = "forest"              # 当前地图主题: "forest"(幽暗森林) / "desert"(沙漠荒地)
        # 联机模式: solo=单机 / host=主机(权威模拟) / client=客户端(只渲染+上报+收快照)
        # 由 LAN 大厅/房间流程设置（B1 双模式分支的依据）；solo 为默认值，单机行为完全不变
        self.net_mode: str = "solo"
        # 联机网络对象（默认 None，由 LAN 大厅/房间流程在 todo 21 注入，本层不创建不持有）：
        # - net_server: host 模式持有的 NetServer 实例（主线程可线程安全调用 broadcast/send_to）
        # - net_client: client 模式持有的 NetClient 实例（主线程每帧 poll 排空入站消息）
        # solo/单机模式下均为 None，网络相关代码一律以「net_mode + 对象非 None」双重闸门跳过
        self.net_server = None
        self.net_client = None
        # 联机运行时身份（由 LAN 大厅派发，todo 21 落地后设置；solo 模式不接触）：
        self.net_player_id: int | None = None   # 本端玩家 id（host=0 固定；client=JOIN_ACCEPT 下发）
        self.net_slot: int = 0                  # 本端槽位号（host=0；client=JOIN_ACCEPT 下发，出生点偏移用）
        self.net_spawn: tuple[int, int] | None = None   # 本端出生点（ROOM_START 下发，联机出生用）
        self.net_max_players: int = 4           # 房间容量（建房者选择，ROOM_START 一致）
        self.net_room_id: str = ""              # 房间号（状态条/日志显示）
        self.net_roster: dict = {}              # 玩家名册 {player_id: {"name": str, "slot": int}}，状态条与幽灵名称用
        self.net_spawns: dict[int, tuple[int, int]] = {}  # 全房出生点 {player_id: (x, y)}，幽灵出生用
        self.net_ready: bool = False            # 本端是否已准备（开始游戏前全员就绪判定；host 恒为 True）
        self.net_wait_reason: str = ""          # 客户端撤离/死亡后回房等待的原因（evac/dead），大厅提示用
        self.net_characters: dict = {}          # 联机玩家角色映射 {player_id: character_id}，主机权威维护，
                                                # 开局前由 SET_CHARACTER 上报更新，ROOM_START 打包下发全房
        # 新手教程状态（首次启动 active=True；跳过/完成后 active=False）
        self.tutorial: TutorialState | None = None


def main():
    """游戏主入口
    
    创建窗口 → 初始化状态 → 显示开始界面 → 运行主循环
    """
    window = GameWindow()
    window.game_state = GameState()

    # 首次启动教程：读取 DB 教程完成标记（tutorial_done），未完成则启用教程
    from db.database import init_db
    from db.settings import is_tutorial_done
    init_db()
    window.game_state.tutorial = TutorialState(active=not is_tutorial_done())

    # 延迟导入避免循环依赖（start_view 会导入其他视图）
    # 启动时先显示开屏动画（品牌展示+光效过渡），完成后进入开始界面
    from views.splash_view import SplashView
    window.show_view(SplashView(window))
    arcade.run()


if __name__ == "__main__":
    main()
