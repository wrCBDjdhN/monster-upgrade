"""撤离系统：读条撤离 + 死亡惩罚

撤离机制：
1. 玩家站在撤离点上开始读条
2. 读条时间 3 秒，期间不能移动
3. 读条完成后将携带物写入仓库
4. 死亡会丢失所有携带物

相关函数：
- commit_run_to_warehouse: 撤离成功，写入仓库
- clear_run: 死亡惩罚，清空携带物
"""

import arcade
from config import EVAC_CHANNEL_TIME, EVAC_RADIUS, EVAC_COLOR


class EvacState:
    """管理撤离读条状态"""
    def __init__(self):
        self._channeling = False
        self._timer = 0.0
        self.active_evac = None  # 当前所在撤离点 (x,y)

    def update(self, player, evac_points: list, delta_time: float) -> str | None:
        """
        检测玩家是否在撤离点上。
        返回 'evacuated' 表示撤离完成，否则 None。
        """
        # 检测是否在撤离点范围内
        self.active_evac = None
        for ex, ey in evac_points:
            dist = ((player.center_x - ex) ** 2 + (player.center_y - ey) ** 2) ** 0.5
            if dist < EVAC_RADIUS:
                self.active_evac = (ex, ey)
                break

        if self.active_evac:
            if not self._channeling:
                self._channeling = True
                self._timer = 0.0
            self._timer += delta_time
            if self._timer >= EVAC_CHANNEL_TIME:
                self._channeling = False
                self._timer = 0.0
                return "evacuated"
        else:
            self._channeling = False
            self._timer = 0.0
        return None

    @property
    def progress(self) -> float:
        """0.0 ~ 1.0"""
        if not self._channeling:
            return 0.0
        return min(1.0, self._timer / EVAC_CHANNEL_TIME)

    @property
    def is_channeling(self) -> bool:
        return self._channeling

    def draw_progress(self, player):
        """在玩家头顶画读条"""
        if not self._channeling:
            return
        bar_w = 50
        bar_h = 6
        x = player.center_x - bar_w // 2
        y = player.center_y + 30
        # 背景
        arcade.draw_rect_filled(
            arcade.XYWH(x + bar_w // 2, y, bar_w, bar_h),
            arcade.color.DARK_GRAY,
        )
        # 进度
        fill_w = bar_w * self.progress
        arcade.draw_rect_filled(
            arcade.XYWH(x + fill_w // 2, y, fill_w, bar_h),
            EVAC_COLOR,
        )


def commit_run_to_warehouse(pid: int, run_carried: dict):
    """撤离成功：将本次携带物写入仓库 DB"""
    from db.database import add_warehouse_item, add_gold
    for item_type, items in run_carried.items():
        if item_type == "gold":
            add_gold(pid, items)
        elif item_type == "resource":
            for item_id, qty in items.items():
                add_warehouse_item(pid, "resource", item_id, qty)
        elif item_type == "weapon":
            # run_carried["weapon"] = {(item_id, level): qty, ...}，入库时保留等级
            from db.database import create_weapon
            from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
            all_weapons = {}
            all_weapons.update(MELEE_WEAPONS)
            all_weapons.update(RANGED_WEAPONS)
            for (weapon_id, level), qty in items.items():
                if weapon_id in all_weapons:
                    info = all_weapons[weapon_id]
                    kind = "melee" if weapon_id in MELEE_WEAPONS else "ranged"
                    for _ in range(qty):
                        create_weapon(pid, weapon_id, kind, info["name"], info["damage"], info["attack_speed"],
                                      level=level)
        elif item_type in ("helmet", "armor"):
            # run_carried[slot] = {(item_id, level): qty, ...}
            from db.database import add_equipment
            for (item_id, level), qty in items.items():
                for _ in range(qty):
                    add_equipment(pid, item_id, item_type, level=level)
        elif item_type == "backpack":
            # run_carried["backpack"] = {(item_id, level): qty, ...}
            from db.database import add_equipment
            for (item_id, level), qty in items.items():
                for _ in range(qty):
                    add_equipment(pid, item_id, "backpack", level=level)
        elif item_type == "potion":
            # run_carried["potion"] = {item_id: qty, ...}（果实等掉落药水入库）
            from db.database import add_potion
            from entities.equipment_defs import POTIONS
            for item_id, qty in items.items():
                info = POTIONS.get(item_id, {})
                if not info:
                    continue
                for _ in range(qty):
                    add_potion(pid, item_id, info["name"], info["effect"], info.get("value", 0), info.get("duration", 0))


def clear_run(run_carried: dict):
    """死亡惩罚：清空玩家【当前携带】的物资（不写入 DB）。

    仅清除本次冒险中携带的物品，包括：
    - gold     : 本次携带的金币（注意：不是玩家数据库里拥有的全部金币）
    - resource : 本次携带的资源
    - weapon   : 本次携带的武器
    - backpack : 本次携带的背包
    - helmet   : 本次携带的头盔
    - armor    : 本次携带的护甲

    玩家在数据库中已拥有的全部金币（players.gold）以及仓库内的物品
    不受影响——本函数只操作内存中的 run_carried 字典，不触碰数据库。
    """
    # 显式清空各个携带类别，确保死亡时只丢失"当前携带"的物资
    for key in ("gold", "resource", "weapon", "backpack", "helmet", "armor", "potion"):
        run_carried.pop(key, None)
