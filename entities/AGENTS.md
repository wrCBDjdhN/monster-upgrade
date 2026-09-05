# entities/ - 静态数据定义

**Updated:** 2026-09-05 | **Files:** 7 | **Lines:** ~1109

## OVERVIEW
数据定义层：武器/装备/资源/怪物/效果/角色模板（weapon/equipment/resource 3 个纯字典 + character_defs 角色定义 + effects_defs 含规则逻辑 + monster_defs 含注册表逻辑）。**添加新条目 = 复制现有条目改值**，不发明新结构。被 db/game/views 三方引用；本层只依赖 config（weapon_defs 用 PROJECTILE_SPEED，monster_defs 用 PROJECTILE_SPEED/PROJECTILE_SIZE，character_defs 用 PLAYER_SPEED/PLAYER_HP）。

## WHERE TO LOOK
| 任务 | 文件 |
|------|------|
| 武器 | weapon_defs.py（MELEE 6 + RANGED 10 + ALL_WEAPONS + get_weapon_visual） |
| 装备/药水 | equipment_defs.py（HELMETS/ARMORS/BACKPACKS/POTIONS + 怪物掉落表 + get_item_def；头盔/护甲/背包各含 `artifact:True` 神器条目，仅锻造获取） |
| 怪物（**数据驱动核心**） | monster_defs.py（MONSTER_CONFIGS 数值 + MONSTER_METADATA 渲染/掉落/武器池 + MELEE/RANGED_MONSTERS 分类 + get_boss_config） |
| 资源 | resource_defs.py（RESOURCES 3 种） |
| 角色 | character_defs.py（CHARACTERS 4 角色 + skill/passive 定义） |
| 效果规则（**含逻辑，非纯数据**） | effects_defs.py（效果池 + 5 级分级表 + 9 个函数） |

## 字段 schema（核心字段必填，其余可选）
- 武器：`kind`(melee/ranged) / `item_id` / `name`(中文) / `damage` / `attack_speed`(越高越快) / `range` / `price`(0=禁购) / `color` / `shape` / `capacity_cost`
- 装备：`name` / `defense`(头盔护甲) 或 `capacity`(背包) / `price` / `color` / `description` / `capacity_cost`；药水用 `effect`+`value`(+`duration`)+`stackable`
- 资源：`name` / `sell_price` / `color`
- 角色：`CHARACTERS[id]` = name / hp / defense / speed / color / price(0=免费) / skill{id/name/cooldown/damage_mult+附加字段} / passive{damage_mult/crit_chance/crit_mult/flat_reduce+...}
- 怪物：`MONSTER_CONFIGS[name]` = size/color/hp/damage/speed/attack_delay/aggro_range(+远程 proj_*)；`MONSTER_METADATA[name]` = weapon_color/loot_key/death_color/weapon_pool(+armor_key/group_count)
- 可选标记：`market_restricted`(市场禁购但保留随机池) / `artifact`(神器仅锻造) / `special`(penetrating/explosive/laser) / `debuff` / `auto_fire` / `is_boss` / `required_weapon_level`

## monster_defs.py 规则（易错点）
- **新增怪物 = 双字典各加一条**：MONSTER_CONFIGS（数值）+ MONSTER_METADATA（渲染/掉落/武器池），无需改 game/monsters.py
- **BOSS 数值内联在 MONSTER_CONFIGS**：BossXxx 条目直接写 `hp*8` / `damage*4` / `speed*0.7` + `is_boss` + `required_weapon_level=5`，不单独建表；`get_boss_config(base_name)` 为备用辅助函数（当前未被调用）
- `MELEE_MONSTERS` / `RANGED_MONSTERS` 分类决定怪物继承哪个基类，新增时必须归入其一（共 13 类：MELEE 5 + RANGED 8，含 4 BOSS）

## effects_defs.py 规则（易错点）
- 效果数量随物品等级：Lv<5=0、5-10=1、10-20=2、20-30=3、30-40=4、40+=5
- 效果等级派生：物品 Lv1-9→效果 Lv1 ... Lv40+→Lv5；`effect_level_for_item_level()`
- 序列化格式 `"id:level"` 逗号分隔（纯 id 视为 Lv1），与 db 的 `effects` 列配套
- **改效果数值需同时改 `EFFECTS` 基础值 和 `EFFECT_LEVELS` 分级值**

## ANTI-PATTERNS
- 字典内不放逻辑/函数调用（effects_defs / monster_defs 是例外，规则/注册函数归它们）
- 数值不硬编码到 game/views：一律查定义字典或从 config 导入
- 不改 `item_id` 键名（db 持久化依赖它）
