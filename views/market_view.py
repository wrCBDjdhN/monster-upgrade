"""市场页面：升级武器/装备 + 购买装备/药水 + 开箱 + 词条重铸（支持滚轮滚动）

阶段 10 设施增益接入：
- 折扣：所有**购买**价（武器/头盔/护甲/背包/药水/宝箱）套 apply_market_discount，
  单件购买与 MarketBulkOverlay 批量购买共用 _build_content 算出的 data["cost"]，
  故批量弹窗自动同价（不存在第二处价格计算）。
- 资源回购：Lv3 解锁（resource_shop_rows 纯函数），三行资源 + 数量步进 1/10。
- 售出回收加成在仓库页结算（views/warehouse_view.py + db sell_* ），
  口径见 entities/facility_defs.market_sell_bonus。
- 顶部「设施 Lv.N ▲」按钮 → FacilityView("market") 升级入口。
"""

import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, UPGRADE_BASE_COST, WEAPON_BOXES, EQUIPMENT_BOXES,
    reforge_cost,
    MARKET_RESOURCE_SHOP_LEVEL, MARKET_RESOURCE_PRICES,
)
from db.database import (
    get_weapons, get_gold, upgrade_weapon,
    get_equipment, upgrade_equipment,
    get_warehouse, spend_gold, spend_warehouse_item, add_warehouse_item,
    reforge_weapon, reforge_equipment,
    get_facility_level,
)
from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS, POTIONS
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
from entities.effects_defs import effects_label, roll_effects_for_slot, serialize_effects
from entities.facility_defs import market_discount
from entities.resource_defs import RESOURCES
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from views.market_bulk import MarketBulkOverlay
from game.sound_manager import sound_manager

# 市场设施 id（查库/进设施页用；等级 0 = 未建造，入口守卫在 start_view._enter_facility）
MARKET_FACILITY_ID = "market"

# ── 词条重铸行内按钮几何（绘制与点击共用 _reforge_btn_rect，避免两处坐标漂移）──
# 位于「升级」按钮左侧，一行两个按钮：重铸 / 升级
REFORGE_BTN_W = 120           # 重铸按钮宽（像素）
REFORGE_BTN_H = 26            # 重铸按钮高（像素，与升级按钮一致）
REFORGE_BTN_CX = WINDOW_WIDTH - 240  # 重铸按钮中心 X（升级按钮中心为 WINDOW_WIDTH - 100）

# ── 设施状态行 + 升级入口按钮几何（绘制与点击共用，避免两处坐标漂移）──
FACILITY_STATUS_X = 60                       # 状态行文本左锚点（顶部左侧，避开居中标题）
FACILITY_STATUS_Y = WINDOW_HEIGHT - 60       # 状态行 / 升级按钮所在行
FACILITY_BTN_W, FACILITY_BTN_H = 160, 34    # 升级入口按钮尺寸（点击矩形由 _facility_btn_rect 从本常量派生）
FACILITY_BTN_CX = WINDOW_WIDTH - 110         # 升级入口按钮中心 X（顶部右侧）
FACILITY_BTN_CY = WINDOW_HEIGHT - 60

# ── 资源回购行按钮几何（步进 1/10 仿批量购买弹窗的加减号）──
RES_BUY_BTN_W, RES_BUY_BTN_H = 90, 26        # 回购按钮（与其他行的购买按钮同宽同高）
RES_STEP_BTN_W, RES_STEP_BTN_H = 44, 26      # 步进按钮
RES_STEP_CXS = (930, 980, 1030, 1080)        # -10 / -1 / +1 / +10 四个步进按钮中心 X
RES_STEP_DELTAS = (-10, -1, 1, 10)           # 与 RES_STEP_CXS 一一对应


def apply_market_discount(price: int, level: int) -> int:
    """市场购买价折扣（纯函数，不查库）

    int(price × (1 - 折扣率)) 向下取整，**最低 1 金**（折扣后不足 1 金时仍收 1 金，
    杜绝 0 价/负价导致的白嫖或 spend_gold 负数入账）。
    折扣率取 entities.facility_defs.market_discount(level)：
    Lv0（未建造）/Lv1 = 0.0 → 原价，越界等级同样回 0.0（回退验证安全）。
    """
    disc = market_discount(level)
    if disc <= 0.0:
        return int(price)
    return max(1, int(int(price) * (1 - disc)))


def resource_shop_rows(level: int) -> list[dict]:
    """资源回购行（纯函数，不查库）：Lv≥MARKET_RESOURCE_SHOP_LEVEL 才解锁

    返回 [{resource_id, name, unit}]，未达等级返回空列表（调用方据此不渲染该区块）。
    单价取 config.MARKET_RESOURCE_PRICES，名称取 entities.resource_defs.RESOURCES。
    """
    if level < MARKET_RESOURCE_SHOP_LEVEL:
        return []
    rows = []
    for rid, unit in MARKET_RESOURCE_PRICES.items():
        rdef = RESOURCES.get(rid, {})
        rows.append({
            "resource_id": rid,
            "name": str(rdef.get("name", rid)),
            "unit": int(unit),
        })
    return rows


