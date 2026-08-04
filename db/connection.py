"""数据库连接管理"""
import sqlite3
import os

# 数据库文件路径（与本文件同目录）
DB_PATH = os.path.join(os.path.dirname(__file__), "game.db")


def _conn():
    """获取数据库连接"""
    return sqlite3.connect(DB_PATH)
