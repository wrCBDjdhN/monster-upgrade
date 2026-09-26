# db/ - SQLite 数据层

**Updated:** 2026-09-05 | **Files:** 11 | **Lines:** ~825

## OVERVIEW
sqlite3 stdlib 数据访问层。**业务方只 `from db.database import ...`**（全量 re-export 兼容层），子模块间只经 `db.connection._conn` 交互。`db/game.db` 为数据文件（pyright 已排除）。

## WHERE TO LOOK
| 任务 | 文件 |
|------|------|
| 连接入口 | connection.py（DB_PATH + `_conn()`） |
| 建表/迁移 + re-export 兼容层 | database.py（`init_db()` 唯一 schema 所有者） |
| 玩家 | players.py（get_or_create_player / get_gold / add_gold / spend_gold） |
| 武器 | weapons.py（CRUD + 同名同级升级 + 售卖回收） |
| 装备（最复杂,12 函数） | equipment.py（CRUD + 穿戴 is_equipped + 升级/售卖/总防御/背包容量） |
| 仓库 | warehouse.py（按 (player_id,item_type,item_id) 叠加数量） |
| 药水 | potions.py（叠加数量 + 使用） |
| 角色（创建/读取/更新角色） | characters.py（解锁/查询已解锁角色） |
| 等级（升级曲线、经验获取） | levels.py（经验累积/自动升级/选择永久加成） |
| 设置键值表（v1.2.0 新增） | settings.py（get/set_setting 通用 KV + get/set_volume + get/set_sound_enabled + get/set/reset_key_bindings + is_tutorial_done/mark_tutorial_done） |
| 图鉴解锁与档位领奖（v1.5.0 新增） | codex.py（unlock_codex_entry / get_codex_unlocks / get_codex_count / get_claimed_rewards / claim_codex_reward；档位阈值口径取 config.codex_tiers_for） |
| 每日任务与成就（v1.5.0 新增） | missions.py（roll_daily / get_daily / bump_mission / claim_daily + get_achievements / bump_achievement / claim_achievement；事件键口径见 entities/mission_defs.MISSION_EVENT_KEYS） |
| 地图星级（v1.5.0 新增） | map_progress.py（get_stars / record_stars，UPSERT 取 max 实现「只升不降」，钳位 config.MAP_MAX_STARS） |
| 设施等级（v1.5.0 新增） | facilities.py（get_facilities / get_facility_level / facility_can_afford / build_facility / upgrade_facility；扣仓库材料+金币后 UPSERT 等级，level 0=未建造） |

## CONVENTIONS
- 统一 `with _conn() as c:` —— sqlite3 Connection 的 with 只做 **commit/rollback，不关闭连接**（靠 GC）；取插入 ID 用 `c.execute("SELECT last_insert_rowid()").fetchone()[0]`
- **新增函数必须追加到 `database.py` 的 re-export 列表**，否则业务方用不了
- 函数级延迟 import：`db → entities → config` 依赖链，函数内 `from entities.*_defs import ...` 防循环导入
- 迁移模式：`try: c.execute("ALTER TABLE ... ADD COLUMN ...") except: pass`（幂等加列）
- `effects` 列 = `"id:level"` 逗号分隔字符串（纯 id 视为 Lv1），与 `entities/effects_defs.py` 的 serialize/parse 配套
- 定价口径从 config 取：`UPGRADE_BASE_COST` / `upgrade_mult_for_level` / `SELL_COST_RECOVERY_RATIO` 等

## 表结构速查（14 张表，init_db() 创建）
```
players            id PK, name UNIQUE, gold=50, created_at
warehouse_items    id PK, player_id FK, item_type CHECK('resource','weapon'), item_id, quantity
weapons            id PK, player_id FK, item_id, kind CHECK('melee','ranged'), name, damage, attack_speed, level, effects
equipment          id PK, player_id FK, slot CHECK('helmet','armor','backpack'), item_id, name, defense, capacity, level, is_equipped, effects
potions            id PK, player_id FK, item_id, name, effect, value, duration, quantity
character_unlocks  id PK, player_id FK, character_id, unlocked_at, UNIQUE(player_id, character_id)
character_levels   id PK, player_id FK, character_id, level=1, exp=0, pending_choices=0, bonus_hp/damage/defense/speed/atk_speed, UNIQUE(player_id, character_id)
settings           key TEXT PK, value TEXT（音量/静音/键位 JSON/tutorial_done 标记；重启保留）
codex_unlocks      id PK, player_id FK, category, item_id, unlocked_at, UNIQUE(player_id, category, item_id)
daily_missions     player_id, slot, mission_id, progress=0, claimed=0, date, PRIMARY KEY(player_id, slot)
mission_progress   player_id, achievement_id, progress=0, claimed=0, PRIMARY KEY(player_id, achievement_id)
codex_rewards      player_id, category, tier, claimed_at, PRIMARY KEY(player_id, category, tier)
map_stars          player_id, theme, stars=0, PRIMARY KEY(player_id, theme)
facilities         player_id, facility_id, level=0, PRIMARY KEY(player_id, facility_id), FK→players(id)
```

## ANTI-PATTERNS
- 业务代码不得直接 import `db.players`/`db.weapons` 等子模块（一律走 database.py re-export）；**新模块/新函数同样必须追加到 `db/database.py` 的 re-export 段**（init_db 后的 `from db.xxx import ...` 块）
- 不直接写裸 sqlite3.connect：统一 `_conn()`
- 不在本层放游戏逻辑（升级公式/掉落规则归 game/ 或 config）
- 阶梯表（`daily_missions`/`mission_progress`/`codex_rewards`/`map_stars`/`facilities`）一律**复合主键**，禁加自增 id（保证同玩家同键只一行，UPSERT 幂等）
- `db/missions.py` 每日任务进度按 `date` 跨天重置，成就进度累计不清零，两张表勿混用同一清零逻辑
