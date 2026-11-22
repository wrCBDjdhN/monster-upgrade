"""宝箱开启：仓库/市场/任务奖励共用的单箱产出逻辑

用户口径 2026-11-21：
- 武器箱/装备箱先存入仓库（每箱占 1 格），在仓库「宝箱」页按数量开启；
- 开箱产物严格校验仓库容量：装得下才入仓，装不下整件转入邮件（source=box_overflow），
  不再出现「开箱把仓库塞爆」。

调用顺序：调用方先 consume_warehouse_box 扣箱，再逐箱调用 open_one_box。
"""
import random

from config import (
    ALL_EQUIP_POOL, ALL_WEAPON_IDS, EQUIPMENT_BOXES, WEAPON_BOXES,
    upgrade_mult_product,
)
from db.database import (
    add_equipment, add_potion, add_weapon, create_mail,
    warehouse_item_capacity, warehouse_remaining_capacity,
)
from entities.equipment_defs import ARMORS, BACKPACKS, HELMETS, POTIONS
from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS


def _weapon_info(item_id: str) -> dict:
    """按 item_id 取武器定义（近战/远程合并）"""
    return MELEE_WEAPONS.get(item_id) or RANGED_WEAPONS.get(item_id) or {}


def _equip_info(slot: str, item_id: str) -> dict:
    """按槽位取装备定义（头盔/护甲/背包）"""
    return {"helmet": HELMETS, "armor": ARMORS, "backpack": BACKPACKS}.get(slot, {}).get(item_id, {})


def _grant_or_mail(pid: int, item_type: str, item_id: str, level: int,
                   quantity: int = 1) -> bool:
    """把单件产物入库；容量不足整件转邮件。返回 True=已入仓，False=已转邮件"""
    need = warehouse_item_capacity(item_type, item_id) * quantity
    if need <= 0 or need <= warehouse_remaining_capacity(pid):
        if item_type == "weapon":
            info = _weapon_info(item_id)
            if not info:
                return True
            # 与市场开箱同口径：武器伤害按等级缩放
            damage = round(info["damage"] * upgrade_mult_product(level), 1)
            for _ in range(quantity):
                add_weapon(pid, item_id, info["name"], info["kind"], damage,
                           info.get("attack_speed", 1.0), level)
        elif item_type in ("helmet", "armor", "backpack"):
            for _ in range(quantity):
                add_equipment(pid, item_id, item_type, level)
        elif item_type == "potion":
            info = POTIONS.get(item_id)
            if not info:
                return True
            for _ in range(quantity):
                add_potion(pid, item_id, info["name"], info["effect"],
                           info.get("value", 0), info.get("duration", 0))
        return True
    # 容量不足：整件转邮件，避免开箱把仓库塞爆
    create_mail(
        pid, "开箱产物补发",
        f"仓库容量不足，开箱获得的 {item_id} 已转入邮件，请清理仓库后领取。",
        attachments=[{
            "item_type": item_type, "item_id": item_id,
            "level": int(level), "quantity": int(quantity),
        }],
        source="box_overflow",
    )
    return False


def open_one_box(pid: int, box_type: str, box_id: str) -> dict:
    """开 1 个箱子：随机产出 → 入仓/转邮件，返回展示用结果"""
    if box_type == "weapon":
        box = WEAPON_BOXES.get(box_id)
        if not box:
            return {"name": box_id, "level": 1, "color": (150, 150, 150), "mailed": False}
        item_id = random.choice(ALL_WEAPON_IDS)
        info = _weapon_info(item_id)
        if not info:
            return {"name": item_id, "level": 1, "color": (150, 150, 150), "mailed": False}
        lo, hi = box["level_range"]
        level = random.randint(int(lo), int(hi))
        mailed = not _grant_or_mail(pid, "weapon", item_id, level)
        return {
            "name": info.get("name", item_id),
            "level": level,
            "color": info.get("color", (255, 200, 50)),
            "mailed": mailed,
        }
    # equipment box
    box = EQUIPMENT_BOXES.get(box_id)
    if not box:
        return {"name": box_id, "level": 1, "color": (150, 150, 150), "mailed": False}
    slot, item_id = random.choice(ALL_EQUIP_POOL)
    info = _equip_info(slot, item_id)
    if not info:
        return {"name": item_id, "level": 1, "color": (150, 150, 150), "mailed": False}
    lo, hi = box["level_range"]
    level = random.randint(int(lo), int(hi))
    mailed = not _grant_or_mail(pid, slot, item_id, level)
    return {
        "name": info.get("name", item_id),
        "level": level,
        "color": info.get("color", (150, 180, 200)),
        "mailed": mailed,
    }


def open_warehouse_boxes(pid: int, box_type: str, box_id: str, quantity: int) -> list[dict]:
    """从仓库扣 N 个箱并逐个开启，返回每箱结果；库存不足返回空列表"""
    from db.boxes import consume_warehouse_box
    qty = int(quantity)
    if qty <= 0:
        return []
    # 先校验箱子定义，避免扣箱后 KeyError 导致箱子白丢
    if box_type == "weapon" and box_id not in WEAPON_BOXES:
        return []
    if box_type == "equipment" and box_id not in EQUIPMENT_BOXES:
        return []
    if not consume_warehouse_box(pid, box_type, box_id, qty):
        return []
    return [open_one_box(pid, box_type, box_id) for _ in range(qty)]
