"""每日任务与成就持久化：进度累加、达成判定、领奖

表 daily_missions（每日任务，config.DAILY_COUNT 条，按 slot 0..N-1 固定行数）由
db/database.py 的 init_db() 创建：
    player_id INTEGER 玩家ID
    slot      INTEGER 槽位（0..DAILY_COUNT-1）
    mission_id TEXT    任务id（entities/mission_defs.DAILY_POOL）
    progress  INTEGER 当前进度
    claimed   INTEGER 是否已领奖（0/1）
    date      TEXT    归属自然日（date.today().isoformat()，跨日即重抽）
    PRIMARY KEY (player_id, slot)

表 mission_progress（成就，一次性奖励、累计不清零）由 init_db() 创建：
    player_id      INTEGER 玩家ID
    achievement_id TEXT    成就id（entities/mission_defs.ACHIEVEMENTS）
    progress       INTEGER 当前进度
    claimed        INTEGER 是否已领奖（0/1）
    PRIMARY KEY (player_id, achievement_id)

事件类型键（与 entities/mission_defs.MISSION_EVENT_KEYS 一致）：
    kill / elite_kill / harvest / chest / evac / forge / reforge

本层只管进度与奖励数据，**不改玩家金币/经验**——claim_* 返回奖励字段，由上层（Task 6.2
mission_tracker / Task 6.3 任务板）负责发奖。CRUD 模式仿 db/codex.py：
`with _conn() as c` + INSERT OR IGNORE 幂等 + 行转 dict 返回。
"""
import random
from datetime import date

from config import DAILY_COUNT
from db.connection import _conn
from entities.mission_defs import (
    ACHIEVEMENTS,
    ACHIEVEMENT_BY_ID,
    DAILY_BY_ID,
    DAILY_POOL,
)


def _today() -> str:
    """当前归属自然日字符串（YYYY-MM-DD）

    与 config.DAILY_REFRESH_HOUR = 0（自然日 0 点刷新）口径一致：跨过 0 点即为新的一天。
    """
    return date.today().isoformat()


def _fetch_daily_rows(player_id: int) -> list[tuple]:
    """读取该玩家全部每日任务行（按 slot 升序）：(slot, mission_id, progress, claimed, date)"""
    with _conn() as c:
        return c.execute(
            "SELECT slot, mission_id, progress, claimed, date FROM daily_missions"
            " WHERE player_id=? ORDER BY slot",
            (player_id,),
        ).fetchall()


def _daily_row_to_dict(row: tuple) -> dict:
    """daily_missions 行 → 任务字典（合并 mission_defs 的展示/奖励字段）

    输出字段：slot / mission_id / desc / event / target / progress / claimed /
    complete / reward_gold / reward_exp / date
    """
    slot, mission_id, progress, claimed, date_str = row[0], row[1], row[2], row[3], row[4]
    defn = DAILY_BY_ID.get(str(mission_id), {})
    target = int(defn.get("target", 0))
    return {
        "slot": slot,
        "mission_id": mission_id,
        "desc": defn.get("desc", str(mission_id)),
        "event": defn.get("event", ""),
        "target": target,
        "progress": progress,
        "claimed": bool(claimed),
        "complete": progress >= target,
        "reward_gold": int(defn.get("reward_gold", 0)),
        "reward_exp": int(defn.get("reward_exp", 0)),
        "date": date_str,
    }


def _ensure_today(player_id: int) -> list[tuple]:
    """确保今日任务已就绪并返回今日行（无记录或日期过期则自动重抽）

    读取路径统一走这里，避免"跨日后读到昨日残留进度"。重抽用独立 random.Random()，
    不污染调用方传入的随机源（roll_daily 的 rng 由调用方决定可复现性）。
    """
    today = _today()
    rows = _fetch_daily_rows(player_id)
    if rows and all(str(r[4]) == today for r in rows):
        return rows
    roll_daily(player_id, today, random.Random())
    return _fetch_daily_rows(player_id)


def roll_daily(player_id: int, date_str: str, rng: random.Random) -> None:
    """从 DAILY_POOL 随机抽 config.DAILY_COUNT 条写入 daily_missions（当日已有则跳过）

    幂等：同一天重复调用直接返回（不会重抽、不会丢失已有进度）。
    重抽时先清掉该玩家的历史日期行，保证表内只有当日 DAILY_COUNT 条。
    """
    with _conn() as c:
        # 当日已抽过则跳过（PRIMARY KEY(player_id, slot) 冲突源）
        exists = c.execute(
            "SELECT 1 FROM daily_missions WHERE player_id=? AND date=? LIMIT 1",
            (player_id, date_str),
        ).fetchone()
        if exists:
            return
        c.execute("DELETE FROM daily_missions WHERE player_id=?", (player_id,))
        picked = rng.sample(DAILY_POOL, k=min(DAILY_COUNT, len(DAILY_POOL)))
        for slot, mission in enumerate(picked):
            c.execute(
                "INSERT OR REPLACE INTO daily_missions"
                "(player_id, slot, mission_id, progress, claimed, date) VALUES(?,?,?,0,0,?)",
                (player_id, slot, str(mission["id"]), date_str),
            )


def get_daily(player_id: int) -> list[dict]:
    """读取今日任务列表（含 progress/claimed/complete）；日期过期自动重抽

    返回 DAILY_COUNT 条（无记录时首次调用即完成抽取）。
    """
    return [_daily_row_to_dict(r) for r in _ensure_today(player_id)]


