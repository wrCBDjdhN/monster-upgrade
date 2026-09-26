"""HUD/小地图/BOSS血条渲染：从 rendering.py 抽离的屏幕固定层绘制函数

包含：怪物绘制辅助（_draw_monster/_draw_skill_prompt）、行动倒计时、
角色等级 HUD、右上角小地图、屏幕顶部 BOSS 血条。
"""

import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, MINIMAP_SIZE, MINIMAP_PADDING, MINIMAP_VIEW_RADIUS,
    MAP_WIDTH, MAP_HEIGHT,
    EVAC_STATE_COLORS,  # 阶段2 主撤离点小地图状态标记配色
    ELITE_SHIELD_BAR_WIDTH, ELITE_SHIELD_BAR_HEIGHT, ELITE_SHIELD_BAR_OFFSET,
    ELITE_SHIELD_BAR_COLOR, ELITE_LABEL_COLOR,  # 阶段3 精英护盾条/词缀名前缀
    ELITE_MINIMAP_COLOR, ELITE_MINIMAP_DOT_RADIUS,  # 阶段3 小地图精英紫点
    # 阶段4 随机事件：开局横幅 + 商队换购弹层 + 小地图事件标记
    EVENT_BANNER_HEIGHT, EVENT_BANNER_Y, EVENT_BANNER_BG, EVENT_BANNER_EDGE,
    EVENT_BANNER_TEXT_COLOR,
    EVENT_PANEL_BG, EVENT_PANEL_ROW_BG, EVENT_PANEL_ROW_HL, EVENT_PANEL_TEXT,
    EVENT_PANEL_TEXT_HL, EVENT_PANEL_ROW_H, EVENT_PANEL_MARGIN, EVENT_PANEL_WIDTH,
    EVENT_BANNER_MAX_HALF_W,
    EVENT_AIRDROP_MINIMAP_COLOR, EVENT_CARAVAN_MINIMAP_COLOR, EVENT_MINIMAP_MARK_SIZE,
)
from game.render_helpers import (
    draw_monster_base, draw_monster_armor, draw_monster_face, draw_monster_weapon,
    draw_monster_body,
)
# 怪物武器颜色从 monster_defs.py 统一读取（原 MONSTER_WEAPON_COLOR 已并入 MONSTER_METADATA）
from entities.monster_defs import MONSTER_METADATA, ELITE_AFFIXES

# 怪物标签中文名映射（本地/远端怪物共用）
_MONSTER_NAMES = {
    "Zombie": "僵尸", "Skeleton": "骷髅",
    "MummyMelee": "木乃伊", "MummyRanged": "木乃伊弓手",
    "Camel": "骆驼",
    "BossZombie": "BOSS僵尸", "BossSkeleton": "BOSS骷髅", "BossMummy": "BOSS木乃伊",
    "Sniper": "狙击兵", "Assault": "突击兵",
    "Bandit": "土匪", "RocketTroop": "火箭兵",
    "BossSpace": "BOSS航天兵",
}
# 怪物武器名映射兜底（本地/远端怪物共用，骆驼=吐口水）
_WEAPON_NAMES = {
    "Zombie": "铁剑", "Skeleton": "短弓",
    "MummyMelee": "弯刀", "MummyRanged": "权杖",
    "Camel": "吐口水", "BossZombie": "铁剑", "BossSkeleton": "短弓", "BossMummy": "弯刀",
    "Sniper": "狙击枪", "Assault": "步枪",
    "Bandit": "手枪", "RocketTroop": "火箭筒",
    "BossSpace": "激光枪",
}


