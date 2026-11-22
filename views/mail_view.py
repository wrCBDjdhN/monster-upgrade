"""邮件页面：系统奖励与满仓溢出物品的统一收件箱

- 列表来源 db.mail.get_mails（未领取在前、新的在前），每行展示标题/正文/
  金币/经验/附件明细 + 领取按钮（已领取置灰）；
- 顶部固定显示仓库剩余容量（领取前预检用同一口径，见 db.warehouse）；
- 领取链：can_claim_mail 预检容量 → claim_mail 标记并返回载荷 → 本页用
  add_gold / add_exp（当前角色）/ add_warehouse_item / create_weapon /
  add_equipment / add_potion 发放；
- 容量不足整封拒绝领取并飘中文原因（提示升级仓库或清理空间）；
- 滚动复用 views/scroll_view.py，返回按钮 + ESC 走基类跳转规则。
"""

import math

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT
from db.database import (
    get_mails, can_claim_mail, claim_mail, warehouse_remaining_capacity,
    add_gold, add_exp, add_warehouse_item, create_weapon, add_equipment,
    add_potion, delete_claimed_mails, add_warehouse_box, box_type_of,
)
from views.scroll_view import ScrollView
from views.text_cache import TextCache
from game.sound_manager import sound_manager


def _item_label(item_type: str, item_id: str, level: int) -> str:
    """附件中文名（资源/武器/装备/药水各取 entities 定义，禁硬编码名称）"""
    if item_type == "resource":
        from entities.resource_defs import RESOURCES
        return RESOURCES.get(item_id, {}).get("name", item_id)
    if item_type == "weapon":
        from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
        info = {**MELEE_WEAPONS, **RANGED_WEAPONS}.get(item_id, {})
        return f"{info.get('name', item_id)} Lv{level}"
    if item_type in ("helmet", "armor", "backpack"):
        from entities.equipment_defs import ARMORS, BACKPACKS, HELMETS
        defs = {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}[item_type]
        return f"{defs.get(item_id, {}).get('name', item_id)} Lv{level}"
    if item_type == "potion":
        from entities.equipment_defs import POTIONS
        return POTIONS.get(item_id, {}).get("name", item_id)
    if item_type == "box":
        from config import EQUIPMENT_BOXES, WEAPON_BOXES
        return {**WEAPON_BOXES, **EQUIPMENT_BOXES}.get(item_id, {}).get("name", item_id)
    return item_id


