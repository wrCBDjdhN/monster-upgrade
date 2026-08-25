# PROJECT KNOWLEDGE BASE - 打怪升级项目

**Updated:** 2026-08-25
**Commit:** 48ec430
**Branch:** master
**Stats:** 66 Python files, ~17,068 行（v1.2.0：开屏动画 + 新手教程 + 设置界面 + 打包管线就位）

## 项目知识库（结构速览）

> 技术栈：Python 3.14 + Arcade 2D 引擎 + SQLite（依赖 `arcade` + `websockets`（局域网联机））。运行：`python main.py`

### 目录结构
```
打怪升级/
├── main.py       # 入口：GameWindow（固定逻辑分辨率投影器）+ GameState + TutorialState（教程状态机）
├── config.py     # 全部数值常量（窗口/玩家/怪物/战斗/掉落/升级公式/宝箱/火箭发射台），调整平衡性只改这里
├── entities/     # 数据定义：weapon_defs / equipment_defs / monster_defs / resource_defs / effects_defs / character_defs（见 entities/AGENTS.md）
├── db/           # SQLite 层：connection / database(建表+CRUD re-export) / players / weapons / equipment / warehouse / potions / characters / levels / settings（见 db/AGENTS.md）
├── game/         # 核心逻辑：怪物AI / 战斗 / 地图生成 / 掉落 / 撤离 / 宝箱 / 特效 / 渲染 / 音效 / 输入 / 刷新 / 回调汇聚 / 角色技能（见 game/AGENTS.md）
├── net/          # 联机网络层：protocol / server / client / thread_bridge + 4 个 _selftest 自检脚本（见 net/AGENTS.md）
├── views/        # UI：splash(开屏) / start / map_select / game(2699行,最大) / lobby / settings / tutorial / warehouse / market / forge / backpack / scroll / text_cache / character_select / level_up（见 views/AGENTS.md）
├── packaging/    # 打包三件套：MonsterUpgrade.spec(PyInstaller) / build.ps1(一键构建) / installer.iss(Inno Setup 安装包)
├── assets/       # 二进制资产：icon.ico(打包用) / sounds/splash.wav(开屏音效)；其余音效由 game/sound_manager.py 程序化合成
└── docs/         # 文档：net-mode-matrix.md（联机模式矩阵）
```

### 高频入口速查
| 想做什么 | 去哪里 |
|---------|--------|
| 调数值/平衡 | `config.py`（禁硬编码数值） |
| 加武器/装备/资源/效果 | `entities/*_defs.py`（effects 规则见 entities/AGENTS.md） |
| 加怪物数据 | `entities/monster_defs.py`（MONSTER_CONFIGS + MONSTER_METADATA 各加一条，13 类含 4 BOSS） |
| 加怪物类 | `game/monsters.py`（继承 `_MeleeMonsterBase`/`_RangedMonsterBase` 的薄类，复制 Zombie 模式） |
| 加怪物装备分配 | `game/monster_utils.py`（assign_monster_weapon/armor/helmet） |
| 加 UI 界面 | `views/*_view.py`（arcade.View 子类，可滚动面板继承 scroll_view.py） |
| 加数据库操作 | `db/`（sqlite3 stdlib，`with _conn() as c`；新函数须追加到 db/database.py re-export） |
| 视图间传数据 | `window.game_state`（main.GameState），禁全局变量 |
| 加联机协议消息 | `net/protocol.py`（MsgType 枚举 + 消息 schema，见 net/AGENTS.md） |
| 跑网络自检 | `python net/_selftest*.py`（4 个自检脚本，返回码 0=通过） |
| 联机模式判定 | `window.game_state.net_mode`（solo/host/client），客户端禁本地仲裁 |
| 角色系统 | `entities/character_defs.py` + `db/characters.py` + `game/character_skills.py` + `views/character_select_view.py` |
| 升级系统 | `db/levels.py` + `views/level_up_view.py` + `game/character_skills.py` |
| 设置界面/按键重绑 | `views/settings_view.py`（UI）+ `db/settings.py`（settings 键值表：音量/静音/键位） |
| 新手教程 | `main.TutorialState`（stage 0-6 状态机）+ `views/tutorial.py`（页面定义+绘制+完成标记） |
| 打包发布 exe/安装包 | `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`（PyInstaller onedir → Inno Setup） |

