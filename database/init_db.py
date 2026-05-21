import os
import sqlite3
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from database.watchlist import seed_default_watchlist
from database.news_sentiment import init_stock_news_sentiment_table
from database.news_raw import init_news_fetch_state_table, init_news_raw_table
from database.dynamic_pool import init_dynamic_pool_table
from database.users import ensure_default_admin, init_users_table
from database.user_settings import init_user_settings_table
from database.backtest_results import init_backtest_results_table
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

init_stock_news_sentiment_table(conn)
init_news_raw_table(conn)
init_news_fetch_state_table(conn)
init_dynamic_pool_table(conn)
init_users_table(conn)
init_watchlist_table(conn)
init_user_settings_table(conn)
init_backtest_results_table(conn)

conn.close()

seeded = seed_default_watchlist(DEFAULT_STOCK_POOL, user_id=1)
if seeded:
    print(f"默认自选股初始化完成: {seeded} 只")

admin = ensure_default_admin()
if admin:
    print("默认管理员已创建: admin / <ADMIN_PASSWORD 或临时默认密码>")

print("数据库初始化完成")
