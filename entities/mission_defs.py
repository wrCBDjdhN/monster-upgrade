"""每日任务与成就清单（纯数据定义，db/missions.py 落库、上层任务板 UI 消费）

两份清单：
- DAILY_POOL：每日任务池，db/missions.roll_daily 每日随机抽 config.DAILY_COUNT 条写入 daily_missions 表
- ACHIEVEMENTS：成就清单（一次性奖励，累计不清零），进度写入 mission_progress 表

字段 schema：
- 任务 DAILY_POOL：id / desc(中文) / event(事件类型键) / target(int 目标次数) /
  reward_gold(int) / reward_pool(奖励池 id)
- 成就 ACHIEVEMENTS：id / desc(中文) / event(事件类型键) / target(int) /
  reward_gold(int) / reward_pool(奖励池 id)

奖励池（用户口径 2026-11-21）：
- 任务/成就不再奖励经验，改为「金币 + 随机奖励池」；
- 奖励池定义见 REWARD_POOLS，领取时由 game/mission_rewards.roll_reward 随机抽 1 项；
- 难度越高池越好：easy=资源/初级箱，normal=药水/Lv1-10 装备/高级箱，
  hard=Lv1-3 神器/特级箱。

事件类型键（仅这八种，与 db/missions.bump_* 的 event 参数一一对应）：
- kill         击杀任意怪物（精英死亡同时计 kill 与 elite_kill；BOSS 亦计入 kill）
- elite_kill   击杀精英怪
- boss_kill    击杀 BOSS（BOSS 死亡同时计 kill 与 boss_kill，两事件并行、各按各自 target 封顶）
- harvest      采集资源
- chest        开启宝箱
- evac         成功撤离
- forge        锻造坊锻造成功
- reforge      市场重铸成功
"""

# 事件类型键全集（上层校验 event 合法性时用）
MISSION_EVENT_KEYS = (
    "kill",        # 击杀任意怪物（精英同时计 elite_kill）
    "elite_kill",  # 击杀精英怪
    "boss_kill",   # 击杀 BOSS（BOSS 同时计 kill 与 boss_kill，阶段D 新增第8键）
    "harvest",     # 采集资源
    "chest",       # 开启宝箱
    "evac",        # 成功撤离
    "forge",       # 锻造强化
    "reforge",     # 市场重铸
)

# 奖励池：entries 为加权随机项，领取时只抽 1 项
# type 说明：resource=资源 / box=武器箱或装备箱 / potion=随机药水 /
#            random_gear=Lv1-10 随机武器/头盔/护甲/背包 / artifact=Lv1-3 随机神器
REWARD_POOLS: dict[str, dict] = {
    "easy": {
        "name": "初级奖励",
        "desc": "资源/初级箱",
        "entries": [
            {"weight": 40, "type": "resource", "item_id": "wood", "qty": (8, 12)},
            {"weight": 20, "type": "resource", "item_id": "stone", "qty": (4, 6)},
            {"weight": 20, "type": "box", "box_id": "weapon_box_basic", "qty": 1},
            {"weight": 20, "type": "box", "box_id": "equip_box_basic", "qty": 1},
        ],
    },
    "normal": {
        "name": "中级奖励",
        "desc": "药水/Lv1-10装备/高级箱×2",
        "entries": [
            {"weight": 30, "type": "potion", "qty": (2, 3)},
            {"weight": 30, "type": "random_gear", "level": (1, 10), "count": 1},
            {"weight": 20, "type": "box", "box_id": "weapon_box_advanced", "qty": 2},
            {"weight": 20, "type": "box", "box_id": "equip_box_advanced", "qty": 2},
        ],
    },
    "hard": {
        "name": "高级奖励",
        "desc": "Lv1-3神器/特级箱×1-10",
        "entries": [
            {"weight": 40, "type": "artifact", "level": (1, 3), "count": 1},
            {"weight": 30, "type": "box", "box_id": "weapon_box_supreme", "qty": (1, 10)},
            {"weight": 30, "type": "box", "box_id": "equip_box_supreme", "qty": (1, 10)},
        ],
    },
}

