"""新手教程模块：向导弹窗组件 + 游戏内引导提示

首次启动启用（db settings 未标记 tutorial_done 时）。教程流程：
  开始界面向导 → 角色选择向导 → 地图选择向导 → 游戏内引导（打怪/拾取/小地图/建造/撤离）
  → 撤离结算 + BOSS/火箭发射台教学（成功与失败两套文案）+ 星级结算
  → 市场买卖/设施/图鉴教学 → 标记完成。

向导弹窗：不透明深色遮罩 + 高亮框 + 中文说明 + 下一步/跳过按钮；
任意时刻按 ESC 或点跳过 → 立即结束教程并标记完成（之后不再出现）。
教程中途退出（关游戏）不标记，下次启动从头开始。

**数值口径铁律（2026-09-26）**：本模块内出现的一切数字与键位一律实查来源，
禁硬编码——键位走 `action_key_label()`（读 db settings 的重绑结果，缺省回落
config.KEY_BINDINGS），造价/读条秒数/星级/设施等级/图鉴档位/角色数走 config
常量与 entities 数据表。理由：这些数值此前散落在各视图的教程文案里写死
（"按 7"、"读条 3 秒"、"每图最多 3 星"、"Lv5+"），玩家一旦改键位或改平衡，
教程就会教出与真实机制冲突的错误操作。
"""

import arcade
from config import (
    EVAC_CHANNEL_TIME,
    EVAC_DEFAULT_THEME,
    KEY_BINDINGS,
    MAP_MAX_STARS,
    MAP_STAR_CRITERIA,
    MAP_UNLOCK_STARS,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
    evac_activate_cost,
)
from entities.monster_defs import MONSTER_CONFIGS

# 下一步 / 跳过按钮固定位置（右下角，避开各界面元素）
NEXT_RECT = arcade.XYWH(WINDOW_WIDTH - 180, 60, 130, 44)
SKIP_RECT = arcade.XYWH(WINDOW_WIDTH - 340, 60, 120, 44)


# ─────────────────────── 教程文案取数辅助（禁硬编码的唯一出口）───────────────────────
# 键位 → 中文短名（"KEY_7"→"7"，"TAB"/"E"/"M" 原样）
_KEY_SUFFIX = "KEY_"
# 撤离造价资源 id → 教学用简称
_COST_SHORT_NAME = {"wood": "木", "stone": "石", "ore": "矿"}


def action_key_label(action: str, default: str = "未绑定") -> str:
    """取某动作**当前生效**的键位显示名（教程文案键位的唯一口径）

    键位可被玩家在设置界面重绑并持久化到 db settings（config.KEY_BINDINGS 只是
    默认值），所以教程文案不能写死"按 7"/"按 E"——必须实查当前绑定，否则重绑后
    教程会教出错误键位。读 db 失败时 get_key_bindings 内部已回落默认值。
    """
    from db.settings import get_key_bindings
    keys = (get_key_bindings() or {}).get(action) or KEY_BINDINGS.get(action)
    if not keys:
        return default
    return "/".join(str(k).removeprefix(_KEY_SUFFIX) for k in keys)


def digit_key_labels(*actions: str) -> str:
    """连续数字键的紧凑标签（"1/2/3"），用于建造选建筑/药水快捷键

    建造模式与药水快捷键**复用同一批数字键**（config.KEY_BINDINGS 的
    potion_1/2/3 = KEY_1/2/3；建造模式下由 game/input_handler.py 改判为选建筑），
    故这里只取 potion_* 的绑定作为数字键代表，文案上不区分语义。
    """
    return "/".join(action_key_label(a) for a in actions)


def evac_cost_label(theme: str) -> str:
    """某地图撤离点激活/修复造价的中文短标签（数值取 config.evac_activate_cost）

    激活与修复同比（config.EVAC_COST_BY_THEME 只存一份表），故合并为一句文案。
    """
    cost = evac_activate_cost(theme)
    return " ".join(f"{_COST_SHORT_NAME.get(k, k)}{v}" for k, v in cost.items())


