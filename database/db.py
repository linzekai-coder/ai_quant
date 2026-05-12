import os
import sqlite3
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")


def save_score(stock_code, score, level):
    conn = sqlite3.connect(DB_NAME)

    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO stock_scores
    (stock_code, score, level, created_at)
    VALUES (?, ?, ?, ?)
    """, (
        stock_code,
        score,
        level,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()

    conn.close()

    print(f"{stock_code} 保存成功")