# 每日任务池：id/desc/event/target/reward_gold/reward_pool
# 难度分三档（easy/normal/hard），保证任意抽 3 条都有梯度感
DAILY_POOL: list[dict] = [
    {
        "id": "daily_kill_20",
        "desc": "击杀 20 只怪物",
        "event": "kill",
        "target": 20,
        "reward_gold": 120,
        "reward_pool": "easy",
    },
    {
        "id": "daily_kill_50",
        "desc": "击杀 50 只怪物",
        "event": "kill",
        "target": 50,
        "reward_gold": 300,
        "reward_pool": "normal",
    },
    {
        "id": "daily_elite_2",
        "desc": "击杀 2 只精英怪",
        "event": "elite_kill",
        "target": 2,
        "reward_gold": 250,
        "reward_pool": "normal",
    },
    {
        "id": "daily_elite_5",
        "desc": "击杀 5 只精英怪",
        "event": "elite_kill",
        "target": 5,
        "reward_gold": 600,
        "reward_pool": "hard",
    },
    {
        # 阶段D：BOSS 击杀日任务（单目标低梯度——BOSS 每局最多 4+1 只，target 宜小）
        "id": "daily_boss_1",
        "desc": "击杀 1 只 BOSS",
        "event": "boss_kill",
        "target": 1,
        "reward_gold": 350,
        "reward_pool": "hard",
    },
    {
        "id": "daily_harvest_15",
        "desc": "采集 15 次资源",
        "event": "harvest",
        "target": 15,
        "reward_gold": 100,
        "reward_pool": "easy",
    },
    {
        "id": "daily_harvest_30",
        "desc": "采集 30 次资源",
        "event": "harvest",
        "target": 30,
        "reward_gold": 220,
        "reward_pool": "normal",
    },
    {
        "id": "daily_chest_3",
        "desc": "开启 3 个宝箱",
        "event": "chest",
        "target": 3,
        "reward_gold": 180,
        "reward_pool": "normal",
    },
    {
        "id": "daily_chest_6",
        "desc": "开启 6 个宝箱",
        "event": "chest",
        "target": 6,
        "reward_gold": 380,
        "reward_pool": "hard",
    },
    {
        "id": "daily_evac_1",
        "desc": "成功撤离 1 次",
        "event": "evac",
        "target": 1,
        "reward_gold": 200,
        "reward_pool": "normal",
    },
    {
        "id": "daily_evac_3",
        "desc": "成功撤离 3 次",
        "event": "evac",
        "target": 3,
        "reward_gold": 520,
        "reward_pool": "hard",
    },
    {
        "id": "daily_forge_2",
        "desc": "锻造强化 2 次",
        "event": "forge",
        "target": 2,
        "reward_gold": 150,
        "reward_pool": "easy",
    },
    {
        "id": "daily_reforge_1",
        "desc": "重铸装备 1 次",
        "event": "reforge",
        "target": 1,
        "reward_gold": 160,
        "reward_pool": "easy",
    },
]

# 成就清单：id/desc/event/target/reward_gold/reward_pool（一次性，累计不清零）
ACHIEVEMENTS: list[dict] = [
    {
        "id": "ach_first_blood",
        "desc": "首次击杀一只怪物",
        "event": "kill",
        "target": 1,
        "reward_gold": 30,
        "reward_pool": "easy",
    },
    {
        "id": "ach_kill_100",
        "desc": "累计击杀 100 只怪物",
        "event": "kill",
        "target": 100,
        "reward_gold": 300,
        "reward_pool": "easy",
    },
    {
        "id": "ach_kill_500",
        "desc": "累计击杀 500 只怪物",
        "event": "kill",
        "target": 500,
        "reward_gold": 1200,
        "reward_pool": "normal",
    },
    {
        "id": "ach_kill_2000",
        "desc": "累计击杀 2000 只怪物",
        "event": "kill",
        "target": 2000,
        "reward_gold": 4000,
        "reward_pool": "hard",
    },
    {
        "id": "ach_elite_10",
        "desc": "累计击杀 10 只精英怪",
        "event": "elite_kill",
        "target": 10,
        "reward_gold": 500,
        "reward_pool": "normal",
    },
    {
        # 阶段D：首次击败 BOSS 成就
        "id": "ach_boss_first",
        "desc": "首次击杀 1 只 BOSS",
        "event": "boss_kill",
        "target": 1,
        "reward_gold": 300,
        "reward_pool": "normal",
    },
    {
        "id": "ach_elite_50",
        "desc": "累计击杀 50 只精英怪",
        "event": "elite_kill",
        "target": 50,
        "reward_gold": 2000,
        "reward_pool": "hard",
    },
    {
        "id": "ach_harvest_200",
        "desc": "累计采集 200 次资源",
        "event": "harvest",
        "target": 200,
        "reward_gold": 400,
        "reward_pool": "easy",
    },
    {
        "id": "ach_chest_30",
        "desc": "累计开启 30 个宝箱",
        "event": "chest",
        "target": 30,
        "reward_gold": 600,
        "reward_pool": "normal",
    },
    {
        "id": "ach_chest_100",
        "desc": "累计开启 100 个宝箱",
        "event": "chest",
        "target": 100,
        "reward_gold": 1800,
        "reward_pool": "hard",
    },
    {
        "id": "ach_evac_10",
        "desc": "累计成功撤离 10 次",
        "event": "evac",
        "target": 10,
        "reward_gold": 800,
        "reward_pool": "normal",
    },
    {
        "id": "ach_evac_50",
        "desc": "累计成功撤离 50 次",
        "event": "evac",
        "target": 50,
        "reward_gold": 3500,
        "reward_pool": "hard",
    },
    {
        "id": "ach_forge_25",
        "desc": "累计锻造强化 25 次",
        "event": "forge",
        "target": 25,
        "reward_gold": 500,
        "reward_pool": "easy",
    },
    {
        "id": "ach_reforge_10",
        "desc": "累计重铸装备 10 次",
        "event": "reforge",
        "target": 10,
        "reward_gold": 450,
        "reward_pool": "easy",
    },
]

# id → 定义 的索引（db/missions.py 按 id 反查 event/target/reward，避免全表线性查找）
DAILY_BY_ID: dict[str, dict] = {str(m["id"]): m for m in DAILY_POOL}
ACHIEVEMENT_BY_ID: dict[str, dict] = {str(a["id"]): a for a in ACHIEVEMENTS}
