"""数据库连接管理"""
import os
import sqlite3
import sys


def _get_data_dir():
    """解析游戏数据目录（存档位置）

    打包模式（PyInstaller 生成的 exe，sys.frozen 为真）下的解析优先级：
    1. exe 同目录的 data_dir.txt（安装包写入，用户可在安装时自定义数据目录）
    2. 环境变量 MONSTER_UPGRADE_DATA_DIR
    3. 兜底 %USERPROFILE%\\.monster_update（安装包默认数据目录）

    源码模式（python main.py 直接运行）维持原状：数据库仍在 db/ 目录下，
    保证开发环境行为完全不变。
    """
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    # 打包模式：优先读取 exe 同目录的 data_dir.txt（安装包写入的用户自定义数据目录）
    exe_dir = os.path.dirname(sys.executable)
    cfg = os.path.join(exe_dir, "data_dir.txt")
    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                path = f.read().strip()
            if path:
                return path
        except OSError:
            pass  # 读取失败则继续尝试环境变量/默认值
    # 其次环境变量
    env = os.environ.get("MONSTER_UPGRADE_DATA_DIR", "").strip()
    if env:
        return env
    # 兜底：用户主目录下的 .monster_update
    return os.path.join(os.path.expanduser("~"), ".monster_update")


def _ensure_data_dir():
    """确保数据目录存在（sqlite 连接前调用，不存在则创建）"""
    path = _get_data_dir()
    os.makedirs(path, exist_ok=True)
    return path


# 数据库文件路径（打包模式位于用户数据目录；源码模式与 db 目录同级）
DB_PATH = os.path.join(_ensure_data_dir(), "game.db")


def _conn():
    """获取数据库连接"""
    return sqlite3.connect(DB_PATH)