### CODE MAP（核心符号）
| 符号 | 类型 | 位置 | 角色 |
|------|------|------|------|
| `GameWindow` | class | main.py:64 | 窗口：FixedLogicalProjector 固定逻辑分辨率 + letterbox；dispatch_event 拦截鼠标坐标换算与 F11 全局全屏 |
| `TutorialState` | class | main.py:148 | 新手教程状态机（stage 0-6/page/击杀拾取计数），首次启动（DB 未标记 tutorial_done）激活 |
| `GameState` | class | main.py:171 | 各 View 共享运行时状态（player_id/run_carried/run_potions/武器/地图种子/net_mode/tutorial） |
| `SplashView` | class | views/splash_view.py:42 | 开屏动画三阶段（品牌展示→光效过渡→切 StartView），任意键跳过，播 assets/sounds/splash.wav |
| `RocketPad` | class | game/rocket_pad.py | 火箭发射台状态机 IDLE→ACTIVATED→BOSS_SPAWNED→BOSS_DEFEATED→DESTROYED/EVACUATING→EVAC_SUCCESS（非 Sprite） |
| `_MONSTER_CLASSES` | dict | views/game_view.py | 怪物类型名 → 类映射（数据驱动注册） |
| `MONSTER_CONFIGS` | dict | entities/monster_defs.py | 怪物数值配置（hp/damage/speed/弹丸参数），BOSS 条目内联倍率（hp×8/damage×4/speed×0.7） |
| `MONSTER_METADATA` | dict | entities/monster_defs.py | 怪物渲染/掉落/武器池元数据（entity_callbacks/monster_utils/rendering 3 处消费） |
| `assign_monster_armor/helmet/weapon` | func | game/monster_utils.py | 怪物装备分配（等级范围取 config） |
| `_MeleeMonsterBase` / `_RangedMonsterBase` | class | game/monsters.py | 近战/远程怪物参数化基类（AI 行为唯一实现处） |
| `entity_callbacks` | module | game/entity_callbacks.py | 怪物死亡回调汇聚 + RocketPad 陷阱注释（:428） |
| `commit_run_to_warehouse` | func | game/evac.py | 撤离入库唯一口径（gold=本次携带金币） |
| `TextCache` | class | views/text_cache.py | 持久 arcade.Text 缓存，避免每帧 draw_text 重建纹理 |
| `CharacterSkills` | class | game/character_skills.py | 角色技能系统（技能效果、冷却、释放） |
| `CharacterSelectView` | class | views/character_select_view.py | 角色选择界面（4角色/技能/经验升级永久加成） |
| `LevelUpView` | class | views/level_up_view.py | 升级面板（3选1永久加成） |
| `CharactersDB` | module | db/characters.py | 角色数据库操作（创建/读取/更新角色） |
| `LevelsDB` | module | db/levels.py | 等级/经验数据操作（升级曲线、经验获取） |

### 关键约定
- 中文 docstring + 中文注释为硬性约定；汇报必须中文
- 视图切换用 `window.show_view()`；函数内延迟 import 避免 views 循环依赖（全仓约 110 处，导航唯一模式：处理函数内 `from views.xxx import XxxView` → `XxxView(self.window_ref)` → `show_view`）
- 启动链：`main()` = init_db → 读 `db.settings.is_tutorial_done` 决定是否激活教程 → SplashView（任意键跳过）→ StartView
- GameWindow 已全局拦截 F11 切全屏，且 dispatch_event 把所有鼠标事件坐标统一换算为 1280×720 逻辑坐标——各视图拿到的 (x,y) 无需再自行换算
- 怪物护甲/头盔/武器分配集中在 `game/monster_utils.py` 的 `assign_monster_armor()`/`assign_monster_helmet()`/`assign_monster_weapon()`
- 怪物数值/元数据集中在 `entities/monster_defs.py`（MONSTER_CONFIGS/MONSTER_METADATA），`game/monsters.py` 禁硬编码数值（数据驱动重构后 13 类，含 4 BOSS）
- `db/database.py` 保留全量 re-export 兼容旧 import；`db/game.db` 为 SQLite 数据文件（pyright 已排除）
- 玩家速度 4px/帧：PhysicsEngineSimple 不乘 delta_time
- 渲染禁空心/线框绘制（`draw_*_outline`/`draw_line` 等会导致闪烁）：一律不透明实心填充（见 game/AGENTS.md）
- game/ 层可经 `db.database` 读数据、`input_handler.py` 反向依赖 views（TAB 开背包）属例外
- net 层铁律：协议禁静默忽略未知消息、回调禁阻塞主线程、回调禁碰 arcade 对象（见 net/AGENTS.md）
- `Player.update()` 已含移动逻辑，禁手动二次调用（否则位移翻倍，player.py 内注释）；RocketPad 非 Sprite，勿按 Sprite 处理（entity_callbacks.py 陷阱注释）
- 动画/读条期间禁滚动/点击（views 层约定，见 views/AGENTS.md）
- 角色系统：`character_defs.py`（角色数据）+ `db/characters.py`（角色持久化）+ `game/character_skills.py`（技能逻辑）+ `views/character_select_view.py`（选角UI）
- 升级系统：`db/levels.py`（等级/经验数据）+ `views/level_up_view.py`（升级界面）+ `game/character_skills.py`（技能效果）

