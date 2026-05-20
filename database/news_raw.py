import os
import sqlite3
from datetime import datetime, timedelta


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_news_raw_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS news_raw (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL,
        source_name TEXT NOT NULL,
        stock_code TEXT,
        title TEXT NOT NULL,
        summary TEXT,
        published_time TEXT NOT NULL,
        url TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(title, published_time)
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_raw_type_time ON news_raw(source_type, published_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_raw_stock_time ON news_raw(stock_code, published_time)")
    conn.commit()


def init_news_fetch_state_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS news_fetch_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT,
        UNIQUE(source_type, cache_key)
    )
    """)
    conn.commit()


def cleanup_old_news(days=7):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_NAME)
    init_news_raw_table(conn)
    init_news_fetch_state_table(conn)
    conn.execute("DELETE FROM news_raw WHERE published_time < ?", (cutoff,))
    conn.commit()
    conn.close()


def upsert_news_raw(items):
    if not items:
        return 0
    conn = sqlite3.connect(DB_NAME)
    init_news_raw_table(conn)
    init_news_fetch_state_table(conn)
    before = conn.total_changes
    conn.executemany("""
    INSERT INTO news_raw (
        source_type,
        source_name,
        stock_code,
        title,
        summary,
        published_time,
        url,
        created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(title, published_time) DO NOTHING
    """, [
        (
            item.get("source_type", ""),
            item.get("source_name", ""),
            item.get("stock_code"),
            item.get("title", ""),
            item.get("summary", ""),
            item.get("published_time", ""),
            item.get("url", ""),
            item.get("created_at") or now_text(),
        )
        for item in items
        if item.get("title") and item.get("published_time")
    ])
    inserted = conn.total_changes - before
    conn.commit()
    conn.close()
    cleanup_old_news(days=7)
    return inserted


def list_news_raw(source_type=None, stock_code=None, start_time=None, end_time=None, limit=200):
    conn = sqlite3.connect(DB_NAME)
    init_news_raw_table(conn)
    init_news_fetch_state_table(conn)
    sql = """
    SELECT *
    FROM news_raw
    WHERE 1 = 1
    """
    params = []
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
    if stock_code is not None:
        sql += " AND stock_code = ?"
        params.append(str(stock_code).zfill(6))
    if start_time:
        sql += " AND published_time >= ?"
        params.append(start_time)
    if end_time:
        sql += " AND published_time <= ?"
        params.append(end_time)
    sql += " ORDER BY published_time DESC LIMIT ?"
    params.append(int(limit))
    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return rows


def has_news_since(source_type, since_time, stock_code=None):
    conn = sqlite3.connect(DB_NAME)
    init_news_raw_table(conn)
    init_news_fetch_state_table(conn)
    sql = """
    SELECT 1
    FROM news_raw
    WHERE source_type = ? AND created_at >= ?
    """
    params = [source_type, since_time]
    if stock_code is not None:
        sql += " AND stock_code = ?"
        params.append(str(stock_code).zfill(6))
    sql += " LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row is not None


def get_fetch_state(source_type, cache_key):
    conn = sqlite3.connect(DB_NAME)
    init_news_fetch_state_table(conn)
    cursor = conn.execute("""
    SELECT source_type, cache_key, fetched_at, status, message
    FROM news_fetch_state
    WHERE source_type = ? AND cache_key = ?
    """, (source_type, cache_key))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "source_type": row[0],
        "cache_key": row[1],
        "fetched_at": row[2],
        "status": row[3],
        "message": row[4],
    }


def set_fetch_state(source_type, cache_key, status="ok", message=""):
    conn = sqlite3.connect(DB_NAME)
    init_news_fetch_state_table(conn)
    conn.execute("""
    INSERT INTO news_fetch_state (
        source_type,
        cache_key,
        fetched_at,
        status,
        message
    )
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(source_type, cache_key) DO UPDATE SET
        fetched_at = excluded.fetched_at,
        status = excluded.status,
        message = excluded.message
    """, (source_type, cache_key, now_text(), status, message))
    conn.commit()
    conn.close()


def has_recent_fetch(source_type, cache_key, since_time):
    state = get_fetch_state(source_type, cache_key)
    if not state:
        return False
    if state.get("status") != "ok":
        return False
    return str(state.get("fetched_at") or "") >= since_time
