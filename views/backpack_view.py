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


class BackpackView(ScrollView):
    """背包界面：查看和丢弃当前装备与本次冒险携带的物品"""

    def __init__(self, window, game_view=None):
        super().__init__(window)
        self._tc = TextCache()  # 文本缓存：复用 arcade.Text 消除 PerformanceWarning
        self.game_view = game_view  # 保存当前 GameView 引用，返回时不用重建
        self.discard_buttons = []  # 两种条目，见模块 docstring
        self.back_rect = arcade.XYWH(WINDOW_WIDTH // 2, 40, 120, 36)
        self._build_content()

    def _build_content(self):
        """构建内容并计算高度"""
        self._tc.clear()  # 内容结构变化，清空文本缓存防止旧 key 残留
        self.discard_buttons = []
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})

        # 计算内容高度
        y_start = WINDOW_HEIGHT - 120
        y = y_start

        # 装备栏区域（固定：标题 + 4个槽位 + 间距）
        y -= 32  # 标题行
        y -= 32 * 4  # 4个槽位
        y -= 20  # 间距

        # 金币区域
        y -= 30

        # 资源区域
        resources = carried.get("resource", {})
        if resources:
            y -= 30 * len(resources)
        else:
            y -= 30
        y -= 20  # 间距

        # 武器区域
        weapons = carried.get("weapon", {})
        if weapons:
            y -= 32 * len(weapons)
        else:
            y -= 30
        y -= 20

        # 头盔区域
        helmets = carried.get("helmet", {})
        if helmets:
            y -= 32 * len(helmets)
        else:
            y -= 30
        y -= 20

        # 护甲区域
        armors = carried.get("armor", {})
        if armors:
            y -= 32 * len(armors)
        else:
            y -= 30
        y -= 20

        # 背包区域
        backpacks = carried.get("backpack", {})
        if backpacks:
            y -= 32 * len(backpacks)
        else:
            y -= 30

        # 本局药水槽区域（run_potions：不占背包容量，热键 1-N 优先使用）
        run_potions = getattr(gs, 'run_potions', {})
        if run_potions:
            y -= 32  # 标题行
            y -= 30 * len(run_potions)
        else:
            y -= 30

        self.content_height = max(y_start - y + 120, WINDOW_HEIGHT)

    def get_bg_color(self):
        return (25, 30, 40)

    def on_mouse_press(self, x, y, button, modifiers):
        if button == arcade.MOUSE_BUTTON_LEFT:
            # 检查丢弃按钮（on_draw 中已将 scroll_offset 算入坐标，直接检测即可）
            for btn in self.discard_buttons:
                if btn[0].point_in_rect((x, y)):
                    if btn[1] == "equip":
                        # 装备栏丢弃：btn = (rect, "equip", slot_type)
                        self._discard_equipped(btn[2])
                    else:
                        # 背包物品丢弃：btn = (rect, "carry", item_type, item_id, level)
                        self._discard_item(btn[2], btn[3], btn[4])
                    return

            # 检查返回按钮（固定在底部，不受滚动影响）
            if self.back_rect.point_in_rect((x, y)):
                if self.game_view:
                    self.window.show_view(self.game_view)
                else:
                    from views.game_view import GameView
                    self.window.show_view(GameView(self.window_ref))
                return

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
        from game.loot import DropItem
        player = self.game_view.player
        angle = _rand.uniform(0, 6.2832)  # 2π
        dist = _rand.uniform(50, 90)
        drop_x = player.center_x + dist * math.cos(angle)
        drop_y = player.center_y + dist * math.sin(angle)
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
        from game.entity_callbacks import _drop_collides, _place_drop_avoiding
        player = self.game_view.player
        obstacles = getattr(self.game_view, 'obstacle_list', None)
        for _ in range(8):
            angle = _rand.uniform(0, 6.2832)  # 2π
            dist = _rand.uniform(50, 120)
            _place_drop_avoiding(drop, player.center_x, player.center_y, angle, dist, obstacles)
            if not _drop_collides(drop, obstacles):
                return True
        # 全部尝试仍碰撞：回退玩家脚下（玩家所在位置必定可通行，物品不会卡墙）
        drop.center_x = player.center_x
        drop.center_y = player.center_y
        return True

    def _discard_item(self, item_type: str, item_id: str, level: int = 1):
        """丢弃指定物品：从 run_carried 移除，并在玩家周围生成地面掉落物

        特殊处理：丢弃背包时同时丢弃所有物品（因为没有背包就无法携带物品）
        """
        gs = self.window_ref.game_state
        carried = getattr(gs, 'run_carried', {})
        if item_type == "gold":
            return  # 金币不可丢弃

        # 本局药水槽（run_potions）丢弃：减 1，生成本局药水掉落物（拾取后重回药水槽）
        if item_type == "run_potion":
            run_potions = getattr(gs, 'run_potions', {})
            if run_potions.get(item_id, 0) <= 0:
                return
            run_potions[item_id] -= 1
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
                drop = DropItem(drop_x, drop_y, "potion", item_id, 1)
                self._place_drop_near_player(drop)
                self.game_view.drops.append(drop)
            self._build_content()
            return

        # 如果丢弃背包，同时丢弃所有物品
        if item_type == "backpack":
            self._discard_all_items()
            return

        slot = carried.get(item_type, {})
        # 武器装备/背包以 (item_id, level) 为键；资源仍以 id 为键
        key = (item_id, level) if item_type in ("weapon", "helmet", "armor", "backpack") else item_id
        if key not in slot:
            return

        # 先记录要丢弃的数量（丢弃1个）
        qty_to_drop = 1

        # 从 run_carried 中移除
        if slot[key] > 1:
            slot[key] -= 1
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
            drop = DropItem(drop_x, drop_y, item_type, item_id, qty_to_drop, level=level)
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

        # ── 本局药水槽（run_potions：不占背包容量，无需背包即可拾取/使用，热键1-N优先）──
        self._tc.text("runpot_title", "本局药水 (不占容量):", 50, y, arcade.color.WHITE, 16)
        y -= 32
        run_potions = getattr(gs, 'run_potions', {})
        if run_potions:
            from entities.equipment_defs import POTIONS
            for i, (item_id, qty) in enumerate(run_potions.items()):
                pdef = POTIONS.get(item_id, {})
                name = pdef.get("name", item_id)
                self._tc.text(f"runpot_{i}", f"{name} x{qty}", 60, y,
                              arcade.color.CYAN, 14)
                btn = arcade.XYWH(WINDOW_WIDTH - 80, y + 8, 80, 24)
                # 丢弃本局药水：item_type="run_potion"（_discard_item 特殊处理）
                self.discard_buttons.append((btn, "carry", "run_potion", item_id, 1))
                y -= 30
        else:
            self._tc.text("runpot_empty", "(空)", 60, y, arcade.color.GRAY, 12)
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