class MailView(ScrollView):
    """邮件收件箱：列表 + 领取按钮 + 仓库剩余容量提示"""

    # ── 布局常量（1280×720 逻辑分辨率）──
    LIST_X = 90
    LIST_W = WINDOW_WIDTH - 180
    ROW_H = 112
    ROW_STEP = 124
    CLAIM_W = 110
    CLAIM_H = 32
    CONTENT_BOTTOM = 70
    # 颜色
    C_PANEL = (38, 42, 54)
    C_PANEL_DONE = (30, 33, 40)
    C_CLAIM_OK = (215, 165, 40)
    C_CLAIM_OK_HOVER = (250, 210, 80)
    C_CLAIM_OFF = (62, 64, 72)
    C_BACK = (110, 30, 30)
    C_BACK_HOVER = arcade.color.DARK_RED
    C_BG = (25, 30, 40)
    # 飘字
    TOAST_LIFE = 1.8
    TOAST_RISE = 38.0

    def __init__(self, window):
        super().__init__(window)
        self._tc = TextCache()
        self._mails: list[dict] = []
        self._remain = 0                      # 仓库剩余容量（打开/领取后刷新）
        self._claim_rects: list[tuple[arcade.Rect, int]] = []
        self._claim_hover = -1
        self.back_hover = False
        self.delete_hover = False
        self._toasts: list[dict] = []
        self._pulse = 0.0
        self._refresh()

    # ── 数据 ──────────────────────────────────────────────────

    def _player_id(self):
        gs = getattr(self.window_ref, "game_state", None)
        return getattr(gs, "player_id", None) if gs is not None else None

    def _refresh(self):
        """重读邮件列表与仓库剩余容量（结构变化后清空文本缓存）"""
        pid = self._player_id()
        if pid is None:
            self._mails = []
            self._remain = 0
        else:
            self._mails = get_mails(pid)
            self._remain = warehouse_remaining_capacity(pid)
        self._tc.clear()
        self._claim_rects = []
        self._claim_hover = -1
        self.content_height = max(0, (len(self._mails) - 1) * self.ROW_STEP + self.ROW_H)
        self.clamp_scroll()

    @property
    def content_top(self) -> float:
        """可滚动内容区顶部 y（顶部标题栏下方）"""
        return WINDOW_HEIGHT - 100

    @property
    def _delete_rect(self) -> arcade.Rect:
        """「删除已领取」按钮矩形（顶部左侧，与标题/容量提示同排）"""
        return arcade.XYWH(self.LIST_X + 80, WINDOW_HEIGHT - 42, 160, 32)

    def _max_scroll(self) -> float:
        depth = max(0, (len(self._mails) - 1) * self.ROW_STEP + self.ROW_H)
        return max(0.0, depth - (self.content_top - self.CONTENT_BOTTOM))

    def clamp_scroll(self):
        self.scroll_offset = max(0.0, min(self._max_scroll(), self.scroll_offset))

    def _scrollbar_max_scroll(self) -> float:
        return self._max_scroll()

    def on_show_view(self):
        super().on_show_view()
        self._refresh()

    # ── 绘制 ──────────────────────────────────────────────────

    def on_draw(self):
        self.clear()
        self._draw_header()
        self._draw_rows()
        self.draw_scrollbar()
        self._draw_back()
        self._draw_toasts()

    def _draw_header(self):
        self._tc.text("title", "邮 件", WINDOW_WIDTH // 2, WINDOW_HEIGHT - 42,
                      arcade.color.GOLD, 30, anchor_x="center")
        pid = self._player_id()
        hint = "--" if pid is None else str(self._remain)
        self._tc.text("remain", f"仓库剩余容量 {hint} 格",
                      WINDOW_WIDTH - 100, WINDOW_HEIGHT - 42,
                      arcade.color.LIGHT_GRAY, 16, anchor_x="center")
        # 已领取邮件批量清理（没有可删的邮件时置灰，点击仍给提示）
        has_claimed = any(m.get("claimed") for m in self._mails)
        if has_claimed and self.delete_hover:
            bg = (185, 60, 55)
        elif has_claimed:
            bg = (130, 45, 42)
        else:
            bg = (58, 60, 70)
        arcade.draw_rect_filled(self._delete_rect, bg)
        self._tc.text("delete_claimed", "删除已领取", self._delete_rect.center_x,
                      self._delete_rect.center_y,
                      arcade.color.WHITE if has_claimed else arcade.color.GRAY,
                      13, anchor_x="center", anchor_y="center")

    def _draw_rows(self):
        self._claim_rects = []
        if not self._mails:
            hint = "暂无邮件" if self._player_id() is not None else "请先创建玩家档案"
            self._tc.text("empty", hint, WINDOW_WIDTH // 2, self.content_top - 40,
                          arcade.color.GRAY, 16, anchor_x="center")
            return
        y = self.content_top - self.ROW_H / 2 + self.scroll_offset
        for i, mail in enumerate(self._mails):
            if self.CONTENT_BOTTOM <= y <= self.content_top:
                self._draw_row(i, mail, y)
            y -= self.ROW_STEP

    def _draw_row(self, i: int, mail: dict, y: float):
        """绘制单封邮件：标题/时间/正文/奖励与附件/领取按钮"""
        rect = arcade.XYWH(self.LIST_X + self.LIST_W / 2, y, self.LIST_W, self.ROW_H)
        claimed = bool(mail.get("claimed"))
        # 渲染铁律：只用不透明实心填充，禁线框/描边
        arcade.draw_rect_filled(rect, self.C_PANEL_DONE if claimed else self.C_PANEL)
        fg = arcade.color.GRAY if claimed else arcade.color.WHITE
        left = rect.left + 16
        self._tc.text(f"mail_title_{i}", mail.get("title", ""),
                      left, rect.top - 18, fg, 18, anchor_y="center")
        self._tc.text(f"mail_time_{i}", str(mail.get("created_at", "")),
                      rect.right - 16, rect.top - 18, arcade.color.GRAY, 11,
                      anchor_x="right", anchor_y="center")
        self._tc.text(f"mail_body_{i}", str(mail.get("body", ""))[:56],
                      left, rect.top - 42, arcade.color.LIGHT_GRAY, 13, anchor_y="center")
        self._tc.text(f"mail_reward_{i}", self._reward_text(mail),
                      left, rect.top - 66,
                      arcade.color.GRAY if claimed else arcade.color.GOLD,
                      13, anchor_y="center")
        # 领取按钮（已领取同位置置灰，保持行结构一致）
        btn = arcade.XYWH(rect.right - 16 - self.CLAIM_W / 2, rect.bottom + 22,
                          self.CLAIM_W, self.CLAIM_H)
        self._claim_rects.append((btn, i))
        if claimed:
            bg, label = self.C_CLAIM_OFF, "已领取"
        elif i == self._claim_hover:
            bg, label = self.C_CLAIM_OK_HOVER, "领 取"
        else:
            glow = 0.5 + 0.5 * math.sin(self._pulse * 4.0)
            bg = tuple(
                int(self.C_CLAIM_OK[c] + (self.C_CLAIM_OK_HOVER[c] - self.C_CLAIM_OK[c]) * glow)
                for c in range(3)
            )
            label = "领 取"
        arcade.draw_rect_filled(btn, bg)
        self._tc.text(f"mail_btn_{i}", label, btn.center_x, btn.center_y,
                      arcade.color.DARK_GRAY if claimed else arcade.color.WHITE,
                      14, anchor_x="center", anchor_y="center")

    def _reward_text(self, mail: dict) -> str:
        """奖励行文本：金币 + 经验 + 附件明细（附件过多时只列前 4 项）"""
        parts = []
        gold = int(mail.get("gold", 0) or 0)
        exp = int(mail.get("exp", 0) or 0)
        if gold > 0:
            parts.append(f"金币 +{gold}")
        if exp > 0:
            parts.append(f"经验 +{exp}")
        items = [
            f"{_item_label(a['item_type'], a['item_id'], int(a['level']))}×{int(a['quantity'])}"
            for a in (mail.get("attachments") or [])
        ]
        if len(items) > 4:
            items = items[:4] + [f"等 {len(items)} 项"]
        if items:
            parts.append("附件：" + "、".join(items))
        return "  ".join(parts) or "（无奖励内容）"

    def _draw_back(self):
        bg = self.C_BACK_HOVER if self.back_hover else self.C_BACK
        arcade.draw_rect_filled(self.back_rect, bg)
        self._tc.text("nav_back", "返回", self.back_rect.center_x, self.back_rect.center_y,
                      arcade.color.WHITE, 13, anchor_x="center", anchor_y="center")

    def _draw_toasts(self):
        for i, t in enumerate(self._toasts):
            fade = max(0.0, 1.0 - max(0.0, t["age"] - (self.TOAST_LIFE - 0.6)) / 0.6)
            color = tuple(
                int(self.C_BG[c] + (t["color"][c] - self.C_BG[c]) * fade) for c in range(3)
            )
            self._tc.text(f"toast_{i}", t["text"], WINDOW_WIDTH // 2,
                          self.content_top - 10 - t["age"] * self.TOAST_RISE,
                          color, 18, anchor_x="center", anchor_y="center", bold=True)

    # ── 交互 ──────────────────────────────────────────────────

    def on_mouse_motion(self, x, y, dx, dy):
        if self._scroll_dragging:
            self.update_scroll_drag(x, y)
        self.back_hover = self.back_rect.point_in_rect((x, y))
        self.delete_hover = self._delete_rect.point_in_rect((x, y))
        self._claim_hover = -1
        for rect, idx in self._claim_rects:
            if rect.point_in_rect((x, y)):
                self._claim_hover = idx
                break

    def on_mouse_release(self, x, y, button, modifiers):
        self.end_scroll_drag()

    def on_mouse_press(self, x, y, button, modifiers):
        if self.handle_back_click(x, y):
            sound_manager.play_ui()
            return
        if self.start_scroll_drag(x, y):
            return
        if self._delete_rect.point_in_rect((x, y)):
            self._delete_claimed()
            return
        for rect, idx in reversed(self._claim_rects):
            if rect.point_in_rect((x, y)):
                self._claim(idx)
                return

    def on_key_press(self, key, modifiers):
        if key == arcade.key.ESCAPE:
            self.handle_back_click(self.back_rect.center_x, self.back_rect.center_y)

    # ── 领取 ──────────────────────────────────────────────────

    def _claim(self, i: int):
        """领取第 i 封邮件：容量预检 → 标记领取 → 按载荷发放 → 刷新"""
        pid = self._player_id()
        if pid is None:
            self._toast("请先创建玩家档案", (255, 160, 120))
            sound_manager.play_ui()
            return
        mail_id = int(self._mails[i]["id"])
        ok, reason, _need, _remain = can_claim_mail(pid, mail_id)
        if not ok:
            self._toast(reason, (255, 160, 120))
            sound_manager.play_ui()
            return
        mail, err = claim_mail(pid, mail_id)
        if mail is None:
            self._toast(err or "领取失败", (255, 160, 120))
            sound_manager.play_ui()
            return
        self._grant(pid, mail)
        self._toast("领取成功，奖励已发放", arcade.color.GOLD)
        sound_manager.play_gold_pickup()
        self._refresh()

    def _delete_claimed(self):
        """删除全部已领取邮件（奖励已发放，仅清理列表；未领取邮件不动）"""
        pid = self._player_id()
        if pid is None:
            return
        n = delete_claimed_mails(pid)
        sound_manager.play_ui()
        if n > 0:
            self._toast(f"已删除 {n} 封已领取邮件", arcade.color.LIGHT_GRAY)
        else:
            self._toast("没有已领取的邮件", (255, 160, 120))
        self._refresh()

    def _grant(self, pid: int, mail: dict):
        """按邮件载荷发放奖励（容量已由 can_claim_mail 预检保证）"""
        gold = int(mail.get("gold", 0) or 0)
        exp = int(mail.get("exp", 0) or 0)
        if gold > 0:
            add_gold(pid, gold)
        if exp > 0:
            gs = getattr(self.window_ref, "game_state", None)
            add_exp(pid, getattr(gs, "character_id", "initial"), exp)
        for a in mail.get("attachments") or []:
            item_type = str(a.get("item_type", ""))
            item_id = str(a.get("item_id", ""))
            level = int(a.get("level", 1) or 1)
            qty = int(a.get("quantity", 0) or 0)
            if qty <= 0:
                continue
            if item_type == "resource":
                add_warehouse_item(pid, "resource", item_id, qty)
            elif item_type == "weapon":
                from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
                info = {**MELEE_WEAPONS, **RANGED_WEAPONS}.get(item_id)
                if not info:
                    continue
                kind = "melee" if item_id in MELEE_WEAPONS else "ranged"
                for _ in range(qty):
                    create_weapon(pid, item_id, kind, info["name"], info["damage"],
                                  info["attack_speed"], level=level)
            elif item_type in ("helmet", "armor", "backpack"):
                for _ in range(qty):
                    add_equipment(pid, item_id, item_type, level=level)
            elif item_type == "potion":
                from entities.equipment_defs import POTIONS
                info = POTIONS.get(item_id)
                if not info:
                    continue
                for _ in range(qty):
                    add_potion(pid, item_id, info["name"], info["effect"],
                               info.get("value", 0), info.get("duration", 0))
            elif item_type == "box":
                # 宝箱进仓库（每箱 1 格，容量已由 can_claim_mail 预检），
                # 玩家到仓库「宝箱」页按数量开启
                box_type = box_type_of(item_id)
                if box_type:
                    add_warehouse_box(pid, box_type, item_id, qty)

    def _toast(self, text: str, color):
        self._toasts.append({"text": text, "color": color, "age": 0.0})
        if len(self._toasts) > 4:
            self._toasts.pop(0)

    def on_update(self, delta_time: float = 1 / 60):
        self._pulse += delta_time
        for t in self._toasts:
            t["age"] += delta_time
        self._toasts = [t for t in self._toasts if t["age"] < self.TOAST_LIFE]
