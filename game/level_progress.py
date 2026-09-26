"""地图难度星级判定（阶段8 Task 8.1）：把单局统计换算成 0~3 星

判定表在 config.MAP_STAR_CRITERIA（每图 3 条条件，按顺序），本模块只提供**纯函数**：
evaluate_stars(criteria, stats) —— 无 IO、无全局状态、可单测，落库由
db/map_progress.record_stars 负责（分层：判定在 game/，存取在 db/）。

核心语义（与 config.MAP_STAR_CRITERIA 的注释一致）：
    **按顺序逐条判定，全部达成才升星** —— 只过前 n 条给 n 星，任一条不达成即停在其前。
    三张图的首条都是「成功撤离」，故撤离失败（evac=False）直接 0 星。

stats 入参口径（由 views/game_view.py 的 run_stats 在局内累加、撤离结算处定格）：
    {"evac": bool, "kills": int, "elite_kills": int, "boss_kills": int, "carried_gold": int}
缺键一律按「未达成」处理（取 0 / False），调用方漏传字段不会误判成达成。
"""
# 布尔型统计字段：以真值判定，不做数值比较（"evac" = 成功撤离）
_BOOL_STATS = frozenset({"evac"})

# 条件 dict 的 "op" 支持的比较符；未列出的一律判不达成（禁静默通过脏配置）
_NUM_OPS = frozenset({">=", ">", "<=", "<", "=="})


def _condition_met(item: dict, stats: dict) -> bool:
    """判定单条星级条件是否达成

    item：config.MAP_STAR_CRITERIA 里的一条条件 dict
          （stat=统计键 / value=阈值 / op=比较符 / desc=文案）
    stats：单局统计字典（见模块 docstring 口径）
    """
    stat = str(item.get("stat", ""))
    if not stat:
        # 无 stat 的条件视为无门槛（允许配置里写纯展示条目），不参与拦截
        return True
    actual = stats.get(stat)
    if stat in _BOOL_STATS:
        # 布尔型条件（如「成功撤离」）：真值即达成
        return bool(actual)
    op = str(item.get("op", ">="))
    if op not in _NUM_OPS:
        return False
    # 数值型条件：缺键/None 按 0 算（不因漏传字段而误判达成）
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        current = 0
    else:
        current = actual
    target = item.get("value", 1)
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        target = 1
    if op == ">=":
        return current >= target
    if op == ">":
        return current > target
    if op == "<=":
        return current <= target
    if op == "<":
        return current < target
    return current == target


def evaluate_stars(criteria: list[dict], stats: dict) -> int:
    """按条件表逐条判定本局星数，返回 0..len(criteria)

    criteria：config.MAP_STAR_CRITERIA[theme]（顺序敏感，勿打乱）
    stats：单局统计字典（见模块 docstring 口径）

    逐条判、遇不达成即停：
        全过        → len(criteria)（3 星）
        只过前 n 条 → n 星（如撤离成功但击杀 25 < 30 → 1 星）
        撤离失败    → 0 星（首条 evac 不达成）
        criteria 为空 → 0 星

    返回值不写库；调用方（撤离结算处）拿到后交 db.map_progress.record_stars 落库。
    """
    stars = 0
    for item in criteria:
        if not _condition_met(item, stats):
            # 顺序达成语义：后一条再满足也不升星，停在当前已达成的星数
            break
        stars += 1
    return stars
