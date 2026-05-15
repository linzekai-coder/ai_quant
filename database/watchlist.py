import os
import sqlite3
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_watchlist_table(conn):
    conn.execute("""
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
    conn.commit()


def list_watchlist(enabled_only=False):
    conn = sqlite3.connect(DB_NAME)
    init_watchlist_table(conn)

    sql = """
    SELECT *
    FROM watchlist
    """
    params = ()
    if enabled_only:
        sql += " WHERE enabled = ?"
        params = (1,)
    sql += " ORDER BY enabled DESC, stock_code"

    rows = conn.execute(sql, params).fetchall()
    columns = [item[0] for item in conn.execute(sql, params).description]
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


def upsert_watchlist_stock(stock_code, stock_name, source="manual", enabled=1, note=None):
    stock_code = str(stock_code).zfill(6)
    timestamp = now_text()

    conn = sqlite3.connect(DB_NAME)
    init_watchlist_table(conn)
    conn.execute("""
    INSERT INTO watchlist (
        stock_code,
        stock_name,
        source,
        enabled,
        note,
        created_at,
        updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_code) DO UPDATE SET
        stock_name = excluded.stock_name,
        source = excluded.source,
        enabled = excluded.enabled,
        note = excluded.note,
        updated_at = excluded.updated_at
    """, (
        stock_code,
        stock_name,
        source,
        int(enabled),
        note,
        timestamp,
        timestamp,
    ))
    conn.commit()
    conn.close()


def set_watchlist_enabled(stock_code, enabled):
    conn = sqlite3.connect(DB_NAME)
    init_watchlist_table(conn)
    conn.execute("""
    UPDATE watchlist
    SET enabled = ?, updated_at = ?
    WHERE stock_code = ?
    """, (int(enabled), now_text(), str(stock_code).zfill(6)))
    conn.commit()
    conn.close()


def delete_watchlist_stock(stock_code):
    conn = sqlite3.connect(DB_NAME)
    init_watchlist_table(conn)
    conn.execute(
        "DELETE FROM watchlist WHERE stock_code = ?",
        (str(stock_code).zfill(6),),
    )
    conn.commit()
    conn.close()


def seed_default_watchlist(default_stocks):
    conn = sqlite3.connect(DB_NAME)
    init_watchlist_table(conn)

    count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    if count > 0:
        conn.close()
        return 0

    timestamp = now_text()
    rows = [
        (
            item["code"],
            item["name"],
            "default",
            1,
            "默认股票池",
            timestamp,
            timestamp,
        )
        for item in default_stocks
    ]
    conn.executemany("""
    INSERT INTO watchlist (
        stock_code,
        stock_name,
        source,
        enabled,
        note,
        created_at,
        updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)
