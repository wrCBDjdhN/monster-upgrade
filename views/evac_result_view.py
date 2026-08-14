"""撤离结果页面：显示撤离成功/失败和当局收益"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from views.text_cache import TextCache  # 持久 Text 对象缓存，替代 draw_text
from entities.resource_defs import RESOURCES       # 资源ID → 中文名
from entities.weapon_defs import ALL_WEAPONS       # 武器ID → 中文名
from entities.equipment_defs import get_item_def   # 装备(头盔/护甲)ID → 中文名


class EvacResultView(arcade.View):
    """撤离结果页面"""
    
    def __init__(self, window, success: bool, run_carried: dict = None):
        """
        初始化撤离结果页面
        
        Args:
            window: 窗口引用
            success: 是否撤离成功
            run_carried: 本次携带的物品（成功时显示收益，失败时为空）
        """
        super().__init__()
        self.window_ref = window
        self._tc = TextCache()  # 持久 Text 对象缓存，避免 draw_text 每帧重建纹理
        self.success = success
        self.run_carried = run_carried or {}
        self.return_rect = arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 180, 220, 50)
        self.return_hover = False
        
    def on_show_view(self):
        self.window.background_color = arcade.color.DARK_SLATE_GRAY
        
    def on_draw(self):
        self.clear()
        
        # 标题
        if self.success:
            title = "撤离成功!"
            title_color = arcade.color.GREEN
            subtitle = "战利品已存入仓库"
        else:
            title = "撤离失败!"
            title_color = arcade.color.RED
            subtitle = "所有携带物品已丢失"
            
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "title",
            title,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 120,
            title_color, size=48, anchor_x="center", bold=True,
        )
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "subtitle",
            subtitle,
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 70,
            arcade.color.LIGHT_GRAY, size=18, anchor_x="center",
        )
        
        # 显示收益详情
        if self.success and self.run_carried:
            y = WINDOW_HEIGHT // 2 + 20
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                "gain",
                "本次收益:",
                WINDOW_WIDTH // 2, y,
                arcade.color.GOLD, size=20, anchor_x="center", bold=True,
            )
            y -= 35
            
            # 金币
            gold = self.run_carried.get("gold", 0)
            if gold > 0:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    "gold",
                    f"金币: {gold}",
                    WINDOW_WIDTH // 2, y,
                    arcade.color.YELLOW, size=16, anchor_x="center",
                )
                y -= 25
                
            BOTTOM_Y = 230  # 资源/武器/装备列表最低绘制线，防止条目过多时超出屏幕
            hidden = 0      # 因空间不足被省略的条目数

            # 资源（显示中文名）
            resources = self.run_carried.get("resource", {})
            for i, (res_id, qty) in enumerate(resources.items()):
                if y < BOTTOM_Y:
                    hidden += 1
                    continue
                res_name = RESOURCES.get(res_id, {}).get("name", res_id)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"res_{i}",
                    f"{res_name}: {qty}",
                    WINDOW_WIDTH // 2, y,
                    arcade.color.WHITE, size=16, anchor_x="center",
                )
                y -= 25
                
            # 武器（显示中文名）
            weapons = self.run_carried.get("weapon", {})
            for i, ((wid, level), qty) in enumerate(weapons.items()):
                if y < BOTTOM_Y:
                    hidden += 1
                    continue
                w_name = ALL_WEAPONS.get(wid, {}).get("name", wid)
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    f"wep_{i}",
                    f"武器: {w_name} Lv{level} x{qty}",
                    WINDOW_WIDTH // 2, y,
                    (100, 200, 255), size=16, anchor_x="center",
                )
                y -= 25
                
            # 装备（显示中文名）
            for slot in ("helmet", "armor"):
                items = self.run_carried.get(slot, {})
                for i, ((iid, level), qty) in enumerate(items.items()):
                    if y < BOTTOM_Y:
                        hidden += 1
                        continue
                    eq_def = get_item_def(slot, iid)
                    e_name = eq_def["name"] if eq_def else iid
                    # 持久 Text 对象，避免 draw_text 每帧重建纹理
                    self._tc.text(
                        f"eq_{slot}_{i}",
                        f"装备: {e_name} Lv{level} x{qty}",
                        WINDOW_WIDTH // 2, y,
                        (180, 180, 220), size=16, anchor_x="center",
                    )
                    y -= 25

            # 空间不足时给出提示，避免信息被省略后用户不知去向
            if hidden > 0:
                # 持久 Text 对象，避免 draw_text 每帧重建纹理
                self._tc.text(
                    "hidden",
                    f"（另有 {hidden} 项已存入仓库）",
                    WINDOW_WIDTH // 2, BOTTOM_Y,
                    arcade.color.GRAY, size=14, anchor_x="center",
                )
        elif not self.success:
            # 失败时显示提示
            # 持久 Text 对象，避免 draw_text 每帧重建纹理
            self._tc.text(
                "fail_tip",
                "下次加油!",
                WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2,
                arcade.color.ORANGE, size=24, anchor_x="center",
            )
        
        # 返回按钮
        btn_color = arcade.color.DARK_GREEN if self.return_hover else arcade.color.GREEN
        arcade.draw_rect_filled(self.return_rect, btn_color)
        # 持久 Text 对象，避免 draw_text 每帧重建纹理
        self._tc.text(
            "btn_back",
            "返回大厅",
            WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 180,
            arcade.color.WHITE, size=18, anchor_x="center", anchor_y="center",
            bold=True,
        )
        
    def on_mouse_motion(self, x, y, dx, dy):
        self.return_hover = self.return_rect.point_in_rect((x, y))
        
    def on_mouse_press(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            if self.return_rect.point_in_rect((x, y)):
                # 联机模式：撤离结果页仅单机路径可达（联机撤离/死亡均走回房等待），
                # 防御性分流：联机回 LobbyView 复用连接，单机回 StartView
                gs = self.window.game_state
                if getattr(gs, "net_mode", "solo") != "solo":
                    from views.lobby_view import LobbyView
                    self.window.show_view(LobbyView(self.window_ref))
                else:
                    from views.start_view import StartView
                    self.window.show_view(StartView(self.window_ref))
