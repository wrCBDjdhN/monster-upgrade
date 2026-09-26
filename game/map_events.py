"""随机地图事件运行时（阶段 4）

职责（views/game_view.py 只做薄编排，核心逻辑全在本模块）：

- 抽选：pick_event(rng) —— 等权随机抽一个事件 id（教程局由调用侧守卫后返回 ""）
- 生效：apply_event(view, event_id) —— 把事件参数写进 view.event_flags
        （monster_cap_mult / reward_mult / artifact_bonus），并把怪物上限倍率
        反算进 game_view 现有的 self._monster_cap（野外补刷读该上限）
- 横幅：event_banner(view) —— 开局 3 秒横幅文案（渲染层消费）
- 落地：trigger_event(view) —— 商队交互点立即生成；空投走 update_event 计时器
- 驱动：update_event(view, dt) —— 横幅计时、空投落地计时、弹层自动关闭
- 奖励：scale_event_drops(view) / award_event_kill_exp(view, monster)
        —— 尸潮事件的掉落与击杀经验倍率（挂在 game_view 的掉落更新与死亡回调处，
        不侵入 entity_callbacks 的掉落/经验主流程）
- 交互：handle_caravan_interaction / caravan_panel_move / caravan_panel_buy

命名空间：view.event_flags / view.event_id（本阶段新增）。
教程局（gs.tutorial.active）不抽事件、不落地空投与商队。
"""

import math
import random

from config import (
    ELITE_EXP_MULT,
    EVENT_AIRDROP_CRATES,
    EVENT_AIRDROP_MIN_DIST,
    EVENT_AIRDROP_MIN_LEVEL,
    EVENT_AIRDROP_TRIES,
    EVENT_BANNER_SEC,
    EVENT_CARAVAN_MIN_DIST,
    EVENT_CARAVAN_PROMPT_CD,
    EVENT_CARAVAN_RANGE,
    EVENT_PICK_RNG_SALT,
    EXP_BOSS_MULT,
    EXP_KILL_BASE,
)
from entities.equipment_defs import POTIONS
from entities.event_defs import EVENT_IDS, MAP_EVENTS, get_event
from entities.resource_defs import RESOURCES
from game.effects import floating_texts


# ─────────────────────────── 抽选 / 生效 / 横幅 ───────────────────────────

def pick_event(rng) -> str:
    """等权随机抽一个地图事件 id（"tide"/"airdrop"/"caravan"/"relic"）

    - rng 由调用侧传入（game_view 用地图种子派生），保证同一种子抽到同一事件；
    - 教程局守卫不在本函数内（签名按计划只收 rng），由调用侧在教程期直接跳过抽选。
    """
    if not EVENT_IDS:
        return ""
    return EVENT_IDS[rng.randrange(len(EVENT_IDS))]


def event_rng(seed: int):
    """按地图种子派生本局事件抽选随机源（host/client 同种子同事件）"""
    return random.Random((int(seed) * 1000003 + EVENT_PICK_RNG_SALT) & 0x7FFFFFFF)


def apply_event(view, event_id: str) -> dict:
    """把事件参数写进 view.event_flags，并把怪物上限倍率反算进 view._monster_cap

    view.event_flags 字段（缺省即无加成）：
    - monster_cap_mult：野外怪物上限倍率（尸潮）
    - reward_mult：击杀掉落/经验倍率（尸潮）
    - artifact_bonus：神器掉落等级加成比例（神器低语）

    返回写入后的 event_flags（便于调用方/调试读取）。
    """
    flags = view.event_flags
    # 每局重置为无加成，再按事件覆盖（避免上一局倍率串入本局）
    flags["monster_cap_mult"] = 1.0
    flags["reward_mult"] = 1.0
    flags["artifact_bonus"] = 0.0
    cfg = get_event(event_id)
    if cfg:
        if "cap_mult" in cfg:
            flags["monster_cap_mult"] = float(cfg["cap_mult"])
        if "reward_mult" in cfg:
            flags["reward_mult"] = float(cfg["reward_mult"])
        if "artifact_bonus" in cfg:
            flags["artifact_bonus"] = float(cfg["artifact_bonus"])
    # 反算野外怪物上限：基线取 _monster_cap_base（__init__ 记录的未加倍率值），
    # 保证多局重开不叠乘
    base = getattr(view, "_monster_cap_base", None)
    if base is None:
        base = int(getattr(view, "_monster_cap", 20))
        view._monster_cap_base = base
    view._monster_cap = max(1, int(round(base * flags["monster_cap_mult"])))
    return flags


