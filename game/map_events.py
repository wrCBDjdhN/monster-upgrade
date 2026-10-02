"""随机地图事件运行时（阶段 4）

职责（views/game_view.py 只做薄编排，核心逻辑全在本模块）：

- 抽选：pick_event(rng) —— 等权随机抽一个事件 id（教程局由调用侧守卫后返回 ""）
- 生效：apply_event(view, event_id) —— 把事件参数写进 view.event_flags
        （monster_cap_mult / reward_mult / artifact_bonus），并把怪物上限倍率
        反算进 game_view 现有的 self._monster_cap（野外补刷读该上限）
- 横幅：event_banner(view) —— 开局 3 秒横幅文案（渲染层消费）
- 落地：trigger_event(view) —— 商队交互点立即生成；空投走 update_event 计时器
- 驱动：update_event(view, dt) —— 横幅计时、空投落地计时
- 奖励：scale_event_drops(view) / award_event_kill_exp(view, monster)
        —— 尸潮事件的掉落与击杀经验倍率（挂在 game_view 的掉落更新与死亡回调处，
        不侵入 entity_callbacks 的掉落/经验主流程）
- 交互：handle_caravan_interaction / caravan_stock / caravan_panel_move / caravan_panel_buy
- 弹层：caravan_panel_layout / caravan_close_button / close_caravan_panel
         —— 换购弹层几何与右上角 ✕ 关闭（绘制层与命中层共用同一份几何）
- 商队货单：caravan_stock(view) —— **按地图种子确定性抽选**的本局货单（药水/武器/神器），
        账号金币购买、单局限购、购入物按类分流（武器/神器→run_carried、药水→run_potions，
        死亡即丢，Q1 决策）；
        两端各自本地算出同一份货单（货单本身不上网），但**记账口径分端**：
        solo/主机本端购买直接本地生效，联机客户端走 **CARRIAGE_BUY 请求 → 主机回执
        CARRIAGE_BUY_RESULT(ok) 才本地生效**（本模块 caravan_request_buy /
        apply_carriage_buy_result / caravan_commit_local_purchase 三件套），
        避免客户端自买自记账造成主机账本 desync。

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
    EVENT_CARAVAN_ARTIFACT_POOL,
    EVENT_CARAVAN_ARTIFACT_PRICE,
    EVENT_CARAVAN_LIMITS,
    EVENT_CARAVAN_MIN_DIST,
    EVENT_CARAVAN_PROMPT_CD,
    EVENT_CARAVAN_RANGE,
    EVENT_CARAVAN_STOCK,
    EVENT_CARAVAN_STOCK_SALT,
    EVENT_CARAVAN_WEAPON_POOL,
    EVENT_PANEL_CLOSE_MARGIN, EVENT_PANEL_CLOSE_SIZE, EVENT_PANEL_MARGIN,
    EVENT_PANEL_ROW_H, EVENT_PANEL_WIDTH,
    EVENT_PICK_RNG_SALT,
    EXP_BOSS_MULT,
    EXP_KILL_BASE,
    RUN_POTION_SLOTS,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from entities.equipment_defs import POTIONS
from entities.event_defs import EVENT_IDS, MAP_EVENTS, get_event
from entities.resource_defs import RESOURCES
from entities.weapon_defs import ALL_WEAPONS
from game.effects import floating_texts
from net.protocol import MsgType  # 联机消息类型（客户端商队购买发 CARRIAGE_BUY 请求）


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
    # 商队货单置 None：下一局（换地图种子）首次访问 caravan_stock 时按新种子重抽，
    # 若不置空会把上一局的货单缓存带进本局（reset 在 setup 早期跑，故必然先于任何访问）
    view._caravan_stock = None
    view._caravan_account_gold = 0
    # 客户端待确认购买计数（CARRIAGE_BUY 已发出未回执）每局清零，
    # 否则上一局没等到回执的记录会让本局回执被误判成「迟到/重复」而不发货
    view._caravan_pending = {}
    # 单局限购计数每局清零（Q4：按 item_id 计数，存 GameState.caravan_bought）
    gs = getattr(view.window, "game_state", None)
    if gs is not None:
        setattr(gs, "caravan_bought", {})
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
      （否则两端各自落地一批箱，出现重复箱）。
    商队弹层不在此关闭：改为面板右上角 ✕ 按钮主动关闭（见 caravan_close_button）。
    """
    if view._event_banner_timer > 0.0:
        view._event_banner_timer = max(0.0, view._event_banner_timer - dt)
    if view._caravan_hint_cd > 0.0:
        view._caravan_hint_cd = max(0.0, view._caravan_hint_cd - dt)
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
    """换购条目的中文显示名（药水取 POTIONS，资源取 RESOURCES，武器/神器取 ALL_WEAPONS，未知回落 item_id）"""
    pdef = POTIONS.get(item_id)
    if pdef:
        return str(pdef.get("name", item_id))
    rdef = RESOURCES.get(item_id)
    if rdef:
        return str(rdef.get("name", item_id))
    # 货单新增武器/神器行（写入 run_carried["weapon"]），显示名走武器定义表
    wdef = ALL_WEAPONS.get(item_id)
    if wdef:
        return str(wdef.get("name", item_id))
    return item_id


