# views/ - UI 视图层

## OVERVIEW
全部界面，均为 `arcade.View` 子类。切换用 `window.show_view()`；共享状态一律走 `window.game_state`（main.GameState）。

## WHERE TO LOOK
| 视图 | 文件 | 要点 |
|------|------|------|
| 开始界面（携带武器显示 + 功能入口按钮） | start_view.py | 入口按钮模式样板，导航枢纽 |
| 地图选择 | map_select_view.py | 地图种子选择（先写 seed/theme 再 gv.setup()） |
| 主游戏视图（最大，~700 行） | game_view.py | HUD、世界/屏幕坐标标签、实体生成编排、委托 game/ 层 |
| 仓库 | warehouse_view.py | 物品管理（资源/武器/装备售卖） |
| 市场 | market_view.py | 买卖 + 开箱动画（500 行，第二大） |
| 锻造坊（升级） | forge_view.py | 材料合成 + 神器，含纯函数 forge_result_level/merge_forge_effects |
| 背包 | backpack_view.py | 本次携带物；保存 game_view 引用返回不重建 |
| 可滚动面板基类 | scroll_view.py | 滚动逻辑复用（5/8 视图继承） |

## CONVENTIONS
- 类名 `XxxView(arcade.View)`；构造接收 window 存 `self.window_ref`
- `on_show_view()` 设背景色；`on_draw()` 先 `self.clear()` 再绘制
- 按钮 = `arcade.XYWH(...)` 矩形 + `xxx_hover` 布尔；`on_mouse_motion` 更新 hover，`on_mouse_press` 处理点击
- 文本用 `arcade.draw_text`（中文直写，anchor 对齐）
- 可滚动面板：继承 `scroll_view.py`（滚轮 + clamp + world_y 逻辑坐标 + 返回按钮）
- 怪物装备分配集中在 `game/monster_utils.py`（`assign_monster_armor/helmet/weapon`），`game_view.setup()` 调用
- 切换流程：StartView 为枢纽 → MapSelect → GameView（先 setup 再 show_view）；GameView ↔ BackpackView（TAB）；GameView 撤离成功 → StartView；ScrollView 返回 → StartView

## ANTI-PATTERNS
- 不重复造按钮/面板：先参考 start_view.py / scroll_view.py 现有模式
- 视图内不直接拼 SQL：经 `db/` 模块函数（如 `get_weapons(player_id)`）
- 跨视图传数据禁止全局变量：一律 `window.game_state`
- 动画/读条中禁滚动、禁点击（forge_view/market_view 开箱动画期间）