def event_banner(view) -> str | None:
    """开局事件横幅文案；无事件或横幅已过期返回 None（渲染层据此不画）"""
    cfg = get_event(getattr(view, "event_id", ""))
    if not cfg:
        return None
    if getattr(view, "_event_banner_timer", 0.0) <= 0.0:
        return None
    return str(cfg.get("banner", ""))


def reward_multiplier(view) -> float:
    """读取本局事件掉落/经验倍率（无加成返回 1.0）"""
    flags = getattr(view, "event_flags", None) or {}
    try:
        return float(flags.get("reward_mult", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 1.0


def artifact_bonus(view) -> float:
    """读取本局事件神器等级加成比例（无加成返回 0.0）"""
    flags = getattr(view, "event_flags", None) or {}
    try:
        return float(flags.get("artifact_bonus", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def tutorial_active(view) -> bool:
    """教程局守卫：GameState.tutorial.active 为真时本局不抽事件、不落地事件实体"""
    gs = getattr(view.window, "game_state", None)
    tut = getattr(gs, "tutorial", None)
    return bool(tut is not None and getattr(tut, "active", False))


# ─────────────────────────── 每局重置 / 开局抽选 ───────────────────────────

def reset_event_state(view) -> None:
    """新一局重置事件运行态（与撤离/精英计时器同一初始化区，由 setup 调用）"""
    view.event_id = ""
    view.event_flags = {"monster_cap_mult": 1.0, "reward_mult": 1.0, "artifact_bonus": 0.0}
    view._event_banner_timer = 0.0
    view._event_airdrop_timer = 0.0
    view._event_airdrop_done = False
    view._event_spawned: set = set()      # 已广播的事件实体序号（防重复广播）
    view._event_scaled_drops: dict = {}   # id(掉落) → 掉落对象（持引用防 id 复用）
    view.caravan_point = None
    view._caravan_panel_open = False
    view._caravan_index = 0
    view._caravan_hint_cd = 0.0
    # 野外怪物上限回到未加倍率基线（apply_event 再按本局事件倍率反算）
    view._monster_cap = getattr(view, "_monster_cap_base", view._monster_cap)


def setup_event(view) -> str:
    """开局抽选并生效本局事件（仅 host/solo 调用；客户端由 EVENT_START 广播驱动）

    - 教程局直接返回 ""：新手教程不参与随机事件（否则教程节奏被空投/商队打断）；
    - 抽中后写 event_flags、开局横幅计时，并立即落地商队交互点（空投走计时器）。
    """
    reset_event_state(view)
    gs = view.window.game_state
    # 教程守卫：教程局无事件（中文注释说明原因）
    if tutorial_active(view):
        return ""
    if getattr(gs, "net_mode", "solo") == "client":
        # 客户端不本地抽选（主机权威，见 EVENT_START）
        return ""
    view.event_id = pick_event(event_rng(getattr(gs, "current_map_seed", 1)))
    if not view.event_id:
        return ""
    apply_event(view, view.event_id)
    view._event_banner_timer = EVENT_BANNER_SEC
    trigger_event(view)
    return view.event_id


# ─────────────────────────── 事件实体落地 ───────────────────────────

def _find_open_position(view, min_dist: float, tries: int) -> tuple | None:
    """在随机房间内找一个离玩家足够远的空位（找不到返回 None）

    口径与宝箱生成（game/chest.spawn_chests）一致：房间内缩 64 像素随机取点。
    """
    rooms = view.map_data.get("rooms") or []
    if not rooms:
        return None
    px, py = view.player.center_x, view.player.center_y
    for _ in range(tries):
        room = random.choice(rooms)
        x = random.randint(room.x + 64, max(room.x + 64, room.x + room.w - 64))
        y = random.randint(room.y + 64, max(room.y + 64, room.y + room.h - 64))
        if math.hypot(x - px, y - py) < min_dist:
            continue
        return float(x), float(y)
    return None


def spawn_airdrop_crates(view, count: int | None = None) -> int:
    """在地图上落地 count 个高级空投补给箱，返回实际生成数量

    - 箱体复用 game/chest.AirdropChest（继承 Chest），因此开箱交互、图鉴解锁、
      障碍物碰撞、渲染与小地图标记全部沿用现有宝箱路径；
    - 等级池按地图主题上浮，且必出 1 件等级 ≥ EVENT_AIRDROP_MIN_LEVEL 的装备。
    """
    from game.chest import AirdropChest

    cfg = get_event("airdrop")
    want = int(count if count is not None else cfg.get("crate_count", EVENT_AIRDROP_CRATES))
    theme = view.map_data.get("theme", "forest")
    bonus = artifact_bonus(view)
    made = 0
    for _ in range(max(0, want)):
        pos = _find_open_position(view, EVENT_AIRDROP_MIN_DIST, EVENT_AIRDROP_TRIES)
        if pos is None:
            break
        chest = AirdropChest(pos[0], pos[1], theme=theme, artifact_bonus=bonus)
        view.chests.append(chest)
        view.obstacle_list.append(chest)
        made += 1
        floating_texts.add(chest.center_x, chest.center_y + 30,
                           "空投补给箱", (255, 180, 60), life=2.5, font_size=13)
    return made


class CaravanPoint:
    """商队交互点（非 Sprite，仅世界坐标 + 招牌文案；口径同 game/rocket_pad.RocketPad）"""

    def __init__(self, center_x: float, center_y: float):
        self.center_x = center_x
        self.center_y = center_y
        self.sign_text = "商队"


def spawn_caravan(view) -> CaravanPoint | None:
    """在地图上生成商队交互点（找不到空位返回 None）"""
    pos = _find_open_position(view, EVENT_CARAVAN_MIN_DIST, EVENT_AIRDROP_TRIES)
    if pos is None:
        return None
    point = CaravanPoint(pos[0], pos[1])
    view.caravan_point = point
    floating_texts.add(point.center_x, point.center_y + 40,
                       "流浪商队", (120, 220, 235), life=2.5, font_size=13)
    return point


def trigger_event(view) -> None:
    """事件实体落地入口（setup 抽中事件后调用）

    - caravan：立即生成商队交互点（无延迟）；
    - airdrop：不立即生成，改由 update_event 的 EVENT_AIRDROP_DELAY 计时器落地；
    - relic：把 artifact_bonus 写进本局所有宝箱（神器档命中时抬等级，见 Chest）；
    - 其余事件（tide）无实体，纯参数生效。
    """
    if not getattr(view, "event_id", ""):
        return
    if view.event_id == "caravan":
        spawn_caravan(view)
    elif view.event_id == "relic":
        # 神器低语：给本局已生成的宝箱注入神器等级加成（宝箱按需生成，
        # 未开启的箱在开箱瞬间读取本属性，故此处一次性遍历即可）
        bonus = artifact_bonus(view)
        if bonus > 0.0:
            for c in getattr(view, "chests", []):
                try:
                    c.artifact_bonus = bonus
                except AttributeError:
                    pass  # 非宝箱实体（理论上不该出现）直接跳过


def update_event(view, dt: float) -> None:
    """事件运行态每帧推进（挂在 game_view.on_update，host/solo 与客户端都调用）

    - 横幅计时递减（到期后 event_banner 返回 None，渲染层不再绘制）；客户端也递减，
      因为横幅是 EVENT_START 广播后由本端计时的表现层元素；
    - 空投：累计满 EVENT_AIRDROP_DELAY 秒后落地补给箱（找不到空位则下一帧重试）；
      **仅 host/solo 本地落地**，客户端的箱体由主机 chest_spawn 广播重建
      （否则两端各自落地一批箱，出现重复箱）；
    - 商队弹层：玩家走远自动关闭，避免弹层悬空。
    """
    if view._event_banner_timer > 0.0:
        view._event_banner_timer = max(0.0, view._event_banner_timer - dt)
    if view._caravan_hint_cd > 0.0:
        view._caravan_hint_cd = max(0.0, view._caravan_hint_cd - dt)
    # 商队弹层：走远自动关闭
    if view._caravan_panel_open and not is_near_caravan(view, EVENT_CARAVAN_RANGE * 1.6):
        view._caravan_panel_open = False
        view._caravan_index = 0
    # 空投计时（仅 airdrop 事件；教程局 event_id 为空直接跳过）
    if view.event_id != "airdrop" or view._event_airdrop_done:
        return
    if getattr(view.window.game_state, "net_mode", "solo") == "client":
        return  # 客户端只显示横幅，空投箱由主机广播生成（禁本地落地）
    delay = float(get_event("airdrop").get("delay", 0.0))
    view._event_airdrop_timer += dt
    if view._event_airdrop_timer < delay:
        return
    if spawn_airdrop_crates(view) > 0:
        view._event_airdrop_done = True
    else:
        # 一个都没落地（房间全在玩家身边）：计时器停在阈值，下一帧重试
        view._event_airdrop_timer = delay


# ─────────────────────────── 尸潮奖励倍率接入 ───────────────────────────

def scale_event_drops(view) -> None:
    """按事件 reward_mult 放大本帧新出现的掉落（金币/资源数量取整）

    挂在 game_view 掉落生命周期更新处（早于主机分配 net_id 与 drop_spawn 广播），
    因此广播给客户端的 quantity 已是放大后的值，两端口径一致。
    无倍率事件（mult<=1）直接返回，零开销。
    """
    mult = reward_multiplier(view)
    if mult <= 1.0:
        return
    scaled = view._event_scaled_drops
    live = set()
    for d in view.drops:
        key = id(d)
        live.add(key)
        if key in scaled:
            continue
        # 持对象引用：防止掉落被回收后 id 复用导致新掉落漏放大
        scaled[key] = d
        if getattr(d, "item_type", "") in ("gold", "resource"):
            d.quantity = max(1, int(round(d.quantity * mult)))
    for key in [k for k in scaled if k not in live]:
        del scaled[key]


def event_exp_bonus(view, base_amount: int) -> int:
    """按事件 reward_mult 计算击杀经验的额外部分（倍率 ≤1 时返回 0）

    纯计算函数（不发放），供两端共用同一口径：
    - solo/host 本端击杀：award_event_kill_exp 调用；
    - client 补刀：network_sync._apply_damage_result 调用。
    """
    mult = reward_multiplier(view)
    if mult <= 1.0 or base_amount <= 0:
        return 0
    return int(base_amount * (mult - 1.0))


def award_event_kill_exp(view, monster) -> int:
    """尸潮事件补发击杀经验（挂在 game_view._on_monster_death 尾部）

    归属口径与 entity_callbacks._award_kill_exp 一致：
    - solo：恒发本端；
    - host：仅击杀者为本端玩家（last_attacker_id==0）时补发；
    - client：不在此发放（客户端在 network_sync._apply_damage_result 自行补发，
      避免与主机重复结算）。

    返回补发的经验值（0 表示未补发）。
    """
    gs = view.window.game_state
    if getattr(gs, "net_mode", "solo") == "client":
        return 0
    if getattr(gs, "net_mode", "solo") == "host" \
            and getattr(monster, "last_attacker_id", 0) != 0:
        return 0
    mult = reward_multiplier(view)
    if mult <= 1.0:
        return 0
    base = EXP_KILL_BASE * (EXP_BOSS_MULT if getattr(monster, "is_boss", False) else 1)
    if getattr(monster, "is_elite", False):
        base *= ELITE_EXP_MULT
    extra = int(base * (mult - 1.0))
    if extra > 0:
        from game.entity_callbacks import _award_exp
        _award_exp(view, extra)
    return extra


# ─────────────────────────── 商队交互 / 换购弹层 ───────────────────────────

def caravan_prices() -> dict[str, int]:
    """商队换购价表（item_id → 局内金币价，取自 config.EVENT_CARAVAN_PRICES）"""
    return dict(get_event("caravan").get("price_table") or {})


def caravan_label(item_id: str) -> str:
    """换购条目的中文显示名（药水取 POTIONS，资源取 RESOURCES，未知回落 item_id）"""
    pdef = POTIONS.get(item_id)
    if pdef:
        return str(pdef.get("name", item_id))
    rdef = RESOURCES.get(item_id)
    if rdef:
        return str(rdef.get("name", item_id))
    return item_id


def caravan_slot(item_id: str) -> str:
    """换购条目写入 run_carried 的槽位：药水入 "potion"，其余按资源入 "resource" """
    return "potion" if item_id in POTIONS else "resource"


def is_near_caravan(view, radius: float = EVENT_CARAVAN_RANGE) -> bool:
    """玩家是否在商队交互范围内（商队点存在才判定）"""
    point = getattr(view, "caravan_point", None)
    if point is None or view.player is None:
        return False
    return math.hypot(view.player.center_x - point.center_x,
                      view.player.center_y - point.center_y) < radius


def handle_caravan_interaction(view) -> None:
    """商队交互（靠近按 E 打开换购弹层；挂在 game_view host/solo 非观战分支）

    客户端不执行：商队交易由主机权威裁决（客户端只渲染招牌与弹层表现）。
    """
    if getattr(view, "caravan_point", None) is None:
        return
    if not getattr(view, "_chest_key_pressed", False):
        return
    if is_near_caravan(view):
        if not view._caravan_panel_open:
            view._caravan_panel_open = True
            view._caravan_index = 0
    elif view._caravan_panel_open:
        view._caravan_panel_open = False
        view._caravan_index = 0


def caravan_panel_move(view, delta: int) -> None:
    """换购弹层上下移动高亮行（列表首尾循环）"""
    if not view._caravan_panel_open:
        return
    items = caravan_prices()
    if not items:
        return
    view._caravan_index = (view._caravan_index + delta) % len(items)


def caravan_panel_buy(view, index: int | None = None) -> bool:
    """购买弹层中第 index 项（默认当前高亮行），成功返回 True

    - 扣 gs.run_carried["gold"]，物品写入 gs.run_carried 的 potion/resource 槽；
    - 背包容量不足或金币不够时给出浮动文字提示并拒绝（不产生半截状态）。
    """
    if not view._caravan_panel_open:
        return False
    # 联机客户端禁本地交易：商队购买走 host 权威（当前实现仅 host/solo 开放，
    # 客户端只看到招牌与弹层表现，禁改本地 run_carried 造成两端金币/物品不一致）
    if getattr(view.window.game_state, "net_mode", "solo") == "client":
        return False
    items = caravan_prices()
    if not items:
        return False
    idx = view._caravan_index if index is None else int(index)
    if not 0 <= idx < len(items):
        return False
    item_id = list(items.keys())[idx]
    price = int(items[item_id])

    gs = view.window.game_state
    carried = getattr(gs, "run_carried", None)
    if carried is None:
        return False
    gold = int(carried.get("gold", 0) or 0)
    if gold < price:
        _caravan_prompt(view, f"金币不足（需 {price}）", (255, 120, 120))
        return False
    # 容量校验：药水/资源都占背包容量，满仓直接拒绝
    from game.loot import _calc_carried_capacity
    cap = int(getattr(gs, "backpack_capacity", 0) or 0)
    probe = copy_run_carried(carried)
    slot = caravan_slot(item_id)
    probe.setdefault(slot, {})
    probe[slot][item_id] = probe[slot].get(item_id, 0) + 1
    if cap and _calc_carried_capacity(probe) > cap:
        _caravan_prompt(view, "背包已满", (255, 150, 90))
        return False
    # 正式扣款入账
    carried["gold"] = gold - price
    carried.setdefault(slot, {})
    carried[slot][item_id] = carried[slot].get(item_id, 0) + 1
    _caravan_prompt(view, f"购入 {caravan_label(item_id)}", (140, 240, 180))
    return True


def copy_run_carried(carried: dict) -> dict:
    """浅两层拷贝 run_carried 的 potion/resource 槽（容量试算用，避免污染真实携带物）"""
    probe = {"gold": carried.get("gold", 0)}
    for slot in ("potion", "resource"):
        probe[slot] = dict(carried.get(slot) or {})
    return probe


def _caravan_prompt(view, text: str, color) -> None:
    """商队提示（浮动文字挂在玩家头顶；节流防按住 E 刷屏）"""
    if view._caravan_hint_cd > 0.0:
        return
    view._caravan_hint_cd = EVENT_CARAVAN_PROMPT_CD
    floating_texts.add(view.player.center_x, view.player.center_y + 60,
                       text, color, life=1.5, font_size=14)


def caravan_min_level() -> int:
    """空投箱必出装备的最低等级（渲染/校验提示复用）"""
    return EVENT_AIRDROP_MIN_LEVEL


def event_id_or_empty(view) -> str:
    """取本局事件 id（无事件/未知 id 归一为空串，供序列化字段使用）"""
    eid = getattr(view, "event_id", "")
    return eid if eid in MAP_EVENTS else ""
