import os
import sqlite3
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_daily_candidates_table(conn):
    conn.execute("""
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


def save_daily_candidates(candidates):
    conn = sqlite3.connect(DB_NAME)
    init_daily_candidates_table(conn)

    timestamp = now_text()
    rows = [
        (
            item["trade_date"],
            item["stock_code"],
            item["stock_name"],
            item["score"],
            item.get("reason"),
            item.get("source", "rule"),
            int(item.get("enabled", 1)),
            timestamp,
        )
        for item in candidates
    ]
    conn.executemany("""
    INSERT INTO daily_candidates (
        trade_date,
        stock_code,
        stock_name,
        score,
        reason,
        source,
        enabled,
        created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(trade_date, stock_code) DO UPDATE SET
        stock_name = excluded.stock_name,
        score = excluded.score,
        reason = excluded.reason,
        source = excluded.source,
        enabled = excluded.enabled,
        created_at = excluded.created_at
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def list_latest_candidates(enabled_only=True):
    conn = sqlite3.connect(DB_NAME)
    init_daily_candidates_table(conn)

    latest_date = conn.execute(
        "SELECT MAX(trade_date) FROM daily_candidates"
    ).fetchone()[0]

    if latest_date is None:
        conn.close()
        return []

    sql = """
    SELECT *
    FROM daily_candidates
    WHERE trade_date = ?
    """
    params = [latest_date]
    if enabled_only:
        sql += " AND enabled = ?"
        params.append(1)
    sql += " ORDER BY score DESC, stock_code"

    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return rows
