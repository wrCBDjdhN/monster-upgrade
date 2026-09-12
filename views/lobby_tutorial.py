"""联机大厅教程辅助模块（非 View 类）

职责：
- 从 LobbyView 中拆出的教程相关逻辑：教程页面构建 + 教程覆盖层绘制。
- 持有 LobbyView 引用（self.lv），所有状态经 self.lv 访问，不复制任何大厅状态。
- 拆分目的：缩小 lobby_view.py 体积（约 -200 行），教程逻辑独立维护。
"""

import arcade
from config import WINDOW_WIDTH, WINDOW_HEIGHT


class LobbyTutorial:
    """联机大厅教程辅助：页面构建 + 覆盖层绘制（状态全部经 self.lv 访问）"""

    def __init__(self, lobby_view):
        """保存大厅视图引用，后续所有状态访问走 self.lv"""
        self.lv = lobby_view

    def _build_tutorial_pages(self) -> list[dict]:
        """构建联机教程页面内容"""
        return [
            {
                "title": "局域网联机 - 概述",
                "content": [
                    "局域网联机支持最多 4 人同时游戏。",
                    "主机创建房间，其他玩家通过 IP 加入。",
                    "主机负责裁决伤害/拾取/撤离，客户端同步显示。",
                    "每局新地图：同一房间多次开局自动重新随机种子。",
                ],
            },
            {
                "title": "建房（主机）",
                "content": [
                    "点击「建房」创建房间，获得房间号。",
                    "选择地图主题：幽暗森林（普通）/ 沙漠荒地（困难）/ 航天基地（极难）。",
                    "选择角色后等待其他玩家加入。",
                    "全员就绪后点击「开始游戏」。",
                    "主机可在房间内访问市场/仓库补充装备。",
                ],
            },
            {
                "title": "加入（客户端）",
                "content": [
                    "点击「加入」搜索局域网内的房间。",
                    "选择房间或手动输入主机 IP 连接。",
                    "连接后选择角色并点击「准备」。",
                    "等待主机开始游戏。",
                    "战备检查：装备价值需达到地图要求。",
                ],
            },
            {
                "title": "操作与快捷键",
                "content": [
                    "WASD / 方向键：移动",
                    "鼠标：瞄准（远程武器）",
                    "鼠标左键：攻击",
                    "E：交互（宝箱/水井/发射台）/ 救援倒地队友",
                    "F：释放角色技能",
                    "TAB：打开/关闭背包",
                    "V：观战模式切换视角（联机）",
                    "M：小地图放大/缩小",
                    "ESC：设置界面 / 关闭弹窗",
                    "F11：全屏切换",
                ],
            },
            {
                "title": "战斗与协作",
                "content": [
                    "击杀怪物获取经验、金币和掉落物。",
                    "拾取武器/装备/药水提升实力。",
                    "BOSS 镇守火箭发射台，击败可夺宝或启用撤离。",
                    "找到撤离点读条 3 秒撤离，带走本局战利品。",
                    "死亡/超时则丢失全部装备。",
                ],
            },
            {
                "title": "玩家救援机制",
                "content": [
                    "当队友 HP 归零时，会进入「倒地」状态。",
                    "倒地玩家保留装备，头顶显示倒计时（60秒）。",
                    "靠近倒地队友按 E 键可发起救援（3秒读条）。",
                    "救援成功：被救者 HP 恢复为 10，装备保留。",
                    "超时未被救：倒地玩家真死，清空装备，进入观战。",
                    "倒地玩家可主动退出观战（视为真死）。",
                    "全场玩家均阵亡/观战 → 全员阵亡，结束本局。",
                ],
            },
            {
                "title": "观战模式",
                "content": [
                    "死亡/撤离后自动进入观战模式。",
                    "V 键切换跟随不同的存活玩家。",
                    "观战期间世界继续模拟（怪物 AI/掉落/快照）。",
                    "全员结束后回房等待下一局。",
                    "倒地玩家可选择退出观战（视为真死）。",
                ],
            },
        ]

    def _draw_tutorial(self, cx: int):
        """绘制联机教程页面（状态经 self.lv 访问）"""
        lv = self.lv
        # 半透明遮罩
        overlay = arcade.ShapeElementList()
        overlay.append(arcade.create_rect_filled(
            arcade.XYWH(cx, WINDOW_HEIGHT // 2, WINDOW_WIDTH, WINDOW_HEIGHT),
            (0, 0, 0, 180)))
        overlay.draw()
        # 教程面板背景
        panel_rect = arcade.XYWH(cx, WINDOW_HEIGHT // 2, 700, 500)
        arcade.draw_rect_filled(panel_rect, (30, 40, 60))
        arcade.draw_rect_outline(panel_rect, arcade.color.GOLD, border_width=3)
        # 标题
        page = lv._tutorial_pages[lv._tutorial_page]
        lv._tc.text("tut_title", page["title"], cx, WINDOW_HEIGHT // 2 + 210,
                    arcade.color.GOLD, size=24, anchor_x="center", bold=True)
        # 内容
        y = WINDOW_HEIGHT // 2 + 170
        for line in page["content"]:
            lv._tc.text(f"tut_{y}", f"• {line}", cx - 300, y,
                        arcade.color.WHITE, size=14)
            y -= 28
        # 页码
        total = len(lv._tutorial_pages)
        lv._tc.text("tut_page", f"第 {lv._tutorial_page + 1}/{total} 页",
                    cx, WINDOW_HEIGHT // 2 - 200, arcade.color.LIGHT_GRAY, size=12,
                    anchor_x="center")
        # 上一页按钮
        if lv._tutorial_page > 0:
            pcolor = arcade.color.CORNFLOWER_BLUE if lv._tutorial_prev_hover else arcade.color.STEEL_BLUE
            arcade.draw_rect_filled(lv._tutorial_prev_rect, pcolor)
            lv._tc.text("tut_prev", "上一页", lv._tutorial_prev_rect.center_x,
                        lv._tutorial_prev_rect.center_y, arcade.color.WHITE, 14,
                        anchor_x="center", anchor_y="center")
        # 下一页按钮
        if lv._tutorial_page < total - 1:
            ncolor = arcade.color.CORNFLOWER_BLUE if lv._tutorial_next_hover else arcade.color.STEEL_BLUE
            arcade.draw_rect_filled(lv._tutorial_next_rect, ncolor)
            lv._tc.text("tut_next", "下一页", lv._tutorial_next_rect.center_x,
                        lv._tutorial_next_rect.center_y, arcade.color.WHITE, 14,
                        anchor_x="center", anchor_y="center")
        # 关闭按钮
        ecolor = arcade.color.DARK_RED if lv._tutorial_close_hover else (120, 40, 40)
        arcade.draw_rect_filled(lv._tutorial_close_rect, ecolor)
        lv._tc.text("tut_close", "我知道了", lv._tutorial_close_rect.center_x,
                    lv._tutorial_close_rect.center_y, arcade.color.WHITE, 14,
                    anchor_x="center", anchor_y="center")