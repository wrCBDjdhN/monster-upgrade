"""随机地图事件定义（阶段 4）：四种局内事件的静态数据表

事件在开局由主机/solo 用地图种子等权随机抽一个（见 game/map_events.pick_event），
抽中后由 game/map_events.apply_event 把参数字段写进 view.event_flags：

- tide（尸潮）：怪物上限 ×cap_mult、击杀掉落 ×reward_mult（怪更多、奖励更高）
- airdrop（空投）：开局 delay 秒后在地图上落地 crate_count 个高级补给箱
- caravan（商队）：地图上生成一个交互点，按 E 用局内金币按 price_table 换购
- relic（神器低语）：本局神器掉落等级 +artifact_bonus 比例

本表只放数据，数值一律从 config 导入（禁在本层硬编码）；banner 为开局横幅文案。
"""

from config import (
    EVENT_AIRDROP_CRATES,
    EVENT_AIRDROP_DELAY,
    EVENT_CARAVAN_PRICES,
    EVENT_RELIC_ARTIFACT_BONUS,
    EVENT_TIDE_CAP_MULT,
    EVENT_TIDE_REWARD_MULT,
)

# 事件 id → 事件定义。四键固定：tide / airdrop / caravan / relic
MAP_EVENTS: dict[str, dict] = {
    "tide": {
        "name": "尸潮",
        "banner": "尸潮来袭：怪物数量大增，击杀掉落同步提升",
        "cap_mult": EVENT_TIDE_CAP_MULT,        # 野外怪物上限倍率（反算进 view._monster_cap）
        "reward_mult": EVENT_TIDE_REWARD_MULT,  # 击杀掉落倍率（金币/资源数量）
    },
    "airdrop": {
        "name": "空投",
        "banner": "空投已投放战场：数个高级补给箱即将落地",
        "delay": EVENT_AIRDROP_DELAY,           # 落地延迟（秒，自开局计时）
        "crate_count": EVENT_AIRDROP_CRATES,    # 落地补给箱数量
    },
    "caravan": {
        "name": "商队",
        "banner": "流浪商队已抵达：靠近招牌按 E 用金币换购补给",
        "price_table": EVENT_CARAVAN_PRICES,    # 换购价表：item_id → 局内金币价
    },
    "relic": {
        "name": "神器低语",
        "banner": "神器低语：本次行动的神器掉落等级提升",
        "artifact_bonus": EVENT_RELIC_ARTIFACT_BONUS,  # 神器等级加成比例
    },
}

# 事件 id 顺序（抽选时用，保证同种子在 host 与 solo 抽到同一事件）
EVENT_IDS: list[str] = list(MAP_EVENTS.keys())


def get_event(event_id: str) -> dict:
    """按事件 id 取事件定义；未知/空 id 返回空字典（调用方据此判定"本局无事件"）"""
    return MAP_EVENTS.get(event_id) or {}
