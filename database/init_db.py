import os
import sqlite3


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

conn.commit()

conn.close()

print("数据库初始化完成")
