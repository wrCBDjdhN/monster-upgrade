"""SQLite 数据库初始化与 CRUD（兼容层）

本文件保留所有原始函数的 re-export，确保现有 import 代码无需修改。
实际实现已拆分到以下模块：
- db/connection.py: 数据库连接
- db/players.py: 玩家管理
- db/weapons.py: 武器管理
- db/equipment.py: 装备管理
- db/warehouse.py: 仓库管理
- db/potions.py: 药水管理
"""

from datetime import datetime

# 数据库连接统一走 db.connection（DB_PATH/_conn 在打包模式下指向用户数据目录，
# 源码模式维持 db/ 目录；此处仅 re-export 保持本模块对外接口不变）
from db.connection import DB_PATH, _conn  # noqa: F401


# ── 数据库初始化（保留在此文件，因为涉及所有表的创建） ──

def init_db():
    """初始化数据库：创建所有表（如果不存在）

    表结构说明：
    1. players: 玩家基础信息
    2. warehouse_items: 仓库物品（支持叠加数量）
    3. weapons: 武器（含伤害、攻速、等级）
    4. equipment: 装备（头盔/护甲/背包，含防御力、容量）
    5. potions: 药水（含效果、数值、持续时间）
    6. character_unlocks: 角色解锁记录（购买过的付费角色）
    7. character_levels: 角色等级（等级/经验/待选升级次数/永久属性加成，按角色独立）
    """
    with _conn() as c:
        # 玩家表
        c.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                gold INTEGER NOT NULL DEFAULT 50,
                created_at TEXT NOT NULL
            )
        """)
        # 仓库物品表（资源和武器）
        c.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK(item_type IN ('resource','weapon')),
                item_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)
        # 武器表（玩家拥有的武器实例）
        c.execute("""
            CREATE TABLE IF NOT EXISTS weapons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('melee','ranged')),
                name TEXT NOT NULL,
                damage REAL NOT NULL,
                attack_speed REAL NOT NULL,
                level INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)
        # 装备表（头盔/护甲/背包）
        c.execute("""
            CREATE TABLE IF NOT EXISTS equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                slot TEXT NOT NULL CHECK(slot IN ('helmet','armor','backpack')),
                item_id TEXT NOT NULL,
                name TEXT NOT NULL,
                defense INTEGER NOT NULL DEFAULT 0,
                capacity INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)
        # 药水表
        c.execute("""
            CREATE TABLE IF NOT EXISTS potions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                name TEXT NOT NULL,
                effect TEXT NOT NULL,
                value REAL NOT NULL,
                duration REAL NOT NULL DEFAULT 0,
                quantity INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)
        # 角色解锁表（付费角色购买记录；初始角色免费自带，不写入本表）
        c.execute("""
            CREATE TABLE IF NOT EXISTS character_unlocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                character_id TEXT NOT NULL,
                unlocked_at TEXT NOT NULL,
                UNIQUE(player_id, character_id),
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)
        # 角色等级表（等级/经验/待选升级次数/永久属性加成，按 (player_id, character_id) 独立）
        c.execute("""
            CREATE TABLE IF NOT EXISTS character_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                character_id TEXT NOT NULL,
                level INTEGER NOT NULL DEFAULT 1,
                exp INTEGER NOT NULL DEFAULT 0,
                pending_choices INTEGER NOT NULL DEFAULT 0,
                bonus_hp REAL NOT NULL DEFAULT 0,
                bonus_damage REAL NOT NULL DEFAULT 0,
                bonus_defense REAL NOT NULL DEFAULT 0,
                bonus_speed REAL NOT NULL DEFAULT 0,
                bonus_atk_speed REAL NOT NULL DEFAULT 0,
                UNIQUE(player_id, character_id),
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)
        # 游戏设置表（键值对：按键绑定/主音量/静音开关，settings_view 读写，重启保留）
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # 为 equipment 表添加 level 列（如果不存在）
        try:
            c.execute("ALTER TABLE equipment ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass  # 列已存在
        # 为 equipment 表添加 is_equipped 列（如果不存在）
        try:
            c.execute("ALTER TABLE equipment ADD COLUMN is_equipped INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass  # 列已存在

        # 为 weapons 表添加 item_id 列（如果不存在）
        try:
            c.execute("ALTER TABLE weapons ADD COLUMN item_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # 列已存在

        # 为 weapons 表添加 effects 列（Lv.5+ 物品随机附加效果，逗号分隔效果id可重复）
        try:
            c.execute("ALTER TABLE weapons ADD COLUMN effects TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # 列已存在
        # 为 equipment 表添加 effects 列（同上）
        try:
            c.execute("ALTER TABLE equipment ADD COLUMN effects TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # 列已存在

        # 更新旧武器数据的 item_id（根据名称匹配）
        from entities.weapon_defs import MELEE_WEAPONS, RANGED_WEAPONS
        all_weapons = {}
        all_weapons.update(MELEE_WEAPONS)
        all_weapons.update(RANGED_WEAPONS)
        # 创建名称到item_id的映射
        name_to_item_id = {}
        for item_id, info in all_weapons.items():
            name_to_item_id[info["name"]] = item_id
        # 更新item_id为空的旧数据
        rows = c.execute("SELECT id, name FROM weapons WHERE item_id = '' OR item_id IS NULL").fetchall()
        for wid, wname in rows:
            new_item_id = name_to_item_id.get(wname, '')
            if new_item_id:
                c.execute("UPDATE weapons SET item_id = ? WHERE id = ?", (new_item_id, wid))


# ── Re-exports（保持向后兼容） ──

# 玩家管理
from db.players import get_or_create_player, get_gold, add_gold, spend_gold  # noqa: F401, E402

# 武器管理
from db.weapons import create_weapon, add_weapon, get_weapons, upgrade_weapon, sell_weapon, delete_weapon  # noqa: F401, E402

# 装备管理
from db.equipment import (  # noqa: F401, E402
    get_equipment, get_equipment_inventory, equip_item, equip_from_inventory,
    unequip_slot, get_total_defense, get_backpack_capacity, add_equipment,
    get_equipment_materials, upgrade_equipment, sell_equipment, delete_equipment,
)

# 仓库管理
from db.warehouse import add_warehouse_item, get_warehouse, sell_warehouse_item  # noqa: F401, E402

# 药水管理
from db.potions import add_potion, get_potions, use_potion, remove_potion  # noqa: F401, E402


def clear_run_equipment(pid: int, weapon_db_id: int | None = None) -> None:
    """清空玩家本局已装备的武器/头盔/护甲/背包和所有药水（死亡/撤离失败用）

    weapon_db_id：已装备武器的 DB row id（weapons 表），为 None 时跳过武器删除。
    操作范围：
    - weapons 表：删除指定武器行
    - equipment 表：删除 is_equipped=1 的全部行（头盔/护甲/背包）
    - potions 表：删除该玩家全部药水行
    """
    from db.connection import _conn
    with _conn() as c:
        if weapon_db_id is not None:
            c.execute("DELETE FROM weapons WHERE id=? AND player_id=?", (weapon_db_id, pid))
        c.execute("DELETE FROM equipment WHERE player_id=? AND is_equipped=1", (pid,))
        c.execute("DELETE FROM potions WHERE player_id=?", (pid,))

# 角色管理（购买解锁持久化）
from db.characters import get_unlocked_characters, is_character_unlocked, unlock_character  # noqa: F401, E402

# 角色等级管理（等级/经验/待选升级/永久加成）
from db.levels import get_character_levels, add_exp, choose_bonus  # noqa: F401, E402

# 游戏设置管理（按键绑定/音量/静音，settings_view 读写）
from db.settings import (  # noqa: F401, E402
    get_setting, set_setting,
    get_key_bindings, set_key_bindings, reset_key_bindings,
    get_volume, set_volume, get_sound_enabled, set_sound_enabled,
)
