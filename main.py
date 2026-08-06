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
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_TITLE


class GameState:
    """各 View 共享的运行时状态
    
    该类的实例存储在 window.game_state 中，所有视图都可以访问。
    主要用途是在不同视图之间传递玩家数据和游戏状态。
    """
    def __init__(self):
        self.player_id: int | None = None          # 玩家数据库 ID（登录后设置）
        self.player_name: str = "hero"              # 玩家名称（默认 "hero"）
        self.run_carried: dict = {}                 # 本次携带物: {"resource": {id: qty}, "gold": int, "weapon": {id: qty}}
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


def main():
    """游戏主入口
    
    创建窗口 → 初始化状态 → 显示开始界面 → 运行主循环
    """
    window = arcade.Window(WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_TITLE)
    window.game_state = GameState()

    # 延迟导入避免循环依赖（start_view 会导入其他视图）
    from views.start_view import StartView
    window.show_view(StartView(window))
    arcade.run()


if __name__ == "__main__":
    main()
