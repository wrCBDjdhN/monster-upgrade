# game/ - 核心游戏逻辑

**Updated:** 2026-09-05 | **Files:** 20 | **Lines:** ~5,145

## OVERVIEW
游戏逻辑层：怪物 AI、战斗、随机地图生成、掉落、撤离、宝箱、可采集物、特效、渲染、音效、输入、刷新。被 views/ 层编排；本层可经 `db.database` 读数据，`input_handler.py` 反向依赖 views（TAB 开背包）属例外。

## WHERE TO LOOK
| 任务 | 文件 |
|------|------|
| 怪物类（**数据驱动**：2 参数化基类 + 13 薄类 + Projectile 弹丸） | monsters.py（603 行；薄类仅 `super().__init__(**MONSTER_CONFIGS[name])`，AI 唯一实现在基类） |
| 怪物数值/元数据（**禁在此硬编码**） | entities/monster_defs.py（MONSTER_CONFIGS/MONSTER_METADATA，见 entities/AGENTS.md） |
| 玩家精灵与移动 | player.py |
| 战斗：近战/远程弹丸/debuff/激光结算 | combat.py |
| 随机地图（房间+走廊+门+主题，3 主题 forest/desert/space） | map_gen.py |
| 掉落表与掉落逻辑 | loot.py |
| 撤离点读条 + 撤离入库/死亡清空 | evac.py |
| 宝箱 | chest.py |
| 可采集物（树/矿石/石头/仙人掌） | harvestable.py |
| 粒子/漂浮文字/音效触发 | effects.py |
| 批量绘制 / 渲染辅助 / 主渲染 | batch_shapes.py / render_helpers.py / rendering.py（760 行，render_game 单函数） |
| 程序化合成音效 | sound_manager.py |
| 键鼠输入映射 | input_handler.py |
| 怪物死亡回调汇聚（含拾取/掉落/装备分配联动） | entity_callbacks.py |
| 火箭发射台（space 主题撤离装置，**非 Sprite**） | rocket_pad.py |
| 怪物护甲/头盔/武器分配 | monster_utils.py |
| 怪物重生 | respawn.py |
| 角色技能（技能效果、冷却、释放） | character_skills.py |

## CONVENTIONS
- 怪物类 = `_MeleeMonsterBase`/`_RangedMonsterBase` 子类（arcade.SpriteSolidColor）；**具体怪物 = 薄类**，构造 `super().__init__(center_x=..., center_y=..., **MONSTER_CONFIGS["Xxx"])`，数值一律来自 entities/monster_defs.py
- 核心属性：`hp/max_hp/damage/speed/_attack_timer/_on_death_cb/_hit_flash/_walls`；护甲 `armor`/`armor_drop_id`；头盔 `helmet`/`helmet_drop_id`；武器 `weapon`（掉落用）
- 附加状态：`debuffs` 列表 + `_debuff_tick`（每 0.5s 结算）+ `_debuff_speed_mult` + `_stunned`；BOSS 标记 `is_boss` + `required_weapon_level`
- 碰撞：手写 AABB `_can_move_to(new_x, new_y, size, walls)`，不穿墙但可通过门出房间；地图边界用 MAP_WIDTH/HEIGHT
- 数值一律从 `config.py` 导入，模块内不硬编码
- 渲染辅助函数（render_helpers/batch_shapes）供 `views/game_view.py` 调用
- 依赖层级：叶子层(batch_shapes/map_gen/player/effects/sound_manager/harvestable/chest/rocket_pad) → 中间层(monster_utils/loot/evac) → 聚合层(monsters/combat/respawn/render_helpers/rendering) → 回调汇聚层(entity_callbacks) → 输入层(input_handler)

## ANTI-PATTERNS
- **新增怪物禁硬编码数值**：数值写进 `entities/monster_defs.py`（MONSTER_CONFIGS + MONSTER_METADATA），类内只写 `**MONSTER_CONFIGS[name]`；禁复制旧版属性直填模式（见根 AGENTS.md 示例）
- 不在本层直接绘制 UI 文本/按钮（那是 views/ 的职责）
- 不直接拼 SQL：数据库读写经 `db/` 模块函数（evac/rendering/input_handler 调用 db.database 属正常）
- **渲染禁空心/线框绘制**：`draw_*_outline`/`draw_arc_outline`/`draw_line` 等透明矢量层会导致闪烁，一律不透明实心填充（render_helpers.py 顶部铁律）
- **玩家位移禁重复 update**：位置更新逻辑已在 player.update() 内实现（player.py 内注释），别再手动调 `player.update()`，否则位移翻倍
- **RocketPad 非 Sprite**：撤离火箭平台不是 arcade.Sprite，勿按 Sprite 处理（entity_callbacks.py 陷阱注释）
- **无背包一律不能拾取**：掉落拾取强制要求有背包（loot.py skipped_no_bag 分支）
- 撤离金币结算口径以 evac.py 为准（避免与战利品入库口径混淆）
