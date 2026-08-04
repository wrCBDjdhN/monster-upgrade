"""游戏内图像增强绘制辅助：怪物造型、资源物造型、玩家/怪物武器造型。

核心原则：所有绘制一律使用【不透明实心填充】。严禁使用 draw_*_outline /
draw_arc_outline / draw_line 等空心或线框绘制——透明矢量层会导致闪烁。

辨识策略：给每类物体一个【唯一轮廓形状】（详见各函数）。

性能：每个绘制函数都接受可选参数 batch（game.batch_shapes.ShapeBatch）。
- batch 为 None -> 走即时模式（arcade.draw_*_filled），用于回退/单测。
- batch 不为 None -> 把图元追加进 batch，由调用方一次性绘制，
  将数百次缓冲上传合并为一次，消除卡顿。
"""

import math
import arcade

from game.batch_shapes import ShapeBatch


# ===================== 怪物 =====================

def draw_monster_base(m, outline_color, width=2, batch=None):
    """在怪物本体【之前】绘制一个略大的不透明实心方块，本体覆盖后只露出边缘。"""
    s = m.width + width * 2
    if batch is not None:
        batch.rect(m.center_x, m.center_y, s, s, outline_color)
    else:
        arcade.draw_rect_filled(arcade.XYWH(m.center_x, m.center_y, s, s), outline_color)


def draw_monster_armor(m, size, armor_color, batch=None):
    """怪物最底层的不透明实心护甲框（比本体大一圈）。"""
    s = m.width + 10
    if batch is not None:
        batch.rect(m.center_x, m.center_y, s, s, armor_color)
    else:
        arcade.draw_rect_filled(arcade.XYWH(m.center_x, m.center_y, s, s), armor_color)


def draw_monster_body(m, batch=None):
    """用不透明实心方块绘制怪物本体（替代 draw_sprite）。受击时整体变白。"""
    body_color = (255, 255, 255) if getattr(m, '_hit_flash', 0) > 0 else m.color
    if batch is not None:
        batch.rect(m.center_x, m.center_y, m.width, m.height, body_color)
    else:
        arcade.draw_rect_filled(
            arcade.XYWH(m.center_x, m.center_y, m.width, m.height), body_color)


def draw_monster_face(m, size, batch=None):
    """在怪物本体【之后】绘制不透明实心造型，区分怪物种类。"""
    cx, cy = m.center_x, m.center_y
    body = m.color
    # 圆眼风格：僵尸、木乃伊及其 BOSS（木乃伊为绷带造型用绷带色眼带）
    if m.__class__.__name__ in ("Zombie", "MummyMelee", "BossZombie", "BossMummy"):
        r = size + 1
        if batch is not None:
            batch.circle(cx, cy, r, body)
            eye_dx = size * 0.35
            eye_y = cy + size * 0.15
            batch.circle(cx - eye_dx, eye_y, 3, (200, 30, 30))
            batch.circle(cx + eye_dx, eye_y, 3, (200, 30, 30))
        else:
            arcade.draw_circle_filled(cx, cy, r, body)
            eye_dx = size * 0.35
            eye_y = cy + size * 0.15
            arcade.draw_circle_filled(cx - eye_dx, eye_y, 3, (200, 30, 30))
            arcade.draw_circle_filled(cx + eye_dx, eye_y, 3, (200, 30, 30))
    else:
        socket_dx = size * 0.32
        socket_y = cy + size * 0.1
        if batch is not None:
            batch.rect(cx - socket_dx, socket_y, 6, 6, (20, 20, 20))
            batch.rect(cx + socket_dx, socket_y, 6, 6, (20, 20, 20))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx - socket_dx, socket_y, 6, 6), (20, 20, 20))
            arcade.draw_rect_filled(arcade.XYWH(cx + socket_dx, socket_y, 6, 6), (20, 20, 20))