## 核心工作流程

每次接收任务后，必须按以下顺序执行：

### 第一步：理解任务
1. **阅读相关代码**：在开始修改前，必须先阅读涉及的文件，理解当前实现
2. **确认理解**：用自己的话复述任务目标，确保理解正确
3. **提出疑问**：如有任何不清楚的地方，必须向用户询问，不要假设

### 第二步：变更确认（硬性要求）
1. **陈述修改方案**：在实施任何代码修改之前，必须向用户清晰陈述当前将要进行的修改——包括涉及的文件、具体修改内容、修改原因
2. **等待用户同意**：必须得到用户明确同意后，才能开始修改；未获同意前不得动手
3. **范围变更重新确认**：实施过程中若修改范围或方案发生变化，须重新向用户陈述并再次获得同意

### 第三步：执行任务
1. **最小化修改**：只修改必要的代码，不要做无关的重构
2. **保持功能不变**：除非用户明确要求，不要改变游戏现有功能
3. **添加注释**：修改处添加中文注释说明修改原因

### 第四步：测试验证
1. **运行测试**：本仓库**无 pytest 套件**，但有 4 个网络自检脚本（`python net/_selftest*.py`，返回码 0=通过）；验证手段 = 网络自检 + 手动运行 `python main.py` + 静态检查 `pyright`（配置见 pyrightconfig.json）
2. **检查语法**：确保代码没有语法错误（可用 `pyright` 或 `python -m py_compile <文件>`）
3. **验证逻辑**：确认修改后的逻辑符合预期

### 第五步：汇报结果
1. **使用中文汇报**：所有汇报必须使用中文
2. **说明修改内容**：清晰列出做了哪些修改
3. **Bug修复说明**：如果是修复bug，必须说明：
   - Bug产生的原因
   - 如何发现的
   - 如何修复的
   - 修复后的效果

---

## 代码修改规范

### 文件修改前
- 先读取文件内容，理解当前实现
- 确认修改不会破坏现有功能

### 文件修改时
- 保持代码风格一致
- 添加必要的注释
- 不要删除用户没有要求删除的代码

### 文件修改后
- 检查语法错误
- 验证修改结果
- 如有测试，运行测试

---

## 扩展性任务规范（重要）

当需要添加新内容时（如新地图、新怪物、新武器、新装备等），**必须参考现有代码模式**，而不是自己发挥。

### 必须参考的现有实现

| 任务类型 | 参考文件 | 参考内容 |
|---------|---------|---------|
| 添加新怪物 | `entities/monster_defs.py` + `game/monsters.py` | 数据驱动：先加 MONSTER_CONFIGS/MONSTER_METADATA 条目，再建继承 `_MeleeMonsterBase`/`_RangedMonsterBase` 的薄类（数值禁写进类内） |
| 添加新怪物生成 | `game/monster_utils.py` | `assign_monster_armor()`、`assign_monster_helmet()`、`assign_monster_weapon()` |
| 添加新武器 | `entities/weapon_defs.py` | 武器定义格式（name/damage/attack_speed/range/price/color） |
| 添加新装备 | `entities/equipment_defs.py` | 装备定义格式（name/defense/price/color/description） |
| 添加新掉落物 | `game/loot.py` | 掉落表格式、掉落逻辑 |
| 添加新资源 | `entities/resource_defs.py` | 资源定义格式（name/sell_price/color） |
| 添加新地图元素 | `game/map_gen.py` | 生成逻辑、位置计算、碰撞处理 |
| 添加新UI元素 | `views/game_view.py` | HUD绘制、世界坐标标签、屏幕坐标标签 |
| 添加新效果 | `game/effects.py` | 粒子系统、浮动文字、音效播放 |
| 添加新角色 | `entities/character_defs.py` + `db/characters.py` | 角色数据定义 + 持久化操作 |
| 添加新技能 | `game/character_skills.py` | 技能效果、冷却、释放逻辑 |