def caravan_stock(view) -> list[dict]:
    """本局商队货单（按地图种子**确定性抽选**，host/client 同种子 → 同货单）

    为什么用种子派生而不是网络广播（Q2 决策）：
    货单只由「地图种子 + 地图主题 + config 构成表」决定，是纯函数式计算；
    两端各自本地算出的货单必然一致，因此**货单本身不需要任何 net 协议消息**
    （购买记账才走 CARRIAGE_BUY 请求-回执，见 caravan_request_buy）。

    - 随机源：random.Random((seed * 1000003 + EVENT_CARAVAN_STOCK_SALT) & 0x7FFFFFFF)
      （沿用 event_rng 的同款派生式，只是换盐避免与事件抽选串味）；
    - 抽取顺序固定为 药水 → 武器 → 神器：rng 是有状态序列，顺序一变两端结果就分叉；
    - 货单条目结构：{"item_id", "kind"("potion"/"weapon"/"artifact"), "price", "level"}
      （药水 level 恒为 0；武器/神器 level 为入库等级，键入 run_carried 时与掉落同契约）；
    - 结果缓存在 view._caravan_stock，每局首次访问才抽一次（reset_event_state 会置空）。
    """
    cached = getattr(view, "_caravan_stock", None)
    if cached:
        return cached
    gs = view.window.game_state
    theme = str(getattr(gs, "map_theme", "forest") or "forest")
    if theme not in EVENT_CARAVAN_STOCK:
        theme = "forest"   # 主题表缺项回落森林档（禁因配错主题直接开天窗）
    cfg = EVENT_CARAVAN_STOCK[theme]
    seed = int(getattr(gs, "current_map_seed", 1) or 1)
    rng = random.Random((seed * 1000003 + EVENT_CARAVAN_STOCK_SALT) & 0x7FFFFFFF)
    lv_range = tuple(cfg.get("weapon_level") or (1, 1))
    stock: list[dict] = []

    # ① 药水：候选 = 商队价表中真实存在的药水 id，价格直接取价表（账号金币价）
    price_table = caravan_prices()
    potion_ids = [pid for pid in price_table if pid in POTIONS]
    if potion_ids:
        want = min(int(cfg.get("potions", 0) or 0), len(potion_ids))
        for pid in rng.sample(potion_ids, want):
            stock.append({"item_id": pid, "kind": "potion",
                          "price": int(price_table[pid]), "level": 0})

    # ② 武器：主题武器池洗牌后按序取 N 件，价格取武器 def 的 price（非卖品 price<=0 跳过）
    pool = list(EVENT_CARAVAN_WEAPON_POOL.get(theme) or [])
    rng.shuffle(pool)
    want_weapons = int(cfg.get("weapons", 0) or 0)
    taken = 0
    for wid in pool:
        if taken >= want_weapons:
            break
        wdef = ALL_WEAPONS.get(wid) or {}
        price = int(wdef.get("price", 0) or 0)
        if price <= 0:
            continue
        stock.append({"item_id": wid, "kind": "weapon",
                      "price": price, "level": rng.randint(int(lv_range[0]), int(lv_range[-1]))})
        taken += 1

    # ③ 神器：整局最多 1 件，按 artifact_chance 掷点；神器 def 的 price=0，故用专属定价
    if (EVENT_CARAVAN_ARTIFACT_POOL
            and rng.random() < float(cfg.get("artifact_chance", 0.0) or 0.0)):
        aid = rng.choice(EVENT_CARAVAN_ARTIFACT_POOL)
        stock.append({"item_id": aid, "kind": "artifact",
                      "price": int(EVENT_CARAVAN_ARTIFACT_PRICE),
                      "level": rng.randint(int(lv_range[0]), int(lv_range[-1]))})

    view._caravan_stock = stock
    return stock


