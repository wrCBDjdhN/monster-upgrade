"""游戏渲染：on_draw 的完整渲染管线"""

import math
import arcade
from config import (
    WINDOW_WIDTH, WINDOW_HEIGHT, PLAYER_COLOR, PLAYER_SIZE,
    EVAC_COLOR, EVAC_RADIUS, DESERT_THEME,
    SPACE_THEME, ACTION_TIME_SPACE, ACTION_TIME_FOREST, ACTION_TIME_DESERT,
    MINIMAP_SIZE, MINIMAP_PADDING,
    DOWNED_TIMEOUT,
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
    draw_harvestable, draw_chest,
    draw_player_base_equipment, draw_player_weapon, draw_drop_icon,
)
from entities.weapon_defs import get_weapon_visual
from entities.equipment_defs import POTIONS
from game.entity_callbacks import get_drop_display_name
from db.database import get_gold, get_weapons
# HUD/小地图/BOSS血条/怪物绘制辅助：从 rendering_hud 导入（原同文件函数抽离）
from game.rendering_hud import (
    draw_action_timer, draw_level_hud, draw_minimap, draw_boss_hp_bar,
    _draw_monster, _draw_skill_prompt,
)


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

    # ── 静态地图几何缓存：背景+地板+墙壁在同一局内不变，仅在切图时重建 ──
    # 用 id(map_data) 作为缓存键，map_data 引用在 setup() 切图时改变
    _geo_cache_key = id(view.map_data)
    _geo_cache = getattr(view, '_static_geo_cache', None)
    if _geo_cache is not None and _geo_cache[0] == _geo_cache_key:
        # 命中缓存：直接绘制预构建的 ShapeElementList，跳过逐帧重建
        _geo_cache[1].draw()
    else:
        # 缓存未命中（首帧或切图）：构建静态几何并缓存
        try:
            from arcade import shape_list as _sl
            _static = _sl.ShapeElementList()
            # 主题配色
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
            # 背景矩形
            mw, mh = view.map_data["map_size"]
            _static.append(_sl.Shape(
                ((0, 0), (mw, 0), (mw, mh), (0, 0), (mw, mh), (0, mh)),
                (bg_color, bg_color, bg_color, bg_color, bg_color, bg_color),
                mode=4))  # TRIANGLES
            # 房间地板
            fc = (floor_color[0], floor_color[1], floor_color[2],
                  floor_color[3] if len(floor_color) > 3 else 255)
            for room in view.map_data["rooms"]:
                rx0, ry0 = room.x, room.y
                rx1, ry1 = rx0 + room.w, ry0 + room.h
                _static.append(_sl.Shape(
                    ((rx0, ry0), (rx1, ry0), (rx1, ry1),
                     (rx0, ry0), (rx1, ry1), (rx0, ry1)),
                    (fc, fc, fc, fc, fc, fc), mode=4))
            # 墙壁
            wc = (wall_color[0], wall_color[1], wall_color[2],
                  wall_color[3] if len(wall_color) > 3 else 255)
            for wx, wy, ww, wh in view.map_data["walls"]:
                if ww > 0 and wh > 0:
                    _static.append(_sl.Shape(
                        ((wx, wy), (wx + ww, wy), (wx + ww, wy + wh),
                         (wx, wy), (wx + ww, wy + wh), (wx, wy + wh)),
                        (wc, wc, wc, wc, wc, wc), mode=4))
            _static.draw()
            view._static_geo_cache = (_geo_cache_key, _static)
        except Exception:
            # ShapeElementList 构建失败（无窗口上下文），回退到即时模式
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
            if wb is not None:
                wb.rect(mw // 2, mh // 2, mw, mh, bg_color)
            else:
                arcade.draw_rect_filled(arcade.XYWH(mw // 2, mh // 2, mw, mh), bg_color)
            for room in view.map_data["rooms"]:
                if wb is not None:
                    wb.rect(room.x + room.w // 2, room.y + room.h // 2, room.w, room.h, floor_color)
                else:
                    arcade.draw_rect_filled(arcade.XYWH(room.x + room.w // 2, room.y + room.h // 2, room.w, room.h), floor_color)
            for wx, wy, ww, wh in view.map_data["walls"]:
                if ww > 0 and wh > 0:
                    if wb is not None:
                        wb.rect(wx + ww // 2, wy + wh // 2, ww, wh, wall_color)
                    else:
                        arcade.draw_rect_filled(arcade.XYWH(wx + ww // 2, wy + wh // 2, ww, wh), wall_color)

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
    if boss_spawn and view._in_view(boss_spawn[0], boss_spawn[1]):
        bx_, by_ = boss_spawn[0], boss_spawn[1]
        tri = [(bx_, by_ - 34), (bx_ - 14, by_ - 16), (bx_ + 14, by_ - 16)]
        if wb is not None:
            wb.poly(tri, (200, 160, 90))
        else:
            arcade.draw_polygon_filled(tri, (200, 160, 90))
        # minimap 图例标签：Boss 房间标记
        view._world_labels.append((bx_, by_ - 50, "Boss", (255, 215, 0), 12))

    # 怪物（本地权威 + 联机远端快照）
    for m in view.monsters:
        if hasattr(m, 'alive') and m.alive and view._in_view(m.center_x, m.center_y):
            _draw_monster(view, m, wb)
            # 技能提示文字（怪物攻击时在头顶显示技能名称）
            _draw_skill_prompt(view, m)

    # 远端怪物（联机客户端由 MONSTER_SNAPSHOT 维护的纯表现层实体）：
    # 修复「客户端看不到怪物」——之前只维护 remote_monsters 字典却从不绘制，
    # 导致主机在模拟 AI 攻击客户端幽灵而客户端视野里没有怪物。
    # 与本地怪物共用同一绘制函数（视口裁剪/血条/攻击冷却条/标签全部一致）。
    for m in view.remote_monsters.values():
        if hasattr(m, 'alive') and m.alive and view._in_view(m.center_x, m.center_y):
            _draw_monster(view, m, wb)
            _draw_skill_prompt(view, m)

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
        _beam.draw(wb)

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
        bar_w = 40
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

    # 倒地玩家渲染（联机模式）：半透明橙色 + 倒计时 + 救援提示
    downed_players = getattr(view, "_downed_players", {})
    for pid, dp in downed_players.items():
        dx, dy = dp["x"], dp["y"]
        timer = dp["timer"]
        if not view._in_view(dx, dy):
            continue
        # 倒地玩家半透明橙色方块
        alpha = int(180 * (timer / DOWNED_TIMEOUT))  # 越接近超时越透明
        downed_color = (255, 140, 0, alpha)
        if pb is not None:
            pb.rect(dx, dy, PLAYER_SIZE * 2, PLAYER_SIZE * 2, downed_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(dx, dy, PLAYER_SIZE * 2, PLAYER_SIZE * 2),
                                    downed_color)
        # 倒计时文字（头顶）
        timer_text = f"救援 {int(timer)}s"
        timer_color = arcade.color.GREEN if timer > 20 else (arcade.color.ORANGE if timer > 10 else arcade.color.RED)
        view._world_labels.append((dx, dy + PLAYER_SIZE + 18, timer_text, timer_color, 10))
        # "需要救援" 提示
        view._world_labels.append((dx, dy - PLAYER_SIZE - 10, "需要救援!", arcade.color.ORANGE, 9))

    # 救援进度条（本地玩家正在救援时显示）
    if getattr(view, "_rescuing", False) and view._rescue_target is not None:
        progress = getattr(view, "_rescue_progress", 0.0)
        bar_w = 80
        bar_h = 8
        bx = view.player.center_x - bar_w // 2
        by = view.player.center_y - PLAYER_SIZE - 20
        # 背景
        if pb is not None:
            pb.rect(bx + bar_w // 2, by, bar_w, bar_h, arcade.color.DARK_GRAY)
            pb.rect(bx + bar_w * progress // 2, by, bar_w * progress, bar_h, arcade.color.CYAN)
        else:
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w // 2, by, bar_w, bar_h), arcade.color.DARK_GRAY)
            arcade.draw_rect_filled(arcade.XYWH(bx + bar_w * progress // 2, by, bar_w * progress, bar_h), arcade.color.CYAN)
        # 救援文字
        view._world_labels.append((view.player.center_x, by - 12, "救援中...", arcade.color.CYAN, 10))

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

    # 玩家手持武器（观战模式不绘制：玩家已撤离/阵亡，武器残影会残留在撤离点，
    # 导致观战时人物看起来"不消失"）
    # 修复：武器图元此前被追加到世界层批次 wb（其 draw() 早已执行完毕、之后不再
    # 提交，导致武器永不显示），改为挂到玩家层批次 pb，并在 pb.draw() 之前提交，
    # 随玩家本体一起渲染（追加顺序在本体之后，武器绘制在本体之上）。
    if not getattr(view, "_spectating", False):
        w_item_id = getattr(view.window.game_state, 'current_weapon_item_id', None)
        w_color, w_shape, w_kind = get_weapon_visual(w_item_id)
        draw_player_weapon(view.player, w_kind, w_color, w_shape, pb)

    # 玩家层一次性绘制
    if pb is not None:
        try:
            pb.draw()
        except Exception:
            pb = None

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

    # BOSS 血条（屏幕顶部，仅当 BOSS 激活时显示）
    draw_boss_hp_bar(view)

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

    # HUD 文本（玩家状态/装备全部左对齐 x=10；从上到下：等级→HP→金币→武器→药水→效果→装备→debuff→技能）
    # 修复：观战模式下隐藏 HUD（HP/金币/武器/技能等），避免遮挡观战视线
    if not getattr(view, "_spectating", False):
        view._hud_text("hp", f"HP: {round(view.player.hp)}/{round(view.player.max_hp)}",
                       10, WINDOW_HEIGHT - 60, arcade.color.WHITE, 12)
        view._hud_text("gold", f"金币: {total_gold} (携带:{carried_gold})",
                       10, WINDOW_HEIGHT - 80, arcade.color.YELLOW, 12)
        # 已取消武器特殊效果显示（用户需求：左侧HUD简化，不再显示吸血/散射/光环等效果文本）
        view._hud_text("weapon",
                       f"武器: {view._cached_weapon_name} | 伤害:{round(gs.weapon_damage)} | 距离:{round(gs.weapon_range)}",
                       10, WINDOW_HEIGHT - 100, arcade.color.ORANGE, 12)
        view._hud_text("hint",
                       "WASD移动 | 鼠标攻击 | 靠近按E拾取物品 | 1-3药水 | E开宝箱 | TAB背包 | M地图 | ESC设置",
                       10, 10, arcade.color.GRAY, 12)

        # 角色技能栏（F 键）：技能名 + 冷却/就绪状态（无技能角色如"初始"不显示）
        # 位置：左侧 debuff 下方（原右侧右对齐，现随玩家状态全部左移，为小地图腾出右上角）
        skill_def = getattr(view.player, "character_def", {}).get("skill")
        if skill_def:
            skill_cd = max(0.0, getattr(view.player, "skill_cd", 0.0))
            if skill_cd > 0:
                skill_txt = f"技能[{skill_def['name']}] 冷却 {skill_cd:.1f}s"
                skill_color = arcade.color.ORANGE
            else:
                skill_txt = f"技能[{skill_def['name']}] 就绪 (F)"
                skill_color = arcade.color.GOLD
            view._hud_text("skill", skill_txt, 10, WINDOW_HEIGHT - 330,
                           skill_color, 11)
        else:
            # 空串也会重绘，保证切换角色后旧文本被清除
            view._hud_text("skill", "", 10, WINDOW_HEIGHT - 330,
                           arcade.color.GOLD, 11)

        # 药水显示：合并本局药水槽（run_potions）+ 仓库药水，顺序与热键 1-3 一致
        # （热键候选 = run 药水在前，仓库药水在后，见 input_handler.handle_key_press）
        run_potions = getattr(gs, "run_potions", None) or {}
        _pot_entries = []  # (名称, 数量)
        for item_id, qty in run_potions.items():
            if qty > 0:
                pdef = POTIONS.get(item_id, {})
                _pot_entries.append((pdef.get("name", item_id), qty))
        _pot_entries.extend((p["name"], p["quantity"]) for p in potions)
        if _pot_entries:
            view._hud_text("pot_title", "药水:", 10, WINDOW_HEIGHT - 122,
                           arcade.color.LIGHT_GRAY, 11)
            for i, (pname, pqty) in enumerate(_pot_entries[:3]):
                view._hud_text(f"pot{i}",
                               f"[{i+1}] {pname} x{pqty}",
                               70, WINDOW_HEIGHT - 122 - i * 15, arcade.color.CYAN, 11)

        # 药水/效果剩余时间显示（速度加速、护盾、狂暴、持续回复）
        eff_y = WINDOW_HEIGHT - 168
        if view.player.speed_effect_timer > 0:
            view._hud_text("eff_speed",
                           f"移速加速: {view.player.speed_effect_timer:.1f}s",
                           10, eff_y, arcade.color.CYAN, 11)
            eff_y -= 15
        if getattr(view.player, "shield_effect_timer", 0) > 0:
            view._hud_text("eff_shield",
                           f"护盾: {view.player.shield:.0f} ({view.player.shield_effect_timer:.1f}s)",
                           10, eff_y, (120, 160, 255), 11)
            eff_y -= 15
        if getattr(view.player, "power_effect_timer", 0) > 0:
            view._hud_text("eff_power",
                           f"狂暴: {view.player.power_effect_timer:.1f}s",
                           10, eff_y, (255, 120, 40), 11)
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
    # 位置：右上角小地图正下方（原 H-20 处让位给小地图），右对齐
    if gs.net_mode in ("host", "client"):
        if gs.net_mode == "host":
            n_players = max(1, len(getattr(gs, "net_roster", {}) or {}))
            net_txt = f"联机(主机) 房间:{getattr(gs, 'net_room_id', '?')} 玩家:{n_players}/{getattr(gs, 'net_max_players', '?')}"
            net_color = arcade.color.LIGHT_CYAN
        else:
            rtt_ms = getattr(view, "_net_rtt_ms", 0.0)
            net_txt = f"联机(客户端) 房间:{getattr(gs, 'net_room_id', '?')} RTT:{rtt_ms:.0f}ms"
            net_color = arcade.color.LIGHT_BLUE
        from config import MINIMAP_SIZE, MINIMAP_PADDING
        view._hud_text("netbar", net_txt, WINDOW_WIDTH - 10,
                       WINDOW_HEIGHT - MINIMAP_SIZE - MINIMAP_PADDING - 20,
                       net_color, 11, anchor_x="right")

    # 装备显示（左移布局：位于效果区下方，H-205 起往下排）
    equip_y = WINDOW_HEIGHT - 205
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

    # 玩家 debuff 显示（中毒/燃烧/冰冻/减速/眩晕，含剩余时间）
    _DEBUFF_NAMES = {"poison": "中毒", "burn": "燃烧", "freeze": "冰冻",
                     "slow": "减速", "stun": "眩晕"}
    _active_debuffs = getattr(view.player, "debuffs", None)
    if _active_debuffs:
        # 逐条显示效果名 + 剩余秒数（duration 由 _update_debuffs 每帧递减）
        _debuff_txt = " | ".join(
            f"{_DEBUFF_NAMES.get(d['id'], d['id'])}{d.get('duration', 0):.1f}s"
            for d in _active_debuffs
        )
        view._hud_text("debuffs", f"状态: {_debuff_txt}", 10, equip_y - 15,
                       arcade.color.RED_ORANGE, 11)
    else:
        # 空串也会重绘，保证状态消失后旧文本被清除
        view._hud_text("debuffs", "", 10, equip_y - 15,
                       arcade.color.RED_ORANGE, 11)

    # ── 右上角小地图（房间/宝箱/撤离点 + 玩家；M 键切换周围视野/全图）──
    draw_minimap(view)

    # 消息提示
    if view._message_timer > 0:
        view._hud_text("msg", view._message, WINDOW_WIDTH // 2, 40,
                       arcade.color.YELLOW, 13, anchor_x="center", bold=True)

    # 退出观战按钮（观战模式下右下角显示，点击返回大厅等待下一局）
    if getattr(view, "_spectating", False):
        exit_rect = getattr(view, "_exit_spectate_rect", None)
        if exit_rect is not None:
            is_hover = getattr(view, "_exit_spectate_hover", False)
            bg_color = (80, 80, 80, 220) if is_hover else (50, 50, 50, 200)
            arcade.draw_rect_filled(exit_rect, bg_color)
            arcade.draw_rect_outline(exit_rect, arcade.color.WHITE)
            view._hud_text("exit_spectate", "退出观战",
                           exit_rect.center_x, exit_rect.center_y,
                           arcade.color.WHITE, 14,
                           anchor_x="center", anchor_y="center", bold=True)

    # 火箭发射台交互提示（靠近且处于可交互状态时显示）
    for pad in getattr(view, 'rocket_pads', []):
        dist = math.hypot(pad.center_x - view.player.center_x,
                          pad.center_y - view.player.center_y)
        if dist < 100:  # 靠近时显示提示
            if pad.state == "idle":
                # 绘制背景框（坐标系为逻辑分辨率，最大化/全屏时由 main.GameWindow 缩放）
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, 80, 250, 40),
                    (0, 0, 0, 200))
                # 复用持久 Text 缓存（key=pad_hint，替代 draw_text 消除 PerformanceWarning）
                view._hud_text("pad_hint", "按 E 激活火箭发射台",
                               WINDOW_WIDTH // 2, 80,
                               arcade.color.YELLOW, 16,
                               anchor_x="center", anchor_y="center",
                               bold=True)
            elif pad.state == "boss_defeated":
                # 绘制背景框
                arcade.draw_rect_filled(
                    arcade.XYWH(WINDOW_WIDTH // 2, 80, 320, 40),
                    (0, 0, 0, 200))
                # 复用持久 Text 缓存（与上方共用 key=pad_hint，文本/颜色变化时自动重建）
                view._hud_text("pad_hint", "按 7 炸毁 | 按 8 启用撤离",
                               WINDOW_WIDTH // 2, 80,
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
                arcade.XYWH(WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60, 280, 50),
                (0, 0, 0, 200))
            # 绘制倒计时文字（<10秒变红）
            color = arcade.color.RED if countdown < 10 else arcade.color.GREEN
            # 复用持久 Text 缓存（key=pad_countdown，替代 draw_text 消除 PerformanceWarning）
            view._hud_text("pad_countdown", f"撤离倒计时: {mins}:{secs:02d}",
                           WINDOW_WIDTH // 2, WINDOW_HEIGHT - 60,
                           color, 22, anchor_x="center", anchor_y="center",
                           bold=True)
