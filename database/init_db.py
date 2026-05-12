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

conn.commit()

conn.close()

print("数据库初始化完成")
