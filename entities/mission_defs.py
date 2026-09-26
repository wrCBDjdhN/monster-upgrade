"""每日任务与成就清单（纯数据定义，db/missions.py 落库、上层任务板 UI 消费）

两份清单：
- DAILY_POOL：每日任务池，db/missions.roll_daily 每日随机抽 config.DAILY_COUNT 条写入 daily_missions 表
- ACHIEVEMENTS：成就清单（一次性奖励，累计不清零），进度写入 mission_progress 表

字段 schema：
- 任务 DAILY_POOL：id / desc(中文) / event(事件类型键) / target(int 目标次数) / reward_gold(int) / reward_exp(int)
- 成就 ACHIEVEMENTS：id / desc(中文) / event(事件类型键) / target(int) / reward_gold(int)

事件类型键（仅这七种，与 db/missions.bump_* 的 event 参数一一对应）：
- kill         击杀任意怪物（精英死亡同时计 kill 与 elite_kill；BOSS 亦计入 kill）
- elite_kill   击杀精英怪
- harvest      采集资源
- chest        开启宝箱
- evac         成功撤离
- forge        锻造坊锻造成功
- reforge      市场重铸成功

数值口径参考 config：EXP_KILL_BASE=20 / EXP_HARVEST=8 / EXP_CHEST=30 / EXP_EVAC=100、
EXP_PER_LEVEL_BASE=100（任务经验奖励约合 0.5~3 级），金币奖励与怪物掉落、宝箱金币（5~15/个）同量级。
"""

# 事件类型键全集（上层校验 event 合法性时用）
MISSION_EVENT_KEYS = (
    "kill",        # 击杀任意怪物（精英同时计 elite_kill）
    "elite_kill",  # 击杀精英怪
    "harvest",     # 采集资源
    "chest",       # 开启宝箱
    "evac",        # 成功撤离
    "forge",       # 锻造强化
    "reforge",     # 市场重铸
)

# 每日任务池：id/desc/event/target/reward_gold/reward_exp
# 难度分三档（低/中/高 target），保证任意抽 3 条都有梯度感
DAILY_POOL: list[dict] = [
    {
        "id": "daily_kill_20",
        "desc": "击杀 20 只怪物",
        "event": "kill",
        "target": 20,
        "reward_gold": 120,
        "reward_exp": 60,
    },
    {
        "id": "daily_kill_50",
        "desc": "击杀 50 只怪物",
        "event": "kill",
        "target": 50,
        "reward_gold": 300,
        "reward_exp": 150,
    },
    {
        "id": "daily_elite_2",
        "desc": "击杀 2 只精英怪",
        "event": "elite_kill",
        "target": 2,
        "reward_gold": 250,
        "reward_exp": 120,
    },
    {
        "id": "daily_elite_5",
        "desc": "击杀 5 只精英怪",
        "event": "elite_kill",
        "target": 5,
        "reward_gold": 600,
        "reward_exp": 300,
    },
    {
        "id": "daily_harvest_15",
        "desc": "采集 15 次资源",
        "event": "harvest",
        "target": 15,
        "reward_gold": 100,
        "reward_exp": 50,
    },
    {
        "id": "daily_harvest_30",
        "desc": "采集 30 次资源",
        "event": "harvest",
        "target": 30,
        "reward_gold": 220,
        "reward_exp": 110,
    },
    {
        "id": "daily_chest_3",
        "desc": "开启 3 个宝箱",
        "event": "chest",
        "target": 3,
        "reward_gold": 180,
        "reward_exp": 90,
    },
    {
        "id": "daily_chest_6",
        "desc": "开启 6 个宝箱",
        "event": "chest",
        "target": 6,
        "reward_gold": 380,
        "reward_exp": 190,
    },
    {
        "id": "daily_evac_1",
        "desc": "成功撤离 1 次",
        "event": "evac",
        "target": 1,
        "reward_gold": 200,
        "reward_exp": 100,
    },
    {
        "id": "daily_evac_3",
        "desc": "成功撤离 3 次",
        "event": "evac",
        "target": 3,
        "reward_gold": 520,
        "reward_exp": 260,
    },
    {
        "id": "daily_forge_2",
        "desc": "锻造强化 2 次",
        "event": "forge",
        "target": 2,
        "reward_gold": 150,
        "reward_exp": 40,
    },
    {
        "id": "daily_reforge_1",
        "desc": "重铸装备 1 次",
        "event": "reforge",
        "target": 1,
        "reward_gold": 160,
        "reward_exp": 45,
    },
]

# 成就清单：id/desc/event/target/reward_gold（一次性，累计不清零，无经验奖励）
ACHIEVEMENTS: list[dict] = [
    {
        "id": "ach_first_blood",
        "desc": "首次击杀一只怪物",
        "event": "kill",
        "target": 1,
        "reward_gold": 30,
    },
    {
        "id": "ach_kill_100",
        "desc": "累计击杀 100 只怪物",
        "event": "kill",
        "target": 100,
        "reward_gold": 300,
    },
    {
        "id": "ach_kill_500",
        "desc": "累计击杀 500 只怪物",
        "event": "kill",
        "target": 500,
        "reward_gold": 1200,
    },
    {
        "id": "ach_kill_2000",
        "desc": "累计击杀 2000 只怪物",
        "event": "kill",
        "target": 2000,
        "reward_gold": 4000,
    },
    {
        "id": "ach_elite_10",
        "desc": "累计击杀 10 只精英怪",
        "event": "elite_kill",
        "target": 10,
        "reward_gold": 500,
    },
    {
        "id": "ach_elite_50",
        "desc": "累计击杀 50 只精英怪",
        "event": "elite_kill",
        "target": 50,
        "reward_gold": 2000,
    },
    {
        "id": "ach_harvest_200",
        "desc": "累计采集 200 次资源",
        "event": "harvest",
        "target": 200,
        "reward_gold": 400,
    },
    {
        "id": "ach_chest_30",
        "desc": "累计开启 30 个宝箱",
        "event": "chest",
        "target": 30,
        "reward_gold": 600,
    },
    {
        "id": "ach_chest_100",
        "desc": "累计开启 100 个宝箱",
        "event": "chest",
        "target": 100,
        "reward_gold": 1800,
    },
    {
        "id": "ach_evac_10",
        "desc": "累计成功撤离 10 次",
        "event": "evac",
        "target": 10,
        "reward_gold": 800,
    },
    {
        "id": "ach_evac_50",
        "desc": "累计成功撤离 50 次",
        "event": "evac",
        "target": 50,
        "reward_gold": 3500,
    },
    {
        "id": "ach_forge_25",
        "desc": "累计锻造强化 25 次",
        "event": "forge",
        "target": 25,
        "reward_gold": 500,
    },
    {
        "id": "ach_reforge_10",
        "desc": "累计重铸装备 10 次",
        "event": "reforge",
        "target": 10,
        "reward_gold": 450,
    },
]

# id → 定义 的索引（db/missions.py 按 id 反查 event/target/reward，避免全表线性查找）
DAILY_BY_ID: dict[str, dict] = {str(m["id"]): m for m in DAILY_POOL}
ACHIEVEMENT_BY_ID: dict[str, dict] = {str(a["id"]): a for a in ACHIEVEMENTS}