def bump_mission(player_id: int, event: str, amount: int = 1) -> list[str]:
    """累加今日任务进度，返回**本次新达成**的任务 id 列表

    event：事件类型键（kill/elite_kill/harvest/chest/evac/forge/reforge）
    amount：本次事件次数（如一次连杀 3 只传 3）
    规则：只处理今日未领奖且 event 匹配的任务；进度按 target 封顶不溢出；
    已达成的任务不再重复计入返回列表。
    """
    today = _today()
    # 先确保今日任务就绪（可能触发重抽），再开写连接，避免跨连接读写交叠
    rows = _ensure_today(player_id)
    done: list[str] = []
    with _conn() as c:
        for row in rows:
            if str(row[4]) != today or int(row[3]):
                continue
            defn = DAILY_BY_ID.get(str(row[1]))
            if defn is None or str(defn["event"]) != event:
                continue
            target = int(defn["target"])
            old = int(row[2])
            if old >= target:
                continue
            new = min(old + amount, target)
            c.execute(
                "UPDATE daily_missions SET progress=? WHERE player_id=? AND slot=?",
                (new, player_id, row[0]),
            )
            if new >= target:
                done.append(str(row[1]))
    return done


def claim_daily(player_id: int, slot: int) -> dict:
    """领取指定槽位（0..DAILY_COUNT-1）的今日任务奖励

    成功：{"ok": True, "mission_id", "desc", "reward_gold", "reward_exp"}
    失败：{"ok": False, "reason": "..."}（槽位无任务 / 未完成 / 已领取）
    本层只返回奖励数据，金币与经验由上层发放。
    """
    for row in _ensure_today(player_id):
        if int(row[0]) != slot:
            continue
        d = _daily_row_to_dict(row)
        if d["claimed"]:
            return {"ok": False, "reason": "该任务奖励已领取"}
        if not d["complete"]:
            return {"ok": False, "reason": "任务尚未完成"}
        with _conn() as c:
            c.execute(
                "UPDATE daily_missions SET claimed=1 WHERE player_id=? AND slot=?",
                (player_id, slot),
            )
        return {
            "ok": True,
            "mission_id": d["mission_id"],
            "desc": d["desc"],
            "reward_gold": d["reward_gold"],
            "reward_exp": d["reward_exp"],
        }
    return {"ok": False, "reason": "槽位无任务"}


def get_achievements(player_id: int) -> list[dict]:
    """读取成就清单（按 ACHIEVEMENTS 定义顺序，含 progress/claimed/complete）

    无记录的成就 progress=0 / claimed=False（不预写行，避免每个新玩家插 13 行）。
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT achievement_id, progress, claimed FROM mission_progress WHERE player_id=?",
            (player_id,),
        ).fetchall()
    saved = {str(r[0]): (int(r[1]), bool(r[2])) for r in rows}
    out: list[dict] = []
    for defn in ACHIEVEMENTS:
        aid = str(defn["id"])
        progress, claimed = saved.get(aid, (0, False))
        target = int(defn["target"])
        out.append({
            "achievement_id": aid,
            "desc": defn["desc"],
            "event": str(defn["event"]),
            "target": target,
            "progress": progress,
            "claimed": claimed,
            "complete": progress >= target,
            "reward_gold": int(defn["reward_gold"]),
        })
    return out


def bump_achievement(player_id: int, event: str, amount: int = 1) -> list[str]:
    """累加成就进度（一次性、累计不清零），返回**本次新达成**的成就 id 列表

    event：事件类型键（同上七种）
    规则：进度按 target 封顶；已领奖的成就不再累加也不重复上报。
    """
    done: list[str] = []
    with _conn() as c:
        for defn in ACHIEVEMENTS:
            if str(defn["event"]) != event:
                continue
            aid = str(defn["id"])
            target = int(defn["target"])
            # INSERT OR IGNORE：无记录时补一条 0 进度行
            c.execute(
                "INSERT OR IGNORE INTO mission_progress"
                "(player_id, achievement_id, progress, claimed) VALUES(?,?,0,0)",
                (player_id, aid),
            )
            row = c.execute(
                "SELECT progress, claimed FROM mission_progress"
                " WHERE player_id=? AND achievement_id=?",
                (player_id, aid),
            ).fetchone()
            if row is None or int(row[1]):
                continue
            old = int(row[0])
            if old >= target:
                continue
            new = min(old + amount, target)
            c.execute(
                "UPDATE mission_progress SET progress=? WHERE player_id=? AND achievement_id=?",
                (new, player_id, aid),
            )
            if new >= target:
                done.append(aid)
    return done


def claim_achievement(player_id: int, achievement_id: str) -> dict:
    """领取成就奖励（一次性）

    成功：{"ok": True, "achievement_id", "desc", "reward_gold"}
    失败：{"ok": False, "reason": "..."}（成就不存在 / 未达成 / 已领取）
    本层只返回奖励数据，金币由上层发放。
    """
    defn = ACHIEVEMENT_BY_ID.get(achievement_id)
    if defn is None:
        return {"ok": False, "reason": "成就不存在"}
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO mission_progress"
            "(player_id, achievement_id, progress, claimed) VALUES(?,?,0,0)",
            (player_id, achievement_id),
        )
        row = c.execute(
            "SELECT progress, claimed FROM mission_progress"
            " WHERE player_id=? AND achievement_id=?",
            (player_id, achievement_id),
        ).fetchone()
    if row is None:
        return {"ok": False, "reason": "成就记录缺失"}
    if int(row[1]):
        return {"ok": False, "reason": "该成就奖励已领取"}
    if int(row[0]) < int(defn["target"]):
        return {"ok": False, "reason": "成就尚未达成"}
    with _conn() as c:
        c.execute(
            "UPDATE mission_progress SET claimed=1 WHERE player_id=? AND achievement_id=?",
            (player_id, achievement_id),
        )
    return {
        "ok": True,
        "achievement_id": achievement_id,
        "desc": defn["desc"],
        "reward_gold": int(defn["reward_gold"]),
    }
