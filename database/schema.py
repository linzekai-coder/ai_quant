from sqlalchemy import text

from database.connection import engine, is_postgres
from database.backtest_results import init_backtest_results_table
from database.dynamic_pool import init_dynamic_pool_table
from database.news_raw import init_news_fetch_state_table, init_news_raw_table
from database.news_sentiment import init_stock_news_sentiment_table
from database.user_settings import init_user_settings_table
from database.users import init_users_table
from database.watchlist import init_watchlist_table


def create_core_tables(conn):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(text(f"""
    CREATE TABLE IF NOT EXISTS stock_scores (
        id {id_type},
        stock_code TEXT,
        score REAL,
        level TEXT,
        created_at TEXT
    )
    """))

    conn.execute(text("""
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
    """))

    conn.execute(text(f"""
    CREATE TABLE IF NOT EXISTS daily_candidates (
        id {id_type},
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
    """))


def init_database():
    with engine.begin() as conn:
        create_core_tables(conn)
    init_users_table()
    init_watchlist_table()
    init_user_settings_table()
    init_dynamic_pool_table()
    init_stock_news_sentiment_table()
    init_news_raw_table()
    init_news_fetch_state_table()
    init_backtest_results_table()
