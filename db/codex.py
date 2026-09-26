"""图鉴解锁持久化：记录玩家已解锁的怪物/武器/装备/药水条目

表 codex_unlocks 由 db/database.py 的 init_db() 创建（UNIQUE(player_id, category, item_id)）。
本模块提供幂等的解锁写入与查询，供击杀/拾取/入库等触发点调用。

表 codex_rewards（阶段7 新增）记录玩家已领取的图鉴档位奖励
（PRIMARY KEY(player_id, category, tier)），本模块同时提供档位进度查询与幂等领取。
tier 列存的是**该类别的实际条数阈值**（按 config.CODEX_TIER_RATIOS 比例动态取得，
如武器 10/18/25、药水 4/6/8），非固定全局档位。
"""
from db.connection import _conn


def unlock_codex_entry(player_id: int, category: str, item_id: str) -> bool:
    """解锁图鉴条目（幂等：重复调用不报错、不产生重复行）

    返回 True 表示本次为新解锁（INSERT 实际插入），False 表示已解锁过。
    调用方可据返回值弹「图鉴解锁」提示，避免重复提示。

    category: "monster" | "weapon" | "equipment" | "potion"
    item_id: 怪物类名（如 "Zombie"）或物品 item_id（如 "wado_ichimonji"）
    """
    with _conn() as c:
        # INSERT OR IGNORE：UNIQUE(player_id, category, item_id) 冲突时静默跳过，
        # 保证重复击杀/拾取/入库不会崩溃也不会产生重复记录
        cur = c.execute(
            "INSERT OR IGNORE INTO codex_unlocks(player_id, category, item_id) VALUES(?,?,?)",
            (player_id, category, item_id),
        )
        # rowcount：新插入=1，IGNORE 跳过=0 → 是否新解锁
        return cur.rowcount > 0


def get_codex_unlocks(player_id: int) -> set[tuple[str, str]]:
    """查询玩家已解锁的全部图鉴条目，返回 {(category, item_id), ...}"""
    with _conn() as c:
        rows = c.execute(
            "SELECT category, item_id FROM codex_unlocks WHERE player_id=?",
            (player_id,),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}


# ── 阶段7：图鉴档位奖励（阈值与奖励数值全部取自 config，禁在本层硬编码）──

def get_codex_count(player_id: int, category: str) -> int:
    """统计玩家某类图鉴的「计入进度」的已解锁条数

    category: "monster" | "weapon" | "equipment" | "potion"
    用于档位奖励的进度口径（已解锁 X / 该类总数 Y）。

    口径：只计 entities.forge_recipes.get_codex_countable_ids 里的条目
    （排除 recipe_only 配方专属产物，否则「集齐」会自锁、进度也会溢出分母），
    与 views/codex_view.py 的图鉴条目列表/进度分母、
    views/forge_view.py 的配方集齐目标（get_codex_category_total）同源同数字。
    """
    # 函数级延迟 import：db → entities 依赖链（防循环导入，同 db/ 层其他模块惯例）
    from entities.forge_recipes import get_codex_countable_ids

    with _conn() as c:
        rows = c.execute(
            "SELECT item_id FROM codex_unlocks WHERE player_id=? AND category=?",
            (player_id, category),
        ).fetchall()
    unlocked_ids = {str(r[0]) for r in rows}
    return len(unlocked_ids & set(get_codex_countable_ids(category)))


def get_claimed_rewards(player_id: int) -> set[tuple[str, int]]:
    """查询玩家已领取的图鉴档位奖励，返回 {(category, tier), ...}"""
    with _conn() as c:
        rows = c.execute(
            "SELECT category, tier FROM codex_rewards WHERE player_id=?",
            (player_id,),
        ).fetchall()
        return {(str(r[0]), int(r[1])) for r in rows}


def claim_codex_reward(player_id: int, category: str, tier: int) -> dict | None:
    """领取某类图鉴的指定档位奖励（幂等）

    成功：{"gold": 金币数, "res": {资源 item_id: 数量}}（本层只返回数据，金币/仓库资源由上层发放）
    不可领（未达档位 / 该类比例档位表内无此档 / 已领取过）→ None

    档位口径：阈值按「该类条目总数」比例动态取（config.codex_tiers_for，
    比例 40%/70%/100%），奖励按档位序号映射（config.codex_tier_reward），
    两者共用 entities.forge_recipes.get_codex_category_total 的分母 —— 与图鉴页
    显示的分母、锻造坊集齐目标是同一个数字。

    幂等保证：codex_rewards 的 PRIMARY KEY(player_id, category, tier) 配合
    INSERT OR IGNORE，二次领取时 rowcount=0 → 返回 None，不会重复发奖。
    """
    # 函数级延迟 import：db → entities/config 依赖链（数值口径只留 config 一处）
    from config import codex_tier_reward
    from entities.forge_recipes import get_codex_category_total

    reward = codex_tier_reward(tier, get_codex_category_total(category))
    if reward is None:
        return None  # 非法档位（不在该类比例档位表内）
    if get_codex_count(player_id, category) < tier:
        return None  # 未达档位
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO codex_rewards(player_id, category, tier) VALUES(?,?,?)",
            (player_id, category, tier),
        )
        # rowcount：新插入=1，PK 冲突（已领取）=0 → 二次领取返回 None
        if cur.rowcount <= 0:
            return None
    return reward
