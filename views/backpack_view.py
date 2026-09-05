"""背包页面：查看/丢弃当前装备与本次冒险携带的物品（支持滚轮滚动）

显示结构（自上而下）：
- 装备栏（当前装备的武器/头盔/护甲/背包，来自 GameState，不占背包容量，可丢弃）
- 金币（不可丢弃）
- 资源（可丢弃）
- 武器（可丢弃，run_carried 中的多余武器）
- 头盔（可丢弃，run_carried 中的多余头盔）
- 护甲（可丢弃，run_carried 中的多余护甲）
- 背包（可丢弃，run_carried 中的多余背包）

丢弃按钮数据结构（self.discard_buttons）：
- 装备栏丢弃: (rect, "equip", slot_type)   slot_type ∈ ("weapon","helmet","armor","backpack")
- 背包物品丢弃: (rect, "carry", item_type, item_id, level)
"""

import math
import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from db.connection import _conn
from entities.resource_defs import RESOURCES
from entities.weapon_defs import ALL_WEAPONS
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from game.sound_manager import sound_manager


class BackpackView(ScrollView):
    """背包界面：查看和丢弃当前装备与本次冒险携带的物品"""

    def __init__(self, window, game_view=None):
        super().__init__(window)
        self._tc = TextCache()  # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        self.game_view = game_view  # 保存当前 GameView 引用，返回时不用重建
        self.discard_buttons = []  # 两种条目，见模块 docstring
        self.back_rect = arcade.XYWH(WINDOW_WIDTH // 2, 40, 120, 36)
        # 丢弃对话框状态
        self._discard_dialog_active = False  # 对话框是否显示
        self._discard_dialog_item = None  # 待丢弃物品信息 (item_type, item_id, level, max_qty)
        self._discard_dialog_qty = 1  # 输入的丢弃数量
        self._discard_dialog_input = "1"  # 输入缓冲区（字符串）
        self._discard_dialog_btn_1 = None  # "丢弃1个" 按钮
        self._discard_dialog_btn_all = None  # "全部丢弃" 按钮
        self._discard_dialog_btn_confirm = None  # "确认" 按钮
        self._discard_dialog_btn_cancel = None  # "取消" 按钮
        self._build_content()

    # Bug 7 fix: 主机开背包时转发 on_update，防止客户端因快照停止而冻结
    def on_update(self, delta_time):
        if self.game_view is not None:
            self.game_view.on_update(delta_time)

    def _build_content(self):
        """构建内容并计算高度（与 on_draw 各区域 y 偏移严格一致）"""
        self._tc.clear()
        self.discard_buttons = []
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})

        y_start = WINDOW_HEIGHT - 120
        y = y_start

        # 装备栏（标题 + 4 槽 + 间距）
        y -= 32          # 标题
        y -= 32 * 4      # 4 个槽位
        y -= 20          # 间距

        # 金币
        y -= 40

        # 资源（标题 + 列表 + 间距）
        resources = carried.get("resource", {})
        y -= 30  # 标题
        y -= 30 * max(len(resources), 1)
        y -= 20  # 间距

        # 武器（标题 + 列表 + 间距）
        weapons = carried.get("weapon", {})
        y -= 32  # 标题
        y -= 32 * max(len(weapons), 1)
        y -= 20  # 间距

        # 头盔（标题 + 列表 + 间距）
        helmets = carried.get("helmet", {})
        y -= 32  # 标题
        y -= 32 * max(len(helmets), 1)
        y -= 20  # 间距

        # 护甲（标题 + 列表 + 间距）
        armors = carried.get("armor", {})
        y -= 32  # 标题
        y -= 32 * max(len(armors), 1)
        y -= 20  # 间距

        # 背包（标题 + 列表，无间距）
        backpacks = carried.get("backpack", {})
        y -= 32  # 标题
        y -= 32 * max(len(backpacks), 1)

        # 药水（标题 + 合并列表）
        run_potions = getattr(gs, 'run_potions', {})
        from db.database import get_potions
        db_potions = get_potions(gs.player_id) if gs.player_id else []
        all_potions_count = len(run_potions) + len(db_potions)
        y -= 32  # 标题
        if all_potions_count > 0:
            y -= 46 * all_potions_count  # 每条：名称行 + 效果描述行
        else:
            y -= 30  # "(空)" 占位

        self.content_height = max(y_start - y + 120, WINDOW_HEIGHT)

    def get_bg_color(self):
        return (25, 30, 40)

    def on_mouse_press(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            sound_manager.play_ui()
            # 丢弃对话框激活时，优先处理对话框按钮
            if self._discard_dialog_active:
                self._handle_dialog_click(x, y)
                return

            # 检查丢弃按钮（on_draw 中已将 scroll_offset 算入坐标，直接检测即可）
            for btn in self.discard_buttons:
                if btn[0].point_in_rect((x, y)):
                    if btn[1] == "equip":
                        # 装备栏丢弃：btn = (rect, "equip", slot_type)
                        self._discard_equipped(btn[2])
                    else:
                        # 背包物品丢弃：btn = (rect, "carry", item_type, item_id, level)
                        self._show_discard_dialog(btn[2], btn[3], btn[4])
                    return

            # 检查返回按钮（固定在底部，不受滚动影响）
            if self.back_rect.point_in_rect((x, y)):
                if self.game_view:
                    self.window.show_view(self.game_view)
                else:
                    from views.game_view import GameView
                    self.window.show_view(GameView(self.window_ref))
                return

    def _show_discard_dialog(self, item_type: str, item_id: str, level: int):
        """显示丢弃对话框，获取该物品的当前数量"""
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})
        slot = carried.get(item_type, {})
        key = (item_id, level) if item_type in ("weapon", "helmet", "armor", "backpack") else item_id
        max_qty = slot.get(key, 0) if slot else 0
        if max_qty <= 0:
            return
        self._discard_dialog_active = True
        self._discard_dialog_item = (item_type, item_id, level, max_qty)
        self._discard_dialog_qty = 1
        self._discard_dialog_input = "1"
        # 定义对话框按钮位置（垂直布局：标题→数量→快捷按钮→输入框→确认/取消）
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        self._discard_dialog_btn_1 = arcade.XYWH(cx - 60, cy + 5, 100, 32)
        self._discard_dialog_btn_all = arcade.XYWH(cx + 60, cy + 5, 100, 32)
        self._discard_dialog_btn_confirm = arcade.XYWH(cx + 60, cy - 75, 80, 32)
        self._discard_dialog_btn_cancel = arcade.XYWH(cx - 60, cy - 75, 80, 32)

    def _handle_dialog_click(self, x, y):
        """处理丢弃对话框中的按钮点击"""
        if self._discard_dialog_btn_1 and self._discard_dialog_btn_1.point_in_rect((x, y)):
            # 丢弃1个
            self._discard_dialog_qty = 1
            self._execute_discard()
            return
        if self._discard_dialog_btn_all and self._discard_dialog_btn_all.point_in_rect((x, y)):
            # 全部丢弃
            self._discard_dialog_qty = self._discard_dialog_item[3]
            self._execute_discard()
            return
        if self._discard_dialog_btn_confirm and self._discard_dialog_btn_confirm.point_in_rect((x, y)):
            # 确认输入数量
            try:
                qty = max(1, min(int(self._discard_dialog_input), self._discard_dialog_item[3]))
            except ValueError:
                qty = 1
            self._discard_dialog_qty = qty
            self._execute_discard()
            return
        if self._discard_dialog_btn_cancel and self._discard_dialog_btn_cancel.point_in_rect((x, y)):
            # 取消
            self._discard_dialog_active = False
            self._discard_dialog_item = None
            return

    def _execute_discard(self):
        """执行丢弃操作并关闭对话框"""
        if not self._discard_dialog_item:
            return
        item_type, item_id, level, _ = self._discard_dialog_item
        qty = self._discard_dialog_qty
        # 关闭对话框
        self._discard_dialog_active = False
        self._discard_dialog_item = None
        # 执行丢弃
        self._discard_item(item_type, item_id, level, qty=qty)

    def on_key_press(self, key, modifiers):
        """处理键盘输入（丢弃对话框数字输入）"""
        if not self._discard_dialog_active:
            return
        max_qty = self._discard_dialog_item[3] if self._discard_dialog_item else 1
        if key == arcade.key.BACKSPACE:
            self._discard_dialog_input = self._discard_dialog_input[:-1]
            if not self._discard_dialog_input:
                self._discard_dialog_input = "1"
        elif key == arcade.key.RETURN or key == arcade.key.NUM_ENTER:
            # 回车确认
            try:
                qty = max(1, min(int(self._discard_dialog_input), max_qty))
            except ValueError:
                qty = 1
            self._discard_dialog_qty = qty
            self._execute_discard()
        elif key == arcade.key.ESCAPE:
            # ESC 取消
            self._discard_dialog_active = False
            self._discard_dialog_item = None
        elif arcade.key.NUM_0 <= key <= arcade.key.NUM_9 or arcade.key.KEY_0 <= key <= arcade.key.KEY_9:
            # 数字键输入
            if key >= arcade.key.NUM_0 and key <= arcade.key.NUM_9:
                digit = str(key - arcade.key.NUM_0)
            else:
                digit = str(key - arcade.key.KEY_0)
            if self._discard_dialog_input == "1" and len(self._discard_dialog_input) == 1:
                self._discard_dialog_input = digit
            else:
                if len(self._discard_dialog_input) < 4:
                    self._discard_dialog_input += digit

    def _draw_discard_dialog(self):
        """绘制丢弃对话框（覆盖层，独立坐标系不受滚动影响）"""
        # 半透明遮罩
        arcade.draw_rect_filled(
            arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
            (0, 0, 0, 150)
        )
        # 对话框背景
        cx, cy = WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2
        dialog_w, dialog_h = 320, 200
        dialog_rect = arcade.XYWH(cx, cy, dialog_w, dialog_h)
        arcade.draw_rect_filled(dialog_rect, (30, 35, 50))
        arcade.draw_rect_outline(dialog_rect, arcade.color.WHITE, 2)

        item_type, item_id, level, max_qty = self._discard_dialog_item
        # 获取物品名称用于显示
        if item_type == "resource":
            from entities.resource_defs import RESOURCES
            name = RESOURCES.get(item_id, {}).get("name", item_id)
        elif item_type in ("weapon",):
            from entities.weapon_defs import ALL_WEAPONS
            name = ALL_WEAPONS.get(item_id, {}).get("name", item_id)
        elif item_type in ("helmet", "armor", "backpack"):
            from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
            pool = {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}
            name = pool.get(item_type, {}).get(item_id, {}).get("name", item_id)
        else:
            name = item_id

        self._tc.text("dlg_title", f"丢弃: {name}", cx, cy + 65,
                      arcade.color.WHITE, 16, anchor_x="center")
        self._tc.text("dlg_max", f"当前数量: {max_qty}", cx, cy + 40,
                      arcade.color.LIGHT_GRAY, 13, anchor_x="center")

        # "丢弃1个" 按钮
        if self._discard_dialog_btn_1:
            arcade.draw_rect_filled(self._discard_dialog_btn_1, (80, 50, 50))
            self._tc.text("dlg_btn_1", "丢弃1个", self._discard_dialog_btn_1.center_x,
                          self._discard_dialog_btn_1.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")

        # "全部丢弃" 按钮
        if self._discard_dialog_btn_all:
            arcade.draw_rect_filled(self._discard_dialog_btn_all, (120, 40, 40))
            self._tc.text("dlg_btn_all", f"全部({max_qty})", self._discard_dialog_btn_all.center_x,
                          self._discard_dialog_btn_all.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")

        # 数量输入框（位于快捷按钮下方、确认/取消上方）
        self._tc.text("dlg_input_label", "自定义数量:", cx, cy - 25,
                      arcade.color.LIGHT_GRAY, 12, anchor_x="center")
        input_rect = arcade.XYWH(cx, cy - 45, 80, 24)
        arcade.draw_rect_filled(input_rect, (50, 50, 60))
        arcade.draw_rect_outline(input_rect, arcade.color.WHITE, 1)
        self._tc.text("dlg_input_val", self._discard_dialog_input, cx, cy - 45,
                      arcade.color.YELLOW, 14, anchor_x="center", anchor_y="center")

        # "确认" 按钮
        if self._discard_dialog_btn_confirm:
            arcade.draw_rect_filled(self._discard_dialog_btn_confirm, (50, 100, 50))
            self._tc.text("dlg_confirm", "确认", self._discard_dialog_btn_confirm.center_x,
                          self._discard_dialog_btn_confirm.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")

        # "取消" 按钮
        if self._discard_dialog_btn_cancel:
            arcade.draw_rect_filled(self._discard_dialog_btn_cancel, (80, 80, 80))
            self._tc.text("dlg_cancel", "取消", self._discard_dialog_btn_cancel.center_x,
                          self._discard_dialog_btn_cancel.center_y, arcade.color.WHITE, 12,
                          anchor_x="center", anchor_y="center")

    def _discard_equipped(self, slot_type: str):
        """丢弃当前装备的物品：清理 GameState 字段 + 删除数据库记录 + 更新游戏属性 + 生成地面掉落物

        注意：装备栏物品不占背包容量，丢弃按钮的可用性只取决于该槽位是否为空
        （即使背包为空/容量为0，武器/头盔/护甲的丢弃按钮仍然可用）
        """
        gs = self.window_ref.game_state
        pid = gs.player_id

        if slot_type == "weapon":
            item_id = getattr(gs, 'current_weapon_item_id', None)
            if not item_id:
                return
            # 删除数据库中的武器记录（equipped_weapon_id 是 DB row id）
            wid = getattr(gs, 'equipped_weapon_id', None)
            if wid and pid:
                from db.weapons import delete_weapon
                delete_weapon(pid, wid)
            # 清理 GameState 武器槽位（还原为默认拳头）
            gs.equipped_weapon_id = None
            gs.current_weapon_id = None
            gs.current_weapon_item_id = None
            gs.current_weapon_kind = "melee"
            gs.weapon_damage = 8
            gs.weapon_speed = 1.0
            gs.weapon_range = 40
            gs.weapon_proj_speed = 0
            gs.weapon_special = ""
            gs.weapon_auto_fire = False
            # 生成掉落物
            self._spawn_drop("weapon", item_id, 1)

        elif slot_type == "helmet":
            item_id = getattr(gs, 'equipped_helmet_id', None)
            if not item_id:
                return
            # 删除数据库中的装备记录（通过 slot 查询 equip_id）
            if pid:
                from db.equipment import delete_equipment
                with _conn() as c:
                    row = c.execute(
                        "SELECT id FROM equipment WHERE player_id=? AND slot='helmet' AND is_equipped=1",
                        (pid,),
                    ).fetchone()
                    if row:
                        delete_equipment(pid, row[0])
            gs.equipped_helmet_id = None
            # 更新防御：减去该头盔提供的防御
            if self.game_view and self.game_view.player:
                from entities.equipment_defs import HELMETS
                defense = HELMETS.get(item_id, {}).get("defense", 0)
                self.game_view.player.defense = max(0, self.game_view.player.defense - defense)
            self._spawn_drop("helmet", item_id, 1)

        elif slot_type == "armor":
            item_id = getattr(gs, 'equipped_armor_id', None)
            if not item_id:
                return
            # 删除数据库中的装备记录（通过 slot 查询 equip_id）
            if pid:
                from db.equipment import delete_equipment
                with _conn() as c:
                    row = c.execute(
                        "SELECT id FROM equipment WHERE player_id=? AND slot='armor' AND is_equipped=1",
                        (pid,),
                    ).fetchone()
                    if row:
                        delete_equipment(pid, row[0])
            gs.equipped_armor_id = None
            # 更新防御：减去该护甲提供的防御
            if self.game_view and self.game_view.player:
                from entities.equipment_defs import ARMORS
                defense = ARMORS.get(item_id, {}).get("defense", 0)
                self.game_view.player.defense = max(0, self.game_view.player.defense - defense)
            self._spawn_drop("armor", item_id, 1)

        elif slot_type == "backpack":
            item_id = getattr(gs, 'equipped_backpack_id', None)
            if not item_id:
                return
            # 删除数据库中的装备记录（通过 slot 查询 equip_id）
            if pid:
                from db.equipment import delete_equipment
                with _conn() as c:
                    row = c.execute(
                        "SELECT id FROM equipment WHERE player_id=? AND slot='backpack' AND is_equipped=1",
                        (pid,),
                    ).fetchone()
                    if row:
                        delete_equipment(pid, row[0])
            gs.equipped_backpack_id = None
            # 先丢弃背包中的所有物品（run_carried 中的物品）
            self._discard_all_items()
            # 清理容量：丢弃背包后不再有携带空间
            if self.game_view and self.game_view.player:
                self.game_view.player.backpack_capacity = 0
            gs.backpack_capacity = 0
            self._spawn_drop("backpack", item_id, 1)

        self._build_content()

    def _spawn_drop(self, item_type: str, item_id: str, level: int = 1):
        """在玩家周围生成地面掉落物（避让墙壁等障碍物，修复丢弃卡墙无法拾取）"""
        if not self.game_view or not hasattr(self.game_view, 'player') or not self.game_view.player:
            return
        import random as _rand
        from config import MAP_WIDTH, MAP_HEIGHT
        from game.loot import DropItem
        player = self.game_view.player
        angle = _rand.uniform(0, 6.2832)  # 2π
        dist = _rand.uniform(50, 90)
        drop_x = player.center_x + dist * math.cos(angle)
        drop_y = player.center_y + dist * math.sin(angle)
        # 修复：clamp 到地图边界内，防止掉落物生成到地图外无法拾取
        drop_x = max(0, min(MAP_WIDTH, drop_x))
        drop_y = max(0, min(MAP_HEIGHT, drop_y))
        drop = DropItem(drop_x, drop_y, item_type, item_id, 1, level=level)
        self._place_drop_near_player(drop)
        self.game_view.drops.append(drop)

    def _place_drop_near_player(self, drop):
        """把丢弃的掉落物放到玩家周围的无障碍位置（修复丢弃靠墙时物品卡墙无法拾取）

        丢弃位置原为玩家周围 50~120px 随机点，若玩家靠墙则可能落在墙内；
        这里复用 game.entity_callbacks 的 _place_drop_avoiding（撞障碍物沿原方向
        缩短半径重试）避让墙壁/环境物/未开宝箱（game_view.obstacle_list），
        多次随机角度尝试，全部失败才回退玩家脚下（玩家所在位置必定可通行）。
        """
        if not self.game_view or not hasattr(self.game_view, 'player') or not self.game_view.player:
            return False
        import random as _rand
        from config import MAP_WIDTH, MAP_HEIGHT
        from game.entity_callbacks import _drop_collides, _place_drop_avoiding
        player = self.game_view.player
        obstacles = getattr(self.game_view, 'obstacle_list', None)
        for _ in range(8):
            angle = _rand.uniform(0, 6.2832)  # 2π
            dist = _rand.uniform(50, 120)
            _place_drop_avoiding(drop, player.center_x, player.center_y, angle, dist, obstacles)
            # 修复：clamp 到地图边界内，防止避让后坐标越界
            drop.center_x = max(0, min(MAP_WIDTH, drop.center_x))
            drop.center_y = max(0, min(MAP_HEIGHT, drop.center_y))
            if not _drop_collides(drop, obstacles):
                return True
        # 全部尝试仍碰撞：回退玩家脚下（玩家所在位置必定可通行，物品不会卡墙）
        drop.center_x = player.center_x
        drop.center_y = player.center_y
        return True

    def _discard_item(self, item_type: str, item_id: str, level: int = 1, qty: int = 1):
        """丢弃指定数量的物品：从 run_carried 移除，并在玩家周围生成地面掉落物

        特殊处理：丢弃背包时同时丢弃所有物品（因为没有背包就无法携带物品）
        """
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})
        if item_type == "gold":
            return  # 金币不可丢弃

        # 本局药水槽（run_potions）丢弃：减 qty，生成本局药水掉落物（拾取后重回药水槽）
        if item_type == "run_potion":
            run_potions = getattr(gs, 'run_potions', {})
            available = run_potions.get(item_id, 0)
            if available <= 0:
                return
            drop_qty = min(qty, available)
            run_potions[item_id] -= drop_qty
            if run_potions[item_id] <= 0:
                del run_potions[item_id]
            if self.game_view and hasattr(self.game_view, 'player') and self.game_view.player:
                import random as _rand
                from game.loot import DropItem
                player = self.game_view.player
                angle = _rand.uniform(0, 6.2832)
                dist = _rand.uniform(50, 90)
                drop_x = player.center_x + dist * math.cos(angle)
                drop_y = player.center_y + dist * math.sin(angle)
                drop = DropItem(drop_x, drop_y, "potion", item_id, drop_qty)
                self._place_drop_near_player(drop)
                self.game_view.drops.append(drop)
            self._build_content()
            return

        # 仓库药水（db_potion）丢弃：从数据库删除 qty 瓶，生成地面掉落物
        if item_type == "db_potion":
            from db.database import remove_potion
            pid = gs.player_id
            if not pid:
                return
            # level 参数复用为 potion DB row id
            potion_db_id = level
            for _ in range(qty):
                remove_potion(pid, potion_db_id)
            if self.game_view and hasattr(self.game_view, 'player') and self.game_view.player:
                import random as _rand
                from game.loot import DropItem
                player = self.game_view.player
                angle = _rand.uniform(0, 6.2832)
                dist = _rand.uniform(50, 90)
                drop_x = player.center_x + dist * math.cos(angle)
                drop_y = player.center_y + dist * math.sin(angle)
                drop = DropItem(drop_x, drop_y, "potion", item_id, qty)
                self._place_drop_near_player(drop)
                self.game_view.drops.append(drop)
            self._build_content()
            return

        slot = carried.get(item_type, {})
        # 武器装备/背包以 (item_id, level) 为键；资源仍以 id 为键
        key = (item_id, level) if item_type in ("weapon", "helmet", "armor", "backpack") else item_id
        if key not in slot:
            return

        # 限制丢弃数量不超过持有量
        available = slot[key]
        drop_qty = min(qty, available)

        # 从 run_carried 中移除
        if slot[key] > drop_qty:
            slot[key] -= drop_qty
        else:
            del slot[key]

        # 在玩家周围生成地面掉落物（避免立即被拾取，且避让墙壁不卡墙）
        if self.game_view and hasattr(self.game_view, 'player') and self.game_view.player:
            import random as _rand
            from game.loot import DropItem
            player = self.game_view.player
            # 在玩家周围 50~90 像素随机位置生成掉落物
            angle = _rand.uniform(0, 6.2832)  # 2π
            dist = _rand.uniform(50, 90)
            drop_x = player.center_x + dist * math.cos(angle)
            drop_y = player.center_y + dist * math.sin(angle)
            drop = DropItem(drop_x, drop_y, item_type, item_id, drop_qty, level=level)
            self._place_drop_near_player(drop)
            self.game_view.drops.append(drop)

        # 重新构建内容
        self._build_content()

    def _discard_all_items(self):
        """丢弃所有物品：从 run_carried 移除所有物品，并在玩家周围生成地面掉落物"""
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})

        if not self.game_view or not hasattr(self.game_view, 'player') or not self.game_view.player:
            return

        import random as _rand
        from game.loot import DropItem
        player = self.game_view.player

        # 收集所有要丢弃的物品
        items_to_drop = []

        # 资源
        resources = carried.get("resource", {})
        for item_id, qty in resources.items():
            for _ in range(qty):
                items_to_drop.append(("resource", item_id, 1))

        # 武器
        weapons = carried.get("weapon", {})
        for (item_id, level), qty in weapons.items():
            for _ in range(qty):
                items_to_drop.append(("weapon", item_id, level))

        # 头盔
        helmets = carried.get("helmet", {})
        for (item_id, level), qty in helmets.items():
            for _ in range(qty):
                items_to_drop.append(("helmet", item_id, level))

        # 护甲
        armors = carried.get("armor", {})
        for (item_id, level), qty in armors.items():
            for _ in range(qty):
                items_to_drop.append(("armor", item_id, level))

        # 背包
        backpacks = carried.get("backpack", {})
        for (item_id, level), qty in backpacks.items():
            for _ in range(qty):
                items_to_drop.append(("backpack", item_id, level))

        # 药水
        potions = carried.get("potion", {})
        for item_id, qty in potions.items():
            for _ in range(qty):
                items_to_drop.append(("potion", item_id, 1))

        # 本局药水槽（run_potions：不占背包容量，丢弃背包时一并丢弃）
        run_potions = getattr(gs, 'run_potions', {})
        for item_id, qty in run_potions.items():
            for _ in range(qty):
                items_to_drop.append(("potion", item_id, 1))

        # 仓库药水（db_potions：丢弃背包时一并删除数据库记录并生成掉落物）
        pid = gs.player_id
        if pid:
            from db.database import get_potions, remove_potion
            db_potions = get_potions(pid)
            for p in db_potions:
                items_to_drop.append(("potion", p["item_id"], 1))
                remove_potion(pid, p["id"])

        # 在玩家周围生成地面掉落物（避让墙壁等障碍物，修复丢弃卡墙无法拾取）
        for item_type, item_id, level in items_to_drop:
            angle = _rand.uniform(0, 6.2832)  # 2π
            dist = _rand.uniform(50, 120)
            drop_x = player.center_x + dist * math.cos(angle)
            drop_y = player.center_y + dist * math.sin(angle)
            drop = DropItem(drop_x, drop_y, item_type, item_id, 1, level=level)
            self._place_drop_near_player(drop)
            self.game_view.drops.append(drop)

        # 清空 run_carried 中的所有物品（保留金币）
        # 修复：carried 与 gs.run_carried 是同一字典，clear 后无法再读取金币，须先保存
        saved_gold = carried.get("gold", 0)
        carried.clear()
        carried["gold"] = saved_gold
        # 清空本局药水槽
        run_potions.clear()

        # 重新构建内容
        self._build_content()

    def on_draw(self):
        self.clear()
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})
        offset = self.scroll_offset
        from db.database import get_potions  # 药水合并显示需要

        # 固定头部
        self._tc.text("header_title", "背 包", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 50,
                      arcade.color.WHITE, 30, anchor_x="center")

        # 显示容量信息
        from game.loot import _calc_carried_capacity
        used_cap = _calc_carried_capacity(carried)
        total_cap = getattr(gs, 'backpack_capacity', 0)
        self._tc.text("header_cap", f"容量: {used_cap}/{total_cap}", WINDOW_WIDTH // 2,
                      WINDOW_HEIGHT - 85, arcade.color.LIGHT_GRAY, 16, anchor_x="center")
        self._tc.text("header_hint", "滚轮滚动查看全部物品", WINDOW_WIDTH // 2,
                      WINDOW_HEIGHT - 105, arcade.color.GRAY, 11, anchor_x="center")

        # 内容起点（带滚动偏移）
        y = WINDOW_HEIGHT - 120 + offset
        self.discard_buttons = []

        # ── 装备栏（当前装备的物品，不占背包容量，用不同颜色标题区分）──
        self._tc.text("equip_title", "装备栏", 50, y, arcade.color.YELLOW, 18)
        y -= 32

        # 武器槽
        equip_weapon_id = getattr(gs, 'current_weapon_item_id', None)
        if equip_weapon_id:
            wdef = ALL_WEAPONS.get(equip_weapon_id, {})
            name = wdef.get("name", equip_weapon_id)
            damage = wdef.get("damage", 0)
            kind = "近战" if wdef.get("kind") == "melee" else "远程"
            self._tc.text("equip_weapon", f"武器: {name} ({kind} {damage}伤害)", 60, y,
                          arcade.color.LIGHT_GRAY, 14)
            btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
            self.discard_buttons.append((btn, "equip", "weapon"))
        else:
            self._tc.text("equip_weapon", "武器: (空)", 60, y, arcade.color.GRAY, 14)
        y -= 32

        # 头盔槽
        equip_helmet_id = getattr(gs, 'equipped_helmet_id', None)
        if equip_helmet_id:
            hdef = HELMETS.get(equip_helmet_id, {})
            name = hdef.get("name", equip_helmet_id)
            defense = hdef.get("defense", 0)
            self._tc.text("equip_helmet", f"头盔: {name} (防御+{defense})", 60, y,
                          arcade.color.LIGHT_GRAY, 14)
            btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
            self.discard_buttons.append((btn, "equip", "helmet"))
        else:
            self._tc.text("equip_helmet", "头盔: (空)", 60, y, arcade.color.GRAY, 14)
        y -= 32

        # 护甲槽
        equip_armor_id = getattr(gs, 'equipped_armor_id', None)
        if equip_armor_id:
            adef = ARMORS.get(equip_armor_id, {})
            name = adef.get("name", equip_armor_id)
            defense = adef.get("defense", 0)
            self._tc.text("equip_armor", f"护甲: {name} (防御+{defense})", 60, y,
                          arcade.color.LIGHT_GRAY, 14)
            btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
            self.discard_buttons.append((btn, "equip", "armor"))
        else:
            self._tc.text("equip_armor", "护甲: (空)", 60, y, arcade.color.GRAY, 14)
        y -= 32

        # 背包槽
        equip_bag_id = getattr(gs, 'equipped_backpack_id', None)
        if equip_bag_id:
            bdef = BACKPACKS.get(equip_bag_id, {})
            name = bdef.get("name", equip_bag_id)
            capacity = bdef.get("capacity", 0)
            self._tc.text("equip_pack", f"背包: {name} (容量+{capacity})", 60, y,
                          arcade.color.LIGHT_GRAY, 14)
            btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
            self.discard_buttons.append((btn, "equip", "backpack"))
        else:
            self._tc.text("equip_pack", "背包: (空)", 60, y, arcade.color.GRAY, 14)
        y -= 32
        y -= 20  # 装备栏与下方区域间距

        # ── 金币 ──
        gold = carried.get("gold", 0)
        self._tc.text("gold", f"金币: {gold}", 50, y, arcade.color.GOLD, 16)
        y -= 40

        # ── 资源列表 ──
        self._tc.text("res_title", "资源:", 50, y, arcade.color.WHITE, 16)
        y -= 30
        resources = carried.get("resource", {})
        if resources:
            for i, (item_id, qty) in enumerate(resources.items()):
                name = RESOURCES.get(item_id, {}).get("name", item_id)
                self._tc.text(f"res_{i}", f"{name} x{qty}", 60, y,
                              arcade.color.LIGHT_GRAY, 14)
                # 丢弃按钮
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                # 资源无等级概念，level 占位传 1（_discard_item 中对资源类型会忽略 level）
                self.discard_buttons.append((btn, "carry", "resource", item_id, 1))
                y -= 30
        else:
            self._tc.text("res_empty", "(空)", 60, y, arcade.color.GRAY, 12)
            y -= 30
        y -= 20

        # ── 武器列表 ──
        self._tc.text("wep_title", "武器:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        weapons = carried.get("weapon", {})
        if weapons:
            for i, ((item_id, level), qty) in enumerate(weapons.items()):
                wdef = ALL_WEAPONS.get(item_id, {})
                name = wdef.get("name", item_id)
                damage = wdef.get("damage", 0)
                kind = "近战" if wdef.get("kind") == "melee" else "远程"
                self._tc.text(f"wep_{i}", f"{name} Lv.{level} ({kind} {damage}伤害) x{qty}", 60, y,
                              arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "carry", "weapon", item_id, level))
                y -= 32
        else:
            self._tc.text("wep_empty", "(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32
        y -= 20

        # ── 头盔列表 ──
        self._tc.text("helm_title", "头盔:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        helmets = carried.get("helmet", {})
        if helmets:
            for i, ((item_id, level), qty) in enumerate(helmets.items()):
                hdef = HELMETS.get(item_id, {})
                name = hdef.get("name", item_id)
                defense = hdef.get("defense", 0)
                self._tc.text(f"helm_{i}", f"{name} Lv.{level} (防御+{defense}) x{qty}", 60, y,
                              arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "carry", "helmet", item_id, level))
                y -= 32
        else:
            self._tc.text("helm_empty", "(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32
        y -= 20

        # ── 护甲列表 ──
        self._tc.text("armor_title", "护甲:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        armors = carried.get("armor", {})
        if armors:
            for i, ((item_id, level), qty) in enumerate(armors.items()):
                adef = ARMORS.get(item_id, {})
                name = adef.get("name", item_id)
                defense = adef.get("defense", 0)
                self._tc.text(f"armor_{i}", f"{name} Lv.{level} (防御+{defense}) x{qty}", 60, y,
                              arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "carry", "armor", item_id, level))
                y -= 32
        else:
            self._tc.text("armor_empty", "(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32
        y -= 20

        # ── 背包列表 ──
        self._tc.text("pack_title", "背包:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        backpacks = carried.get("backpack", {})
        if backpacks:
            for i, ((item_id, level), qty) in enumerate(backpacks.items()):
                bdef = BACKPACKS.get(item_id, {})
                name = bdef.get("name", item_id)
                capacity = bdef.get("capacity", 0)
                self._tc.text(f"pack_{i}", f"{name} Lv.{level} (容量+{capacity}) x{qty}", 60, y,
                              arcade.color.LIGHT_GRAY, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "carry", "backpack", item_id, level))
                y -= 32
        else:
            self._tc.text("pack_empty", "(空)", 60, y, arcade.color.GRAY, 12)
            y -= 32

        # ── 药水区域（合并显示本局药水 + 仓库药水）──
        self._tc.text("pot_title", "药水:", 50, y, arcade.color.WHITE, 16)
        y -= 32
        run_potions = getattr(gs, 'run_potions', {})
        db_potions = get_potions(gs.player_id) if gs.player_id else []
        if run_potions or db_potions:
            from entities.equipment_defs import POTIONS
            # 先显示本局药水（cyan 标识，不占容量，热键优先）
            for i, (item_id, qty) in enumerate(run_potions.items()):
                pdef = POTIONS.get(item_id, {})
                name = pdef.get("name", item_id)
                desc = pdef.get("description", "")
                self._tc.text(f"runpot_{i}", f"  [本局] {name} x{qty}", 60, y,
                              arcade.color.CYAN, 14)
                if desc:
                    self._tc.text(f"runpot_desc_{i}", f"      {desc}", 70, y - 16,
                                  arcade.color.GRAY, 11)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "carry", "run_potion", item_id, 1))
                y -= 46
            # 再显示仓库药水（light_green 标识，从 DB 读取）
            for i, p in enumerate(db_potions):
                item_id = p["item_id"]
                name = p["name"]
                qty = p["quantity"]
                pdef = POTIONS.get(item_id, {})
                desc = pdef.get("description", "")
                self._tc.text(f"dbpot_{i}", f"  [仓库] {name} x{qty}", 60, y,
                              arcade.color.LIGHT_GREEN, 14)
                if desc:
                    self._tc.text(f"dbpot_desc_{i}", f"      {desc}", 70, y - 16,
                                  arcade.color.GRAY, 11)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                self.discard_buttons.append((btn, "carry", "db_potion", item_id, p["id"]))
                y -= 46
        else:
            self._tc.text("pot_empty", "(空，击杀怪物或市场购买获取)", 60, y, arcade.color.GRAY, 12)
            y -= 30

        # 绘制丢弃按钮（rect 坐标已含 scroll_offset，直接使用即可）
        for i, btn in enumerate(self.discard_buttons):
            rect = btn[0]
            draw_rect = arcade.XYWH(rect.center_x, rect.center_y, rect.width, rect.height)
            arcade.draw_rect_filled(draw_rect, arcade.color.DARK_RED)
            self._tc.text(f"discard_{i}", "丢弃", draw_rect.center_x, draw_rect.center_y,
                          arcade.color.WHITE, 11, anchor_x="center", anchor_y="center")

        # 返回按钮
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_BLUE)
        self._tc.text("nav_back", "返回游戏", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 14, anchor_x="center", anchor_y="center")

        # 丢弃对话框（覆盖层，独立坐标系不受滚动影响）
        if self._discard_dialog_active and self._discard_dialog_item:
            self._draw_discard_dialog()
