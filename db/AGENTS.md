# db/ - SQLite 数据层

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

## CONVENTIONS
- 统一 `with _conn() as c:` —— sqlite3 Connection 的 with 只做 **commit/rollback，不关闭连接**（靠 GC）；取插入 ID 用 `c.execute("SELECT last_insert_rowid()").fetchone()[0]`
- **新增函数必须追加到 `database.py` 的 re-export 列表**，否则业务方用不了
- 函数级延迟 import：`db → entities → config` 依赖链，函数内 `from entities.*_defs import ...` 防循环导入
- 迁移模式：`try: c.execute("ALTER TABLE ... ADD COLUMN ...") except: pass`（幂等加列）
- `effects` 列 = `"id:level"` 逗号分隔字符串（纯 id 视为 Lv1），与 `entities/effects_defs.py` 的 serialize/parse 配套
- 定价口径从 config 取：`UPGRADE_BASE_COST` / `upgrade_mult_for_level` / `SELL_COST_RECOVERY_RATIO` 等

## 表结构速查（5 张表，init_db() 创建）
```
players          id PK, name UNIQUE, gold=50, created_at
warehouse_items  id PK, player_id FK, item_type CHECK('resource','weapon'), item_id, quantity
weapons          id PK, player_id FK, item_id, kind CHECK('melee','ranged'), name, damage, attack_speed, level, effects
equipment        id PK, player_id FK, slot CHECK('helmet','armor','backpack'), item_id, name, defense, capacity, level, is_equipped, effects
potions          id PK, player_id FK, item_id, name, effect, value, duration, quantity
```

## ANTI-PATTERNS
- 业务代码不得直接 import `db.players`/`db.weapons` 等子模块（一律走 database.py re-export）
- 不直接写裸 sqlite3.connect：统一 `_conn()`
- 不在本层放游戏逻辑（升级公式/掉落规则归 game/ 或 config）
