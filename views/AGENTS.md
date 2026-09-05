# views/ - UI 视图层

**Updated:** 2026-09-05 | **Files:** 17 | **Lines:** ~7,746

## OVERVIEW
全部界面，均为 `arcade.View` 子类（`text_cache.py` 文本缓存与 `tutorial.py` 教程辅助两模块除外，非 View 类）。切换用 `window.show_view()`；共享状态一律走 `window.game_state`（main.GameState）。

## WHERE TO LOOK
| 视图 | 文件 | 要点 |
|------|------|------|
| 开屏动画（v1.2.0 新增） | splash_view.py | 三阶段 PHASE_LOGO→GLOW→EXIT（品牌展示→光效过渡→切 StartView）；粒子+光环+射线；任意键跳过；播 assets/sounds/splash.wav |
| 开始界面（携带武器显示 + 功能入口按钮 + 设置入口） | start_view.py | 入口按钮模式样板，导航枢纽；init_db/get_or_create_player 引导点 |
| 地图选择（3 地图 forest/desert/space） | map_select_view.py | 地图种子选择（先写 seed/theme 再 gv.setup()）；教程阶段 2 挂钩 |
| 联机大厅（建房/加入/观战） | lobby_view.py | 联机入口：主机建房/客户端加入、全员就绪开局、观战（net_mode 判定） |
| 主游戏视图（**最大,2699 行**） | game_view.py | HUD、世界/屏幕坐标标签、实体生成编排、委托 game/ 层；教程阶段 3 钩子（draw_in_game_tutorial） |
| 设置界面（音量滑块/静音/按键重绑两列） | settings_view.py | 可从开始界面打开，也可携 game_view 引用从游戏内打开返回不重建；ESC 关闭；读写 db.settings |
| 新手教程辅助（**非 View**） | tutorial.py | TutorialPage 页定义 + draw_in_game_tutorial（游戏内覆盖层）+ finish_tutorial（完成标记，10 处视图调用）；状态机 TutorialState 在 main.py（stage 0-6） |
| 仓库 | warehouse_view.py | 物品管理（资源/武器/装备售卖） |
| 市场 | market_view.py | 买卖 + 开箱动画（856 行，第二大） |
| 锻造坊（升级） | forge_view.py | 材料合成 + 神器，含纯函数 forge_result_level/merge_forge_effects |
| 背包 | backpack_view.py | 本次携带物；保存 game_view 引用返回不重建 |
| 撤离结果 | evac_result_view.py | 撤离成功/失败结算页（接 run_carried 副本）；教程阶段 4 挂钩 |
| 可滚动面板基类 | scroll_view.py | 滚动逻辑复用（ScrollView 子类继承） |
| 文本缓存工具（非 View） | text_cache.py | TextCache：持久 arcade.Text 缓存，避免每帧 draw_text 重建纹理 |
| 角色选择界面（4 角色/技能/经验升级永久加成） | character_select_view.py | 角色选择 UI：4 角色展示、技能说明、经验升级永久加成；教程阶段 1 挂钩 |
| 升级面板（3 选 1 永久加成） | level_up_view.py | 升级面板：3 选 1 永久加成（arcade.View 子类） |

## CONVENTIONS
- 类名 `XxxView(arcade.View)`；构造接收 window 存 `self.window_ref`
- `on_show_view()` 设背景色；`on_draw()` 先 `self.clear()` 再绘制
- 按钮 = `arcade.XYWH(...)` 矩形 + `xxx_hover` 布尔；`on_mouse_motion` 更新 hover，`on_mouse_press` 处理点击
- 文本优先经 `TextCache`（中文直写，anchor 对齐），避免每帧重建纹理
- 可滚动面板：继承 `scroll_view.py`（滚轮 + clamp + world_y 逻辑坐标 + 返回按钮）
- 覆盖层模式（背包/升级面板/设置）：构造时保存 `game_view` 引用，关闭时 show_view 回原 GameView 不重建
- 教程接入：各界面按 stage 绘制对应 TutorialPage 并在推进处调 `finish_tutorial(window.game_state.tutorial)`；中途退出不写标记，下次启动重来
- 教程覆盖层遵守渲染铁律：全部不透明实心填充（禁空心/线框）
- 怪物装备分配集中在 `game/monster_utils.py`（`assign_monster_armor/helmet/weapon`），`game_view.setup()` 调用
- 切换流程：SplashView → StartView 为枢纽 → CharacterSelect → MapSelect → GameView（先 setup 再 show_view）；GameView ↔ BackpackView/SettingsView（TAB/ESC）；GameView 撤离成功 → EvacResultView → StartView；ScrollView 返回 → StartView

## ANTI-PATTERNS
- 不重复造按钮/面板：先参考 start_view.py / scroll_view.py 现有模式
- 视图内不直接拼 SQL：经 `db/` 模块函数（如 `get_weapons(player_id)`）
- 跨视图传数据禁止全局变量：一律 `window.game_state`
- 动画/读条中禁滚动、禁点击（forge_view/market_view 开箱动画期间）