def _draw_monster(view, m, wb):
    """绘制单个怪物（本地权威 + 联机远端快照共用同一套表现逻辑）

    - 本地怪物：weapon/armor 为携带装备 dict，攻击冷却条读本地 AI 的 _attack_timer，
      护盾条读本地 shield/max_shield；
    - 远端怪物（remote_monsters 里的真实怪物类实例）：weapon/armor 为 None，
      武器/护甲/头盔颜色与名称走主机广播的 net_weapon_color/net_armor_color/
      net_helmet_color 等快照值（修复远端怪物装备与主机不一致）；
      护盾条优先读主机广播的 net_shield/net_max_shield（客户端幽灵只有
      MONSTER_CONFIGS 默认值，不读则精英护盾条永不显示）；
      攻击冷却条优先读主机广播的归一化剩余比例 net_attack_cd（客户端按 dt
      线性衰减，与主机曲线同源），无该字段的旧主机退回 net_attack_anim；
      武器名标签优先取 net_weapon。
    """
    # 怪物尺寸：所有怪物继承基类，_size 属性已统一设置
    m_size = getattr(m, '_size', 20)
    # 护甲颜色：远端怪物优先取主机广播的 net_armor_color（修复远端怪物无护甲），
    # 本地怪物回退读携带护甲 dict
    if getattr(m, 'net_armor_color', None):
        armor_color = tuple(m.net_armor_color[:3])
        draw_monster_armor(m, m_size, armor_color, wb)
    elif hasattr(m, 'armor') and m.armor:
        armor_color = m.armor.get("color", (150, 150, 150))
        draw_monster_armor(m, m_size, armor_color, wb)
    outline_color = m.color  # 各怪物自带主题色
    draw_monster_base(m, outline_color, width=2, batch=wb)
    draw_monster_body(m, wb)
    draw_monster_face(m, m_size, wb)
    # 武器颜色：远端怪物优先取主机广播的 net_weapon_color（修复远端怪物武器颜色不一致），
    # 本地怪物优先取实际携带武器颜色，未携带时按类名从 MONSTER_METADATA 兜底（默认灰）
    m_cls = m.__class__.__name__
    if getattr(m, 'net_weapon_color', None):
        w_color = tuple(m.net_weapon_color[:3])
    elif hasattr(m, 'weapon') and m.weapon:
        w_color = m.weapon.get("color", (180, 180, 180))
    else:
        w_color = MONSTER_METADATA.get(m_cls, {}).get("weapon_color", (180, 180, 180))
    if w_color is None:
        w_color = (180, 180, 180)
    draw_monster_weapon(m, m_size, w_color, wb)

    # 血条：BOSS 怪物不绘制头顶小血条（改由 draw_boss_hp_bar 在屏幕顶部绘制）
    is_boss = getattr(m, 'is_boss', False)
    if not is_boss:
        bar_w = 30
        hp_ratio = m.hp / m.max_hp
        bx = m.center_x - bar_w // 2
        by = m.center_y + m_size + 8
        if wb is not None:
            wb.rect(bx + bar_w // 2, by, bar_w, 4, arcade.color.DARK_RED)
            wb.rect(bx + bar_w * hp_ratio // 2, by, bar_w * hp_ratio, 4, arcade.color.RED)
        else:
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w // 2, by, bar_w, 4), arcade.color.DARK_RED)
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w * hp_ratio // 2, by, bar_w * hp_ratio, 4), arcade.color.RED)
    else:
        bar_w = 30
        bx = m.center_x - bar_w // 2
        by = m.center_y + m_size + 8

    # 阶段3 护盾词缀：头顶护盾条（实心填充，禁线框；尺寸/偏移/配色由 config 驱动）
    # 画在血条上方（by - OFFSET），与下方攻击冷却条不重叠
    # 主机/幽灵双读（同 net_armor_color 模式）：远端怪物优先读主机广播的
    # net_shield/net_max_shield（客户端幽灵只有 MONSTER_CONFIGS 默认值 shield=0，
    # 不读快照则精英护盾条在客户端永远不显示）；旧主机未下发该字段（值为
    # None）时回退读本地 shield/max_shield；本地怪物无 net_* 字段同样回退。
    shield = getattr(m, 'net_shield', None)
    if shield is None:
        shield = getattr(m, 'shield', 0)
    max_shield = getattr(m, 'net_max_shield', None)
    if max_shield is None:
        max_shield = getattr(m, 'max_shield', 0)
    shield_ratio = 0.0
    if shield > 0 and max_shield > 0:
        shield_ratio = shield / max_shield
    if shield_ratio > 0:
        shield_w = ELITE_SHIELD_BAR_WIDTH
        sy = by - ELITE_SHIELD_BAR_OFFSET
        sbx = m.center_x - shield_w // 2
        if wb is not None:
            wb.rect(sbx + shield_w // 2, sy, shield_w, ELITE_SHIELD_BAR_HEIGHT, arcade.color.DARK_BLUE)
            wb.rect(sbx + shield_w * shield_ratio // 2, sy,
                    shield_w * shield_ratio, ELITE_SHIELD_BAR_HEIGHT, ELITE_SHIELD_BAR_COLOR)
        else:
            arcade.draw_rect_filled(
                arcade.XYWH(sbx + shield_w // 2, sy, shield_w, ELITE_SHIELD_BAR_HEIGHT),
                arcade.color.DARK_BLUE)
            arcade.draw_rect_filled(
                arcade.XYWH(sbx + shield_w * shield_ratio // 2, sy,
                           shield_w * shield_ratio, ELITE_SHIELD_BAR_HEIGHT),
                ELITE_SHIELD_BAR_COLOR)

    # 攻击冷却条（血条上方）：_attack_delay 已由基类统一设置。
    # 三条取值路径（主机 / 远端新主机 / 远端旧主机）：
    # 1) 远端怪物且主机下发了归一化剩余比例 net_attack_cd（0~1）：直接用
    #    1 - ratio 作填充比例。该比例由客户端按 dt/attack_delay 线性衰减
    #    （game_view 客户端块），与主机 _attack_timer 曲线同源，不会出现脏值；
    # 2) 远端怪物但主机未下发该字段（旧主机）：退回裸计时器 net_attack_anim，
    #    分母优先用主机广播的 net_attack_delay（词缀/等级可能改过冷却长度），
    #    再退回本地 _attack_delay；
    # 3) 本地怪物：读 AI 实时 _attack_timer / _attack_delay（主机/solo 权威本地模拟）。
    net_cd = getattr(m, 'net_attack_cd', None)
    if net_cd is not None:
        cd_ratio = 1.0 - max(0.0, min(1.0, net_cd))
        show_cd = net_cd > 0.0
    else:
        atk_timer = getattr(m, 'net_attack_anim', getattr(m, '_attack_timer', 0.0))
        max_delay = getattr(m, 'net_attack_delay', None) or getattr(m, '_attack_delay', 1.0)
        show_cd = atk_timer > 0
        cd_ratio = 1.0 - (atk_timer / max_delay)
    cd_ratio = max(0.0, min(1.0, cd_ratio))  # 夹紧防脏进度条（比例越界时画满/画空）
    if show_cd:
        cy = by + 7
        if wb is not None:
            wb.rect(bx + bar_w // 2, cy, bar_w, 3, (40, 40, 40))
            wb.rect(bx + bar_w * cd_ratio // 2, cy, bar_w * cd_ratio, 3, arcade.color.ORANGE)
        else:
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w // 2, cy, bar_w, 3), (40, 40, 40))
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w * cd_ratio // 2, cy, bar_w * cd_ratio, 3), arcade.color.ORANGE)

    # 怪物标签（按类名映射中文名）
    label_y = by + 15
    monster_name = _MONSTER_NAMES.get(m_cls, m_cls)
    # 阶段3 精英词缀：头顶名加词缀前缀并转紫色（如「[狂暴] 僵尸」）
    # 远端快照怪物读 net_affix（客户端不跑 apply_affix）
    affix_id = getattr(m, 'affix', None) or getattr(m, 'net_affix', None)
    label_color = (255, 200, 200)
    if affix_id:
        affix_name = ELITE_AFFIXES.get(affix_id, {}).get("name", affix_id)
        monster_name = f"[{affix_name}]{monster_name}"
        label_color = ELITE_LABEL_COLOR
    view._world_labels.append((m.center_x, label_y, monster_name, label_color, 10))
    label_y += 13
    # 武器名标签：优先取快照同步的 net_weapon（远端怪物）→ 实际携带武器名 → 类名映射兜底
    net_weapon = getattr(m, 'net_weapon', None)
    if net_weapon:
        weapon_name = net_weapon
    elif hasattr(m, 'weapon') and m.weapon:
        weapon_name = m.weapon.get("name", "武器")
    else:
        weapon_name = _WEAPON_NAMES.get(m_cls, "武器")
    view._world_labels.append((m.center_x, label_y, weapon_name, (200, 200, 200), 9))
    label_y += 12
    # 护甲/头盔名标签：远端怪物优先取主机广播的 net_armor/net_helmet（修复远端无装备标签），
    # 本地怪物读携带装备 dict
    armor_name = getattr(m, 'net_armor', None)
    if armor_name is None and hasattr(m, 'armor') and m.armor:
        armor_name = m.armor.get("name", "护甲")
    if armor_name:
        view._world_labels.append((m.center_x, label_y, armor_name, (180, 180, 220), 9))
        label_y += 12
    helmet_name = getattr(m, 'net_helmet', None)
    if helmet_name is None and hasattr(m, 'helmet') and m.helmet:
        helmet_name = m.helmet.get("name", "头盔")
    if helmet_name:
        view._world_labels.append((m.center_x, label_y, helmet_name, (180, 220, 180), 9))


def _draw_skill_prompt(view, m):
    """绘制怪物技能提示文字（攻击时在头顶显示技能名称，持续1.5秒后消失）"""
    prompt_timer = getattr(m, '_skill_prompt_timer', 0)
    if prompt_timer <= 0:
        return
    text = getattr(m, '_skill_prompt_text', None)
    color = getattr(m, '_skill_prompt_color', (255, 255, 100))
    if not text:
        return
    # 文字在怪物头顶上方浮动（随 timer 逐渐上移，使用1.5秒计算）
    prompt_y = m.center_y + 30 + (1.5 - prompt_timer) * 20
    view._world_labels.append((m.center_x, prompt_y, text, color, 14))


def draw_action_timer(view):
    """绘制行动倒计时（space 主题，从 render_game 抽离以便单独调用/测试）。

    从 view.window.game_state 读取剩余时间，颜色随剩余时长变化：
    >60s 白色 / 30~60s 黄色 / <=30s 红色。
    """
    gs = view.window.game_state
    if not (hasattr(gs, 'action_time_remaining') and gs.action_time_remaining is not None):
        return
    remaining = gs.action_time_remaining
    minutes = int(remaining) // 60
    seconds = int(remaining) % 60
    if remaining > 60:
        time_color = arcade.color.WHITE
    elif remaining > 30:
        time_color = arcade.color.YELLOW
    else:
        time_color = arcade.color.RED
    view._hud_text("action_time",
                   f"行动时间: {minutes}:{seconds:02d}",
                   WINDOW_WIDTH // 2, WINDOW_HEIGHT - 30,
                   time_color, 14, anchor_x="center", bold=True)


def draw_level_hud(view):
    """绘制角色等级 HUD：等级文本 + 经验条 + 待选升级闪烁提示

    - 位置：左上角（等级文本 y=H-30、经验条 y=H-44；原右上角让位给小地图，
      HP/金币/武器等玩家状态已整体左移，等级 HUD 跟随并入左侧玩家信息区）；
    - 等级/经验随 _level_data 缓存刷新（经验发放时同步更新，见 game/entity_callbacks.py _award_exp）；
    - 待选升级（pending_choices>0）时在屏幕上方居中闪烁提示「按 TAB 选择加成」，
      升级面板（views/level_up_view.py）由 input_handler TAB 打开；
    - 无 player_id / 无等级数据时零开销跳过（客户端未初始化前安全）。
    """
    gs = view.window.game_state
    if not getattr(gs, "player_id", None):
        return
    ld = getattr(view, "_level_data", None)
    if not ld:
        return
    from config import exp_needed_for_level
    level = ld.get("level", 1)
    exp = ld.get("exp", 0)
    need = exp_needed_for_level(level)

    # 等级 + 经验文本（左上角，左对齐；位于 HP 上方，玩家信息区最顶部）
    if need == 0:
        lvl_txt = f"Lv.{level}（已满级）"
    else:
        lvl_txt = f"Lv.{level}  经验 {exp}/{need}"
    view._hud_text("lvl", lvl_txt, 10, WINDOW_HEIGHT - 30,
                   arcade.color.GOLD, 12, anchor_x="left", bold=True)

    # 经验条（未满级时绘制，左对齐紧贴文本下方）
    if need > 0:
        bar_w, bar_h = 170, 8
        bx = 10
        by = WINDOW_HEIGHT - 44
        arcade.draw_rect_filled(arcade.XYWH(bx + bar_w // 2, by + bar_h // 2, bar_w, bar_h),
                                (50, 55, 65))
        fill = min(1.0, exp / need)
        if fill > 0:
            arcade.draw_rect_filled(
                arcade.XYWH(bx + bar_w * fill // 2, by + bar_h // 2, bar_w * fill, bar_h),
                (110, 210, 130))

    # 待选升级提示（闪烁；按 TAB 打开升级面板）
    if ld.get("pending_choices", 0) > 0:
        blink = (getattr(view, "_frame", 0) // 25) % 2 == 0
        hint_color = arcade.color.GOLD if blink else arcade.color.YELLOW
        view._hud_text("lvl_pending", "升级！按 TAB 选择加成",
                       WINDOW_WIDTH // 2, WINDOW_HEIGHT - 120,
                       hint_color, 16, anchor_x="center", bold=True)


def draw_minimap(view):
    """绘制右上角小地图：房间/宝箱/撤离点（space 用发射台）+ 玩家位置

    - 位置：右上角 MINIMAP_SIZE x MINIMAP_SIZE 区域（MINIMAP_PADDING 边距），
      固定不透明实心填充（渲染铁律：禁空心/线框绘制防闪烁）；
    - 两种模式（M 键切换，见 input_handler minimap_zoom 绑定）：
      * 默认「周围视野」：以玩家为中心 ±MINIMAP_VIEW_RADIUS 的世界范围映射；
      * view._minimap_full=True 时「全图」：整张 MAP_WIDTH x MAP_HEIGHT 映射。
    - 数据源全部来自 view.map_data（rooms/chest_positions/evac_points/rocket_pads），
      与主渲染共用同一份地图数据，无需额外生成。
    """
    if not view.map_data or not view.player:
        return
    # 小地图左下角（arcade 左下原点，右上角区域 = 屏幕顶部右侧）
    mm_x = WINDOW_WIDTH - MINIMAP_SIZE - MINIMAP_PADDING
    mm_y = WINDOW_HEIGHT - MINIMAP_SIZE - MINIMAP_PADDING

    # 世界坐标 → 小地图坐标的映射范围（周围视野 / 全图）
    full = getattr(view, "_minimap_full", False)
    if full:
        min_x, min_y = 0.0, 0.0
        span_x, span_y = float(MAP_WIDTH), float(MAP_HEIGHT)
    else:
        px, py = view.player.center_x, view.player.center_y
        r = MINIMAP_VIEW_RADIUS
        min_x = max(0.0, px - r)
        min_y = max(0.0, py - r)
        span_x = min(MAP_WIDTH, px + r) - min_x
        span_y = min(MAP_HEIGHT, py + r) - min_y
    scale_x = MINIMAP_SIZE / span_x if span_x > 0 else 0
    scale_y = MINIMAP_SIZE / span_y if span_y > 0 else 0

    def _mm(wx, wy):
        """世界坐标 → 小地图局部坐标（未加 mm_x/mm_y 偏移）"""
        return (wx - min_x) * scale_x, (wy - min_y) * scale_y

    # 背景（不透明实心）
    arcade.draw_rect_filled(
        arcade.XYWH(mm_x + MINIMAP_SIZE / 2, mm_y + MINIMAP_SIZE / 2,
                    MINIMAP_SIZE, MINIMAP_SIZE),
        (18, 22, 18))

    # 边框（不透明实心：外圈画一圈深灰，四边各 2px）
    # 必须先画边框再画内容：若画在内容之后，196x196 内层背景会覆盖掉
    # 房间/宝箱/玩家（修复小地图空白 bug）
    arcade.draw_rect_filled(
        arcade.XYWH(mm_x + MINIMAP_SIZE / 2, mm_y + MINIMAP_SIZE / 2,
                    MINIMAP_SIZE, MINIMAP_SIZE),
        (95, 105, 100))
    arcade.draw_rect_filled(
        arcade.XYWH(mm_x + MINIMAP_SIZE / 2, mm_y + MINIMAP_SIZE / 2,
                    MINIMAP_SIZE - 4, MINIMAP_SIZE - 4),
        (18, 22, 18))

    # 房间（浅灰矩形，超出小地图范围的部分裁剪到边界内）
    for room in view.map_data.get("rooms", []):
        rx, ry = _mm(room.x, room.y)
        rw, rh = room.w * scale_x, room.h * scale_y
        # 裁剪：与小地图区域求交
        cx1 = max(rx, 0.0)
        cy1 = max(ry, 0.0)
        cx2 = min(rx + rw, MINIMAP_SIZE)
        cy2 = min(ry + rh, MINIMAP_SIZE)
        if cx2 > cx1 and cy2 > cy1:
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + (cx1 + cx2) / 2, mm_y + (cy1 + cy2) / 2,
                            cx2 - cx1, cy2 - cy1),
                (60, 66, 60))

    # 宝箱（黄色小方块）
    for cx, cy in view.map_data.get("chest_positions", []):
        sx, sy = _mm(cx, cy)
        if 0 <= sx <= MINIMAP_SIZE and 0 <= sy <= MINIMAP_SIZE:
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + sx, mm_y + sy, 4, 4),
                (230, 200, 60))

    # 撤离点（阶段2）：主撤离点按状态配色 + 放大标记（防守中/被拆额外加白色外圈告警）；
    # 无主撤离点时回退旧撤离圈坐标（space 主题改绘火箭发射台，青色）
    evac_point = getattr(view, "evac_point", None)
    if evac_point is not None:
        sx, sy = _mm(evac_point.x, evac_point.y)
        if 0 <= sx <= MINIMAP_SIZE and 0 <= sy <= MINIMAP_SIZE:
            if evac_point.state in ("defending", "destroyed"):
                # 白色实心外圈：先画大块再画内层状态色，形成醒目告警环
                arcade.draw_rect_filled(
                    arcade.XYWH(mm_x + sx, mm_y + sy, 11, 11),
                    (240, 240, 240))
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + sx, mm_y + sy, 7, 7),
                EVAC_STATE_COLORS.get(evac_point.state, (80, 230, 120)))
    else:
        evac_pts = view.map_data.get("evac_points", [])
        if view.map_data.get("theme") == "space":
            evac_pts = view.map_data.get("rocket_pads", [])
            evac_color = (120, 200, 255)
        else:
            evac_color = (80, 230, 120)
        for ex, ey in evac_pts:
            sx, sy = _mm(ex, ey)
            if 0 <= sx <= MINIMAP_SIZE and 0 <= sy <= MINIMAP_SIZE:
                arcade.draw_rect_filled(
                    arcade.XYWH(mm_x + sx, mm_y + sy, 4, 4),
                    evac_color)

    # BOSS 建筑（金色小方块）：沙漠金字塔 BOSS 区域在小地图上标记为金色
    boss_spawn = view.map_data.get("boss_spawn")
    if boss_spawn:
        sx, sy = _mm(boss_spawn[0], boss_spawn[1])
        if 0 <= sx <= MINIMAP_SIZE and 0 <= sy <= MINIMAP_SIZE:
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + sx, mm_y + sy, 6, 6),
                (255, 215, 0))

    # 阶段3 精英词缀怪（紫色实心圆点）：场上精英一眼可见，避免漏掉高价值目标。
    # 客户端读 net_is_elite（快照同步，见 views/game/network_sync.py）。
    for m in getattr(view, "monsters", []):
        if not getattr(m, "alive", False):
            continue
        if not (getattr(m, "is_elite", False) or getattr(m, "net_is_elite", False)):
            continue
        ex, ey = _mm(m.center_x, m.center_y)
        if 0 <= ex <= MINIMAP_SIZE and 0 <= ey <= MINIMAP_SIZE:
            arcade.draw_circle_filled(mm_x + ex, mm_y + ey,
                                      ELITE_MINIMAP_DOT_RADIUS, ELITE_MINIMAP_COLOR)

    # 阶段4 事件标记：空投补给箱（橙色）+ 商队交互点（青色），均为实心小方块。
    # 读 view.chests 里 is_airdrop 的箱（空投是运行时生成，map_data.chest_positions 不含）
    # 与 view.caravan_point（主机/客户端由 EVENT_START + FULL_STATE 同步）。
    for chest in getattr(view, "chests", []):
        if getattr(chest, "opened", False) or not getattr(chest, "is_airdrop", False):
            continue
        ax, ay = _mm(chest.center_x, chest.center_y)
        if 0 <= ax <= MINIMAP_SIZE and 0 <= ay <= MINIMAP_SIZE:
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + ax, mm_y + ay, EVENT_MINIMAP_MARK_SIZE, EVENT_MINIMAP_MARK_SIZE),
                EVENT_AIRDROP_MINIMAP_COLOR)
    caravan_point = getattr(view, "caravan_point", None)
    if caravan_point is not None:
        vx, vy = _mm(caravan_point.center_x, caravan_point.center_y)
        if 0 <= vx <= MINIMAP_SIZE and 0 <= vy <= MINIMAP_SIZE:
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + vx, mm_y + vy, EVENT_MINIMAP_MARK_SIZE, EVENT_MINIMAP_MARK_SIZE),
                EVENT_CARAVAN_MINIMAP_COLOR)

    # 玩家（白色实心方块，居中于小地图中心附近；小地图外不绘制）
    px, py = view.player.center_x, view.player.center_y
    sx, sy = _mm(px, py)
    if 0 <= sx <= MINIMAP_SIZE and 0 <= sy <= MINIMAP_SIZE:
        arcade.draw_rect_filled(
            arcade.XYWH(mm_x + sx, mm_y + sy, 5, 5),
            arcade.color.WHITE)

    # 联机远端玩家（橙色方块）：其他玩家的幽灵位置由主机快照权威同步
    # （客户端上报本人快照 → 主机更新幽灵 → 主机广播全房玩家位置），
    # 已撤离/阵亡（alive=False）的幽灵不显示，避免小地图残留静止标记。
    # solo 模式 remote_players 恒为空，零开销。
    for ghost in getattr(view, "remote_players", {}).values():
        if not getattr(ghost, "alive", True):
            continue
        gx, gy = _mm(ghost.center_x, ghost.center_y)
        if 0 <= gx <= MINIMAP_SIZE and 0 <= gy <= MINIMAP_SIZE:
            arcade.draw_rect_filled(
                arcade.XYWH(mm_x + gx, mm_y + gy, 5, 5),
                (255, 150, 60))


