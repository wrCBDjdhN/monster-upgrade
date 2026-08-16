"""游戏渲染：on_draw 的完整渲染管线"""

import math
import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, PLAYER_COLOR, PLAYER_SIZE,
    EVAC_COLOR, EVAC_RADIUS, DESERT_THEME,
    SPACE_THEME, ACTION_TIME_SPACE, ACTION_TIME_FOREST, ACTION_TIME_DESERT,
)

# 可破坏环境物中文名映射
_HARVEST_NAMES = {"tree": "树木", "ore": "矿石", "stone": "石头", "cactus": "仙人掌"}
from game.batch_shapes import ShapeBatch
from game.monsters import (
    Zombie, Skeleton, MummyMelee, MummyRanged, Camel,
    BossZombie, BossSkeleton, BossMummy,
    Sniper, Assault, Bandit, RocketTroop, BossSpace,
)
from game.sound_manager import sound_manager
from game.effects import particle_system, floating_texts
from game.render_helpers import (
    draw_monster_base, draw_monster_armor, draw_monster_face, draw_monster_weapon,
    draw_monster_body,
    draw_harvestable, draw_chest,
    draw_player_base_equipment, draw_player_weapon, draw_drop_icon,
)
from entities.weapon_defs import get_weapon_visual
# 怪物武器颜色从 monster_defs.py 统一读取（原 MONSTER_WEAPON_COLOR 已并入 MONSTER_METADATA）
from entities.monster_defs import MONSTER_METADATA
from game.entity_callbacks import get_drop_display_name
from db.database import get_gold, get_weapons

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

    - 本地怪物：weapon/armor 为携带装备 dict，攻击冷却条读本地 AI 的 _attack_timer；
    - 远端怪物（remote_monsters 里的真实怪物类实例）：weapon/armor 为 None，
      武器/护甲/头盔颜色与名称走主机广播的 net_weapon_color/net_armor_color/
      net_helmet_color 等快照值（修复远端怪物装备与主机不一致）；
      攻击冷却条读主机广播的 net_attack_anim（客户端不跑怪物 AI，_attack_timer 恒为 0）；
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

    # 血条
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

    # 攻击冷却条（血条上方）：_attack_delay 已由基类统一设置。
    # 远端怪物客户端不跑 AI，_attack_timer 恒 0，改读主机广播的 net_attack_anim 快照值
    atk_timer = getattr(m, 'net_attack_anim', getattr(m, '_attack_timer', 0.0))
    if atk_timer > 0:
        max_delay = getattr(m, '_attack_delay', 1.0)
        cd_ratio = 1.0 - (atk_timer / max_delay)
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
    view._world_labels.append((m.center_x, label_y, monster_name, (255, 200, 200), 10))
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


