"""撤离系统：读条撤离 + 死亡惩罚

撤离机制：
1. 玩家站在撤离点上按住交互键读条（松手暂停、进度保留）
2. 读条时间 3 秒，期间不能移动
3. 读条完成后将携带物写入仓库
4. 死亡会丢失所有携带物

相关函数：
- commit_run_to_warehouse: 撤离成功，按仓库容量**部分**写入仓库，返回未入仓报告
- publish_warehouse_overflow / take_warehouse_overflow: 未入仓报告经 game_state 传递给结算页
- clear_run: 死亡惩罚，清空携带物（传入基线时改走合流回滚 rollback_run）
- copy_run_carried / snapshot_run_state / merge_run_carried / rollback_run: 账本快照与合流回滚
- _merge_authoritative: 结算清单口径——**主机账本为唯一权威**，客户端上报载荷
  仅用于对账日志（打差异），绝不并入结果（禁客户端凭空上报骗结算）
"""

import arcade
from config import (
    EVAC_CHANNEL_TIME,
    EVAC_COLOR,
    EVAC_DEFEND_TIME,
    EVAC_POINT_HP,
    EVAC_RADIUS,
    EVAC_WAVE_INTERVAL,
)


class EvacPoint:
    """防守式撤离点状态机。

    dormant →（激活）→ defending →（倒计时归零）→ secured；
    defending 中血量归零会进入 destroyed，修复后重新开始防守。
    secured 后由 GameView 把本点坐标交给 EvacState，复用现有三秒读条与结算链。
    资源扣减由主机调用方完成；activate 的 free 参数仅保留航天图免费激活语义。
    """

    def __init__(self, x: float, y: float, theme: str) -> None:
        self.x = x
        self.y = y
        self.theme = theme
        self.state = "dormant"
        self.max_hp = EVAC_POINT_HP
        self.hp = self.max_hp
        self._defend_duration = float(EVAC_DEFEND_TIME.get(theme, EVAC_DEFEND_TIME["forest"]))
        self.defend_left = self._defend_duration
        self.wave_timer = EVAC_WAVE_INTERVAL
        self.wave_no = 0
        self._wave_pending = False

    def activate(self, *, free: bool = False) -> bool:
        """激活休眠撤离点；free 供击败 BOSS 后免费建立的航天撤离点使用。"""
        del free  # 资源由主机调用方校验并扣除，本方法只负责状态转换。
        if self.state != "dormant":
            return False
        self.state = "defending"
        self.hp = self.max_hp
        self._reset_defense()
        return True

    def repair(self) -> bool:
        """修复被摧毁的撤离点，并重置血量、波次与防守倒计时。"""
        if self.state != "destroyed":
            return False
        self.hp = self.max_hp
        self.state = "defending"
        self._reset_defense()
        return True

    def take_damage(self, damage: int) -> bool:
        """承受进攻伤害；返回 True 表示本次攻击摧毁了撤离点。"""
        if self.state != "defending" or damage <= 0:
            return False
        self.hp = max(0, self.hp - damage)
        if self.hp > 0:
            return False
        self.state = "destroyed"
        self.defend_left = 0.0
        self._wave_pending = False
        return True

    def update(self, delta_time: float) -> None:
        """推进防守倒计时；波次到点时记录一次待生成事件。"""
        if self.state != "defending":
            return
        self.defend_left = max(0.0, self.defend_left - delta_time)
        if self.defend_left <= 0.0:
            self.state = "secured"
            self._wave_pending = False
            return

        self.wave_timer -= delta_time
        if self.wave_timer <= 0.0:
            self.wave_no += 1
            self.wave_timer = EVAC_WAVE_INTERVAL
            self._wave_pending = True

    def consume_wave_event(self) -> bool:
        """读取并清除一次待生成波次事件，防止同一帧重复刷怪。"""
        pending = self._wave_pending
        self._wave_pending = False
        return pending

    def progress(self) -> float:
        """返回 0.0~1.0 防守进度；secured 固定为 1.0。"""
        if self.state == "secured":
            return 1.0
        if self.state != "defending" or self._defend_duration <= 0.0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - self.defend_left / self._defend_duration))

    def _reset_defense(self) -> None:
        """开始新的防守周期。"""
        self.defend_left = self._defend_duration
        self.wave_timer = EVAC_WAVE_INTERVAL
        self.wave_no = 0
        self._wave_pending = False


