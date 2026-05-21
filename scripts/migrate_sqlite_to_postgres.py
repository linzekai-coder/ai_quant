import os
import sqlite3
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLITE_PATH = os.path.join(PROJECT_ROOT, "quant.db")


TABLES = [
    "users",
    "watchlist",
    "user_settings",
    "stock_scores",
    "score_validation",
    "daily_candidates",
    "dynamic_pool",
    "dynamic_pool_scan_runs",
    "stock_news_sentiment",
    "news_raw",
    "news_fetch_state",
    "backtest_results",
]


def main():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url.startswith("postgresql"):
        raise SystemExit("DATABASE_URL 必须指向 PostgreSQL，例如 postgresql://user:password@localhost:5432/quant_db")
    if not os.path.exists(SQLITE_PATH):
        raise SystemExit(f"未找到 SQLite 数据库: {SQLITE_PATH}")

    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    from database.schema import init_database

    init_database()
    pg_engine = create_engine(database_url, future=True)
    sqlite_conn = sqlite3.connect(SQLITE_PATH)

    for table in TABLES:
        exists = sqlite_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            print(f"跳过不存在表: {table}")
            continue

        df = pd.read_sql_query(f"SELECT * FROM {table}", sqlite_conn)
        if df.empty:
            print(f"跳过空表: {table}")
            continue

        with pg_engine.begin() as conn:
            conn.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))
        df.to_sql(table, pg_engine, if_exists="append", index=False, method="multi", chunksize=1000)
        print(f"已迁移 {table}: {len(df)} 行")

    sqlite_conn.close()
    print("SQLite -> PostgreSQL 数据迁移完成")


if __name__ == "__main__":
    main()