def render_game(view):
    """完整的渲染管线，对应原 GameView.on_draw 方法。"""
    view.clear()
    if not view.map_data or not view.player:
        return

    # 相机
    view.controller.use_camera()

    # 批量绘制
    try:
        wb = ShapeBatch()   # 世界层
        pb = ShapeBatch()   # 玩家层
    except Exception:
        wb = None
        pb = None

    # 主题配色：森林用默认字面色，沙漠用沙色系，space 用深空色系
    theme = view.map_data.get("theme", "forest")
    if theme == "desert":
        bg_color = DESERT_THEME["bg"]
        floor_color = DESERT_THEME["room_floor"]
        wall_color = DESERT_THEME["wall"]
    elif theme == "space":
        bg_color = SPACE_THEME["bg"]
        floor_color = SPACE_THEME["room_floor"]
        wall_color = SPACE_THEME["wall"]
    else:
        bg_color = (15, 18, 15)
        floor_color = (45, 50, 40)
        wall_color = (60, 60, 60)

    # 地图背景（深色虚空）
    if wb is not None:
        wb.rect(view.map_data["map_size"][0] // 2,
                view.map_data["map_size"][1] // 2,
                view.map_data["map_size"][0],
                view.map_data["map_size"][1], bg_color)
    else:
        arcade.draw_rect_filled(
            arcade.XYWH(view.map_data["map_size"][0] // 2,
                         view.map_data["map_size"][1] // 2,
                         view.map_data["map_size"][0],
                         view.map_data["map_size"][1]),
            bg_color,
        )

    # 房间地板（浅色矩形）
    for room in view.map_data["rooms"]:
        if wb is not None:
            wb.rect(room.x + room.w // 2, room.y + room.h // 2, room.w, room.h, floor_color)
        else:
            arcade.draw_rect_filled(
                arcade.XYWH(room.x + room.w // 2, room.y + room.h // 2, room.w, room.h),
                floor_color)

    # 墙壁（灰色矩形，有碰撞）
    for wx, wy, ww, wh in view.map_data["walls"]:
        if ww > 0 and wh > 0:
            if wb is not None:
                wb.rect(wx + ww // 2, wy + wh // 2, ww, wh, wall_color)
            else:
                arcade.draw_rect_filled(
                    arcade.XYWH(wx + ww // 2, wy + wh // 2, ww, wh), wall_color)

    # 可破坏环境物（树/矿石/石头）—— 按类型绘制独特造型
    for h in view.harvestables:
        if h.alive and view._in_view(h.center_x, h.center_y):
            draw_harvestable(h, wb)
            # HP 条
            bar_w = 24
            hp_ratio = h.hp / h.max_hp
            bx = h.center_x - bar_w // 2
            by = h.center_y + 18
            if wb is not None:
                wb.rect(bx + bar_w // 2, by, bar_w, 3, arcade.color.DARK_RED)
                wb.rect(bx + bar_w * hp_ratio // 2, by, bar_w * hp_ratio, 3, arcade.color.GREEN)
            else:
                arcade.draw_rect_filled(arcade.XYWH(bx + bar_w // 2, by, bar_w, 3), arcade.color.DARK_RED)
                arcade.draw_rect_filled(arcade.XYWH(bx + bar_w * hp_ratio // 2, by, bar_w * hp_ratio, 3), arcade.color.GREEN)
            # 资源名称标签
            h_name = _HARVEST_NAMES.get(h.resource_type, h.resource_type)
            view._world_labels.append((h.center_x, h.center_y + 25, h_name, (200, 255, 200), 9))

    # 火箭发射台（传入 wb 批次，随世界层一起绘制，避免被随后刷新的地板/房间覆盖）
    _PAD_STATE_TEXT = {
        "idle": ("火箭发射台", "按E激活", (255, 255, 100)),
        "activated": ("发射台已激活", "BOSS即将出现!", (255, 200, 50)),
        "boss_spawned": ("发射台", "BOSS出现! 击败它!", (255, 80, 30)),
        "boss_defeated": ("发射台已瘫痪", "7=炸毁 8=启用撤离", (100, 255, 100)),
    }
    for pad in getattr(view, 'rocket_pads', []):
        pad.draw(wb)
        info = _PAD_STATE_TEXT.get(pad.state)
        if info:
            title, desc, color = info
            view._world_labels.append((pad.center_x, pad.center_y - 30, title, color, 11))
            view._world_labels.append((pad.center_x, pad.center_y - 44, desc, (255, 255, 255), 9))
        elif pad.state == "evacuating":
            # 撤离中：从 pad 对象读取倒计时
            remaining = int(pad.get_countdown())
            mins = remaining // 60
            secs = remaining % 60
            view._world_labels.append((pad.center_x, pad.center_y - 30,
                                       "发射台运行中", (0, 255, 100), 11))
            view._world_labels.append((pad.center_x, pad.center_y - 44,
                                       f"剩余撤离时间: {mins}:{secs:02d}", (255, 255, 255), 9))

    # 宝箱
    for chest in view.chests:
        if not chest.opened and view._in_view(chest.center_x, chest.center_y):
            draw_chest(chest, wb)
            # 靠近宝箱时显示按E提示
            dist = math.hypot(chest.center_x - view.player.center_x,
                              chest.center_y - view.player.center_y)
            if dist < 60:
                view._world_labels.append((chest.center_x, chest.center_y - 20,
                                          "按E打开", (255, 220, 80), 10))

    # 掉落物（资源 + 怪物掉落）—— 图标化 + 文字标签
    for d in view.drops:
        if view._in_view(d.center_x, d.center_y):
            draw_drop_icon(d, wb)
            drop_name = get_drop_display_name(d)
            if d.quantity > 1:
                drop_name = f"{drop_name}x{d.quantity}"
            view._world_labels.append((d.center_x, d.center_y - 16, drop_name, arcade.color.WHITE, 9))

    # 撤离点（不透明实心圆环）
    for s in view.evac_sprites:
        cx, cy = s.center_x, s.center_y
        ring = view._evac_unit_ring
        if ring is None:
            outer, inner = EVAC_RADIUS, EVAC_RADIUS - 4
            seg = 48
            ring = []
            for k in range(seg):
                a = 2 * math.pi * k / seg
                ring.append((math.cos(a) * outer, math.sin(a) * outer))
                ring.append((math.cos(a) * inner, math.sin(a) * inner))
            view._evac_unit_ring = ring
        shifted = [(px_ + cx, py_ + cy) for px_, py_ in ring]
        if wb is not None:
            wb.poly(shifted, EVAC_COLOR)
        else:
            arcade.draw_polygon_filled(shifted, EVAC_COLOR)

    # 水井（沙漠地图：灰色圆井口 + 井沿矩形）
    well = view.map_data.get("water_well")
    if well and view._in_view(well[0], well[1]):
        wx_, wy_ = well[0], well[1]
        if wb is not None:
            wb.circle(wx_, wy_, 16, (110, 120, 130))     # 井口外沿
            wb.circle(wx_, wy_, 10, (40, 45, 55))        # 井内深色
        else:
            arcade.draw_circle_filled(wx_, wy_, 16, (110, 120, 130))
            arcade.draw_circle_filled(wx_, wy_, 10, (40, 45, 55))
        view._world_labels.append((wx_, wy_ - 24, "水井", (180, 220, 255), 10))
        # 靠近水井时显示按E提示（与宝箱「按E打开」提示一致，提示交互可用）
        dist = math.hypot(wx_ - view.player.center_x, wy_ - view.player.center_y)
        if dist < 60:
            view._world_labels.append((wx_, wy_ + 24,
                                      "按E使用", (180, 220, 255), 10))

    # 金字塔 BOSS 建筑塔尖标记（装饰性，建筑本体由 walls 渲染）
    boss_spawn = view.map_data.get("boss_spawn")
    if boss_spawn and theme == "desert" and view._in_view(boss_spawn[0], boss_spawn[1]):
        bx_, by_ = boss_spawn[0], boss_spawn[1]
        tri = [(bx_, by_ - 34), (bx_ - 14, by_ - 16), (bx_ + 14, by_ - 16)]
        if wb is not None:
            wb.poly(tri, (200, 160, 90))
        else:
            arcade.draw_polygon_filled(tri, (200, 160, 90))

    # 怪物（本地权威 + 联机远端快照）
    for m in view.monsters:
        if hasattr(m, 'alive') and m.alive and view._in_view(m.center_x, m.center_y):
            _draw_monster(view, m, wb)

    # 远端怪物（联机客户端由 MONSTER_SNAPSHOT 维护的纯表现层实体）：
    # 修复「客户端看不到怪物」——之前只维护 remote_monsters 字典却从不绘制，
    # 导致主机在模拟 AI 攻击客户端幽灵而客户端视野里没有怪物。
    # 与本地怪物共用同一绘制函数（视口裁剪/血条/攻击冷却条/标签全部一致）。
    for m in view.remote_monsters.values():
        if hasattr(m, 'alive') and m.alive and view._in_view(m.center_x, m.center_y):
            _draw_monster(view, m, wb)

    # 世界层一次性绘制
    if wb is not None:
        try:
            wb.draw()
        except Exception:
            wb = None

    # 骷髅弹丸
    view.skeleton_projectiles.draw()

    # 远端怪物弹丸（联机客户端）：由主机 PROJECTILE_SNAPSHOT 维护的纯表现层精灵列表；
    # 空列表绘制安全（solo 模式始终为空，零开销），弹丸命中消失由快照缺失自动删除
    view.remote_projectiles.draw()

    # 远端激光（联机客户端）：由主机 PROJECTILE_SNAPSHOT 的 lasers 段维护的纯表现层激光，
    # 与远端弹丸同口径（跳过自己发射的激光，本地已有 combat.spawn_laser 表现），
    # 快照缺失自动删除；solo 模式为空字典零开销（修复客户端看不到主机激光）
    for _beam in view.remote_lasers.values():
        _beam.draw()

    # 玩家弹丸
    view.combat.draw()

    # 玩家本体 + 装备底层 + 血条：观战模式（主机已撤离/阵亡）不再绘制，人物从地图消失
    # （修复：之前装备底层 draw_player_base_equipment 在观战判断外无条件绘制，
    #   主机撤离后装备底层残影仍留在撤离点，观战时人物看起来"不消失"）
    if not getattr(view, "_spectating", False):
        # 玩家装备底层（仅在非观战模式绘制，随本体一起隐藏）
        draw_player_base_equipment(
            view.player,
            view._cached_equip if view._cached_equip is not None else {},
            pb,
        )
        px, py = view.player.center_x, view.player.center_y
        if view._player_hit_flash > 0:
            body_color = (255, 80, 80)
        elif view._player_attack_flash > 0:
            body_color = (255, 255, 150)
        else:
            body_color = PLAYER_COLOR
        if pb is not None:
            pb.rect(px, py, view.player.width, view.player.height, body_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(px, py, view.player.width, view.player.height), body_color)

        # 玩家血条
        bar_w = 40
        hp_ratio = view.player.hp / view.player.max_hp
        bx = view.player.center_x - bar_w // 2
        by = view.player.center_y + PLAYER_SIZE + 10
        if pb is not None:
            pb.rect(bx + bar_w // 2, by, bar_w, 5, arcade.color.DARK_RED)
            pb.rect(bx + bar_w * hp_ratio // 2, by, bar_w * hp_ratio, 5, arcade.color.GREEN)
        else:
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w // 2, by, bar_w, 5), arcade.color.DARK_RED)
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w * hp_ratio // 2, by, bar_w * hp_ratio, 5), arcade.color.GREEN)

    # 远端玩家幽灵（联机）：主机渲染客户端幽灵 / 客户端渲染主机幽灵，全房玩家可见。
    # 纯表现层：位置/朝向/HP 由 PLAYER_SNAPSHOT 维护；不透明实心填充（禁空心线框）；
    # 死亡幽灵（alive=False / hp<=0）不再绘制
    for pid, ghost in view.remote_players.items():
        if not getattr(ghost, "alive", True):
            continue  # 死亡幽灵：消失（快照 alive=False 时 hp 已归零）
        if not view._in_view(ghost.center_x, ghost.center_y):
            continue  # 视口裁剪：图元数随房间玩家数线性增长，裁剪省绘制
        g_color = (100, 160, 220)  # 幽灵色（区别于本地玩家 PLAYER_COLOR 的绿色系）
        if pb is not None:
            pb.rect(ghost.center_x, ghost.center_y, ghost.width, ghost.height, g_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(ghost.center_x, ghost.center_y,
                                                ghost.width, ghost.height), g_color)
        # 幽灵血条
        g_hp = getattr(ghost, "hp", 0)
        g_max = max(1, int(getattr(ghost, "max_hp", 0) or 1))
        g_ratio = max(0.0, min(1.0, g_hp / g_max))
        gbx = ghost.center_x - bar_w // 2
        gby = ghost.center_y + PLAYER_SIZE + 10
        if pb is not None:
            pb.rect(gbx + bar_w // 2, gby, bar_w, 5, arcade.color.DARK_RED)
            pb.rect(gbx + bar_w * g_ratio // 2, gby, bar_w * g_ratio, 5, arcade.color.GREEN)
        else:
            arcade.draw_rect_filled(arcade.XYWH(gbx + bar_w // 2, gby, bar_w, 5), arcade.color.DARK_RED)
            arcade.draw_rect_filled(arcade.XYWH(gbx + bar_w * g_ratio // 2, gby, bar_w * g_ratio, 5), arcade.color.GREEN)
        # 幽灵头顶名称（名册驱动，经 _world_labels 世界坐标→屏幕坐标渲染）
        gname = getattr(ghost, "net_name", f"玩家{pid}")
        view._world_labels.append((ghost.center_x, ghost.center_y + PLAYER_SIZE + 18,
                                   gname, arcade.color.LIGHT_BLUE, 9))

    # 近战攻击范围可视化（挥砍刀光）
    if view._attack_visual:
        px, py, mx, my, atk_range, timer = view._attack_visual
        base_deg = math.degrees(math.atan2(my - py, mx - px))
        total = 0.2
        progress = max(0.0, min(1.0, (total - timer) / total))
        sweep = -60.0 + 120.0 * progress
        ghosts = (
            (50.0, 4, (255, 200, 80, 35)),
            (35.0, 6, (255, 220, 100, 60)),
            (20.0, 9, (255, 235, 150, 100)),
        )
        for lag, bw, g_color in ghosts:
            ga = math.radians(base_deg + sweep - lag)
            gx = px + math.cos(ga) * atk_range
            gy = py + math.sin(ga) * atk_range
            if pb is not None:
                pb.line(px, py, gx, gy, g_color, bw)
            else:
                arcade.draw_line(px, py, gx, gy, g_color, bw)
        # 主刀刃
        ma = math.radians(base_deg + sweep)
        ex = px + math.cos(ma) * atk_range
        ey = py + math.sin(ma) * atk_range
        if pb is not None:
            pb.line(px, py, ex, ey, (255, 255, 220, 240), 14)
        else:
            arcade.draw_line(px, py, ex, ey, (255, 255, 220, 240), 14)

    # 玩家层一次性绘制
    if pb is not None:
        try:
            pb.draw()
        except Exception:
            pb = None

    # 玩家手持武器（观战模式不绘制：玩家已撤离/阵亡，武器残影会残留在撤离点，
    # 导致观战时人物看起来"不消失"）
    if not getattr(view, "_spectating", False):
        w_item_id = getattr(view.window.game_state, 'current_weapon_item_id', None)
        w_color, w_shape, w_kind = get_weapon_visual(w_item_id)
        draw_player_weapon(view.player, w_kind, w_color, w_shape, wb)

    # 粒子效果
    particle_system.draw()

    # 漂浮文字
    floating_texts.draw()

    # 撤离读条（观战模式不绘制：玩家已撤离，撤离进度条不应残留在原地）
    if not getattr(view, "_spectating", False):
        view.evac.draw_progress(view.player)

    # ── HUD（屏幕固定位置）──
    view.window.default_camera.use()
    gs = view.window.game_state

    # 绘制世界坐标标签
    cam = view.controller.camera.position
    # 复用持久 Text 缓存（key 用 f"wl_{i}" 索引，替代每帧 draw_text 消除 PerformanceWarning）
    for i, (wx, wy, text, color, font_size) in enumerate(view._world_labels):
        sx = wx - cam.x + WINDOW_WIDTH / 2
        sy = wy - cam.y + WINDOW_HEIGHT / 2
        if -50 < sx < WINDOW_WIDTH + 50 and -50 < sy < WINDOW_HEIGHT + 50:
            view._hud_text(f"wl_{i}", text, sx, sy, color, font_size,
                           anchor_x="center", anchor_y="center", bold=True)
    view._world_labels.clear()

    # 节流刷新缓存（每 15 帧约 4 次/秒）
    if view._frame - view._cache_frame >= 15:
        view._cache_frame = view._frame
        if gs.player_id:
            view._cached_db_gold = get_gold(gs.player_id)
            from db.database import get_potions, get_equipment
            view._cached_potions = get_potions(gs.player_id)
            view._cached_equip = get_equipment(gs.player_id)
            # 合并局内免费拾取的装备（不在数据库中，需从 GameState 补充到 HUD 缓存）
            _free_ids = getattr(gs, 'free_equipped_item_ids', set())
            if _free_ids:
                from entities.equipment_defs import HELMETS, ARMORS, BACKPACKS
                _h_id = getattr(gs, 'equipped_helmet_id', None)
                if _h_id and _h_id in _free_ids and "helmet" not in view._cached_equip:
                    _ed = HELMETS.get(_h_id, {})
                    view._cached_equip["helmet"] = {
                        "id": None, "item_id": _h_id,
                        "name": _ed.get("name", "头盔"), "defense": _ed.get("defense", 0),
                        "capacity": 0, "level": 1, "effects": []}
                _a_id = getattr(gs, 'equipped_armor_id', None)
                if _a_id and _a_id in _free_ids and "armor" not in view._cached_equip:
                    _ed = ARMORS.get(_a_id, {})
                    view._cached_equip["armor"] = {
                        "id": None, "item_id": _a_id,
                        "name": _ed.get("name", "护甲"), "defense": _ed.get("defense", 0),
                        "capacity": 0, "level": 1, "effects": []}
                _b_id = getattr(gs, 'equipped_backpack_id', None)
                if _b_id and _b_id in _free_ids and "backpack" not in view._cached_equip:
                    _bd = BACKPACKS.get(_b_id, {})
                    view._cached_equip["backpack"] = {
                        "id": None, "item_id": _b_id,
                        "name": _bd.get("name", "背包"), "defense": 0,
                        "capacity": _bd.get("capacity", 0), "level": 1, "effects": []}

    db_gold = view._cached_db_gold if view._cached_db_gold is not None else 0
    potions = view._cached_potions if view._cached_potions is not None else []
    equip = view._cached_equip if view._cached_equip is not None else {}
    carried_gold = gs.run_carried.get("gold", 0) if hasattr(gs, 'run_carried') else 0
    total_gold = db_gold + carried_gold

    # 缓存武器名称
    if not hasattr(view, '_cached_weapon_name') or view._cached_weapon_id != gs.current_weapon_id:
        view._cached_weapon_id = gs.current_weapon_id
        view._cached_weapon_name = "拳头"
        if gs.current_weapon_id:
            weapons = get_weapons(gs.player_id) if gs.player_id else []
            for w in weapons:
                if w["id"] == gs.current_weapon_id:
                    view._cached_weapon_name = w["name"]
                    break

    # HUD 文本
    view._hud_text("hp", f"HP: {round(view.player.hp)}/{round(view.player.max_hp)}",
                   10, WINDOW_HEIGHT - 30, arcade.color.WHITE, 12)
    view._hud_text("gold", f"金币: {total_gold} (携带:{carried_gold})",
                   10, WINDOW_HEIGHT - 50, arcade.color.YELLOW, 12)
    wep_effect_txt = ""
    if getattr(view, "_cached_weapon_effects", "无") != "无":
        wep_effect_txt = f" | 效果:{view._cached_weapon_effects}"
    view._hud_text("weapon",
                   f"武器: {view._cached_weapon_name} | 伤害:{round(gs.weapon_damage)} | 距离:{round(gs.weapon_range)}{wep_effect_txt}",
                   10, WINDOW_HEIGHT - 70, arcade.color.ORANGE, 12)
    view._hud_text("hint",
                   "WASD移动 | 鼠标攻击 | 靠近按E拾取物品 | 1-3药水 | E开宝箱 | TAB背包",
                   10, 10, arcade.color.GRAY, 12)

    # 角色技能栏（F 键）：技能名 + 冷却/就绪状态（无技能角色如"初始"不显示）
    # 位置：右侧右对齐（WINDOW_WIDTH-10, -85），避免夹在左侧武器/药水提示之间
    skill_def = getattr(view.player, "character_def", {}).get("skill")
    if skill_def:
        skill_cd = max(0.0, getattr(view.player, "skill_cd", 0.0))
        if skill_cd > 0:
            skill_txt = f"技能[{skill_def['name']}] 冷却 {skill_cd:.1f}s"
            skill_color = arcade.color.ORANGE
        else:
            skill_txt = f"技能[{skill_def['name']}] 就绪 (F)"
            skill_color = arcade.color.GOLD
        view._hud_text("skill", skill_txt, WINDOW_WIDTH - 10, WINDOW_HEIGHT - 85,
                       skill_color, 11, anchor_x="right")
    else:
        # 空串也会重绘，保证切换角色后旧文本被清除
        view._hud_text("skill", "", WINDOW_WIDTH - 10, WINDOW_HEIGHT - 85,
                       arcade.color.GOLD, 11, anchor_x="right")

    # 药水显示
    if potions:
        view._hud_text("pot_title", "药水:", 10, WINDOW_HEIGHT - 90,
                       arcade.color.LIGHT_GRAY, 11)
        for i, pot in enumerate(potions[:3]):
            view._hud_text(f"pot{i}",
                           f"[{i+1}] {pot['name']} x{pot['quantity']}",
                           70, WINDOW_HEIGHT - 90 - i * 15, arcade.color.CYAN, 11)

    # 药水/效果剩余时间显示（速度加速、持续回复）
    eff_y = WINDOW_HEIGHT - 138
    if view.player.speed_effect_timer > 0:
        view._hud_text("eff_speed",
                       f"移速加速: {view.player.speed_effect_timer:.1f}s",
                       10, eff_y, arcade.color.CYAN, 11)
        eff_y -= 15
    if view.player.heal_duration > 0:
        view._hud_text("eff_heal",
                       f"回复中: {view.player.heal_duration:.1f}s",
                       10, eff_y, arcade.color.GREEN, 11)

    # ── 行动倒计时（space 主题）──
    draw_action_timer(view)

    # ── 角色等级 HUD（等级/经验条/待选升级提示）──
    draw_level_hud(view)

    # ── 联机状态条 E3（host/client 显示；solo 不绘制）──
    # 位置：右上角，显示房间号/玩家数/RTT；solo 模式零开销（不进入分支）
    if gs.net_mode in ("host", "client"):
        if gs.net_mode == "host":
            n_players = max(1, len(getattr(gs, "net_roster", {}) or {}))
            net_txt = f"联机(主机) 房间:{getattr(gs, 'net_room_id', '?')} 玩家:{n_players}/{getattr(gs, 'net_max_players', '?')}"
            net_color = arcade.color.LIGHT_CYAN
        else:
            rtt_ms = getattr(view, "_net_rtt_ms", 0.0)
            net_txt = f"联机(客户端) 房间:{getattr(gs, 'net_room_id', '?')} RTT:{rtt_ms:.0f}ms"
            net_color = arcade.color.LIGHT_BLUE
        view._hud_text("netbar", net_txt, WINDOW_WIDTH - 10, WINDOW_HEIGHT - 20,
                       net_color, 11, anchor_x="right")

    # 装备显示
    equip_y = WINDOW_HEIGHT - 160
    if "helmet" in equip:
        view._hud_text("eq_helmet", f"头盔: {equip['helmet']['name']}", 10,
                       equip_y, arcade.color.LIGHT_BLUE, 11)
        equip_y -= 15
    if "armor" in equip:
        view._hud_text("eq_armor", f"护甲: {equip['armor']['name']}", 10,
                       equip_y, arcade.color.LIGHT_BLUE, 11)
        equip_y -= 15
    if "backpack" in equip:
        from game.loot import _calc_carried_capacity
        used_cap = _calc_carried_capacity(gs.run_carried) if hasattr(gs, 'run_carried') else 0
        total_cap = equip['backpack']['capacity']
        view._hud_text("eq_pack",
                       f"背包: {equip['backpack']['name']} ({used_cap}/{total_cap})",
                       10, equip_y, arcade.color.LIGHT_BLUE, 11)

    # 玩家 debuff 显示（中毒/燃烧/冰冻/减速/眩晕）
    _DEBUFF_NAMES = {"poison": "中毒", "burn": "燃烧", "freeze": "冰冻",
                     "slow": "减速", "stun": "眩晕"}
    _active_debuffs = view.player.get_active_debuffs()
    if _active_debuffs:
        _debuff_txt = " | ".join(_DEBUFF_NAMES.get(d, d) for d in _active_debuffs)
        view._hud_text("debuffs", f"状态: {_debuff_txt}", 10, equip_y - 15,
                       arcade.color.RED_ORANGE, 11)
    else:
        # 空串也会重绘，保证状态消失后旧文本被清除
        view._hud_text("debuffs", "", 10, equip_y - 15,
                       arcade.color.RED_ORANGE, 11)

    # 消息提示
    if view._message_timer > 0:
        view._hud_text("msg", view._message, WINDOW_WIDTH // 2, 40,
                       arcade.color.YELLOW, 13, anchor_x="center", bold=True)

    # 火箭发射台交互提示（靠近且处于可交互状态时显示）
    for pad in getattr(view, 'rocket_pads', []):
        dist = math.hypot(pad.center_x - view.player.center_x,
                          pad.center_y - view.player.center_y)
        if dist < 100:  # 靠近时显示提示
            if pad.state == "idle":
                # 绘制背景框
                arcade.draw_rect_filled(
                    arcade.XYWH(view.window.width // 2, 80, 250, 40),
                    (0, 0, 0, 200))
                # 复用持久 Text 缓存（key=pad_hint，替代 draw_text 消除 PerformanceWarning）
                view._hud_text("pad_hint", "按 E 激活火箭发射台",
                               view.window.width // 2, 80,
                               arcade.color.YELLOW, 16,
                               anchor_x="center", anchor_y="center",
                               bold=True)
            elif pad.state == "boss_defeated":
                # 绘制背景框
                arcade.draw_rect_filled(
                    arcade.XYWH(view.window.width // 2, 80, 320, 40),
                    (0, 0, 0, 200))
                # 复用持久 Text 缓存（与上方共用 key=pad_hint，文本/颜色变化时自动重建）
                view._hud_text("pad_hint", "按 7 炸毁 | 按 8 启用撤离",
                               view.window.width // 2, 80,
                               arcade.color.GREEN, 16,
                               anchor_x="center", anchor_y="center",
                               bold=True)
        # 撤离倒计时显示
        elif pad.state == "evacuating":
            countdown = pad.get_countdown()
            mins = int(countdown) // 60
            secs = int(countdown) % 60
            # 绘制倒计时背景
            arcade.draw_rect_filled(
                arcade.XYWH(view.window.width // 2, view.window.height - 60, 280, 50),
                (0, 0, 0, 200))
            # 绘制倒计时文字（<10秒变红）
            color = arcade.color.RED if countdown < 10 else arcade.color.GREEN
            # 复用持久 Text 缓存（key=pad_countdown，替代 draw_text 消除 PerformanceWarning）
            view._hud_text("pad_countdown", f"撤离倒计时: {mins}:{secs:02d}",
                           view.window.width // 2, view.window.height - 60,
                           color, 22, anchor_x="center", anchor_y="center",
                           bold=True)


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

    - 位置：右上角（联机状态条 E3 下方），等级/经验随 _level_data 缓存刷新
      （经验发放时同步更新，见 game/entity_callbacks.py _award_exp）；
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

    # 等级 + 经验文本（右上角，右对齐；位于联机状态条 E3 下方）
    if need == 0:
        lvl_txt = f"Lv.{level}（已满级）"
    else:
        lvl_txt = f"Lv.{level}  经验 {exp}/{need}"
    view._hud_text("lvl", lvl_txt, WINDOW_WIDTH - 10, WINDOW_HEIGHT - 50,
                   arcade.color.GOLD, 12, anchor_x="right", bold=True)

    # 经验条（未满级时绘制，右对齐紧贴文本下方）
    if need > 0:
        bar_w, bar_h = 170, 8
        bx = WINDOW_WIDTH - 10 - bar_w
        by = WINDOW_HEIGHT - 62
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