class EvacState:
    """管理撤离读条状态"""
    def __init__(self):
        self._channeling = False
        self._timer = 0.0
        self.active_evac = None  # 当前所在撤离点 (x,y)

    def update(self, player, evac_points: list, delta_time: float,
               holding: bool = True) -> str | None:
        """检测玩家是否在撤离点上，返回 'evacuated' 表示撤离完成，否则 None。

        - holding：调用方传入的「是否按住交互键」状态（键位取自玩家键位绑定，调用方判定）。
          修复读条「无条件自启」：渲染层一直提示「按E撤离」，实际站在点上即自动读条，
          提示与行为不一致；改后只有 holding=True 时读条才推进。
        - 松开（holding=False）时保持在点上但进度保留：不回退、不清零（最保守实现）；
          离开撤离点范围仍按原逻辑清零。
        """
        # 检测是否在撤离点范围内
        self.active_evac = None
        for ex, ey in evac_points:
            dist = ((player.center_x - ex) ** 2 + (player.center_y - ey) ** 2) ** 0.5
            if dist < EVAC_RADIUS:
                self.active_evac = (ex, ey)
                break

        if self.active_evac:
            if not self._channeling:
                self._channeling = True
                self._timer = 0.0
            if holding:
                self._timer += delta_time
                if self._timer >= EVAC_CHANNEL_TIME:
                    self._channeling = False
                    self._timer = 0.0
                    return "evacuated"
        else:
            self._channeling = False
            self._timer = 0.0
        return None

    @property
    def progress(self) -> float:
        """0.0 ~ 1.0"""
        if not self._channeling:
            return 0.0
        return min(1.0, self._timer / EVAC_CHANNEL_TIME)

    @property
    def is_channeling(self) -> bool:
        return self._channeling

    def draw_progress(self, player):
        """在玩家头顶画读条"""
        if not self._channeling:
            return
        bar_w = 50
        bar_h = 6
        x = player.center_x - bar_w // 2
        y = player.center_y + 30
        # 背景
        arcade.draw_rect_filled(
            arcade.XYWH(x + bar_w // 2, y, bar_w, bar_h),
            arcade.color.DARK_GRAY,
        )
        # 进度
        fill_w = bar_w * self.progress
        arcade.draw_rect_filled(
            arcade.XYWH(x + fill_w // 2, y, fill_w, bar_h),
            EVAC_COLOR,
        )


# ── 结算清单口径：主机账本唯一权威（客户端载荷仅对账，绝不采信）───────────────
# 安全背景：EVAC_REQUEST 的可选键 carried 是**客户端自报**的本地携带清单，属不可信输入。
# 旧实现做「键级差集补齐」（把客户端独有的键补进权威清单），等于把自报数据当成已入账事实：
# 客户端可凭空上报资源/武器骗结算（desync 高危）。现改为：结果一律 = 主机账本，
# 自报载荷只用于打对账日志，任何「客户端有、主机没有」的键/条目都不并入结果。
_RECONCILE_ITEM_LIMIT = 6  # 单槽对账明细上限（防长局刷屏，超出只报剩余数）


def _fmt_carried_key(key) -> str:
    """把 run_carried 的键格式化为可读文本：元组键 (item_id, level) → "item_id×level"。"""
    if isinstance(key, tuple):
        return "×".join(str(part) for part in key)
    return str(key)


def _brief_value(value, limit: int = 48) -> str:
    """把槽值压成一行短文本（对账日志防长 dict 刷屏）。"""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _join_keys(keys: list) -> str:
    """拼接对账明细里的键列表，超出上限时截断并标注剩余数。"""
    shown = [_fmt_carried_key(k) for k in keys[:_RECONCILE_ITEM_LIMIT]]
    if len(keys) > _RECONCILE_ITEM_LIMIT:
        shown.append(f"…共 {len(keys)} 项")
    return "、".join(shown)


def _reconcile_carried(authoritative: dict, reported: dict) -> list[str]:
    """逐槽对比主机账本与客户端上报载荷，返回中文差异描述（纯只读，不改任何账本）

    三类差异分列，便于定位问题来源：
    - 客户端独有：主机未记到 → 结算忽略（这是被修复的凭空上报入口）
    - 主机独有  ：主机已记、客户端没有 → 正常现象（主机账本为准）
    - 数量不一致：同一键数量不同 → 取主机值
    """
    diffs: list[str] = []
    for slot in sorted(set(authoritative) | set(reported), key=str):
        host_value = authoritative.get(slot)
        cli_value = reported.get(slot)
        if not (isinstance(host_value, dict) and isinstance(cli_value, dict)):
            # 标量槽（gold）、单边缺失或类型异常：金额/数值口径唯一，一律取主机值
            if host_value == cli_value:
                continue
            if slot not in authoritative:
                diffs.append(f"槽 {slot} 客户端独有整槽（{_brief_value(cli_value)}）"
                             f"→ 已忽略，主机账本无此槽")
            elif slot not in reported:
                diffs.append(f"槽 {slot} 主机独有整槽（{_brief_value(host_value)}）"
                             f"→ 以主机账本为准")
            else:
                diffs.append(f"槽 {slot} 数值不一致（客户端 {_brief_value(cli_value)} / "
                             f"主机 {_brief_value(host_value)}）→ 以主机为准")
            continue
        only_cli, only_host, mismatched = [], [], []
        for key in sorted(set(host_value) | set(cli_value), key=repr):
            if key not in host_value:
                only_cli.append(key)
            elif key not in cli_value:
                only_host.append(key)
            elif host_value[key] != cli_value[key]:
                mismatched.append((key, cli_value[key], host_value[key]))
        if only_cli:
            diffs.append(f"槽 {slot} 客户端独有 {len(only_cli)} 项（{_join_keys(only_cli)}）"
                         f"→ 已忽略，不计入结算")
        if only_host:
            diffs.append(f"槽 {slot} 主机独有 {len(only_host)} 项（{_join_keys(only_host)}）"
                         f"→ 以主机账本为准")
        for key, cli_qty, host_qty in mismatched[:_RECONCILE_ITEM_LIMIT]:
            diffs.append(f"槽 {slot} 条目 {_fmt_carried_key(key)} 数量不一致"
                         f"（客户端 {cli_qty} / 主机 {host_qty}）→ 以主机 {host_qty} 为准")
        if len(mismatched) > _RECONCILE_ITEM_LIMIT:
            diffs.append(f"槽 {slot} 另有 {len(mismatched) - _RECONCILE_ITEM_LIMIT} "
                         f"条数量不一致（略）")
    return diffs


def _merge_authoritative(authoritative: dict, client_carried: dict | None, *,
                         player_id: int | None = None) -> dict:
    """结算清单 = 主机账本权威（主机账本 ⊘ 客户端上报载荷，仅对账不采信）

    为什么不再补差额：旧版做「客户端独有键补入」的键级差集合流，等于承认客户端自报
    数据是已入账事实，客户端可凭空上报资源/武器骗结算（desync）。联机客户端的本地
    购买已改走请求-回执入主机账本（商队购买由后续任务接线），主机账本此时是唯一
    权威数据源，因此这里不再补任何键。

    - 返回值一律是主机账本的逐槽副本（标量槽 gold 原样带过），调用方拿到的就是结算事实；
    - client_carried 只用于打中文对账日志（标明哪些槽/条目不一致、一律以主机为准），
      **不采信任何客户端独有键/条目**；
    - client_carried 为 None/空 → 无对账对象，直接原对象直传（与旧版逐字节一致，
      不复制、不污染）；
    - player_id 仅用于日志标注（主机调用方应传 sender_id；不传则标注为「未知」）；
    - 金币不走本函数：gold 槽取主机值，金额口径唯一，禁双端各记一次。
    """
    if not client_carried:
        return authoritative  # 未传 → 原对象直传，行为与旧版完全一致（且不复制、不污染）
    host_ledger = authoritative if isinstance(authoritative, dict) else {}
    reported = client_carried if isinstance(client_carried, dict) else {}
    diffs = _reconcile_carried(host_ledger, reported)
    if diffs:
        who = "未知" if player_id is None else player_id
        print(f"[撤离对账] 玩家 {who} 的客户端上报清单与主机账本存在 {len(diffs)} 处差异，"
              f"已全部以主机账本为准（客户端载荷仅对账、不采信）：")
        for line in diffs:
            print(f"[撤离对账]   - {line}")
    # 返回主机账本的逐槽副本：既保证结果完全等于权威值，也避免调用方误改主机账本
    return copy_run_carried(host_ledger)


def commit_run_to_warehouse(pid: int, run_carried: dict, *,
                           client_carried: dict | None = None) -> dict:
    """撤离成功：将本次携带物写入仓库 DB（仓库有容量上限 → **能存多少存多少**）

    - run_carried 即**主机账本**，是本次结算的唯一权威数据源（联机端 = 主机下发的
      EVAC_RESULT 结算清单，单机/主机 = 本端已仲裁的携带物）；
    - client_carried: 可选的「客户端上报清单」——**不采信**，仅用于对账日志。传入时
      `_merge_authoritative` 逐槽与权威清单对比，有差异就打中文日志（标明玩家、哪些
      槽/条目不一致、一律以主机为准），但**绝不把客户端独有的键/条目并入入库清单**
      （修复客户端凭空上报资源/武器骗结算的 desync）；
      **不传时行为与旧版逐字节一致**（直接按 run_carried 入库，不复制、不合流）。

    返回「未入仓报告」dict，字段：
    - dropped:      因仓库已满而**没存进去**的物品件数（0 = 全部入库，调用方可静默）
    - dropped_items: 明细，形如 ["木材×12", "铁剑×1"]，供结算页显示「N 件未入仓」
    - stored:       本次成功入库的物品件数（金币不计入：金币走 players.gold，不受容量限制）

    为什么不用 db.add_warehouse_item_checked（满仓整笔拒绝）：
    那边是市场回购 / 图鉴领奖这类**局外整笔事务**，装不下就该一笔不买；
    撤离是「本局战利品落袋」，部分入库远比整笔丢弃合理——装得下的先保住，
    剩下的由玩家自己去升级仓库扩容后再想办法（这正是仓库等级系统的意义）。

    容量口径完全交给 db.warehouse.warehouse_item_capacity（与背包 game/loot.py 同源），
    本函数**不复制口径逻辑**，只做「按剩余容量逐件扣减」。
    """
    from db.database import (
        add_equipment,
        add_gold,
        add_potion,
        add_warehouse_item,
        create_weapon,
        warehouse_item_capacity,
        warehouse_remaining_capacity,
    )
    from entities.equipment_defs import ARMORS, BACKPACKS, HELMETS, POTIONS
    from entities.resource_defs import RESOURCES
    from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS

    # 剩余容量预算：每次成功入库后立刻扣减，全程只查一次库（避免逐件查库）
    remaining = warehouse_remaining_capacity(pid)
    stored = 0
    dropped: list[tuple[str, int]] = []  # (中文名, 件数)
    # 入库清单 = 本参数（主机账本）的逐槽副本（单机/主机原口径，零变化）；
    # 传了 client_carried 只做对账日志，客户端上报的任何条目都不并入
    source = _merge_authoritative(run_carried, client_carried, player_id=pid)

    def _take(unit_cost: int, count: int) -> int:
        """按剩余容量预算扣减，返回本次能存下的件数（0 = 全存不下）

        unit_cost ≤ 0（金币/药水等不占容量的物品）时不受容量限制，全量放行。
        """
        nonlocal remaining, stored
        count = int(count)
        if count <= 0:
            return 0
        if unit_cost <= 0:
            stored += count
            return count
        if remaining <= 0:
            return 0
        can = min(count, remaining // unit_cost)
        remaining -= can * unit_cost
        stored += can
        return can

    def _drop(name: str, count: int, can: int) -> None:
        """把装不下的部分登记进未入仓清单。"""
        if can < count:
            dropped.append((name, count - can))

    for item_type, items in source.items():
        if item_type == "gold":
            # 金币不受仓库容量限制（存 players.gold，不入仓表）
            add_gold(pid, items)
        elif item_type == "resource":
            for item_id, qty in items.items():
                name = RESOURCES.get(item_id, {}).get("name", item_id)
                can = _take(warehouse_item_capacity("resource", item_id), qty)
                if can:
                    add_warehouse_item(pid, "resource", item_id, can)
                _drop(name, int(qty), can)
        elif item_type == "weapon":
            # run_carried["weapon"] = {(item_id, level): qty, ...}，入库时保留等级
            all_weapons = {**MELEE_WEAPONS, **RANGED_WEAPONS}
            for (weapon_id, level), qty in items.items():
                info = all_weapons.get(weapon_id)
                if not info:
                    continue
                kind = "melee" if weapon_id in MELEE_WEAPONS else "ranged"
                can = _take(warehouse_item_capacity("weapon", weapon_id), qty)
                for _ in range(can):
                    create_weapon(pid, weapon_id, kind, info["name"], info["damage"],
                                  info["attack_speed"], level=level)
                _drop(info["name"], int(qty), can)
        elif item_type in ("helmet", "armor", "backpack"):
            # run_carried[slot] = {(item_id, level): qty, ...}
            # （原代码把 helmet/armor 与 backpack 分成两段，但都只是 add_equipment(slot)，
            #   行为完全一致；此处合并为一条，让三类装备共用同一套容量扣减）
            slot_defs = {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}
            for (item_id, level), qty in items.items():
                name = slot_defs[item_type].get(item_id, {}).get("name", item_id)
                can = _take(warehouse_item_capacity(item_type, item_id), qty)
                for _ in range(can):
                    add_equipment(pid, item_id, item_type, level=level)
                _drop(name, int(qty), can)
        elif item_type == "potion":
            # run_carried["potion"] = {item_id: qty, ...}（果实等掉落药水入库；不占容量）
            for item_id, qty in items.items():
                info = POTIONS.get(item_id, {})
                if not info:
                    continue
                can = _take(warehouse_item_capacity("potion", item_id), qty)
                for _ in range(can):
                    add_potion(pid, item_id, info["name"], info["effect"],
                               info.get("value", 0), info.get("duration", 0))
                _drop(info["name"], int(qty), can)

    report = {
        "stored": stored,
        "dropped": sum(n for _, n in dropped),
        "dropped_items": [f"{name}×{n}" for name, n in dropped if n > 0],
    }
    # 本函数**自行**发布未入仓报告：调用方里包含禁改的 views/game_view.py，
    # 若依赖调用方转发，报告会在单机/主机撤离路径上被丢掉。发布后仍照常返回，
    # 便于将来可改的调用方按需自行处理（例如联机端）。
    publish_warehouse_overflow(report)
    return report


def _overflow_target_gs():
    """安全取 window.game_state；取不到（无窗口/窗口已关闭）时返回 None

    为什么要 try/except：arcade.get_window() 在**没有活动窗口**时是**抛
    RuntimeError**（"No window is active..."），不是返回 None——所以
    `if window is not None` 这种兜底拦不住它。窗口关闭后的收尾路径、单元测试、
    无头脚本都会踩到，报告机制不该把调用方炸掉。
    """
    try:
        window = arcade.get_window()
    except RuntimeError:
        return None
    return getattr(window, "game_state", None)


def publish_warehouse_overflow(report: dict) -> None:
    """把「未入仓」报告挂到 window.game_state，供撤离结算页取用并显示提示

    为什么不靠调用方层层转发：撤离结算发生在局内，而提示要显示在**局外**的
    撤离结算页（跨了一次视图切换）；而触发撤离的 views/game_view.py 不在本次
    可改文件范围内，无法让它把返回值接到局外视图。唯一合规的跨视图共享通道
    就是 main.GameState（根 AGENTS.md：视图间传数据走 window.game_state，禁全局变量）。
    """
    if not report or int(report.get("dropped", 0)) <= 0:
        return  # 全部入库 → 无需提示，game_state 不留脏数据
    gs = _overflow_target_gs()
    if gs is not None:
        gs.warehouse_overflow = report


def take_warehouse_overflow() -> dict:
    """取出并清空 game_state 上的「未入仓」报告（结算页消费一次即可，空则返回 {}）"""
    gs = _overflow_target_gs()
    if gs is None:
        return {}
    report = getattr(gs, "warehouse_overflow", None)
    gs.warehouse_overflow = None  # 取走即清空，防止残留导致下次撤离误显示
    return report if isinstance(report, dict) else {}


# ── 账本回滚（合流口径，取代整体覆盖）──────────────
# run_carried 槽结构契约（跨层，见 game/loot.py try_pickup 与本文件入库分支）：
#   "gold": int（标量槽）
#   "resource" / "potion": {item_id: qty}
#   "weapon" / "helmet" / "armor" / "backpack": {(item_id, level): qty}
# 嵌套槽必须**逐层拷贝**：回滚窗口内的写入是原地改嵌套 dict（loot 拾取、商队购买、
# 建造扣料都走 setdefault + 赋值），浅拷贝快照会把窗口期写入一起"快照"进去，回滚即失效。
_CARRIED_SLOTS = ("gold", "resource", "weapon", "backpack", "helmet", "armor", "potion")


def copy_run_carried(carried: dict) -> dict:
    """深拷贝一档 run 账本（逐槽拷贝嵌套 dict），用作回滚基线快照。

    逐槽遍历而非写死槽位清单：run_carried 的槽会随玩法增长（武器元组键槽、药水槽等），
    写死列表会在新增槽时静默漏拷（漏拷 = 该槽回滚失效）。
    """
    snap: dict = {}
    for slot, items in (carried or {}).items():
        snap[slot] = dict(items) if isinstance(items, dict) else items
    return snap


def snapshot_run_state(run_carried: dict, run_potions: dict | None = None) -> tuple:
    """取一档可回滚基线快照，返回 (carried, potions)（potions 无则为空 dict）。

    快照必须在**写入之前**取（拾取/购买之前）；窗口期的并发写入由
    merge_run_carried 的键级差集保留，不会被快照吞掉。
    """
    return copy_run_carried(run_carried), copy_run_carried(run_potions or {})


def merge_run_carried(current: dict, baseline: dict, *, drop: dict | None = None,
                      overlay: dict | None = None) -> dict:
    """把 current 按 key 差异合流回 baseline 之上（**原地**改 current，返回同一字典）。

    为什么必须合流而不是整体覆盖（`gs.run_carried = snap` / `= {}`）：
    回滚窗口（联机拾取请求往返、撤离结果落地）内可能还有**合法并发写入**——最典型是
    客户端本地商队/市场购买，扣的是本端账号金币、写的是本端 run_carried，全程不广播。
    整体覆盖会把这些"已付钱"的写入一并丢弃，玩家白扣金币；合流只撤销该撤销的部分。

    合流规则（按 key 差异，槽结构见上方契约）：
    - 基线里**存在**的键 → 还原为基线值（撤销窗口期对该键的写入，含被拒的乐观拾取 +qty）
    - 基线里**不存在**、当前有的键 → 保留（窗口期新增 = 未被回滚的合法来源）
    - 标量槽（gold）→ 取基线值（金额口径唯一）
    - drop    : 精确撤销 {slot: {key, ...}}（用于基线本就没有该键、需单独剔除的场景）
    - overlay : 在基线还原**之后**再叠加的增量 {slot: {key: qty}}，即"重新叠加合法来源"
                （只支持嵌套槽；标量槽请直接改用 drop / 自行赋值）
    """
    if not isinstance(current, dict):
        return current
    for slot, base_value in (baseline or {}).items():
        cur_value = current.get(slot)
        if isinstance(base_value, dict):
            if not isinstance(cur_value, dict):
                current[slot] = dict(base_value)  # 窗口期整槽丢失 → 按基线重建
                continue
            for key, qty in base_value.items():
                cur_value[key] = qty                # 还原窗口期对该键的写入
        else:
            current[slot] = base_value              # 标量槽（gold）
    for slot, keys in (drop or {}).items():
        bucket = current.get(slot)
        if isinstance(bucket, dict):
            for key in keys:
                bucket.pop(key, None)
    for slot, adds in (overlay or {}).items():
        bucket = current.get(slot)
        if not isinstance(bucket, dict):
            bucket = current.setdefault(slot, {})
        for key, qty in (adds or {}).items():
            bucket[key] = bucket.get(key, 0) + qty
    return current


def rollback_run(run_carried: dict, *, baseline: dict | None = None,
                 run_potions: dict | None = None, potions_baseline: dict | None = None,
                 drop: dict | None = None, overlay: dict | None = None) -> None:
    """回滚一档 run 账本（撤离失败/超时/拾取被拒共用）。

    - baseline 为 None → 退化为**整体清空**（旧语义，向后兼容：无基线的调用方不变）
    - baseline 有值   → 合流回滚。**原地**改 run_carried（保持字典身份）：调用方
                       普遍持引用（HUD / 背包 / BuildSystem），`gs.run_carried = snap`
                       式重绑会丢引用，并把窗口期的合法写入一并覆盖掉
    - run_potions / potions_baseline 同理（potions 为纯 {item_id: qty} 嵌套槽）
    """
    if baseline is None:
        for key in _CARRIED_SLOTS:
            run_carried.pop(key, None)
        if isinstance(run_potions, dict):
            run_potions.clear()  # 原地清空（等值于旧式 `gs.run_potions = {}`）
        return
    merge_run_carried(run_carried, baseline, drop=drop, overlay=overlay)
    if run_potions is not None:
        merge_run_carried(run_potions, potions_baseline or {},
                          drop=drop.get("run_potions") if isinstance(drop, dict) else None,
                          overlay=overlay.get("run_potions") if isinstance(overlay, dict) else None)


def clear_run(run_carried: dict, *, baseline: dict | None = None,
              run_potions: dict | None = None, drop: dict | None = None,
              overlay: dict | None = None):
    """死亡惩罚：清空玩家【当前携带】的物资（不写入 DB）。

    仅清除本次冒险中携带的物品，包括：
    - gold     : 本次携带的金币（注意：不是玩家数据库里拥有的全部金币）
    - resource : 本次携带的资源
    - weapon   : 本次携带的武器
    - backpack : 本次携带的背包
    - helmet   : 本次携带的头盔
    - armor    : 本次携带的护甲

    玩家在数据库中已拥有的全部金币（players.gold）以及仓库内的物品
    不受影响——本函数只操作内存中的 run_carried 字典，不触碰数据库。

    可选参数（**默认行为与旧版逐字节一致**）：
    - baseline 有值时改走合流回滚 rollback_run（只还原本次 run 基线，保留窗口期合法写入）
    - run_potions 传入时一并原地清空（旧式调用方自己写 `gs.run_potions = {}`，不受影响）
    """
    # 显式清空各个携带类别，确保死亡时只丢失"当前携带"的物资
    if baseline is not None:
        rollback_run(run_carried, baseline=baseline, run_potions=run_potions,
                     drop=drop, overlay=overlay)
        return
    for key in _CARRIED_SLOTS:
        run_carried.pop(key, None)
    if isinstance(run_potions, dict):
        run_potions.clear()
