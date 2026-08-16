# views/ - UI 视图层

**Updated:** 2026-08-16 | **Files:** 12 | **Lines:** ~6,546

## OVERVIEW
全部界面，均为 `arcade.View` 子类（`text_cache.py` 除外，是文本缓存工具类）。切换用 `window.show_view()`；共享状态一律走 `window.game_state`（main.GameState）。

## WHERE TO LOOK
| 视图 | 文件 | 要点 |
|------|------|------|
| 开始界面（携带武器显示 + 功能入口按钮） | start_view.py | 入口按钮模式样板，导航枢纽；init_db/get_or_create_player 引导点 |
| 地图选择（3 地图 forest/desert/space） | map_select_view.py | 地图种子选择（先写 seed/theme 再 gv.setup()） |
| 联机大厅（建房/加入/观战） | lobby_view.py | 联机入口：主机建房/客户端加入、全员就绪开局、观战（net_mode 判定） |
| 主游戏视图（**最大,3038 行**） | game_view.py | HUD、世界/屏幕坐标标签、实体生成编排、委托 game/ 层 |
| 仓库 | warehouse_view.py | 物品管理（资源/武器/装备售卖） |
| 市场 | market_view.py | 买卖 + 开箱动画（856 行，第二大） |
| 锻造坊（升级） | forge_view.py | 材料合成 + 神器，含纯函数 forge_result_level/merge_forge_effects |
| 背包 | backpack_view.py | 本次携带物；保存 game_view 引用返回不重建 |
| 撤离结果 | evac_result_view.py | 撤离成功/失败结算页（接 run_carried 副本） |
| 可滚动面板基类 | scroll_view.py | 滚动逻辑复用（ScrollView 子类继承） |
| 文本缓存工具（非 View） | text_cache.py | TextCache：持久 arcade.Text 缓存，避免每帧 draw_text 重建纹理 |

## CONVENTIONS
- 类名 `XxxView(arcade.View)`；构造接收 window 存 `self.window_ref`
- `on_show_view()` 设背景色；`on_draw()` 先 `self.clear()` 再绘制
- 按钮 = `arcade.XYWH(...)` 矩形 + `xxx_hover` 布尔；`on_mouse_motion` 更新 hover，`on_mouse_press` 处理点击
- 文本用 `arcade.draw_text`（中文直写，anchor 对齐）
- 可滚动面板：继承 `scroll_view.py`（滚轮 + clamp + world_y 逻辑坐标 + 返回按钮）
- 怪物装备分配集中在 `game/monster_utils.py`（`assign_monster_armor/helmet/weapon`），`game_view.setup()` 调用
- 切换流程：StartView 为枢纽 → MapSelect → GameView（先 setup 再 show_view）；GameView ↔ BackpackView（TAB）；GameView 撤离成功 → EvacResultView → StartView；ScrollView 返回 → StartView

## ANTI-PATTERNS
- 不重复造按钮/面板：先参考 start_view.py / scroll_view.py 现有模式
- 视图内不直接拼 SQL：经 `db/` 模块函数（如 `get_weapons(player_id)`）
- 跨视图传数据禁止全局变量：一律 `window.game_state`
- 动画/读条中禁滚动、禁点击（forge_view/market_view 开箱动画期间）
