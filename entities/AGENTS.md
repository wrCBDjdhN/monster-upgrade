# entities/ - 静态数据定义

## OVERVIEW
数据定义层：武器/装备/资源/效果模板（3 个纯字典 + effects_defs 含规则逻辑）。**添加新条目 = 复制现有条目改值**，不发明新结构。被 db/game/views 三方引用；本层只依赖 config（weapon_defs 用 PROJECTILE_SPEED）。

## WHERE TO LOOK
| 任务 | 文件 |
|------|------|
| 武器 | weapon_defs.py（MELEE 6 + RANGED 10 + ALL_WEAPONS + get_weapon_visual） |
| 装备/药水 | equipment_defs.py（HELMETS/ARMORS/BACKPACKS/POTIONS + 怪物掉落表 + get_item_def） |
| 资源 | resource_defs.py（RESOURCES 3 种） |
| 效果规则（**含逻辑，非纯数据**） | effects_defs.py（效果池 + 5 级分级表 + 9 个函数） |

## 字段 schema（核心字段必填，其余可选）
- 武器：`kind`(melee/ranged) / `item_id` / `name`(中文) / `damage` / `attack_speed`(越高越快) / `range` / `price`(0=禁购) / `color` / `shape` / `capacity_cost`
- 装备：`name` / `defense`(头盔护甲) 或 `capacity`(背包) / `price` / `color` / `description` / `capacity_cost`；药水用 `effect`+`value`(+`duration`)+`stackable`
- 资源：`name` / `sell_price` / `color`
- 可选标记：`market_restricted`(市场禁购但保留随机池) / `artifact`(神器仅锻造) / `special`(penetrating/explosive/laser) / `debuff` / `auto_fire`

## effects_defs.py 规则（易错点）
- 效果数量随物品等级：Lv<5=0、5-10=1、10-20=2、20-30=3、30-40=4、40+=5
- 效果等级派生：物品 Lv1-9→效果 Lv1 ... Lv40+→Lv5；`effect_level_for_item_level()`
- 序列化格式 `"id:level"` 逗号分隔（纯 id 视为 Lv1），与 db 的 `effects` 列配套
- **改效果数值需同时改 `EFFECTS` 基础值 和 `EFFECT_LEVELS` 分级值**

## ANTI-PATTERNS
- 字典内不放逻辑/函数调用（effects_defs 是唯一例外，规则函数归它）
- 数值不硬编码到 game/views：一律查定义字典或从 config 导入
- 不改 `item_id` 键名（db 持久化依赖它）