def draw_boss_hp_bar(view):
    """在屏幕顶部绘制 BOSS 血条（仅当 active_boss 存活时显示）

    - 血条宽度 400px，高度 20px，居中显示
    - 颜色渐变：血量高→绿，中→黄，低→红
    - 显示 BOSS 名称 + 血量百分比
    """
    boss = getattr(view, 'active_boss', None)
    if boss is None or not boss.alive:
        return
    # 血条参数
    bar_w = 400
    bar_h = 20
    bar_x = WINDOW_WIDTH // 2
    bar_y = WINDOW_HEIGHT - 30
    hp_ratio = max(0.0, min(1.0, boss.hp / boss.max_hp))
    # 颜色渐变：血量高→绿，中→黄，低→红
    if hp_ratio > 0.6:
        hp_color = arcade.color.GREEN
    elif hp_ratio > 0.3:
        hp_color = arcade.color.YELLOW
    else:
        hp_color = arcade.color.RED
    # 背景（深灰）
    arcade.draw_rect_filled(
        arcade.XYWH(bar_x, bar_y, bar_w, bar_h),
        (40, 40, 40))
    # 血量填充
    fill_w = bar_w * hp_ratio
    if fill_w > 0:
        arcade.draw_rect_filled(
            arcade.XYWH(bar_x - bar_w // 2 + fill_w // 2, bar_y, fill_w, bar_h),
            hp_color)
    # 边框（白色）
    arcade.draw_rect_outline(
        arcade.XYWH(bar_x, bar_y, bar_w, bar_h),
        arcade.color.WHITE, border_width=2)
    # BOSS 名称 + 血量百分比（用 _MONSTER_NAMES 中文映射，MONSTER_METADATA 无 name 键）
    boss_cls = boss.__class__.__name__
    boss_name = _MONSTER_NAMES.get(boss_cls, boss_cls)
    hp_text = f"{boss_name}  {int(hp_ratio * 100)}%"
    if not hasattr(draw_boss_hp_bar, '_txt'):
        draw_boss_hp_bar._txt = arcade.Text(
            "", bar_x, bar_y + bar_h // 2 + 2,
            arcade.color.WHITE, 12, anchor_x="center", anchor_y="bottom", bold=True)
    _t = draw_boss_hp_bar._txt
    _t.value = hp_text
    _t.position = (bar_x, bar_y + bar_h // 2 + 2)
    _t.draw()


# ===================== 阶段4：随机事件横幅 / 商队换购弹层 =====================

def draw_event_banner(view):
    """绘制开局随机事件横幅（阶段4）

    - 文案与显示时长由 game/map_events.event_banner() 决定（无事件/超时返回 None）；
    - 一律不透明实心矩形（底板 + 内层实心条当"边框"，禁 outline/线框绘制防闪烁）。
    """
    from game.map_events import event_banner

    text = event_banner(view)
    if not text:
        return
    half_w = min(EVENT_BANNER_MAX_HALF_W, _event_banner_text_width(text))
    cx = WINDOW_WIDTH // 2
    # 底板（实心）
    arcade.draw_rect_filled(
        arcade.XYWH(cx, EVENT_BANNER_Y, half_w * 2, EVENT_BANNER_HEIGHT), EVENT_BANNER_BG)
    # 内层实心高亮条（替代线框边框，视觉分隔仍清晰）
    arcade.draw_rect_filled(
        arcade.XYWH(cx, EVENT_BANNER_Y + EVENT_BANNER_HEIGHT / 2 - 3,
                    half_w * 2 - 8, 3), EVENT_BANNER_EDGE)
    if not hasattr(draw_event_banner, "_txt"):
        draw_event_banner._txt = arcade.Text(
            "", cx, EVENT_BANNER_Y, EVENT_BANNER_TEXT_COLOR, 15,
            anchor_x="center", anchor_y="center", bold=True)
    _t = draw_event_banner._txt
    _t.value = text
    _t.position = (cx, EVENT_BANNER_Y)
    _t.draw()


def _event_banner_text_width(text: str) -> int:
    """按字符数粗估中文/英文混排宽度（arcade.Text 尺寸需 GL 上下文，避免每帧重建）"""
    width = 0
    for ch in text:
        width += 15 if ord(ch) > 0x2E80 else 8
    return width + 40


def draw_caravan_panel(view):
    """绘制商队换购弹层（阶段4，仅 view._caravan_panel_open 时绘制）

    - 行内容：物品中文名 + 单价 + 当前携带金币 + 已持有数量；
    - 买不起的行用暗色，购买成功/失败由 map_events 弹浮动文字提示；
    - 客户端同样渲染（主机权威：客户端 E 键不生效，此处只做表现层同步）。
    """
    if not getattr(view, "_caravan_panel_open", False):
        return
    from game.map_events import caravan_label, caravan_prices

    items = caravan_prices()
    if not items:
        return
    gs = view.window.game_state
    carried = getattr(gs, "run_carried", None) or {}
    gold = int(carried.get("gold", 0) or 0)
    owned = dict(carried.get("potion") or {})
    for rid, qty in (carried.get("resource") or {}).items():
        owned[rid] = owned.get(rid, 0) + qty

    title = f"流浪商队  持有金币 {gold}"
    hint = "↑↓ 选择   E/回车 购买   走远自动关闭"
    panel_w = EVENT_PANEL_WIDTH
    panel_h = EVENT_PANEL_MARGIN * 2 + 30 + len(items) * EVENT_PANEL_ROW_H + 18
    px = WINDOW_WIDTH // 2
    py = WINDOW_HEIGHT // 2
    # 面板底（实心）
    arcade.draw_rect_filled(
        arcade.XYWH(px, py, panel_w, panel_h), EVENT_PANEL_BG)
    # 标题实心条
    arcade.draw_rect_filled(
        arcade.XYWH(px, py + panel_h / 2 - 24, panel_w - 8, 30), EVENT_PANEL_ROW_BG)
    _caravan_text(view, title, px, py + panel_h / 2 - 24, 14, EVENT_PANEL_TEXT_HL,
                  anchor_y="center", bold=True)
    rows_top = py + panel_h / 2 - 44
    sel = getattr(view, "_caravan_index", 0)
    for idx, (item_id, price) in enumerate(items.items()):
        ry = rows_top - idx * EVENT_PANEL_ROW_H
        row_w = panel_w - 16
        arcade.draw_rect_filled(
            arcade.XYWH(px, ry, row_w, EVENT_PANEL_ROW_H - 4),
            EVENT_PANEL_ROW_HL if idx == sel else EVENT_PANEL_ROW_BG)
        color = EVENT_PANEL_TEXT_HL if idx == sel else EVENT_PANEL_TEXT
        name = caravan_label(item_id)
        count = int(owned.get(item_id, 0) or 0)
        text = f"{name}   {price}金" + (f"（持有{count}）" if count else "")
        if price > gold:
            text += "  [金币不足]"
            color = (150, 110, 110) if idx != sel else EVENT_PANEL_TEXT_HL
        _caravan_text(view, text, px - row_w / 2 + 10, ry, 12, color,
                      anchor_x="left", anchor_y="center")
    _caravan_text(view, hint, px, py - panel_h / 2 + 16, 10, EVENT_PANEL_TEXT,
                  anchor_y="center")


def _caravan_text(view, text, x, y, size, color, anchor_x="center", anchor_y="center",
                  bold=False):
    """弹层文本绘制（arcade.Text 缓存复用，禁每帧重建纹理）"""
    cache = getattr(draw_caravan_panel, "_texts", None)
    if cache is None:
        cache = {}
        draw_caravan_panel._texts = cache
    key = (text, x, y, size, color, anchor_x, anchor_y, bold)
    t = cache.get(key)
    if t is None:
        # 缓存键过多时清空重建（弹层条目固定 7 项，不会膨胀）
        if len(cache) > 64:
            cache.clear()
        t = arcade.Text(text, x, y, color, size,
                        anchor_x=anchor_x, anchor_y=anchor_y, bold=bold)
        cache[key] = t
    t.value = text
    t.position = (x, y)
    t.draw()