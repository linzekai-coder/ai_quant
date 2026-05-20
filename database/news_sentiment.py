import json
import os
import sqlite3
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_stock_news_sentiment_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_news_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        news_count INTEGER NOT NULL DEFAULT 0,
        sentiment TEXT NOT NULL,
        sentiment_score REAL NOT NULL DEFAULT 0,
        news_titles TEXT,
        analysis_reason TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(stock_code, trade_date)
    )
    """)
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(stock_news_sentiment)").fetchall()
    }
    migrations = {
        "macro_sentiment_cn": "ALTER TABLE stock_news_sentiment ADD COLUMN macro_sentiment_cn REAL NOT NULL DEFAULT 0",
        "macro_sentiment_global": "ALTER TABLE stock_news_sentiment ADD COLUMN macro_sentiment_global REAL NOT NULL DEFAULT 0",
        "industry_sentiment": "ALTER TABLE stock_news_sentiment ADD COLUMN industry_sentiment REAL NOT NULL DEFAULT 0",
        "final_sentiment_score": "ALTER TABLE stock_news_sentiment ADD COLUMN final_sentiment_score REAL NOT NULL DEFAULT 0",
        "score_breakdown": "ALTER TABLE stock_news_sentiment ADD COLUMN score_breakdown TEXT",
    }
    for column, sql in migrations.items():
        if column not in columns:
            conn.execute(sql)
    conn.commit()


def sentiment_exists(stock_code, trade_date):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    row = conn.execute("""
    SELECT 1
    FROM stock_news_sentiment
    WHERE stock_code = ? AND trade_date = ?
    """, (str(stock_code).zfill(6), trade_date)).fetchone()
    conn.close()
    return row is not None


def get_existing_sentiment_status(stock_code, trade_date):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    row = conn.execute("""
    SELECT sentiment, news_count, score_breakdown, analysis_reason
    FROM stock_news_sentiment
    WHERE stock_code = ? AND trade_date = ?
    """, (str(stock_code).zfill(6), trade_date)).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "sentiment": row[0],
        "news_count": int(row[1] or 0),
        "has_breakdown": bool(row[2]),
        "analysis_reason": row[3] or "",
    }


def save_news_sentiment(item):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    conn.execute("""
    INSERT INTO stock_news_sentiment (
        stock_code,
        trade_date,
        news_count,
        sentiment,
        sentiment_score,
        news_titles,
        analysis_reason,
        created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_code, trade_date) DO NOTHING
    """, (
        str(item["stock_code"]).zfill(6),
        item["trade_date"],
        int(item.get("news_count", 0)),
        item.get("sentiment", "无数据"),
        float(item.get("sentiment_score", 0)),
        json.dumps(item.get("news_titles", []), ensure_ascii=False),
        item.get("analysis_reason", ""),
        item.get("created_at") or now_text(),
    ))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def upsert_news_sentiment(item):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    conn.execute("""
    INSERT INTO stock_news_sentiment (
        stock_code,
        trade_date,
        news_count,
        sentiment,
        sentiment_score,
        news_titles,
        analysis_reason,
        macro_sentiment_cn,
        macro_sentiment_global,
        industry_sentiment,
        final_sentiment_score,
        score_breakdown,
        created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stock_code, trade_date) DO UPDATE SET
        news_count = excluded.news_count,
        sentiment = excluded.sentiment,
        sentiment_score = excluded.sentiment_score,
        news_titles = excluded.news_titles,
        analysis_reason = excluded.analysis_reason,
        macro_sentiment_cn = excluded.macro_sentiment_cn,
        macro_sentiment_global = excluded.macro_sentiment_global,
        industry_sentiment = excluded.industry_sentiment,
        final_sentiment_score = excluded.final_sentiment_score,
        score_breakdown = excluded.score_breakdown,
        created_at = excluded.created_at
    """, (
        str(item["stock_code"]).zfill(6),
        item["trade_date"],
        int(item.get("news_count", 0)),
        item.get("sentiment", "无数据"),
        float(item.get("sentiment_score", 0)),
        json.dumps(item.get("news_titles", []), ensure_ascii=False),
        item.get("analysis_reason", ""),
        float(item.get("macro_sentiment_cn", 0)),
        float(item.get("macro_sentiment_global", 0)),
        float(item.get("industry_sentiment", 0)),
        float(item.get("final_sentiment_score", item.get("sentiment_score", 0))),
        json.dumps(item.get("score_breakdown", {}), ensure_ascii=False),
        item.get("created_at") or now_text(),
    ))
    conn.commit()
    conn.close()
    return True


def get_news_sentiment(stock_code, trade_date):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    cursor = conn.execute("""
    SELECT *
    FROM stock_news_sentiment
    WHERE stock_code = ? AND trade_date = ?
    """, (str(stock_code).zfill(6), trade_date))
    row = cursor.fetchone()
    columns = [item[0] for item in cursor.description]
    conn.close()
    if row is None:
        return None
    result = dict(zip(columns, row))
    try:
        result["news_titles"] = json.loads(result.get("news_titles") or "[]")
    except json.JSONDecodeError:
        result["news_titles"] = []
    try:
        result["score_breakdown"] = json.loads(result.get("score_breakdown") or "{}")
    except json.JSONDecodeError:
        result["score_breakdown"] = {}
    return result


def list_latest_news_sentiments(stock_code=None, limit=50):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    sql = """
    SELECT *
    FROM stock_news_sentiment
    """
    params = []
    if stock_code:
        sql += " WHERE stock_code = ?"
        params.append(str(stock_code).zfill(6))
    sql += " ORDER BY trade_date DESC, created_at DESC LIMIT ?"
    params.append(int(limit))
    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    for row in rows:
        try:
            row["news_titles"] = json.loads(row.get("news_titles") or "[]")
        except json.JSONDecodeError:
            row["news_titles"] = []
    return rows


def list_news_sentiments_by_date(trade_date, stock_code=None):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    sql = """
    SELECT
        s.*,
        COALESCE(w.stock_name, '') AS stock_name
    FROM stock_news_sentiment s
    LEFT JOIN watchlist w
        ON w.stock_code = s.stock_code
    WHERE s.trade_date = ?
    """
    params = [trade_date]
    if stock_code:
        sql += " AND s.stock_code = ?"
        params.append(str(stock_code).zfill(6))
    sql += " ORDER BY s.sentiment_score DESC, s.stock_code"
    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    for row in rows:
        try:
            row["news_titles"] = json.loads(row.get("news_titles") or "[]")
        except json.JSONDecodeError:
            row["news_titles"] = []
    return rows


def list_news_sentiments(stock_code, start_date=None, end_date=None, limit=30):
    conn = sqlite3.connect(DB_NAME)
    init_stock_news_sentiment_table(conn)
    sql = """
    SELECT *
    FROM stock_news_sentiment
    WHERE stock_code = ?
    """
    params = [str(stock_code).zfill(6)]
    if start_date:
        sql += " AND trade_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date <= ?"
        params.append(end_date)
    sql += " ORDER BY trade_date DESC, created_at DESC LIMIT ?"
    params.append(int(limit))
    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    for row in rows:
        try:
            row["news_titles"] = json.loads(row.get("news_titles") or "[]")
        except json.JSONDecodeError:
            row["news_titles"] = []
    return list(reversed(rows))