### 操作步骤

1. **先读取参考文件**：找到类似的现有实现
2. **复制模式**：按照现有代码的结构和风格编写新代码
3. **保持一致**：变量命名、注释风格、代码结构必须与现有代码一致
4. **不要创新**：除非现有模式无法满足需求，否则不要发明新的实现方式

### 示例

**正确做法**（添加新怪物，数据驱动模式）：
```python
# 1) entities/monster_defs.py 中 MONSTER_CONFIGS 加数值条目、MONSTER_METADATA 加渲染/掉落元数据
# 2) game/monsters.py 建薄类（复制 Zombie 模式，数值全部来自 MONSTER_CONFIGS）
class NewMonster(_MeleeMonsterBase):
    """新怪物（近战）：描述其特性"""

    def __init__(self, center_x=0, center_y=0):
        super().__init__(center_x=center_x, center_y=center_y, **MONSTER_CONFIGS["NewMonster"])
```

**错误做法**（自己发挥，旧版硬编码模式）：
```python
# 不参考现有代码，自己发明新的结构，且数值硬编码在类内
class NewMonster:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.health = 100  # 变量名不一致
        self.attack_power = 10  # 属性名不一致
        # 缺少必要的属性和方法；数值未走 MONSTER_CONFIGS 数据驱动
```

---

## 沟通规范

### 任务开始前
```
我理解您的任务是：[复述任务]
涉及的文件有：[列出文件]
我的计划是：[简述步骤]
如有疑问：[提出问题]
如无异议，请确认，确认后我再开始修改
```

### 任务完成后
```
## 修改完成

### 修改内容
1. [修改1]
2. [修改2]

### Bug修复说明（如果是bug修复）
- **Bug原因**：[原因]
- **修复方法**：[方法]
- **验证结果**：[结果]

### 测试结果
- [测试项1]：通过/失败
- [测试项2]：通过/失败
```

---

## 禁止事项

1. **不要假设用户意图**：不清楚就问
2. **不要过度修改**：只做必要的改动
3. **不要删除功能**：除非用户明确要求
4. **不要跳过测试**：修改后必须验证
5. **不要使用英文汇报**：必须使用中文
6. **不要自己发挥**：添加新内容时必须参考现有代码模式
7. **不要未经同意就修改**：实施任何修改前，必须先将将要进行的修改向用户陈述，得到用户明确同意后才能执行
8. **不要静默忽略**：net 层未知消息必须显式处理或记录日志（protocol.py 铁律，:359）
9. **不要阻塞主线程**：net 回调禁 sleep/等待，经 thread_bridge 队列汇入主循环
10. **不要改数据键名**：entities 的 item_id/资源名是跨层契约（DB/掉落/渲染共用）

---

## 示例

### 正确做法
```
用户：修复宝箱和撤离点重叠的问题

AI：
我理解您的任务是：修复宝箱可能与撤离点重叠的问题
涉及的文件有：game/map_gen.py
我的计划是：在生成宝箱位置时检查是否与撤离点过近，如果是则重新生成
请确认是否可以开始修改？

用户：可以

[执行修改]

## 修改完成

### 修改内容
1. 在 game/map_gen.py 中添加了宝箱与撤离点的最小距离检查（150像素）
2. 如果宝箱位置与撤离点过近，会重新生成位置（最多尝试20次）

### Bug修复说明
- **Bug原因**：宝箱和撤离点是独立生成的，没有检查它们之间的距离
- **修复方法**：生成宝箱时检查是否与任何撤离点距离小于150像素
- **验证结果**：宝箱现在不会与撤离点重叠
```

### 错误做法
```
用户：修复宝箱和撤离点重叠的问题

AI：[直接修改代码，没有说明理解，没有测试，用英文汇报]
```
