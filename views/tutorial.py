"""新手教程模块：向导弹窗组件 + 游戏内引导提示

首次启动启用（db settings 未标记 tutorial_done 时）。教程流程：
  开始界面向导 → 角色选择向导 → 地图选择向导 → 游戏内引导（打怪/拾取/小地图/撤离）
  → 撤离结算 + 航天基地火箭发射台教学 → 市场买卖教学 → 标记完成。

向导弹窗：不透明深色遮罩 + 高亮框 + 中文说明 + 下一步/跳过按钮；
任意时刻按 ESC 或点跳过 → 立即结束教程并标记完成（之后不再出现）。
教程中途退出（关游戏）不标记，下次启动从头开始。
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT

# 下一步 / 跳过按钮固定位置（右下角，避开各界面元素）
NEXT_RECT = arcade.XYWH(WINDOW_WIDTH - 180, 60, 130, 44)
SKIP_RECT = arcade.XYWH(WINDOW_WIDTH - 340, 60, 120, 44)


class TutorialPage:
    """单页向导数据：标题 + 多行正文 + 可选高亮目标 + 下一步按钮文字"""

    def __init__(self, title: str, lines: list, highlight=None, next_text="下一步"):
        self.title = title
        self.lines = lines          # list[str]，每行一条正文
        self.highlight = highlight  # arcade.XYWH | None：高亮的目标矩形（按钮等）
        self.next_text = next_text


def _draw_highlight_mask(rect):
    """镂空遮罩：4 块实心矩形围住高亮区 + 亮黄边框细条

    高亮区【不填充】（下方真实 UI 内容透出可见），四周用不透明深色块压暗，
    再贴边画 4 条实心亮黄色细条提示目光焦点。
    全部实心填充，符合渲染铁律（禁空心/线框）。
    """
    color = (10, 13, 16)
    W, H = WINDOW_WIDTH, WINDOW_HEIGHT
    cx, cy = rect.center_x, rect.center_y
    hw, hh = rect.width / 2, rect.height / 2
    left, right = cx - hw, cx + hw
    top, bottom = cy + hh, cy - hh
    # 顶部块（屏幕顶 → 高亮上缘）
    arcade.draw_rect_filled(arcade.XYWH(W / 2, (H + top) / 2, W, H - top), color)
    # 底部块（高亮下缘 → 屏幕底）
    arcade.draw_rect_filled(arcade.XYWH(W / 2, bottom / 2, W, bottom), color)
    # 左块（屏幕左 → 高亮左缘，高度=高亮高度）
    arcade.draw_rect_filled(arcade.XYWH(left / 2, cy, left, rect.height), color)
    # 右块（高亮右缘 → 屏幕右）
    arcade.draw_rect_filled(arcade.XYWH((right + W) / 2, cy, W - right, rect.height), color)
    # 亮黄高亮边框：4 条实心细条贴在镂空边缘外侧（不覆盖高亮内容）
    b = 3
    border = (255, 200, 60)
    arcade.draw_rect_filled(arcade.XYWH(cx, top + b / 2, rect.width + b * 2, b), border)     # 上
    arcade.draw_rect_filled(arcade.XYWH(cx, bottom - b / 2, rect.width + b * 2, b), border)  # 下
    arcade.draw_rect_filled(arcade.XYWH(left - b / 2, cy, b, rect.height + b * 2), border)   # 左
    arcade.draw_rect_filled(arcade.XYWH(right + b / 2, cy, b, rect.height + b * 2), border)  # 右


def draw_tutorial_page(view, page: TutorialPage, page_idx: int, total: int,
                       tc, next_hover: bool = False):
    """在 view 上绘制向导弹窗覆盖层；返回 (next_rect, skip_rect) 供点击检测

    view：当前 arcade.View（未直接使用，保持接口一致便于后续扩展）
    tc：TextCache 实例（持久文字缓存，避免每帧重建纹理）
    next_hover：下一步按钮是否悬停（由 View 的 on_mouse_motion 更新）
    """
    # 遮罩：有高亮目标 → 镂空遮罩（高亮区透出真实内容）；无 → 全屏遮罩
    # 全屏/镂空均为不透明实心填充，符合渲染铁律
    if page.highlight is not None:
        _draw_highlight_mask(page.highlight)
    else:
        arcade.draw_rect_filled(
            arcade.XYWH(WINDOW_WIDTH / 2, WINDOW_HEIGHT / 2, WINDOW_WIDTH, WINDOW_HEIGHT),
            (10, 13, 16))

    # 标题
    tc.text(f"tut_title_{page_idx}", page.title,
            WINDOW_WIDTH / 2, WINDOW_HEIGHT - 140,
            arcade.color.GOLD, size=32, anchor_x="center", bold=True)

    # 正文多行
    start_y = WINDOW_HEIGHT - 210
    for i, line in enumerate(page.lines):
        tc.text(f"tut_line_{page_idx}_{i}", line,
                WINDOW_WIDTH / 2, start_y - i * 34,
                arcade.color.WHITE, size=18, anchor_x="center")

    # 页码
    tc.text(f"tut_page_{page_idx}", f"{page_idx + 1} / {total}",
            WINDOW_WIDTH / 2, 62,
            arcade.color.GRAY, size=14, anchor_x="center")

    # 下一步按钮
    arcade.draw_rect_filled(
        NEXT_RECT, (90, 140, 200) if next_hover else (60, 100, 150))
    tc.text("tut_next", page.next_text,
            NEXT_RECT.center_x, NEXT_RECT.center_y,
            arcade.color.WHITE, size=16, anchor_x="center", anchor_y="center")

    # 跳过按钮
    arcade.draw_rect_filled(SKIP_RECT, (70, 70, 80))
    tc.text("tut_skip", "跳过(ESC)",
            SKIP_RECT.center_x, SKIP_RECT.center_y,
            arcade.color.LIGHT_GRAY, size=14, anchor_x="center", anchor_y="center")

    return NEXT_RECT, SKIP_RECT


def tut_banner(view, text: str, tc, key: str, color=(255, 230, 120), size=18):
    """游戏内顶部引导提示条（阶段 3 用）：半屏顶部不透明横幅 + 文字

    提示条始终不透明实心，符合渲染铁律；文字经 TextCache 缓存不每帧重建。
    """
    banner_h = 44
    arcade.draw_rect_filled(
        arcade.XYWH(WINDOW_WIDTH / 2, WINDOW_HEIGHT - banner_h / 2,
                    WINDOW_WIDTH - 40, banner_h),
        (16, 20, 26))
    tc.text(key, text,
            WINDOW_WIDTH / 2, WINDOW_HEIGHT - banner_h / 2,
            color, size=size, anchor_x="center", anchor_y="center")


def draw_in_game_tutorial(view, tc):
    """游戏内教程引导横幅（阶段 3）：按教程进度显示对应提示

    调用点：render_game HUD 阶段末尾。仅在教程激活且处于游戏内阶段时绘制。
    """
    gs = view.window.game_state
    tut = getattr(gs, "tutorial", None)
    if tut is None or not tut.active or tut.stage != 3:
        return
    if tut.kill_count == 0:
        # 引导打怪前：先讲基础规则
        tut_banner(view, "规则：WASD 移动 | 鼠标左键攻击 | E 交互 | TAB 背包 | M 小地图 | 先击杀一只怪物",
                   tc, "tut_banner_kill")
    elif tut.pickup_count == 0:
        # 击杀完成：引导拾取 + 顺带提小地图（拾取按 E 键交互，非自动）
        tut_banner(view, "击杀成功！靠近地上的掉落物后按 E 键拾取（M 键可查看小地图）",
                   tc, "tut_banner_pickup")
    elif not tut.minimap_taught:
        # 已拾取：引导看小地图（若尚未讲解过）
        tut_banner(view, "按 M 切换小地图视野/全图：房间=浅灰 宝箱=黄 撤离点=绿 自己=白 队友=橙",
                   tc, "tut_banner_map")
    else:
        # 小地图已讲解：引导撤离
        tut_banner(view, "去地图绿色标记的撤离点，站上去读条 3 秒即可撤离（倒计时结束前）",
                   tc, "tut_banner_evac")


def finish_tutorial(window) -> None:
    """结束新手教程：标记完成并关闭教程（跳过/走完的统一出口）"""
    from db.settings import mark_tutorial_done
    mark_tutorial_done()
    gs = window.game_state
    tut = getattr(gs, "tutorial", None)
    if tut is not None:
        tut.active = False
        tut.stage = 6
