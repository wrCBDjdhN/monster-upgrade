"""游戏设置管理：键值对持久化（按键绑定 / 主音量 / 静音开关）

表结构（settings）：
    key   TEXT PRIMARY KEY   设置键名
    value TEXT               设置值（字符串）

存储约定：
- 按键绑定：key="key_bindings"，value=json.dumps({动作名: [键名列表]})，
  键名为 arcade.key 的属性名（如 "W"/"E"/"TAB"/"KEY_1"/"V"/"M"），
  运行时 getattr(arcade.key, name) 解析为键码；
- 主音量：key="sound_volume"，value="0.0"~"1.0"；
- 静音开关：key="sound_enabled"，value="1"/"0"。

settings_view（游戏内 ESC 设置界面）读写本模块，重启后自动保留。
"""
import json
from db.connection import _conn
from config import KEY_BINDINGS


def get_setting(key: str, default: str | None = None) -> str | None:
    """读取单个设置值；无记录返回 default"""
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    """写入/覆盖单个设置值（键值表，主键冲突即覆盖）"""
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
            (key, value),
        )


# ── 按键绑定 ──

def get_key_bindings() -> dict:
    """读取按键绑定（JSON 反序列化）；无记录/解析失败返回 config 默认值

    返回 {动作名: [键名, ...]}，键名为 arcade.key 属性名（字符串）。
    """
    raw = get_setting("key_bindings")
    if not raw:
        return json.loads(json.dumps(KEY_BINDINGS))  # 深拷贝默认，防止调用方误改全局常量
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return json.loads(json.dumps(KEY_BINDINGS))
        # 与默认合并：新增动作（未来版本）缺失时自动补默认键，旧数据不受影响
        merged = json.loads(json.dumps(KEY_BINDINGS))
        for action, keys in data.items():
            if isinstance(keys, list) and keys:
                merged[action] = list(keys)
        return merged
    except Exception:
        return json.loads(json.dumps(KEY_BINDINGS))


def set_key_bindings(bindings: dict) -> None:
    """保存按键绑定（JSON 序列化）"""
    set_setting("key_bindings", json.dumps(bindings))


def reset_key_bindings() -> dict:
    """恢复默认按键绑定（覆盖已保存的配置），返回默认映射"""
    set_key_bindings(json.loads(json.dumps(KEY_BINDINGS)))
    return json.loads(json.dumps(KEY_BINDINGS))


# ── 主音量 / 静音开关 ──

def get_volume() -> float:
    """读取主音量（0.0~1.0）；无记录返回默认 0.4（与 SoundManager 默认一致）"""
    raw = get_setting("sound_volume")
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.4


def set_volume(volume: float) -> None:
    """保存主音量（钳制 0.0~1.0）"""
    set_setting("sound_volume", str(max(0.0, min(1.0, float(volume)))))


def get_sound_enabled() -> bool:
    """读取静音开关；无记录默认开启（"0"=关，其余=开）"""
    return get_setting("sound_enabled", "1") != "0"


def set_sound_enabled(enabled: bool) -> None:
    """保存静音开关"""
    set_setting("sound_enabled", "1" if enabled else "0")


# ── 新手教程标记 ──
# 首次启动未标记 tutorial_done 时启用新手教程；教程被跳过或完整走完后标记，
# 之后不再出现。教程中途退出（未走完）不标记，下次启动重新从头开始。

def is_tutorial_done() -> bool:
    """新手教程是否已完成（settings 表 tutorial_done=1）"""
    return get_setting("tutorial_done") == "1"


def mark_tutorial_done() -> None:
    """标记新手教程已完成（永久关闭教程）"""
    set_setting("tutorial_done", "1")