def draw_monster_weapon(m, size, weapon_color, batch=None):
    """在怪物身上绘制不透明实心武器。近战->剑；远程->斜握弓。"""
    if weapon_color is None:
        return
    cx, cy = m.center_x, m.center_y
    h = size
    if m.__class__.__name__ == "Skeleton":
        if batch is not None:
            batch.poly([
                (cx - h * 0.5, cy - h * 0.7), (cx - h * 0.5 + 4, cy - h * 0.7),
                (cx + h * 0.5 + 4, cy + h * 0.7), (cx + h * 0.5, cy + h * 0.7),
            ], weapon_color)
            batch.rect(cx - h * 0.5 + 2, cy - h * 0.7, 8, 3, weapon_color)
            batch.rect(cx + h * 0.5 - 2, cy + h * 0.7, 8, 3, weapon_color)
        else:
            arcade.draw_polygon_filled([
                (cx - h * 0.5, cy - h * 0.7), (cx - h * 0.5 + 4, cy - h * 0.7),
                (cx + h * 0.5 + 4, cy + h * 0.7), (cx + h * 0.5, cy + h * 0.7),
            ], weapon_color)
            arcade.draw_rect_filled(arcade.XYWH(cx - h * 0.5 + 2, cy - h * 0.7, 8, 3), weapon_color)
            arcade.draw_rect_filled(arcade.XYWH(cx + h * 0.5 - 2, cy + h * 0.7, 8, 3), weapon_color)
    else:
        if batch is not None:
            batch.rect(cx + h * 0.6, cy, 3, int(h * 1.2), weapon_color)
            batch.rect(cx + h * 0.6 - 3, cy + h * 0.4, 9, 3, (180, 180, 190))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx + h * 0.6, cy, 3, int(h * 1.2)), weapon_color)
            arcade.draw_rect_filled(arcade.XYWH(cx + h * 0.6 - 3, cy + h * 0.4, 9, 3), (180, 180, 190))


# ===================== 资源物 =====================

def draw_harvestable(h, batch=None):
    """按资源类型绘制不透明实心独特造型。受击时整个造型变白后恢复。"""
    cx, cy = h.center_x, h.center_y
    rtype = h.resource_type
    flash = getattr(h, '_hit_flash', 0) > 0
    if rtype == "tree":
        if batch is not None:
            batch.rect(cx, cy - 6, 8, 16, (255, 255, 255) if flash else (95, 60, 30))
            batch.circle(cx, cy + 5, 14, (255, 255, 255) if flash else (40, 170, 55))
            batch.circle(cx - 6, cy + 9, 8, (255, 255, 255) if flash else (55, 190, 70))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy - 6, 8, 16),
                                    (255, 255, 255) if flash else (95, 60, 30))
            arcade.draw_circle_filled(cx, cy + 5, 14,
                                      (255, 255, 255) if flash else (40, 170, 55))
            arcade.draw_circle_filled(cx - 6, cy + 9, 8,
                                      (255, 255, 255) if flash else (55, 190, 70))
    elif rtype == "ore":
        if batch is not None:
            batch.rect(cx, cy - 4, 24, 18, (255, 255, 255) if flash else (120, 85, 45))
            batch.tri((cx - 8, cy + 2), (cx - 2, cy + 16), (cx + 2, cy + 2),
                      (255, 255, 255) if flash else (210, 180, 110))
            batch.tri((cx + 2, cy + 2), (cx + 8, cy + 18), (cx + 12, cy + 2),
                      (255, 255, 255) if flash else (230, 210, 140))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy - 4, 24, 18),
                                    (255, 255, 255) if flash else (120, 85, 45))
            arcade.draw_triangle_filled(
                cx - 8, cy + 2, cx - 2, cy + 16, cx + 2, cy + 2,
                (255, 255, 255) if flash else (210, 180, 110))
            arcade.draw_triangle_filled(
                cx + 2, cy + 2, cx + 8, cy + 18, cx + 12, cy + 2,
                (255, 255, 255) if flash else (230, 210, 140))
    elif rtype == "cactus":
        # 仙人掌：绿色主茎 + 两侧小臂 + 底部阴影
        green = (255, 255, 255) if flash else (60, 150, 70)
        dark = (255, 255, 255) if flash else (45, 120, 55)
        if batch is not None:
            batch.rect(cx, cy, 12, 24, green)              # 主茎
            batch.rect(cx - 9, cy + 5, 8, 8, green)        # 左臂
            batch.rect(cx + 1, cy + 8, 8, 8, green)        # 右臂
            batch.rect(cx, cy - 14, 16, 3, (120, 100, 70)) # 沙地阴影
            batch.rect(cx - 10, cy + 8, 2, 4, dark)        # 刺
            batch.rect(cx + 8, cy + 11, 2, 4, dark)
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy, 12, 24), green)
            arcade.draw_rect_filled(arcade.XYWH(cx - 9, cy + 5, 8, 8), green)
            arcade.draw_rect_filled(arcade.XYWH(cx + 1, cy + 8, 8, 8), green)
            arcade.draw_rect_filled(arcade.XYWH(cx, cy - 14, 16, 3), (120, 100, 70))
            arcade.draw_rect_filled(arcade.XYWH(cx - 10, cy + 8, 2, 4), dark)
            arcade.draw_rect_filled(arcade.XYWH(cx + 8, cy + 11, 2, 4), dark)
    else:  # stone
        pts = [
            (cx - 13, cy - 8), (cx - 9, cy + 10), (cx + 2, cy + 13),
            (cx + 13, cy + 4), (cx + 10, cy - 11), (cx - 4, cy - 13),
        ]
        if batch is not None:
            batch.poly(pts, (255, 255, 255) if flash else (150, 150, 155))
            batch.rect(cx - 2, cy - 2, 6, 6, (255, 255, 255) if flash else (120, 120, 125))
        else:
            arcade.draw_polygon_filled(pts, (255, 255, 255) if flash else (150, 150, 155))
            arcade.draw_rect_filled(arcade.XYWH(cx - 2, cy - 2, 6, 6),
                                    (255, 255, 255) if flash else (120, 120, 125))


