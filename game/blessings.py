"""阶段5 祝福状态机：持有列表 / 加权抽取 / 属性聚合 / 幂等重算

分层口径：
- 本模块只管「祝福 → 属性加成」的纯计算，不碰 arcade 对象、不碰网络；
  UI（views/blessing_view.py）与触发点（game/entity_callbacks.py）都只调本模块接口。
- 单一数据源：``ids`` 直接引用 ``GameState.blessings`` 同一个 list 对象，
  因此 ``gs.blessings.clear()`` 与 ``BlessingState.clear()`` 效果一致，run 结束清理只有一处口径。
- 防漂移：属性一律「基准 + 祝福聚合」绝对重算（``Player.apply_bonus_stats``），
  禁止在已有加成上做增量加减，避免反复重算/换装后越叠越高。
- 阶段9 套装共用本模块的 ``recompute`` 作为唯一收口：先套装（并入属性基准）后祝福，
  故装备变化只需走 GameView.refresh_blessing_stats 一处，套装/祝福都不会漏算。

数值口径见 entities/blessing_defs.py：平铺字段（max_hp/regen/defense）与
比率字段（speed/atk_speed/damage/crit_chance/lifesteal/thorns）分开处理。
"""

import random

from config import BLESSING_MAX
from entities.blessing_defs import BLESSING_FIELDS, BLESSING_RATIO_FIELDS, BLESSINGS
from game.player import equipped_item_ids_from_gs

__all__ = ["BlessingState"]


# 参与「持续重算」的字段：shield 不在其中（护盾为选中时一次性授予，见 grant_one_shot）
_RECOMPUTE_FIELDS: tuple[str, ...] = tuple(f for f in BLESSING_FIELDS if f != "shield")

# 平铺字段（直接加到绝对值）；其余为比率字段（乘 1 + 合计比率）
_FLAT_FIELDS: frozenset[str] = frozenset(f for f in _RECOMPUTE_FIELDS if f not in BLESSING_RATIO_FIELDS)