class MarketView(ScrollView):
    def __init__(self, window):
        super().__init__(window)
        # 文本缓存：持久 arcade.Text 对象，复用纹理避免每帧重建文本（消除 PerformanceWarning）
        self._tc = TextCache()
        self.wh_rect = arcade.XYWH(WINDOW_WIDTH - 100, 40, 120, 36)
        # 顶部分类 Tab 栏：按分类构建内容，切换时重建并归零滚动
        self._tabs = ["武器", "装备", "药水", "宝箱"]
        self._tab = "武器"
        self.tab_rects = {}
        tab_w, tab_h, gap = 120, 30, 12
        total_w = len(self._tabs) * tab_w + (len(self._tabs) - 1) * gap
        start_x = (WINDOW_WIDTH - total_w) // 2
        for i, name in enumerate(self._tabs):
            self.tab_rects[name] = arcade.XYWH(
                start_x + i * (tab_w + gap) + tab_w / 2, WINDOW_HEIGHT - 135, tab_w, tab_h,
            )
        # 开箱动画状态（支持批量：_box_results 为本次批量开出的结果队列）
        self._box_opening = False
        self._box_open_timer = 0.0
        self._box_open_duration = 1.5  # 单个宝箱动画持续时间（秒）
        self._box_results = []  # 批量开箱结果队列：[{"name":..., "level":..., "color":...}, ...]
        self._box_index = 0     # 当前播放到第几个（0 起）
        self._box_total = 0     # 本次批量开箱总数
        # 批量购买弹窗状态（None = 关闭）
        self._bulk_state = None
        # 词条重铸确认弹层状态（None = 关闭）：
        # {"item_type": 行类型, "data": 行数据, "cost": {"gold","ore"}, "slot": effects 分类}
        # 仅记录待重铸目标，点确认后才扣资源（防手滑）
        self._reforge_state = None
        self._input_cursor_timer = 0.0  # 输入框光标闪烁计时
        # 图鉴解锁 toast：购买/开箱产生新解锁时短暂显示（on_update 递减）
        self._toast_text = ""
        self._toast_timer = 0.0
        # 批量购买弹窗 + 开箱逻辑（拆分至 market_bulk.py，经 self.bulk 委托）
        self.bulk = MarketBulkOverlay(self)
        # 新手教程（阶段 5）：市场买卖/设施/重铸教学向导（教程最后一站，完成后标记）
        self.tut_pages = self._build_tutorial_pages()
        self.tut_next_rect = None
        self.tut_skip_rect = None
        self.tut_next_hover = False
        # 阶段10 设施（Lv.N）缓存：**每帧查库不可取**，构造时读一次 + on_show_view 刷新。
        # 等级只影响折扣/回购解锁，故只需在进入界面时重读，界面内升级后经 _refresh_facility_level 手动同步
        self._facility_level = 0
        # 资源回购数量步进状态 {resource_id: 数量}（Lv3 解锁后生效，构造时先备好键）
        self._res_qty = {rid: 1 for rid in MARKET_RESOURCE_PRICES}
        self._refresh_facility_level()
        self._build_content()

    def _refresh_facility_level(self):
        """重读市场设施等级（进入界面 / 从设施页返回时调用，避免每帧查库）"""
        pid = self.window.game_state.player_id
        self._facility_level = get_facility_level(pid, MARKET_FACILITY_ID)

    def on_show_view(self):
        """每次进入刷新设施等级与货架价格（可能在设施页升级过）"""
        super().on_show_view()
        self._refresh_facility_level()
        self._build_content()

    def facility_status_text(self) -> str:
        """顶部状态行文案：Lv.N + 当前折扣（Lv0/Lv1 无折扣，Lv2/3 显示 X%）

        折扣百分比由 market_discount 反算（(1-disc)×100 去掉小数），保证与实际计价同源。
        """
        level = self._facility_level
        disc = market_discount(level)
        if disc <= 0.0:
            return f"市场 Lv.{level} · 无折扣"
        return f"市场 Lv.{level} · 当前折扣 {int(round(disc * 100))}%"

    # ── 设施升级入口（顶部右侧「设施 Lv.N ▲」按钮）──────────────────

    def _facility_btn_rect(self) -> arcade.Rect:
        """设施升级入口按钮矩形（绘制与点击共用，保证判定与画面一致）"""
        return arcade.XYWH(FACILITY_BTN_CX, FACILITY_BTN_CY, FACILITY_BTN_W, FACILITY_BTN_H)

    def _draw_facility_bar(self):
        """绘制顶部设施状态行 + 升级入口按钮（实心填充，禁线框）"""
        self._tc.text("facility_status", self.facility_status_text(),
                      FACILITY_STATUS_X, FACILITY_STATUS_Y, arcade.color.GOLD, 12)
        btn = self._facility_btn_rect()
        arcade.draw_rect_filled(btn, (72, 58, 30))
        # 按钮放大后标签同步加大（11→13），双 center 锚点由 rect 中心驱动，无手算居中
        self._tc.text("facility_btn", f"设施 Lv.{self._facility_level} ▲",
                      btn.center_x, btn.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

    def _open_facility(self):
        """进入市场设施详情页（建造/升级）——导航唯一模式：处理函数内延迟 import"""
        from views.facility_view import FacilityView
        self.window.show_view(FacilityView(self.window_ref, MARKET_FACILITY_ID))

    # ── 资源回购（Lv3 解锁）────────────────────────────────────

    def _res_buy_rect(self, screen_y: float) -> arcade.Rect:
        """资源回购行「回 购」按钮矩形（绘制与点击共用）"""
        return arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, RES_BUY_BTN_W, RES_BUY_BTN_H)

    def _res_step_rects(self, screen_y: float) -> list[arcade.Rect]:
        """资源回购行四个步进按钮（-10 / -1 / +1 / +10）矩形，顺序与 RES_STEP_DELTAS 一致"""
        return [
            arcade.XYWH(cx, screen_y + 5, RES_STEP_BTN_W, RES_STEP_BTN_H)
            for cx in RES_STEP_CXS
        ]

    def _step_resource_qty(self, resource_id: str, delta: int, unit: int, gold: int):
        """步进资源回购数量：夹取到 [1, 金币买得起的最大个数]

        金币不足 1 个时上限取 1（下限保护），避免出现 0 数量或负数。
        """
        cur = max(1, int(self._res_qty.get(resource_id, 1)))
        target = max(1, cur + delta)
        if unit > 0:
            target = min(target, max(1, gold // unit))
        self._res_qty[resource_id] = target
        self._build_content()

    def _buy_resource(self, resource_id: str, name: str, unit: int):
        """资源回购：先校验金币 → 扣金 → 入库（先校验后扣，杜绝半扣）"""
        pid = self.window.game_state.player_id
        qty = max(1, int(self._res_qty.get(resource_id, 1)))
        total = int(unit) * qty
        if get_gold(pid) < total:
            self.show_toast(f"金币不足（需 {total} 金币），未购入")
            return
        if not spend_gold(pid, total):
            self.show_toast("金币扣除失败，未购入")
            return
        add_warehouse_item(pid, "resource", resource_id, qty)
        sound_manager.play_upgrade()
        self.show_toast(f"购入 {name}×{qty}，花费 {total} 金币")
        self._build_content()  # 刷新金币可购状态（回购不改变货架行数，仅状态变化）

    def _build_tutorial_pages(self):
        """新手教程阶段 5：市场买卖教学（末页完成后标记 tutorial_done）

        缺口补齐（2026-09-26）：原 2 页只讲了「买/卖/开箱」，漏了 v1.5.0 的
        设施等级、词条重铸、图鉴档位三个新机制——玩家在市场页看不到这些入口
        的来由。现追加 2 页（设施升级 / 重铸+图鉴），末页仍是「完成教程」。

        数值去硬编码：折扣率/等级上限/图鉴档位比例一律实查 config
        （MARKET_DISCOUNT / FORGE_COST_DISCOUNT / FACILITY_MAX_LEVEL /
        CODEX_TIER_RATIOS / MARKET_RESOURCE_SHOP_LEVEL / FORGE_REFUND_LEVEL）。
        """
        from config import (
            CODEX_TIER_RATIOS,
            FACILITY_MAX_LEVEL,
            FORGE_COST_DISCOUNT,
            FORGE_REFUND_CHANCE,
            FORGE_REFUND_LEVEL,
            MARKET_DISCOUNT,
            MARKET_RESOURCE_SHOP_LEVEL,
        )
        from views.tutorial import TutorialPage
        m_disc = [(lv, MARKET_DISCOUNT.get(lv, 0.0)) for lv in sorted(MARKET_DISCOUNT)]
        m_top = max((d for _, d in m_disc), default=0.0)
        f_disc = max(FORGE_COST_DISCOUNT.values(), default=0.0)
        tiers = " / ".join(f"{int(round(r * 100))}%" for r in CODEX_TIER_RATIOS)
        return [
            TutorialPage("市场 · 买卖装备", [
                "市场可购买武器/装备/药水，也能开神秘宝箱。",
                "顶部『武器/装备/药水/宝箱』分类栏切换货架，点列表项购买。",
                "点『仓库』可存入/取出物品，市场与仓库是战利品的集中管理地。",
            ]),
            # ── 阶段5 扩充（2026-09-26）：设施等级（新机制，此前无教程）──
            TutorialPage("设施升级", [
                f"市场/锻造坊可升到 Lv.{FACILITY_MAX_LEVEL}，升级吃仓库材料 + 金币：",
                f"  · 市场最高折扣 {int(round(m_top * 100))}%，"
                f"Lv.{MARKET_RESOURCE_SHOP_LEVEL} 额外解锁资源回购",
                f"  · 锻造坊最高折扣 {int(round(f_disc * 100))}%，"
                f"Lv.{FORGE_REFUND_LEVEL} 熔炉余温有 "
                f"{int(round(FORGE_REFUND_CHANCE * 100))}% 概率返还部分锻造费",
                "点右上角『设施 Lv.N ▲』进入建造/升级页。",
            ], highlight=self._facility_btn_rect()),
            # ── 阶段5 扩充（2026-09-26）：重铸 + 图鉴档位 ──
            TutorialPage("重铸与图鉴", [
                "买到重复词条的武器？在市场里选中它即可【重铸】洗出新词条（花金币）。",
                f"图鉴集齐条目到 {tiers} 三档，各领一次金币 + 材料奖励，",
                "条目集齐还会解锁对应【锻造配方】，去锻造坊就能合成那件装备。",
            ]),
            TutorialPage("自由逛逛吧", [
                "点击上方分类栏切换看看各个货架（可购买一件装备体验）。",
                "结束后点左下角『返回大厅』即可完成新手教程！",
            ], highlight=self.tab_rects["武器"], next_text="完成教程"),
        ]

    def _tut_showing(self):
        """教程向导是否正在本界面显示（阶段 5 且未翻完页）"""
        tut = getattr(self.window.game_state, "tutorial", None)
        return (tut is not None and tut.active and tut.stage == 5
                and tut.page < len(self.tut_pages))

    def _build_content(self):
        """构建当前分类页的内容列表，每项记录类型和逻辑Y坐标

        顶部分类 Tab（武器/装备/药水/宝箱）决定只构建对应区块，
        切换 Tab 时（on_mouse_press 内联处理）重建此列表并归零滚动。
        """
        self._tc.clear()  # 内容结构重建，清空文本缓存避免旧 key 残留
        self.content_items = []  # [(type, y, data)]
        pid = self.window.game_state.player_id
        weapons = get_weapons(pid)
        gold = get_gold(pid)
        # 重铸要花的仓库矿石存量（金币已有 gold，重铸费用按物品等级走 config.reforge_cost）
        ore_owned = sum(w["quantity"] for w in get_warehouse(pid) if w["item_id"] == "ore")

        # 统计武器数量
        weapon_counts = {}
        for w in weapons:
            key = (w["name"], w["level"])
            weapon_counts[key] = weapon_counts.get(key, 0) + 1

        y = 0  # 从0开始计算逻辑Y

        if self._tab == "武器":
            # === 武器升级区域 ===
            self.content_items.append(("header", y, "─── 武器升级 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 同名同级武器 x1 + 金币 (伤害+15% 攻速+15%)"))
            y -= 50

            if weapons:
                for w in weapons:
                    kind_label = "近战" if w["kind"] == "melee" else "远程"
                    cost = UPGRADE_BASE_COST * w["level"]
                    key = (w["name"], w["level"])
                    has_material = weapon_counts.get(key, 0) >= 2
                    rcost = reforge_cost(w["level"])
                    self.content_items.append(("weapon_upgrade", y, {
                        "id": w["id"], "name": w["name"], "level": w["level"],
                        "kind": kind_label, "damage": w["damage"], "cost": cost,
                        "has_material": has_material, "can_upgrade": has_material and gold >= cost,
                        "effects": w.get("effects") or [],
                        "reforge_cost": rcost,
                        "can_reforge": gold >= rcost["gold"] and ore_owned >= rcost["ore"],
                    }))
                    y -= 50
            else:
                self.content_items.append(("text", y, "(无武器，击杀怪物获取)"))
                y -= 40

            # === 购买武器区域===
            y -= 20
            self.content_items.append(("header", y, "─── 购买武器 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (购买后可在仓库装备使用)"))
            y -= 40
            for wdef in list(MELEE_WEAPONS.values()) + list(RANGED_WEAPONS.values()):
                # 拳头是初始武器，不可购买
                if wdef["item_id"] == "fist":
                    continue
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if wdef.get("price", 100) <= 0:
                    continue
                # 受限制武器（枪械/权杖/诅咒弯刀等）不可购买，只能开箱/锻造/掉落获得
                if wdef.get("market_restricted"):
                    continue
                cost = apply_market_discount(wdef.get("price", 100), self._facility_level)
                kind_label = "近战" if wdef["kind"] == "melee" else "远程"
                self.content_items.append(("buy_weapon", y, {
                    "item_id": wdef["item_id"], "name": wdef["name"],
                    "kind": wdef["kind"], "kind_label": kind_label,
                    "damage": wdef["damage"], "attack_speed": wdef.get("attack_speed", 1.0),
                    "range": wdef.get("range", 40), "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

        elif self._tab == "装备":
            # === 升级头盔（显示全部拥有的头盔）===
            from db.database import get_equipment_inventory, get_equipment_materials
            inventory = get_equipment_inventory(pid)
            owned_helmets = [e for e in inventory if e["slot"] == "helmet"]
            if owned_helmets:
                y -= 20
                self.content_items.append(("header", y, "─── 升级头盔 ───"))
                self.content_items.append(("subheader", y - 18, "条件: 同名同级头盔 x1 + 金币 (防+15%)"))
                y -= 40
                for helm in owned_helmets:
                    upgrade_cost = helm["defense"] * 30
                    materials = get_equipment_materials(pid, "helmet", helm["name"], helm["level"], exclude_id=helm["id"])
                    has_material = len(materials) > 0
                    status = "★已装备" if helm["is_equipped"] else ""
                    rcost = reforge_cost(helm["level"])
                    self.content_items.append(("upgrade_helmet", y, {
                        "name": helm["name"], "defense": helm["defense"], "level": helm["level"],
                        "cost": upgrade_cost, "can_upgrade": has_material and gold >= upgrade_cost,
                        "material_id": materials[0] if has_material else None,
                        "has_material": has_material, "equip_id": helm["id"], "status": status,
                        "effects": helm.get("effects") or [],
                        "reforge_cost": rcost,
                        "can_reforge": gold >= rcost["gold"] and ore_owned >= rcost["ore"],
                    }))
                    y -= 40

            # === 购买头盔 ===
            y -= 20
            self.content_items.append(("header", y, "─── 购买头盔 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (穿戴后减少受到的伤害)"))
            y -= 40
            for item_id, info in HELMETS.items():
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if info.get("price", 100) <= 0:
                    continue
                # 受限制物品（木乃伊头盔等）不可购买，只能开箱/锻造/掉落获得
                if info.get("market_restricted"):
                    continue
                cost = apply_market_discount(info.get("price", 100), self._facility_level)
                self.content_items.append(("buy_helmet", y, {
                    "item_id": item_id, "name": info["name"],
                    "defense": info["defense"], "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

            # === 升级护甲（显示全部拥有的护甲）===
            owned_armors = [e for e in inventory if e["slot"] == "armor"]
            if owned_armors:
                y -= 20
                self.content_items.append(("header", y, "─── 升级护甲 ───"))
                self.content_items.append(("subheader", y - 18, "条件: 同名同级护甲 x1 + 金币 (防+15%)"))
                y -= 40
                for arm in owned_armors:
                    upgrade_cost = arm["defense"] * 30
                    materials = get_equipment_materials(pid, "armor", arm["name"], arm["level"], exclude_id=arm["id"])
                    has_material = len(materials) > 0
                    status = "★已装备" if arm["is_equipped"] else ""
                    rcost = reforge_cost(arm["level"])
                    self.content_items.append(("upgrade_armor", y, {
                        "name": arm["name"], "defense": arm["defense"], "level": arm["level"],
                        "cost": upgrade_cost, "can_upgrade": has_material and gold >= upgrade_cost,
                        "material_id": materials[0] if has_material else None,
                        "has_material": has_material, "equip_id": arm["id"], "status": status,
                        "effects": arm.get("effects") or [],
                        "reforge_cost": rcost,
                        "can_reforge": gold >= rcost["gold"] and ore_owned >= rcost["ore"],
                    }))
                    y -= 40

            # === 购买护甲 ===
            y -= 20
            self.content_items.append(("header", y, "─── 购买护甲 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (穿戴后减少受到的伤害)"))
            y -= 40
            for item_id, info in ARMORS.items():
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if info.get("price", 150) <= 0:
                    continue
                # 受限制物品（木乃伊护甲等）不可购买，只能开箱/锻造/掉落获得
                if info.get("market_restricted"):
                    continue
                cost = apply_market_discount(info.get("price", 150), self._facility_level)
                self.content_items.append(("buy_armor", y, {
                    "item_id": item_id, "name": info["name"],
                    "defense": info["defense"], "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

            # === 购买背包 ===
            y -= 20
            self.content_items.append(("header", y, "─── 购买背包 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (没有背包不能拾取资源)"))
            y -= 40
            for item_id, info in BACKPACKS.items():
                # 神器等价格为0的物品不可在市场购买（仅锻造获得）
                if info.get("price", 200) <= 0:
                    continue
                cost = apply_market_discount(info.get("price", 200), self._facility_level)
                self.content_items.append(("buy_backpack", y, {
                    "item_id": item_id, "name": info["name"],
                    "capacity": info["capacity"], "cost": cost,
                    "can_buy": gold >= cost,
                }))
                y -= 40

        elif self._tab == "药水":
            # === 购买药水 ===
            self.content_items.append(("header", y, "─── 购买药水 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (按1-3使用)"))
            y -= 40
            for pot_id, info in POTIONS.items():
                # 价格为0的物品（仙人掌果实等）不可在市场购买（仅沙漠掉落获得）
                if info.get("price", 50) <= 0:
                    continue
                # 受限制物品不可购买（与武器/装备判断逻辑保持一致）
                if info.get("market_restricted"):
                    continue
                cost = apply_market_discount(info.get("price", 50), self._facility_level)
                self.content_items.append(("buy_potion", y, {
                    "item_id": pot_id, "name": info["name"],
                    "desc": info.get("description", ""), "cost": cost,
                    "effect": info["effect"], "value": info["value"],
                    "duration": info.get("duration", 0),
                    "can_buy": gold >= cost,
                }))
                y -= 40

        else:  # 宝箱
            # === 武器箱区域 ===
            self.content_items.append(("header", y, "─── 武器箱 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (随机获得一把武器)"))
            y -= 40
            for box_id, box in WEAPON_BOXES.items():
                cost = apply_market_discount(box["price"], self._facility_level)
                self.content_items.append(("buy_box", y, {
                    "box_id": box_id, "name": box["name"],
                    "desc": box["description"], "color": box["color"],
                    "cost": cost, "box_type": "weapon",
                    "can_buy": gold >= cost,
                }))
                y -= 40

            # === 装备箱区域 ===
            y -= 20
            self.content_items.append(("header", y, "─── 装备箱 ───"))
            self.content_items.append(("subheader", y - 18, "条件: 金币 (随机获得一件装备)"))
            y -= 40
            for box_id, box in EQUIPMENT_BOXES.items():
                cost = apply_market_discount(box["price"], self._facility_level)
                self.content_items.append(("buy_box", y, {
                    "box_id": box_id, "name": box["name"],
                    "desc": box["description"], "color": box["color"],
                    "cost": cost, "box_type": "equipment",
                    "can_buy": gold >= cost,
                }))
                y -= 40

        # === 资源回购区（阶段10：Lv3 设施解锁；未达等级不渲染任何行）===
        # 放在所有分类末尾：各 Tab 货架之后统一追加，购买/回购后重建内容仍在本区块之前
        for row in resource_shop_rows(self._facility_level):
            if not self.content_items:
                self.content_items.append(("header", y, "─── 资源回购 ───"))
                self.content_items.append(
                    ("subheader", y - 18, "条件: 金币 (设施 Lv.3 解锁，可回购木材/石材/矿石)"))
                y -= 40
            qty = max(1, int(self._res_qty.get(row["resource_id"], 1)))
            total = row["unit"] * qty
            self.content_items.append(("buy_resource", y, {
                **row, "qty": qty, "total": total,
                "can_buy": gold >= total,
            }))
            y -= 40

        self.content_height = abs(y) + 300  # 总内容高度（多加一些确保能滚到底）

    def _rebuild_keep_view(self):
        """重建内容列表，并调整 scroll_offset 使当前视口锚点保持稳定。

        购买物品后内容列表会变化（新增升级区/行会推动后续项的逻辑Y坐标），
        若只重建不补偿偏移，页面会莫名跳动/上滑。这里锚定重建前视口内
        第一个带唯一标识的可见项，重建后把它拉回原屏幕位置。
        """
        def _item_key(item_type, data):
            """返回能唯一标识同一种内容项的键（header/subheader/text 无标识返回 None）。"""
            if not isinstance(data, dict):
                return None
            return (data.get("item_id") or data.get("id")
                    or data.get("equip_id") or data.get("box_id")
                    or data.get("resource_id"))

        # 1) 记录锚点：视口内第一个有唯一标识的可见项及其屏幕Y
        anchor = None
        content_top = WINDOW_HEIGHT - 160  # 与 on_draw / on_mouse_press 一致（下方为 Tab 栏）
        for item_type, item_y, data in self.content_items:
            key = _item_key(item_type, data)
            if key is None:
                continue
            screen_y = content_top + item_y + self.scroll_offset
            if 40 <= screen_y <= content_top + 20:
                anchor = (item_type, key, screen_y)
                break

        # 2) 重建内容
        self._build_content()

        # 3) 若锚点项仍在，补偿偏移使其回到原来的屏幕位置
        if anchor is not None:
            a_type, a_key, old_screen_y = anchor
            for item_type, item_y, data in self.content_items:
                key = _item_key(item_type, data)
                if item_type == a_type and key == a_key:
                    new_screen_y = content_top + item_y + self.scroll_offset
                    self.scroll_offset += old_screen_y - new_screen_y
                    self.clamp_scroll()
                    break

    def get_bg_color(self):
        return (25, 20, 35)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """覆盖基类：开箱动画 / 重铸弹层打开时禁止滚动"""
        if self._box_opening or self._reforge_state:
            return
        super().on_mouse_scroll(x, y, scroll_x, scroll_y)

    def show_toast(self, text: str, duration: float = 3.0):
        """图鉴解锁等短暂提示：设置文案并启动倒计时（on_draw 顶层绘制）"""
        self._toast_text = text
        self._toast_timer = duration

    def on_update(self, delta_time: float):
        """更新开箱动画计时器（批量：逐个播放结果队列）"""
        self._input_cursor_timer += delta_time
        # 图鉴解锁 toast 倒计时
        if self._toast_timer > 0.0:
            self._toast_timer -= delta_time
            if self._toast_timer <= 0.0:
                self._toast_timer = 0.0
                self._toast_text = ""
        if self._box_opening:
            self._box_open_timer += delta_time
            if self._box_open_timer >= self._box_open_duration:
                # 当前宝箱动画结束，播放下一个或收尾
                self._box_index += 1
                if self._box_index >= len(self._box_results):
                    self._box_opening = False
                    self._box_results = []
                    self._box_index = 0
                    self._box_total = 0
                else:
                    self._box_open_timer = 0.0

    def _reforge_btn_rect(self, screen_y: float) -> arcade.Rect:
        """行内「重铸」按钮矩形（绘制与 on_mouse_press 共用，保证点击判定与画面完全一致）"""
        return arcade.XYWH(REFORGE_BTN_CX, screen_y + 5, REFORGE_BTN_W, REFORGE_BTN_H)

    def _draw_reforge_btn(self, key: str, screen_y: float, data: dict):
        """绘制行内「重铸」按钮：实心填充（禁线框）+ 费用文案；资源不足置灰

        重铸语义提醒：词条完全重新随机、可能变差，故按钮只负责打开确认弹层，不直接扣费。
        """
        cost = data["reforge_cost"]
        can = data["can_reforge"]
        btn = self._reforge_btn_rect(screen_y)
        arcade.draw_rect_filled(btn, (78, 62, 118) if can else (60, 60, 60))
        self._tc.text(f"{key}_label", f"重铸({cost['gold']}金+{cost['ore']}矿)",
                      btn.center_x, btn.center_y, arcade.color.WHITE, 9,
                      anchor_x="center", anchor_y="center")

    def on_draw(self):
        self.clear()
        pid = self.window.game_state.player_id
        gold = get_gold(pid)
        equip = get_equipment(pid)
        offset = self.scroll_offset

        # === 固定头部 ===
        self._tc.text("header_title", "市 场", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30,
                      arcade.color.GOLD, 30, anchor_x="center")
        self._tc.text("header_gold", f"金币: {gold}", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
                      arcade.color.YELLOW, 18, anchor_x="center")
        self._tc.text("header_hint", "滚轮滚动查看全部商品", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 80,
                      arcade.color.GRAY, 11, anchor_x="center")

        # 阶段10 设施状态行 + 升级入口按钮（顶部左侧状态 / 右侧按钮）
        self._draw_facility_bar()

        # 当前装备
        equip_text = ""
        if "helmet" in equip:
            equip_text += f"头盔:{equip['helmet']['name']}(防+{equip['helmet']['defense']}) "
        if "armor" in equip:
            equip_text += f"护甲:{equip['armor']['name']}(防+{equip['armor']['defense']}) "
        if "backpack" in equip:
            equip_text += f"背包:{equip['backpack']['name']}(容量:{equip['backpack']['capacity']})"
        if equip_text:
            self._tc.text("equip_now", f"当前装备: {equip_text}", 60, WINDOW_HEIGHT - 100,
                          arcade.color.CORNFLOWER_BLUE, 11)
        else:
            self._tc.text("equip_now", "当前装备: 无", 60, WINDOW_HEIGHT - 100,
                          arcade.color.GRAY, 11)
        content_top = WINDOW_HEIGHT - 160  # 内容区顶部（下方固定 Tab 栏）

        # === 顶部 Tab 栏（固定，不随内容滚动）===
        for name, rect in self.tab_rects.items():
            active = (name == self._tab)
            bg = (80, 130, 80) if active else (50, 55, 65)
            arcade.draw_rect_filled(rect, bg)
            self._tc.text(f"tab_{name}", name, rect.center_x, rect.center_y,
                          arcade.color.WHITE if active else arcade.color.LIGHT_GRAY,
                          15, anchor_x="center", anchor_y="center")

        # === 可滚动内容 ===
        for i, (item_type, item_y, data) in enumerate(self.content_items):
            screen_y = content_top + item_y + offset
            # 跳过不在视口内的项
            if screen_y < 40 or screen_y > content_top + 20:
                continue

            if item_type == "header":
                self._tc.text(f"header_{i}", data, 60, screen_y, arcade.color.LIGHT_GRAY, 13)
            elif item_type == "subheader":
                self._tc.text(f"subheader_{i}", data, 60, screen_y, arcade.color.GRAY, 10)
            elif item_type == "text":
                self._tc.text(f"text_{i}", data, 60, screen_y, arcade.color.GRAY, 12)

            elif item_type == "weapon_upgrade":
                # 武器信息（含附加效果显示）
                eff_txt = ""
                if data.get("effects"):
                    eff_txt = f"  效果:{effects_label(data['effects'])}"
                self._tc.text(
                    f"weapon_upgrade_{i}",
                    f"[{data['kind']}] {data['name']}  Lv.{data['level']}  伤害:{data['damage']:.0f}{eff_txt}",
                    60, screen_y, arcade.color.CORNFLOWER_BLUE, 12,
                )
                # 升级按钮
                btn_color = arcade.color.DARK_GREEN if data["can_upgrade"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                label = f"升级({data['cost']}G)" if data["has_material"] else "缺材料"
                self._tc.text(f"weapon_upgrade_btn_{i}", label, btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
                # 重铸按钮（升级按钮左侧）
                self._draw_reforge_btn(f"weapon_reforge_{i}", screen_y, data)

            elif item_type in ("upgrade_helmet", "upgrade_armor"):
                status = data.get("status", "")
                eff_txt = ""
                if data.get("effects"):
                    eff_txt = f"  效果:{effects_label(data['effects'])}"
                self._tc.text(
                    f"{item_type}_{i}",
                    f"Lv{data['level']} {data['name']}  防+{data['defense']}  {status}{eff_txt}",
                    60, screen_y, arcade.color.CORNFLOWER_BLUE, 12,
                )
                btn_color = arcade.color.DARK_GREEN if data["can_upgrade"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                label = f"升级({data['cost']}G)" if data["has_material"] else "缺材料"
                self._tc.text(f"{item_type}_btn_{i}", label, btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")
                # 重铸按钮（升级按钮左侧）
                self._draw_reforge_btn(f"{item_type}_reforge_{i}", screen_y, data)

            elif item_type in ("buy_helmet", "buy_armor", "buy_backpack"):
                label = f"{data['name']}  "
                if "defense" in data:
                    label += f"防+{data['defense']}"
                elif "capacity" in data:
                    label += f"容量:{data['capacity']}"
                self._tc.text(f"{item_type}_{i}", label, 60, screen_y, arcade.color.GREEN, 12)
                # 购买按钮
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"{item_type}_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_potion":
                # 第一行：药水名
                self._tc.text(f"buy_potion_{i}", f"{data['name']}", 60, screen_y,
                              arcade.color.GREEN, 13)
                # 第二行：效果介绍（从 POTIONS 定义取 description）
                effect_desc = data.get("desc", "")
                if effect_desc:
                    self._tc.text(f"buy_potion_desc_{i}", f"    {effect_desc}", 70, screen_y - 16,
                                  arcade.color.LIGHT_GRAY, 11)
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"buy_potion_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_weapon":
                self._tc.text(
                    f"buy_weapon_{i}",
                    f"[{data['kind_label']}] {data['name']}  伤害:{data['damage']}  距离:{data['range']}",
                    60, screen_y, arcade.color.CORNFLOWER_BLUE, 12,
                )
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"buy_weapon_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_box":
                color = data.get("color", (200, 150, 50))
                self._tc.text(
                    f"buy_box_{i}",
                    f"📦 {data['name']}  {data['desc']}",
                    60, screen_y, color, 12,
                )
                btn_color = arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60)
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                arcade.draw_rect_filled(btn, btn_color)
                self._tc.text(f"buy_box_btn_{i}", f"购买({data['cost']}金币)", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 10, anchor_x="center", anchor_y="center")

            elif item_type == "buy_resource":
                # 资源回购行（Lv3）：名称+单价+数量+小计 + 四个步进按钮 + 回购按钮（全部实心填充）
                btn = self._res_buy_rect(screen_y)
                step_rects = self._res_step_rects(screen_y)
                self._tc.text(
                    f"buy_resource_{i}",
                    f"{data['name']}  单价 {data['unit']} 金币/个  数量 ×{data['qty']}"
                    f"  小计 {data['total']} 金币",
                    60, screen_y, arcade.color.GREEN, 12,
                )
                for kind, rect in zip(("m10", "m1", "p1", "p10"), step_rects):
                    arcade.draw_rect_filled(rect, (70, 70, 82))
                    self._tc.text(f"res_step_{kind}_{i}", kind, rect.center_x, rect.center_y,
                                  arcade.color.WHITE, 11, anchor_x="center", anchor_y="center")
                arcade.draw_rect_filled(btn, arcade.color.DARK_GREEN if data["can_buy"] else (60, 60, 60))
                self._tc.text(f"res_buy_btn_{i}", "回 购", btn.center_x, btn.center_y,
                              arcade.color.WHITE, 11, anchor_x="center", anchor_y="center")

        # 滚动条（基类统一绘制 + 支持鼠标拖拽）
        self.draw_scrollbar(content_top)

        # === 固定底部导航 ===
        arcade.draw_rect_filled(self.back_rect, arcade.color.DARK_RED)
        self._tc.text("nav_back", "返回大厅", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(self.wh_rect, arcade.color.DARK_BLUE)
        self._tc.text("nav_wh", "仓库", self.wh_rect.center_x, self.wh_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

        # === 开箱动画覆盖层（支持批量：逐个播放 _box_results 队列）===
        if self._box_opening and self._box_results:
            # 半透明黑色遮罩
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
                (0, 0, 0, 180),
            )
            # 动画进度 0→1
            progress = min(1.0, self._box_open_timer / self._box_open_duration)
            result = self._box_results[self._box_index]
            # 缩放效果：从0.5倍放大到1.0倍
            scale = 0.5 + 0.5 * min(1.0, progress * 2)
            # 闪光效果：前0.3秒有闪光
            flash_alpha = max(0, 1.0 - progress * 3) * 255
            # 宝箱图标
            box_color = result.get("color", (255, 200, 50))
            box_size = int(80 * scale)
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 40, box_size, box_size),
                box_color,
            )
            if flash_alpha > 0:
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 40, box_size + 20, box_size + 20),
                    (255, 255, 255, int(flash_alpha)),
                )
            # 获得物品信息（动画后半段显示）
            if progress > 0.5:
                text_alpha = min(1.0, (progress - 0.5) * 4)
                name = result.get("name", "")
                level = result.get("level", 1)
                text = f"获得 {name} Lv.{level}!"
                self._tc.text(
                    "box_result", text, WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 40,
                    arcade.color.GOLD, 22, anchor_x="center", anchor_y="center",
                )
            # 批量进度（仅一次购买多个宝箱时显示）
            if self._box_total > 1:
                self._tc.text(
                    "box_progress", f"宝箱 {self._box_index + 1} / {self._box_total}",
                    WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 150,
                    arcade.color.LIGHT_GRAY, 14, anchor_x="center", anchor_y="center",
                )
                # 整体进度条（尾部两相进度 = 已播完数量 + 当前箱内进度）
                total_progress = (self._box_index + progress) / self._box_total
                bar_w = 360
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 120, bar_w, 10),
                    (60, 60, 60),
                )
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2 - bar_w / 2 + bar_w * total_progress / 2,
                                WINDOW_HEIGHT // 2 + 120, bar_w * total_progress, 10),
                    arcade.color.GOLD,
                )

        # === 批量购买弹窗覆盖层 ===
        self._draw_bulk_overlay()

        # === 词条重铸确认弹层（画在批量弹层之上：两者互斥，不会同时打开）===
        self._draw_reforge_overlay()

        # === 图鉴解锁 toast（画在弹窗之上、教程之下，短暂显示后自动消失）===
        if self._toast_timer > 0.0 and self._toast_text:
            toast_w = max(280, len(self._toast_text) * 14 + 40)
            arcade.draw_rect_filled(
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT - 180, toast_w, 36),
                (30, 35, 20),
            )
            self._tc.text(
                "codex_toast", self._toast_text, WINDOW_WIDTH // 2, WINDOW_HEIGHT - 180,
                arcade.color.GOLD, 14, anchor_x="center", anchor_y="center",
            )

        # === 新手教程（阶段 5）：买卖/设施/重铸教学向导弹窗（画在最上层）===
        if self._tut_showing():
            from views.tutorial import draw_tutorial_page
            tut = getattr(self.window.game_state, "tutorial", None)
            page = self.tut_pages[tut.page]
            self.tut_next_rect, self.tut_skip_rect = draw_tutorial_page(
                self, page, tut.page, len(self.tut_pages),
                self._tc, self.tut_next_hover)

    def on_mouse_press(self, x, y, button, modifiers):
        # 动画中禁止所有点击
        if self._box_opening:
            return

        sound_manager.play_ui()
        # 新手教程向导显示：只响应 下一步/跳过（最后一页点完成后标记教程结束）
        if self._tut_showing():
            from views.tutorial import finish_tutorial
            if self.tut_skip_rect and self.tut_skip_rect.point_in_rect((x, y)):
                finish_tutorial(self.window)
                return
            if self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)):
                tut = getattr(self.window.game_state, "tutorial", None)
                if tut is not None and tut.page + 1 >= len(self.tut_pages):
                    # 最后一页：完成教程并标记（留在市场可自由浏览/购买）
                    finish_tutorial(self.window)
                elif tut is not None:
                    tut.page += 1
                return
            return

        # 批量购买弹窗打开时：只处理弹窗交互，不穿透到下方列表
        if self._bulk_state:
            self._handle_bulk_press(x, y)
            return

        # 词条重铸弹层打开时：只处理弹层交互，不穿透到下方列表（防手滑误触）
        if self._reforge_state:
            self._handle_reforge_press(x, y)
            return

        pid = self.window.game_state.player_id
        offset = self.scroll_offset

        # 固定按钮
        if self.handle_back_click(x, y):
            # 新手教程：市场是最后一站，点返回时确保标记完成（防中途未点完成按钮）
            tut = getattr(self.window.game_state, "tutorial", None)
            if tut is not None and tut.active and tut.stage == 5:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return
        if self.wh_rect.point_in_rect((x, y)):
            from views.warehouse_view import WarehouseView
            self.window.show_view(WarehouseView(self.window_ref))
            return

        # 阶段10 设施升级入口（顶部右侧「设施 Lv.N ▲」）：进设施详情页建造/升级
        if self._facility_btn_rect().point_in_rect((x, y)):
            self._open_facility()
            return

        # 顶部 Tab 栏切换分类
        for name, rect in self.tab_rects.items():
            if rect.point_in_rect((x, y)):
                if name != self._tab:
                    self._tab = name
                    self.scroll_offset = 0.0  # 切换分类时归零滚动
                    self._build_content()
                return

        # 计算内容区域顶部（须与 on_draw 完全一致）
        get_equipment(pid)  # 保持与绘制时一致的状态读取
        content_top = WINDOW_HEIGHT - 160

        # 右侧滚动条拖拽
        if self.start_scroll_drag(x, y, content_top):
            return

        # 检测可滚动内容中的按钮点击
        for item_type, item_y, data in self.content_items:
            screen_y = content_top + item_y + offset
            if screen_y < 40 or screen_y > content_top + 20:
                continue

            if item_type == "weapon_upgrade":
                # 重铸按钮（左侧）：资源不足置灰不响应；点击只开确认弹层，不直接扣费
                if self._reforge_btn_rect(screen_y).point_in_rect((x, y)):
                    if data["can_reforge"]:
                        self._open_reforge(item_type, data)
                    else:
                        self.show_toast("金币或矿石不足，无法重铸")
                    return
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_upgrade"]:
                    upgrade_weapon(pid, data["id"])
                    self._rebuild_keep_view()
                    return

            elif item_type in ("upgrade_helmet", "upgrade_armor"):
                if self._reforge_btn_rect(screen_y).point_in_rect((x, y)):
                    if data["can_reforge"]:
                        self._open_reforge(item_type, data)
                    else:
                        self.show_toast("金币或矿石不足，无法重铸")
                    return
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_upgrade"]:
                    upgrade_equipment(pid, data["equip_id"], data["material_id"])
                    self._rebuild_keep_view()
                    return

            elif item_type in ("buy_helmet", "buy_armor", "buy_backpack"):
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"]:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_potion":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"]:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_weapon":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"]:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_box":
                btn = arcade.XYWH(WINDOW_WIDTH - 100, screen_y + 5, 90, 26)
                if btn.point_in_rect((x, y)) and data["can_buy"] and not self._box_opening:
                    self._open_bulk(item_type, data)
                    return

            elif item_type == "buy_resource":
                rid, unit = data["resource_id"], data["unit"]
                for delta, rect in zip(RES_STEP_DELTAS, self._res_step_rects(screen_y)):
                    if rect.point_in_rect((x, y)):
                        self._step_resource_qty(rid, delta, unit, get_gold(pid))
                        return
                if self._res_buy_rect(screen_y).point_in_rect((x, y)):
                    self._buy_resource(rid, data["name"], unit)
                    return

    # ── 批量购买弹窗（逻辑拆分至 market_bulk.py，此处仅保留委托）──

    def _open_bulk(self, item_type, data):
        """委托 → bulk._open_bulk"""
        return self.bulk._open_bulk(item_type, data)

    def _cancel_bulk(self):
        """委托 → bulk._cancel_bulk"""
        return self.bulk._cancel_bulk()

    def _set_qty(self, qty):
        """委托 → bulk._set_qty"""
        return self.bulk._set_qty(qty)

    def _slider_handle_x(self):
        """委托 → bulk._slider_handle_x"""
        return self.bulk._slider_handle_x()

    def _handle_bulk_press(self, x, y):
        """委托 → bulk._handle_bulk_press"""
        return self.bulk._handle_bulk_press(x, y)

    def on_mouse_motion(self, x, y, dx, dy):
        """滚动条拖拽实时滚动；滑块拖拽时实时更新数量"""
        # 新手教程向导显示：只更新下一步按钮悬停态
        if self._tut_showing():
            self.tut_next_hover = bool(
                self.tut_next_rect and self.tut_next_rect.point_in_rect((x, y)))
            return
        if self._scroll_dragging:
            self.update_scroll_drag(x, y)
            return
        st = self._bulk_state
        if st and st["dragging"]:
            track_left = 640 - 215
            track_w = 430
            self._set_qty(round((x - track_left) / track_w * (st["max_qty"] - 1)) + 1)

    def on_mouse_release(self, x, y, button, modifiers):
        """松开鼠标：结束滚动条拖拽与滑块拖拽"""
        self.end_scroll_drag()
        if self._bulk_state:
            self._bulk_state["dragging"] = False

    def on_key_press(self, symbol, modifiers):
        """弹窗键盘交互：Esc 取消、Enter 确认、输入态下支持数字与退格"""
        # 新手教程激活：ESC 立即跳过并标记完成（优先于批量弹窗的 Esc 取消）
        tut = getattr(self.window.game_state, "tutorial", None)
        if tut is not None and tut.active:
            if symbol == arcade.key.ESCAPE:
                from views.tutorial import finish_tutorial
                finish_tutorial(self.window)
            return
        st = self._bulk_state
        rf = self._reforge_state
        if rf and not st:
            # 重铸弹层：Esc 取消、Enter 确认（其余按键忽略，防止误输入）
            if symbol == arcade.key.ESCAPE:
                self._cancel_reforge()
            elif symbol == arcade.key.ENTER:
                self._confirm_reforge()
            return
        if not st:
            return
        if symbol == arcade.key.ESCAPE:
            self._cancel_bulk()
        elif symbol == arcade.key.ENTER:
            self._confirm_bulk()
        elif st["input_active"]:
            if symbol == arcade.key.BACKSPACE:
                s = str(st["qty"])[:-1] or "1"
                self._set_qty(int(s))
            elif arcade.key.KEY_0 <= symbol <= arcade.key.KEY_9:
                s = str(st["qty"]) + chr(symbol)
                self._set_qty(int(s))
            elif arcade.key.KEY_NUM_0 <= symbol <= arcade.key.KEY_NUM_9:
                s = str(st["qty"]) + chr(symbol - arcade.key.KEY_NUM_0 + ord("0"))
                self._set_qty(int(s))

    def _confirm_bulk(self):
        """委托 → bulk._confirm_bulk"""
        return self.bulk._confirm_bulk()

    def _roll_box(self, box_type, box_id):
        """委托 → bulk._roll_box"""
        return self.bulk._roll_box(box_type, box_id)

    def _start_box_sequence(self):
        """委托 → bulk._start_box_sequence"""
        return self.bulk._start_box_sequence()

    def _draw_bulk_overlay(self):
        """委托 → bulk._draw_bulk_overlay"""
        return self.bulk._draw_bulk_overlay()

    # ── 词条重铸（行内按钮 + 确认弹层）──

    def _open_reforge(self, item_type: str, data: dict):
        """打开重铸确认弹层：仅记录待重铸目标与费用，本步不扣任何资源

        item_type: weapon_upgrade / upgrade_helmet / upgrade_armor（_build_content 的行类型）
        slot     : 词条抽取分类（武器走 debuff 池，头盔/护甲走 passive 池），
                   与 entities.effects_defs.roll_effects_for_slot 的 slot 口径一致
        """
        slot = {"weapon_upgrade": "weapon", "upgrade_helmet": "helmet",
                "upgrade_armor": "armor"}.get(item_type, "weapon")
        self._reforge_state = {
            "item_type": item_type,
            "data": data,
            "cost": data["reforge_cost"],
            "slot": slot,
        }

    def _cancel_reforge(self):
        """取消重铸：关闭弹层，不产生任何消费"""
        self._reforge_state = None

    def _handle_reforge_press(self, x, y):
        """处理重铸弹层内的点击：点面板外部遮罩即取消 / 确认 / 取消（不穿透到下方列表）"""
        st = self._reforge_state
        if not st:
            return
        panel = arcade.XYWH(640, 360, 480, 300)
        if not panel.point_in_rect((x, y)):
            self._cancel_reforge()
            return
        if arcade.XYWH(560, 250, 170, 42).point_in_rect((x, y)):
            self._confirm_reforge()
            return
        if arcade.XYWH(720, 250, 170, 42).point_in_rect((x, y)):
            self._cancel_reforge()

    def _confirm_reforge(self):
        """确认重铸：校验并扣金币+仓库矿石 → 按物品等级重 roll 全新词条 → 落库 → 刷新列表 + 飘字

        扣费顺序（防半扣）：先校验金币够 → 再扣矿石（spend_warehouse_item 返回 False 即中止，
        库存不受影响）→ 最后扣金币。UI 单线程无并发，先校验后扣可保证不会出现半扣状态。
        词条重 roll 一律传 existing=None（**完全重摇，可能变差**；与升级「只升不降」互不冲突）。
        """
        st = self._reforge_state
        if not st:
            return
        pid = self.window.game_state.player_id
        data = st["data"]
        cost = st["cost"]
        if get_gold(pid) < cost["gold"]:
            self.show_toast(f"金币不足（需 {cost['gold']} 金币），重铸未执行")
            return
        if not spend_warehouse_item(pid, "ore", cost["ore"]):
            self.show_toast(f"矿石不足（需 {cost['ore']} 个），重铸未执行")
            return
        spend_gold(pid, cost["gold"])
        # 按物品等级重新 roll 全新词条（复用 effects_defs 现有口径，武器 debuff 池 / 装备 passive 池）
        new_effects = roll_effects_for_slot(data["level"], st["slot"])
        if st["item_type"] == "weapon_upgrade":
            reforge_weapon(data["id"], serialize_effects(new_effects))
        else:
            reforge_equipment(data["equip_id"], serialize_effects(new_effects))
        self._reforge_state = None
        sound_manager.play_forge()
        # 重建内容：刷新该行词条显示（等级/伤害/防御由 DB 保证不变）
        self._rebuild_keep_view()
        self.show_toast(f"重铸成功：{effects_label(new_effects)}", 3.0)
        # 阶段6.2 任务/成就进度：重铸成功（局外事件，市场本就是各端本地 UI/本地库，各端自计；
        # player_id 参数在局外事件不参与，故此处省略——它只用于局内事件的联机归属）
        from game.mission_tracker import on_event as mission_on_event
        mission_on_event(self, "reforge", 1)

    def _draw_reforge_overlay(self):
        """绘制重铸确认弹层：全屏遮罩 + 面板 + 物品/当前词条/费用 + 防手滑警示 + 确认取消

        全部不透明实心填充（禁线框/空心绘制），与 market_bulk 弹层视觉口径一致。
        """
        st = self._reforge_state
        if not st:
            return
        data = st["data"]
        cost = st["cost"]
        # 全屏半透明遮罩
        arcade.draw_rect_filled(
            arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
            (0, 0, 0, 180),
        )
        # 面板
        arcade.draw_rect_filled(arcade.XYWH(640, 360, 480, 300), (34, 28, 46))
        self._tc.text("reforge_title", "词 条 重 铸", 640, 470, arcade.color.GOLD, 20,
                      anchor_x="center", anchor_y="center")
        self._tc.text("reforge_item", f"{data['name']}  Lv.{data['level']}", 640, 430,
                      arcade.color.CORNFLOWER_BLUE, 16, anchor_x="center", anchor_y="center")
        self._tc.text("reforge_cur", f"当前词条：{effects_label(data.get('effects') or [])}", 640, 395,
                      arcade.color.LIGHT_GRAY, 12, anchor_x="center", anchor_y="center")
        self._tc.text("reforge_cost", f"费用：{cost['gold']} 金币 + {cost['ore']} 矿石", 640, 355,
                      arcade.color.YELLOW, 14, anchor_x="center", anchor_y="center")
        # 防手滑警示：重铸是纯随机重摇，必须明确告知可能变差
        self._tc.text("reforge_warn", "词条将完全重新随机，可能变差", 640, 310,
                      arcade.color.ORANGE_RED, 14, anchor_x="center", anchor_y="center")
        self._tc.text("reforge_warn2", "物品等级不会变化，重铸后不可撤销", 640, 285,
                      arcade.color.GRAY, 11, anchor_x="center", anchor_y="center")
        # 确认 / 取消按钮
        arcade.draw_rect_filled(arcade.XYWH(560, 250, 170, 42), arcade.color.DARK_GREEN)
        self._tc.text("reforge_confirm", "确认重铸", 560, 250, arcade.color.WHITE, 14,
                      anchor_x="center", anchor_y="center")
        arcade.draw_rect_filled(arcade.XYWH(720, 250, 170, 42), arcade.color.DARK_RED)
        self._tc.text("reforge_cancel", "取消", 720, 250, arcade.color.WHITE, 14,
                      anchor_x="center", anchor_y="center")