def evac_hold_seconds() -> int:
    """撤离读条秒数（config.EVAC_CHANNEL_TIME，四舍五入取整展示）"""
    return int(round(EVAC_CHANNEL_TIME))


def boss_level_label() -> str:
    """BOSS 挑战的装备等级门槛文案（取全部 BOSS 的 required_weapon_level 最小值）

    数据源 entities/monster_defs.MONSTER_CONFIGS 里 is_boss 条目的
    required_weapon_level（game/monster_base.py 门禁判定用的同一字段）。
    """
    levels = [int(cfg.get("required_weapon_level", 0) or 0)
              for cfg in MONSTER_CONFIGS.values() if cfg.get("is_boss")]
    lv = min(levels) if levels else 0
    return f"Lv{lv}+" if lv > 0 else "无等级门槛"


def map_max_stars() -> int:
    """单图星级上限（config.MAP_MAX_STARS）"""
    return int(MAP_MAX_STARS)


def star_criteria_labels(theme: str) -> str:
    """某图星级达成条件的中文短标签（逐条取 config.MAP_STAR_CRITERIA 的 desc 字段）"""
    descs = [str(c.get("desc", "")) for c in MAP_STAR_CRITERIA.get(theme, [])]
    return "、".join(d for d in descs if d)


def unlock_star_label(theme: str) -> str:
    """某图的累计星数解锁门槛文案（config.MAP_UNLOCK_STARS；0 星 = 初始解锁）"""
    need = int(MAP_UNLOCK_STARS.get(theme, 0))
    return "初始解锁" if need <= 0 else f"累计 ★{need} 解锁"


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


def tut_banner(view, text: str, tc, key: str, color=(255, 230, 120), size=18,
               sub: str = ""):
    """游戏内顶部引导提示条（阶段 3 用）：不透明横幅 + 主行文字（+ 可选副行）

    sub 非空时在主行下方追加一行小字——建造/祝福（主行）与撤离（副行）同屏展示
    用，避免一条横幅塞不下两段引导。
    提示条始终不透明实心，符合渲染铁律；文字经 TextCache 缓存不每帧重建。
    """
    banner_h = 44 * (2 if sub else 1)
    cy = WINDOW_HEIGHT - banner_h / 2
    arcade.draw_rect_filled(
        arcade.XYWH(WINDOW_WIDTH / 2, cy, WINDOW_WIDTH - 40, banner_h),
        (16, 20, 26))
    tc.text(key, text,
            WINDOW_WIDTH / 2, cy + (11 if sub else 0),
            color, size=size, anchor_x="center", anchor_y="center")
    if sub:
        tc.text(f"{key}_sub", sub,
                WINDOW_WIDTH / 2, cy - 12,
                (200, 210, 225), size=14, anchor_x="center", anchor_y="center")