def _caravan_add_to_carried(carried: dict, kind: str, item_id: str, level: int,
                            qty: int = 1) -> None:
    """把商队购入项按 run_carried 既有键契约写入（原地 +qty）

    契约与 game/loot 的掉落拾取完全一致：药水入 "potion" 且以 item_id 为键；
    武器/神器入 "weapon" 且以 **(item_id, level) 元组**为键（同名不同等级可并存，
    撤离入库 game/evac 也按元组键遍历，禁改成纯 id 键）。
    容量试算副本与正式入库共用本函数，保证两处键口径永不漂移。
    （2026-10-01 起商队购入药水改走 gs.run_potions 分流、不再经本函数；
    药水分支保留作键契约参考，容量试算现在只会以武器/神器调用本函数。）
    qty 供 CARRIAGE_BUY 回执按主机核定的笔数批量发货（单笔购买时默认 1）。
    """
    if kind == "potion":
        slot, key = "potion", item_id
    else:
        slot, key = "weapon", (item_id, int(level))
    add = max(1, int(qty or 1))
    carried.setdefault(slot, {})
    carried[slot][key] = carried[slot].get(key, 0) + add


def is_near_caravan(view, radius: float = EVENT_CARAVAN_RANGE) -> bool:
    """玩家是否在商队交互范围内（商队点存在才判定）"""
    point = getattr(view, "caravan_point", None)
    if point is None or view.player is None:
        return False
    return math.hypot(view.player.center_x - point.center_x,
                      view.player.center_y - point.center_y) < radius


def handle_caravan_interaction(view) -> None:
    """商队交互（靠近按 E 打开换购弹层）

    host / solo / client 三端一致可开可买（Q2 决策）：弹层行与货单来自本地
    caravan_stock()（按地图种子确定性派生，两端同货单），账号金币本地读取。
    购买生效口径分端：**solo/主机本端直接本地生效**（玩家即权威方）；
    **联机客户端等主机回执 CARRIAGE_BUY_RESULT(ok) 后才本地生效**（见
    caravan_request_buy / apply_carriage_buy_result），主机记账、本地不预扣。

    弹层关闭只走右上角 ✕ 按钮（close_caravan_panel），不再有「走远自动关闭」。
    """
    if getattr(view, "caravan_point", None) is None:
        return
    if not getattr(view, "_chest_key_pressed", False):
        return
    if is_near_caravan(view) and not view._caravan_panel_open:
        view._caravan_panel_open = True
        view._caravan_index = 0
        # 打开瞬间缓存账号金币：弹层每帧绘制余额，逐帧查库太重，缓存后由购买侧同步递减
        from db.database import get_gold

        gs = view.window.game_state
        pid = getattr(gs, "player_id", None)
        view._caravan_account_gold = int(get_gold(pid) or 0) if pid is not None else 0


