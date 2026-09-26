"""地图星级进度持久化（阶段8 Task 8.1）：记录玩家在各主题地图上已达成的星数

表 map_stars 由 db/database.py 的 init_db() 创建：
    player_id INTEGER 玩家ID
    theme     TEXT    主题地图 id（forest / desert / space，见 config.MAP_STAR_CRITERIA）
    stars     INTEGER 当前星数（0..config.MAP_MAX_STARS）
    PRIMARY KEY (player_id, theme)

星级**只升不降**：record_stars 内部取 max，重复通关 / 低星复盘不会把已有星数打回低档
（星级是玩家已达成的历史成就，不是本局表现）。星数的**判定**不在本层
（判定属游戏逻辑，见 game/level_progress.py 的 evaluate_stars），
本层只负责存取，落库值按 config.MAP_MAX_STARS 钳位防脏数据。

CRUD 模式仿 db/codex.py / db/missions.py：`with _conn() as c` + UPSERT 幂等写入。
"""
from config import MAP_MAX_STARS
from db.connection import _conn


def get_stars(player_id: int) -> dict[str, int]:
    """查询玩家各主题地图的当前星数，返回 {theme: stars}

    未打过的主题不出现在返回字典里（调用方用 MAP_UNLOCK_STARS.get(theme, 0) 取缺省值）。
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT theme, stars FROM map_stars WHERE player_id=?",
            (player_id,),
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def record_stars(player_id: int, theme: str, stars: int) -> int:
    """记录玩家在某张图上达成的星数（**只升不降**），返回落库后的新星数

    stars：本次结算评出的星数（evaluate_stars 的返回值，0..MAP_MAX_STARS）；
    内部按 MAP_MAX_STARS 钳位（负数归 0、超上限归上限），避免脏数据撑破 ★×N/3 的显示。
    同一 (player_id, theme) 已存在记录时取 max 覆盖 —— 重玩低星局不会降星。
    """
    value = max(0, min(MAP_MAX_STARS, int(stars)))
    with _conn() as c:
        # UPSERT：主键冲突时 stars=MAX(旧值, 新值)，实现「只升不降」的原子写入
        c.execute(
            "INSERT INTO map_stars(player_id, theme, stars) VALUES(?,?,?)"
            " ON CONFLICT(player_id, theme) DO UPDATE SET stars=MAX(stars, excluded.stars)",
            (player_id, theme, value),
        )
        row = c.execute(
            "SELECT stars FROM map_stars WHERE player_id=? AND theme=?",
            (player_id, theme),
        ).fetchone()
    return int(row[0]) if row else 0
