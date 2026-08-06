# game/ - 核心游戏逻辑

**Updated:** 2026-08-06 | **Files:** 21 | **Lines:** ~3,500

## OVERVIEW
游戏逻辑层：怪物 AI、战斗、随机地图生成、掉落、撤离、宝箱、可采集物、特效、渲染、音效、输入、刷新。被 views/ 层编排；本层可经 `db.database` 读数据，`input_handler.py` 反向依赖 views（TAB 开背包）属例外。

## WHERE TO LOOK
| 任务 | 文件 |
|------|------|
| 怪物类（8 类：Zombie/Skeleton/Mummy/Camel/3 BOSS + 2 基类 + Projectile 弹丸） | monsters.py（最大,758 行） |
| 玩家精灵与移动 | player.py |
| 战斗：近战/远程弹丸/debuff/激光结算 | combat.py |
| 随机地图（房间+走廊+门+主题） | map_gen.py |
| 掉落表与掉落逻辑 | loot.py |
| 撤离点读条 + 撤离入库/死亡清空 | evac.py |
| 宝箱 | chest.py |
| 可采集物（树/矿石/石头/仙人掌） | harvestable.py |
| 粒子/漂浮文字/音效触发 | effects.py |
| 批量绘制 / 渲染辅助 / 主渲染 | batch_shapes.py / render_helpers.py / rendering.py |
| 程序化合成音效 | sound_manager.py |
| 键鼠输入映射 | input_handler.py |
| 怪物死亡回调 / 护甲头盔武器分配 / 工具函数 | entity_callbacks.py / monster_utils.py |
| 怪物重生 | respawn.py |

## CONVENTIONS
- 怪物类 = `arcade.SpriteSolidColor` 子类；构造接收 `center_x/center_y`；核心属性 `hp/max_hp/damage/speed/_attack_timer/_on_death_cb/_hit_flash/_walls`
- 怪物护甲/头盔：`armor`/`helmet`（dict 或 None）+ 掉落 id `armor_drop_id`/`helmet_drop_id`
- 附加状态：`debuffs` 列表 + `_debuff_tick`（每 0.5s 结算）+ `_debuff_speed_mult` + `_stunned`
- 碰撞：手写 AABB `_can_move_to(new_x, new_y, size, walls)`，不穿墙但可通过门出房间；地图边界用 MAP_WIDTH/HEIGHT
- 数值一律从 `config.py` 导入，模块内不硬编码
- 渲染辅助函数（render_helpers/batch_shapes）供 `views/game_view.py` 调用
- 依赖层级：叶子层(batch_shapes/map_gen/player/effects/sound_manager/harvestable/chest) → 中间层(monster_utils/loot/evac) → 聚合层(monsters/combat/respawn/render_helpers/rendering) → 回调汇聚层(entity_callbacks) → 输入层(input_handler)

## ANTI-PATTERNS
- 新增怪物不得改类结构：复制 Zombie 模式（见根 AGENTS.md 示例），不发明新属性名
- 不在本层直接绘制 UI 文本/按钮（那是 views/ 的职责）
- 不直接拼 SQL：数据库读写经 `db/` 模块函数（evac/rendering/input_handler 调用 db.database 属正常）
- **渲染禁空心/线框绘制**：`draw_*_outline`/`draw_arc_outline`/`draw_line` 等透明矢量层会导致闪烁，一律不透明实心填充（render_helpers.py 顶部铁律）
- **玩家位移禁重复 update**：位置更新逻辑已在 player.py 内实现，别再手动调 `player.update()`，否则位移翻倍（player.py:202 注释）
- **RocketPad 非 Sprite**：撤离火箭平台不是 arcade.Sprite，勿按 Sprite 处理（entity_callbacks.py:465 陷阱注释）
- **无背包一律不能拾取**：掉落拾取强制要求有背包（loot.py:174）
- 撤离金币结算口径以 evac.py 为准（避免与战利品入库口径混淆）