# ===================== 玩家装备 =====================

def draw_player_base_equipment(player, equip, batch=None):
    """在玩家本体【之前】绘制不透明实心装备底层（护甲框 + 头盔条）。"""
    if "armor" in equip:
        armor_color = equip["armor"].get("color", (150, 150, 150))
        s = player.width + 4
        if batch is not None:
            batch.rect(player.center_x, player.center_y, s, s, armor_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(player.center_x, player.center_y, s, s), armor_color)
    if "helmet" in equip:
        helm_color = equip["helmet"].get("color", (139, 90, 43))
        half = player.width / 2
        if batch is not None:
            batch.rect(player.center_x, player.center_y + half - 1, int(player.width * 0.8), 5, helm_color)
        else:
            arcade.draw_rect_filled(
                arcade.XYWH(player.center_x, player.center_y + half - 1, int(player.width * 0.8), 5),
                helm_color)


def draw_player_weapon(player, weapon_kind, weapon_color, weapon_shape, batch=None):
    """在玩家本体右侧绘制不透明实心手持武器，按 shape 区分形态。"""
    if weapon_color is None or weapon_shape in ("none", None):
        return
    cx, cy = player.center_x, player.center_y
    h = player.width / 2
    wx = cx + h + 5  # 固定在玩家右侧

    if weapon_shape == "sword":
        blade_len = int(h * 1.2)
        if batch is not None:
            batch.rect(wx + blade_len * 0.5, cy, 3, blade_len, weapon_color)
            batch.rect(wx, cy, 11, 3, (180, 180, 190))
        else:
            arcade.draw_rect_filled(arcade.XYWH(wx + blade_len * 0.5, cy, 3, blade_len), weapon_color)
            arcade.draw_rect_filled(arcade.XYWH(wx, cy, 11, 3), (180, 180, 190))
    elif weapon_shape == "mace":
        handle_len = int(h * 1.0)
        if batch is not None:
            batch.rect(wx + handle_len * 0.5, cy, 5, handle_len, weapon_color)
            batch.circle(wx + handle_len, cy, 6, weapon_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(wx + handle_len * 0.5, cy, 5, handle_len), weapon_color)
            arcade.draw_circle_filled(wx + handle_len, cy, 6, weapon_color)
    elif weapon_shape == "bow":
        bow_len = int(h * 1.4)
        if batch is not None:
            batch.rect(wx + bow_len * 0.5, cy, 4, bow_len, weapon_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(wx + bow_len * 0.5, cy, 4, bow_len), weapon_color)
    elif weapon_shape == "staff":
        staff_len = int(h * 1.3)
        if batch is not None:
            batch.rect(wx + staff_len * 0.5, cy, 3, staff_len, weapon_color)
            batch.circle(wx + staff_len, cy, 5, weapon_color)
        else:
            arcade.draw_rect_filled(arcade.XYWH(wx + staff_len * 0.5, cy, 3, staff_len), weapon_color)
            arcade.draw_circle_filled(wx + staff_len, cy, 5, weapon_color)