def draw_in_game_tutorial(view, tc):
    """游戏内教程引导横幅（阶段 3）：按教程进度显示对应提示

    调用点：render_game HUD 阶段末尾。仅在教程激活且处于游戏内阶段时绘制。
    推进条件链（只看可观测事实，禁额外埋点）：kill_count → pickup_count →
    minimap_taught → 终态（建造/祝福 + 撤离）。终态不再自动推进，避免玩家
    没开过建造模式就被卡在建造页看不到撤离指引。
    """
    gs = view.window.game_state
    tut = getattr(gs, "tutorial", None)
    if tut is None or not tut.active or tut.stage != 3:
        return
    # 键位实查（玩家可重绑，见 action_key_label）
    k_move = "/".join(action_key_label(a) for a in ("move_up", "move_left", "move_down", "move_right"))
    k_atk = action_key_label("interact")
    k_bag = action_key_label("backpack")
    k_map = action_key_label("minimap_zoom")
    k_build = action_key_label("build")
    k_num = digit_key_labels("potion_1", "potion_2", "potion_3")
    # 撤离造价按本局地图主题取（分图分档，config.evac_activate_cost）
    theme = str(getattr(gs, "map_theme", "") or EVAC_DEFAULT_THEME)
    if tut.kill_count == 0:
        # 引导打怪前：先讲基础规则 + 药水快捷键
        tut_banner(view, f"操作：{k_move} 移动 | 鼠标左键攻击 | {k_atk} 交互 | "
                        f"{k_bag} 背包 | {k_num} 药水 | 先击杀一只怪物",
                   tc, "tut_banner_kill")
    elif tut.pickup_count == 0:
        # 击杀完成：引导拾取 + 顺带提小地图（拾取按 E 键交互，非自动）
        tut_banner(view, f"击杀成功！靠近地上的掉落物后按 {k_atk} 键拾取"
                        f"（{k_map} 键可查看小地图）",
                   tc, "tut_banner_pickup")
    elif not tut.minimap_taught:
        # 已拾取：引导看小地图（若尚未讲解过）
        tut_banner(view, f"按 {k_map} 切换小地图视野/全图  图例: 浅灰=房间 黄=宝箱 绿=撤离点 "
                        f"金=BOSS 白=自己 橙=队友",
                   tc, "tut_banner_map")
    else:
        # 小地图已讲解：主行讲建造 + 祝福，副行讲撤离（BOSS 介绍在撤离后单独展示）
        # 教程事实错误修正（2026-09-26）：原文案「站上去读条 3 秒即可撤离」是 v1
        # 规则。现行机制 = 蓝色方块撤离点须靠近按 E 消耗材料激活 → 进入防守波次
        # （倒计时防守）→ 守住后再按 E 读条撤离；撤离点被打成废墟可按 E
        # 消耗材料修复重守。造价/读条秒数一律实查 config.evac_activate_cost(theme)
        # 与 config.EVAC_CHANNEL_TIME，此处不写死数字以免再次过期。
        tut_banner(view, f"按 {k_build} 进入建造模式：{k_num} 选建筑、左键放置、"
                        f"ESC 退出；击杀精英会触发祝福 3 选 1",
                   tc, "tut_banner_build",
                   sub=f"撤离：靠近蓝色方块按 {k_atk} 消耗 {evac_cost_label(theme)} 激活 → "
                       f"守住防守波次 → 再按 {k_atk} 读条 {evac_hold_seconds()} 秒撤离"
                       f"（被拆成废墟可按 {k_atk} 修复）")


def build_boss_intro_pages():
    """BOSS 介绍向导页（4 页）：撤离后展示，介绍 BOSS 房间机制

    装备等级门槛实查 boss_level_label()（读 MONSTER_CONFIGS 里 BOSS 条目的
    required_weapon_level），禁写死 "Lv5+"——门槛一改这里就会过期。
    """
    return [
        TutorialPage("BOSS 房间介绍", [
            "地图上标有金色方块的房间是 BOSS 房间。",
            "",
            "进入 BOSS 房间后，门洞会被封锁，",
            "你将无法离开，直到击败 BOSS 或阵亡。",
        ]),
        TutorialPage("🔒 房间锁定", [
            "BOSS 房间门洞会被不透明方块封住：",
            "BOSS 存活期间无法穿越或瞬移出去。",
            "（刺客影袭同样无法穿墙逃脱）",
            "",
            "击败 BOSS 后门洞自动解除封锁。",
        ]),
        TutorialPage("💥 BOSS 技能", [
            "每个 BOSS 拥有 2 个专属技能，",
            "技能会定期自动释放，造成范围伤害。",
            "",
            "注意观察 BOSS 的动作前摇：",
            "技能释放前会有短暂的粒子特效提示。",
        ]),
        TutorialPage("准备挑战", [
            "确保装备充足、药水备好！",
            "",
            f"推荐 {boss_level_label()} 装备再挑战 BOSS。",
            "击败 BOSS 可获得稀有装备和神器。",
            "",
            "祝你好运，勇者！",
        ], next_text="了解"),
    ]


def finish_tutorial(window) -> None:
    """结束新手教程：标记完成并关闭教程（跳过/走完的统一出口）"""
    from db.settings import mark_tutorial_done
    mark_tutorial_done()
    gs = window.game_state
    tut = getattr(gs, "tutorial", None)
    if tut is not None:
        tut.active = False
        tut.stage = 6