def caravan_panel_layout(view) -> tuple[float, float, float, float] | None:
    """换购弹层几何 (px, py, panel_w, panel_h)（屏幕逻辑坐标，中心式）；未开启返回 None

    行数按本局货单条目数 caravan_stock(view) 计算（货单是种子确定性抽出来的，
    每局条数可能不同，面板高度必须跟着货单走，不能写死）。

    绘制层（rendering_hud.draw_caravan_panel）与命中层（input_handler 的 ✕ 按钮命中）
    必须读同一份几何：两处各算一套会出现「按钮画在一处、点在另一处」。
    """
    if not getattr(view, "_caravan_panel_open", False):
        return None
    stock = caravan_stock(view)
    if not stock:
        return None
    panel_w = float(EVENT_PANEL_WIDTH)
    panel_h = float(EVENT_PANEL_MARGIN * 2 + 30 + len(stock) * EVENT_PANEL_ROW_H + 18)
    return float(WINDOW_WIDTH // 2), float(WINDOW_HEIGHT // 2), panel_w, panel_h


def caravan_close_button(view) -> tuple[float, float, float, float] | None:
    """弹层右上角「✕ 关闭」按钮几何 (cx, cy, w, h)（屏幕逻辑坐标，中心式）；未开启返回 None

    坐标由弹层几何推导（随面板位置计算，禁写死屏幕绝对坐标）；
    绘制（实心底板 + 实心 ✕ 图形）与鼠标命中共用本函数，保证按钮与命中区对齐。
    """
    layout = caravan_panel_layout(view)
    if layout is None:
        return None
    px, py, panel_w, panel_h = layout
    half = EVENT_PANEL_CLOSE_SIZE / 2
    cx = px + panel_w / 2 - EVENT_PANEL_CLOSE_MARGIN - half
    cy = py + panel_h / 2 - EVENT_PANEL_CLOSE_MARGIN - half
    return cx, cy, float(EVENT_PANEL_CLOSE_SIZE), float(EVENT_PANEL_CLOSE_SIZE)


def close_caravan_panel(view) -> None:
    """关闭换购弹层（面板右上角 ✕ 按钮的唯一入口）

    与打开侧（handle_caravan_interaction）对称地收口状态迁移：关面板同时复位高亮行，
    后续按 E 可重新打开；面板关闭后 input_handler 的按键吞噬随之解除，
    WASD/E/TAB 等恢复正常响应。
    """
    view._caravan_panel_open = False
    view._caravan_index = 0


def caravan_panel_move(view, delta: int) -> None:
    """换购弹层上下移动高亮行（本局货单列表首尾循环）"""
    if not view._caravan_panel_open:
        return
    stock = caravan_stock(view)
    if not stock:
        return
    view._caravan_index = (view._caravan_index + delta) % len(stock)


def caravan_panel_buy(view, index: int | None = None) -> bool:
    """购买货单第 index 项（默认当前高亮行），成功返回 True

    本版口径（Q1/Q4 决策 + 客户端请求-回执两阶段）：
    - 花**账号金币**（db/players.spend_gold，函数内原子复核余额），不是局内金币；
      金币不足直接拒绝并给中文提示（账已花光也不能留半截状态）；
    - 购入物按类分流入库（局内携带、死亡即丢，Q1；2026-10-01 修复）：
      * 武器/神器 → gs.run_carried，(item_id, level) 元组键入 "weapon" 槽，
        与掉落拾取/撤离入库同契约；
      * 药水 → gs.run_potions（本局药水槽，与地面拾取同口径）：不占背包容量、
        无需背包、上限 config.RUN_POTION_SLOTS；
    - 单局限购：上限取 config.EVENT_CARAVAN_LIMITS[kind]，按 item_id 计数存
      gs.caravan_bought（每局 reset_event_state 清零，Q4）。**客户端的 bought 计数
      只在收到主机 ok 回执后才自增**（见 caravan_commit_local_purchase），
      与主机侧计数保持同一口径；
    - 背包容量（仅武器/神器）：把候选物品加进 run_carried 副本试算（禁污染真实
      携带物）；无背包（cap<=0）直接拒绝（与拾取 skipped_no_bag 同口径），
      有背包则按试算占用超限拒绝；药水不走本检查（拾取药水同样在 no-bag 检查
      之前就入 run_potions，本函数保持同口径）；
    - **solo / 主机本端**：预检通过后直接本地生效（玩家就是权威方，无需请求）；
      主机侧由 game/input_handler._caravan_buy 把本端 run_carried 镜像回自己的
      权威携带清单 _players_run_carried；
    - **联机客户端**：只做预检并发 CARRIAGE_BUY 请求（caravan_request_buy），
      **本地既不扣金币也不写 run_carried / run_potions**；等主机回执
      CARRIAGE_BUY_RESULT（ok=True）后由 apply_carriage_buy_result →
      caravan_commit_local_purchase 才本地生效，ok=False 只提示主机给的拒绝原因。
      修复（desync 根治）：旧版客户端购买是「本地直接扣账号金币 + 写本端
      run_carried（药水还双写）」，主机账本 _players_run_carried 完全不知这笔
      购入 → 主机拒绝客户端的 POTION_USE（校验 _players_run_carried）、
      撤离又按主机账本结算，两端携带清单分叉。

    返回值口径：solo/host = 本地已发货；client = 请求已发出（尚未发货）。
    """
    if not getattr(view, "_caravan_panel_open", False):
        return False
    stock = caravan_stock(view)
    if not stock:
        return False
    idx = int(view._caravan_index if index is None else index)
    if not 0 <= idx < len(stock):
        return False
    entry = stock[idx]
    item_id = str(entry["item_id"])
    kind = str(entry["kind"])
    price = int(entry["price"])
    level = int(entry.get("level", 0) or 0)

    # 本地预检（限购/金币/药水槽/背包容量）：两端同一口径，只是提前拦掉明显
    # 不可买的请求，不替代主机裁决（主机按自己账本复核金价自洽与限购）
    if not _caravan_precheck_buy(view, kind, item_id, level, price):
        return False

    gs = view.window.game_state
    if getattr(gs, "net_mode", "solo") == "client":
        # 阶段 1：只发请求，绝不本地预扣金币/预写携带物（那才是 desync 源）
        return caravan_request_buy(view, kind, item_id, level, price)
    # solo / 主机本端：阶段 1 与阶段 2 合一，玩家自己就是权威方
    return caravan_commit_local_purchase(view, kind, item_id, level, 1, price)


def _caravan_precheck_buy(view, kind: str, item_id: str, level: int, price: int) -> bool:
    """购买本地预检：限购 / 账号金币 / 本局药水槽 / 背包容量，任一不过即提示并返回 False

    三端共用同一套判据（禁各写一套，否则提示与主机裁决会分叉）：
    - 限购：上限 config.EVENT_CARAVAN_LIMITS[kind]，已购数读 gs.caravan_bought；
      客户端该表只在收到主机 ok 回执后自增，故这里拦的是「已确认买过的那几件」，
      连发的待确认请求由主机裁决（超限会回 ok=False + reason）；
    - 金币：预检只用于提前给提示，真正扣款以 spend_gold 的原子复核为准；
    - 药水槽：总量 sum 对比 config.RUN_POTION_SLOTS（与 loot.py 拾取同式），
      药水不占背包容量、无需背包，故完全不走背包校验；
    - 背包容量（仅武器/神器）：候选物品加进 run_carried 副本后重算占用，
      无背包（cap<=0）直接拒绝（game/loot.py skipped_no_bag 同口径），避免
      「不带背包也能把武器购入 run_carried」的漏洞。
    """
    gs = view.window.game_state
    from db.database import get_gold

    pid = getattr(gs, "player_id", None)
    if pid is None:
        # 账号未落库（异常上下文）：无账号金币可扣，禁白送物品
        _caravan_prompt(view, "账号未就绪", (255, 120, 120))
        return False

    # 单局限购：按 kind 取上限、按 item_id 计已购数（同一货品整局最多 N 件）
    limit = int(EVENT_CARAVAN_LIMITS.get(kind, 1))
    bought = _caravan_bought_map(gs)
    if int(bought.get(item_id, 0) or 0) >= limit:
        _caravan_prompt(view, f"{caravan_label(item_id)} 已达本局限购", (255, 150, 90))
        return False

    carried = getattr(gs, "run_carried", None)
    if not isinstance(carried, dict):
        return False

    # 账号金币预检（仅用于提前给提示；真正扣款以 spend_gold 的原子复核为准）
    if int(get_gold(pid) or 0) < price:
        _caravan_prompt(view, f"金币不足（需 {price}）", (255, 120, 120))
        return False

    if kind == "potion":
        run_potions = getattr(gs, "run_potions", None)
        if not isinstance(run_potions, dict):
            return False
        if sum(int(q) for q in run_potions.values()) + 1 > RUN_POTION_SLOTS:
            _caravan_prompt(view, "本局药水槽已满", (255, 150, 90))
            return False
        return True

    # 容量校验（仅武器/神器）：候选物品加进副本后重算总占用（按各自 capacity_cost 计）
    from game.loot import _calc_carried_capacity

    cap = int(getattr(gs, "backpack_capacity", 0) or 0)
    if cap <= 0:
        _caravan_prompt(view, "无背包，无法购买", (255, 150, 90))
        return False
    probe = copy_run_carried(carried)
    _caravan_add_to_carried(probe, kind, item_id, level)
    if _calc_carried_capacity(probe) > cap:
        _caravan_prompt(view, "背包已满", (255, 150, 90))
        return False
    return True


def caravan_request_buy(view, kind: str, item_id: str, level: int, price: int) -> bool:
    """联机客户端阶段 1：发 CARRIAGE_BUY 请求购买（**本地不扣金、不写携带物**）

    - 只发请求。购入物与金币都要等主机回执 CARRIAGE_BUY_RESULT（ok=True）才由
      caravan_commit_local_purchase 生效；禁「先本地扣金/写 run_carried 再等回执」，
      那正是旧版 desync 的根源（主机账本不知这笔 → POTION_USE 被拒、撤离按
      主机账本结算，两端携带清单分叉）；
    - 载荷 kind/item_id/level/qty 取本端货单（两端同种子同货单，必然一致），
      gold_before/gold_after 供主机做「报文自洽」校验（金价 = 两者差值，与主机
      自己的价表比对），本端不因 gold_after 改任何账（金币是各端本地账号库）；
    - 已发出未回执的笔数登记在 view._caravan_pending[(kind, item_id)]，供回执
      逐笔消费（同一货品限购 >1 时可连发多笔），防迟到/重复回执重复发货；
    - 无连接时**显式拒绝**（不回退成本地直买：没有主机裁决就没有权威账本，
      本地发货只会立刻制造 desync）。
    """
    gs = view.window.game_state
    from db.database import get_gold

    pid = getattr(gs, "player_id", None)
    net_client = getattr(gs, "net_client", None)
    if pid is None or net_client is None:
        _caravan_prompt(view, "联机未连接，无法购买", (255, 120, 120))
        return False
    gold_before = int(get_gold(pid) or 0)
    net_client.send((MsgType.CARRIAGE_BUY, {
        "player_id": getattr(gs, "net_player_id", 0),
        "kind": kind,
        "item_id": item_id,
        "level": int(level),
        "qty": 1,
        "gold_before": float(gold_before),
        "gold_after": float(gold_before - int(price)),
    }))
    pending = _caravan_pending_map(view)
    key = (kind, item_id)
    pending[key] = int(pending.get(key, 0) or 0) + 1
    _caravan_prompt(view, "已提交购买请求", (255, 215, 0))
    return True


def caravan_commit_local_purchase(view, kind: str, item_id: str, level: int,
                                  qty: int = 1, price: int = 0,
                                  force_prompt: bool = False) -> bool:
    """本地发货：扣本端账号金币 + 按类写入 run_potions / run_carried（唯一发货口径）

    两条路径共用，禁各写一份：
    - solo / 主机本端：caravan_panel_buy 预检通过后直接调用（玩家是自己，主机即权威）；
    - 联机客户端：收到 CARRIAGE_BUY_RESULT 且 ok=True 后由 apply_carriage_buy_result
      调用（此刻主机已把同一笔购入记进自己的权威携带清单 _players_run_carried，
      本端再补上「本地金币 + 本地携带物」这份镜像账）。

    限购计数 gs.caravan_bought 在此自增：主机 ok 回执才发货，故客户端计数与
    主机侧计数同口径（主机拒绝的请求不进账，客户端也不会虚增）。
    金币是**各端本地账号库**（players.gold）里同一笔账：主机不代扣客户端金币，
    故客户端回执 ok 后要真扣自己的本地库；扣款仍走 spend_gold 原子复核，出现
    余额不足（本地余额被别的入口改动）时不发货并给提示，避免「扣了钱没给货」。
    force_prompt=True 时发货提示跳过节流（回执路径用：局域往返快于提示节流，
    否则「已提交购买请求」会把「购入 XX」吃掉，玩家看不到结果）。
    """
    gs = view.window.game_state
    from db.database import spend_gold

    qty = max(1, int(qty or 1))
    price = max(0, int(price or 0))
    pid = getattr(gs, "player_id", None)
    if pid is None:
        _caravan_prompt(view, "账号未就绪", (255, 120, 120))
        return False
    carried = getattr(gs, "run_carried", None)
    if not isinstance(carried, dict):
        return False
    run_potions = getattr(gs, "run_potions", None)
    if kind == "potion" and not isinstance(run_potions, dict):
        return False

    # 正式扣账号金币（原子复核余额，失败即拒绝，保证不会出现「扣了钱没给货」）
    if price > 0 and not spend_gold(pid, price):
        _caravan_prompt(view, f"金币不足（需 {price}）", (255, 120, 120))
        return False

    if kind == "potion":
        # 本局药水槽（修复：原写 run_carried["potion"]，热键 1-3 与 TAB 药水区
        # 只读 run_potions，购入药水无法使用也无法看见）
        run_potions[item_id] = int(run_potions.get(item_id, 0) or 0) + qty
        # 联机客户端双写保撤离（只在 client 生效）：EVAC_REQUEST 只上报 run_carried
        # （发送侧仅 serialize run_carried，不带 run_potions），双写一份让撤离合流
        # 不丢购入药水；主机侧同一笔已由 CARRIAGE_BUY 记进自己账本，撤离合流按
        # _merge_authoritative 键级差集、权威优先不重复计入，故双写不会重复入库。
        # solo/host 禁双写（撤离时 _fold_run_potions 会把 run_potions 折入
        # run_carried["potion"]，双写将重复入库）。
        if getattr(gs, "net_mode", "solo") == "client":
            pslot = carried.setdefault("potion", {})
            pslot[item_id] = int(pslot.get(item_id, 0) or 0) + qty
    else:
        _caravan_add_to_carried(carried, kind, item_id, level, qty)
    bought = _caravan_bought_map(gs)
    bought[item_id] = int(bought.get(item_id, 0) or 0) + qty
    # 缓存余额同步递减（弹层标题显示用，避免每帧回查数据库）
    view._caravan_account_gold = max(
        0, int(getattr(view, "_caravan_account_gold", 0) or 0) - price)
    _caravan_prompt(view, f"购入 {caravan_label(item_id)}", (140, 240, 180),
                    force=force_prompt)
    return True


def apply_carriage_buy_result(view, payload: dict) -> bool:
    """客户端应用主机 CARRIAGE_BUY_RESULT（两阶段购买回执入口，供 views/game_view.py 接线）

    接线方式与 BUILD_RESULT / INTERACTION_RESULT 同款（客户端消息循环里分发 payload）：
        elif msg_type == MsgType.CARRIAGE_BUY_RESULT:
            from game.map_events import apply_carriage_buy_result
            apply_carriage_buy_result(self, payload)
    （若下游选择经 network_sync 转发，则调 apply_carriage_buy_result(self.gv, payload)。）

    - ok=True：主机已校验金价自洽/限购并把这笔购入记入自己的权威携带清单，本端
      **此刻才**扣本地账号金币 + 写 run_potions / run_carried
      （caravan_commit_local_purchase），bought 计数同步自增；
    - ok=False：本地既没扣钱也没发货（阶段 1 什么都没改），天然一致，只按主机
      reason 给中文提示（禁静默吞掉，玩家要看到为什么没买到）；
    - 回执载荷不含 level/price，故等级与价格仍取**本端货单**（两端同种子同货单，
      必然与主机一致；找不到对应条目说明货单已变，显式提示不发货）；
    - 只消费自己发起过的待确认购买（view._caravan_pending），重复/迟到的回执不重复发货。
    """
    gs = view.window.game_state
    my_id = getattr(gs, "net_player_id", None)
    player_id = payload.get("player_id")
    if my_id is not None and player_id != my_id:
        return False  # 他人的购买回执：本端无动作（禁按他人结果改自己状态）
    kind = str(payload.get("kind") or "")
    item_id = str(payload.get("item_id") or "")
    qty = max(1, int(payload.get("qty", 1) or 1))
    ok = bool(payload.get("ok"))
    if not ok:
        _caravan_pending_consume(view, kind, item_id)
        reason = payload.get("reason") or "主机拒绝"
        print(f"[Client] 商队购买被主机拒绝：kind={kind!r} item={item_id!r} reason={reason}")
        _caravan_prompt(view, f"购买失败：{reason}", (255, 120, 120), force=True)
        return False
    if not _caravan_pending_consume(view, kind, item_id):
        # 无待确认记录（重复/迟到回执，或本局已重置）：禁重复发货
        print(f"[Client] 收到非待确认的商队购买成功回执，已忽略：kind={kind!r} item={item_id!r}")
        return False
    entry = _caravan_stock_entry(view, kind, item_id)
    if entry is None:
        _caravan_prompt(view, "购买未生效（货单已变化）", (255, 150, 90), force=True)
        return False
    level = int(entry.get("level", 0) or 0)
    price = int(entry.get("price", 0) or 0)
    return caravan_commit_local_purchase(view, kind, item_id, level, qty, price,
                                         force_prompt=True)


def _caravan_stock_entry(view, kind: str, item_id: str) -> dict | None:
    """按 (kind, item_id) 在本局货单里找条目（回执不含 level/price，故仍取本端货单）"""
    for entry in caravan_stock(view):
        if str(entry.get("kind")) == kind and str(entry.get("item_id")) == item_id:
            return entry
    return None


def _caravan_bought_map(gs) -> dict:
    """取本局限购计数表 gs.caravan_bought（缺省补空表并回写，禁各处重复判空）"""
    bought = getattr(gs, "caravan_bought", None)
    if not isinstance(bought, dict):
        bought = {}
        setattr(gs, "caravan_bought", bought)
    return bought


def _caravan_pending_map(view) -> dict:
    """取客户端待确认购买计数表 view._caravan_pending（缺省补空表并回写）

    键 = (kind, item_id)，值 = 已发出但未回执的笔数；按笔数计而不是布尔，
    因为 config.EVENT_CARAVAN_LIMITS 允许同一货品整局买多件（药水上限 2），
    连发两笔时两条回执要各发货一次。
    """
    pending = getattr(view, "_caravan_pending", None)
    if not isinstance(pending, dict):
        pending = {}
        view._caravan_pending = pending
    return pending


def _caravan_pending_consume(view, kind: str, item_id: str) -> bool:
    """消费一笔待确认购买（回执到达时调用）；无待确认记录返回 False（防重复发货）"""
    pending = _caravan_pending_map(view)
    key = (kind, item_id)
    left = int(pending.get(key, 0) or 0)
    if left <= 0:
        return False
    if left <= 1:
        pending.pop(key, None)
    else:
        pending[key] = left - 1
    return True


def copy_run_carried(carried: dict) -> dict:
    """浅拷贝 run_carried 作容量试算副本（禁污染真实携带物）

    **逐槽遍历**而不是写死 slot 列表：run_carried 的槽会随玩法增长（weapon 元组键槽、
    backpack 等），写死列表会让新增槽漏算占用。试算副本必须与真实携带物同构，
    故这里对每个 dict 值槽做一层 dict(...) 拷贝，标量槽（如 "gold"）原样带过。
    """
    probe: dict = {}
    for slot, value in (carried or {}).items():
        probe[slot] = dict(value) if isinstance(value, dict) else value
    probe.setdefault("gold", 0)
    return probe


def _caravan_prompt(view, text: str, color, force: bool = False) -> None:
    """商队提示（浮动文字挂在玩家头顶；节流防按住 E 刷屏）

    force=True 时**跳过节流**：主机 CARRIAGE_BUY_RESULT 回执的成败提示必须让玩家
    看见——局域往返往往快于 config.EVENT_CARAVAN_PROMPT_CD，若随购买请求提示一起
    被节流掉，玩家会「按了完全没反馈」。
    """
    if not force and view._caravan_hint_cd > 0.0:
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
