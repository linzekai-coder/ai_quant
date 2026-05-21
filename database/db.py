from datetime import datetime

from sqlalchemy import text

from database.connection import engine, is_postgres
from utils.logger import logger


def init_stock_scores_table():
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with engine.begin() as db:
        db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS stock_scores (
            id {id_type},
            stock_code TEXT,
            score REAL,
            level TEXT,
            created_at TEXT
        )
        """))


def save_score(stock_code, score, level):
    with engine.begin() as db:
        db.execute(text("""
        INSERT INTO stock_scores
        (stock_code, score, level, created_at)
        VALUES (:stock_code, :score, :level, :created_at)
        """), {
            "stock_code": stock_code,
            "score": score,
            "level": level,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    logger.info("%s 保存成功", stock_code)
    print(f"{stock_code} 保存成功")