# ===================== 掉落物 =====================

def draw_drop_icon(drop, batch=None):
    """按掉落类型绘制不透明实心图标。"""
    cx, cy = drop.center_x, drop.center_y
    it = drop.item_type
    if it == "gold":
        if batch is not None:
            batch.circle(cx, cy, 8, (255, 215, 0))
            batch.circle(cx, cy, 4, (210, 170, 0))
        else:
            arcade.draw_circle_filled(cx, cy, 8, (255, 215, 0))
            arcade.draw_circle_filled(cx, cy, 4, (210, 170, 0))
    elif it == "helmet":
        color = drop.color
        if batch is not None:
            batch.circle(cx, cy, 8, color)
            batch.rect(cx, cy + 6, 12, 4, (255, 255, 255))
        else:
            arcade.draw_circle_filled(cx, cy, 8, color)
            arcade.draw_rect_filled(arcade.XYWH(cx, cy + 6, 12, 4), (255, 255, 255))
    elif it == "armor":
        color = drop.color
        if batch is not None:
            batch.rect(cx, cy, 14, 14, color)
            batch.rect(cx, cy, 6, 6, (255, 255, 255))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy, 14, 14), color)
            arcade.draw_rect_filled(arcade.XYWH(cx, cy, 6, 6), (255, 255, 255))
    elif it == "weapon":
        if batch is not None:
            batch.rect(cx, cy - 1, 3, 12, (100, 200, 255))
            batch.rect(cx - 4, cy + 4, 11, 3, (180, 180, 200))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy - 1, 3, 12), (100, 200, 255))
            arcade.draw_rect_filled(arcade.XYWH(cx - 4, cy + 4, 11, 3), (180, 180, 200))
    else:
        color = drop.color
        if batch is not None:
            batch.circle(cx, cy, 7, color)
        else:
            arcade.draw_circle_filled(cx, cy, 7, color)


# ===================== 宝箱 =====================

def draw_chest(chest, batch=None):
    """用不透明实心形状绘制宝箱。"""
    cx, cy = chest.center_x, chest.center_y
    opened = getattr(chest, "opened", False)
    if batch is not None:
        batch.rect(cx, cy, 22, 18, (150, 95, 45))
        if opened:
            batch.rect(cx, cy - 12, 22, 8, (110, 70, 35))
        else:
            batch.rect(cx, cy - 6, 22, 8, (120, 75, 38))
            batch.rect(cx, cy, 6, 6, (220, 180, 60))
    else:
        arcade.draw_rect_filled(arcade.XYWH(cx, cy, 22, 18), (150, 95, 45))
        if opened:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy - 12, 22, 8), (110, 70, 35))
        else:
            arcade.draw_rect_filled(arcade.XYWH(cx, cy - 6, 22, 8), (120, 75, 38))
            arcade.draw_rect_filled(arcade.XYWH(cx, cy, 6, 6), (220, 180, 60))
