import os
import sqlite3
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from database.watchlist import seed_default_watchlist
from stock_pool import DEFAULT_STOCK_POOL

DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")

conn = sqlite3.connect(DB_NAME)

cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS stock_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT,
    score REAL,
    level TEXT,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS score_validation (
    score_id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    score REAL NOT NULL,
    level TEXT NOT NULL,
    score_date TEXT NOT NULL,
    base_close REAL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    evaluated_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL UNIQUE,
    stock_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS daily_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'rule',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(trade_date, stock_code)
)
""")

conn.commit()

conn.close()

seeded = seed_default_watchlist(DEFAULT_STOCK_POOL)
if seeded:
    print(f"默认自选股初始化完成: {seeded} 只")

print("数据库初始化完成")