class BlessingState:
    """单局祝福持有状态

    - ``ids``：持有的 blessing_id 列表（外部传入时**直接复用**该 list，不复制）
    - ``roll_options``：按 weight 加权无重复抽 n 条候选（默认 3 选 1）
    - ``recompute``：把聚合加成绝对重算到 Player 与武器数值，可反复调用且结果一致
    """

    def __init__(self, ids: list[str] | None = None) -> None:
        # 复用外部 list（GameState.blessings）保持单一数据源；未传则自建
        self.ids: list[str] = ids if ids is not None else []
        # 上一次 recompute 实际应用的聚合值：仅用于「武器基准未缓存时反推基准」的兜底
        self._applied: dict[str, float] = self.aggregate()

    # ── 持有 / 清理 ──────────────────────────────────────────────
    def add(self, blessing_id: str) -> bool:
        """尝试持有祝福。成功返回 True；未知 id / 超出上限 / 不可叠加且已持有时返回 False"""
        if blessing_id not in BLESSINGS:
            return False
        if len(self.ids) >= BLESSING_MAX:
            return False
        if not BLESSINGS[blessing_id].get("stack", False) and blessing_id in self.ids:
            return False
        self.ids.append(blessing_id)
        return True

    def clear(self) -> None:
        """清空本局祝福（run 结束：撤离成功 / 失败），随后需由调用方 recompute 还原属性"""
        self.ids.clear()
        self._applied = self.aggregate()

    def owns(self, blessing_id: str) -> bool:
        """是否已持有某祝福（联机面板/调试展示用）"""
        return blessing_id in self.ids

    # ── 抽取 ────────────────────────────────────────────────────
    def roll_options(self, count: int = 3) -> list[str]:
        """按 weight 加权无重复抽 count 条候选祝福

        候选池排除「已持有且不可叠加」的祝福；池空或 count<=0 时返回空 list。
        """
        pool: dict[str, int] = {
            bid: max(1, int(cfg.get("weight", 1)))
            for bid, cfg in BLESSINGS.items()
            if cfg.get("stack", False) or bid not in self.ids
        }
        picked: list[str] = []
        for _ in range(min(count, len(pool))):
            candidates = list(pool)
            weights = [pool[bid] for bid in candidates]
            # 每轮抽中后从池中移除 → 天然保证同批候选互不重复
            chosen = random.choices(candidates, weights=weights, k=1)[0]
            picked.append(chosen)
            del pool[chosen]
        return picked

    # ── 聚合 / 重算 ─────────────────────────────────────────────
    def applied_bonus(self) -> dict[str, float]:
        """上次 recompute 实际应用的聚合加成（供「反推无祝福基准」使用）

        换装/换武器时装备是在「已含祝福的当前值」上做 += 叠加的，直接把当前值当基准
        会把祝福加成固化进基准、越叠越高。调用方用本值从当前值反推真实基准：
        平铺字段做减法、比率字段做除法（见 Player.snapshot_base_stats）。
        """
        return self._applied

    def aggregate(self) -> dict[str, float]:
        """把持有的祝福按字段求和：平铺字段直接相加，比率字段相加为总比率"""
        bonus: dict[str, float] = dict.fromkeys(_RECOMPUTE_FIELDS, 0.0)
        for bid in self.ids:
            definition = BLESSINGS.get(bid)
            if not definition:
                continue  # 未知 id（联机脏数据）静默跳过，不阻断重算
            for field in _RECOMPUTE_FIELDS:
                value = definition.get(field)
                if value:
                    bonus[field] += float(value)
        return bonus

    def recompute(self, player, gs=None) -> dict[str, float]:
        """把当前祝福聚合绝对重算到 player 与武器数值，返回本次应用的加成

        阶段9 套装与祝福的**单点收口**（套装先、祝福后）：装备属性一变
        （开局 setup / 局内拾取换装 / 升级）都会走到 GameView.refresh_blessing_stats
        → 本方法，故在此先按 ``gs`` 当前穿戴清单重算套装（``refresh_set_bonuses``
        把套装加值并入属性基准），再叠加祝福——实际顺序恒为
        「装备/等级基准 → 套装 → 祝福」，两套系统互不覆盖。
        gs 为 None 时无法取穿戴清单（纯面板调用），跳过套装重算、只叠祝福。
        """
        bonus = self.aggregate()
        if player is not None:
            # 套装先：并入属性基准（幂等，反复调用不漂移）
            player.refresh_set_bonuses(equipped_item_ids_from_gs(gs))
            player.apply_bonus_stats(bonus)
        if gs is not None:
            dmg_base, spd_base = self._weapon_base(gs)
            gs.weapon_damage = dmg_base * (1.0 + bonus["damage"])
            gs.weapon_speed = spd_base * (1.0 + bonus["atk_speed"])
        self._applied = bonus
        return bonus

    def _weapon_base(self, gs) -> tuple[float, float]:
        """取武器伤害/攻速的「无祝福基准」

        正常路径：GameView 在装备结算后写入 ``weapon_damage_base`` / ``weapon_speed_base``。
        兜底路径：基准缺失时用「当前值 ÷ 上次已应用倍率」反推，保证首次/异常调用也不会重复叠加。
        """
        dmg_base = getattr(gs, "weapon_damage_base", None)
        spd_base = getattr(gs, "weapon_speed_base", None)
        if dmg_base is None:
            dmg_base = float(gs.weapon_damage) / (1.0 + self._applied.get("damage", 0.0))
            gs.weapon_damage_base = dmg_base
        if spd_base is None:
            spd_base = float(gs.weapon_speed) / (1.0 + self._applied.get("atk_speed", 0.0))
            gs.weapon_speed_base = spd_base
        return float(dmg_base), float(spd_base)

    # ── 一次性效果 / 联机上报 ───────────────────────────────────
    def grant_one_shot(self, player, blessing_id: str) -> float:
        """授予选中祝福的一次性效果（当前仅护盾），返回实际授予值

        护盾**不参与** recompute，否则每次重算都会白给，故只在选中瞬间结算一次。
        """
        definition = BLESSINGS.get(blessing_id)
        shield = float(definition.get("shield", 0.0)) if definition else 0.0
        if shield > 0.0 and player is not None:
            player.shield += shield
        return shield

    def stats_payload(self) -> dict[str, float]:
        """联机上报用：当前祝福加成后的有效属性（host 直接套到该玩家幽灵上）"""
        bonus = self._applied or self.aggregate()
        return {
            "weapon_damage_mult": 1.0 + bonus["damage"],
            "weapon_speed_mult": 1.0 + bonus["atk_speed"],
            "defense_bonus": bonus["defense"],
            "max_hp_bonus": bonus["max_hp"],
            "regen_bonus": bonus["regen"],
            "crit_chance_bonus": bonus["crit_chance"],
            "lifesteal_bonus": bonus["lifesteal"],
            "thorns_bonus": bonus["thorns"],
            "count": float(len(self.ids)),
        }
