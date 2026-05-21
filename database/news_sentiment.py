import json
from datetime import datetime

from sqlalchemy import text

from database.connection import engine, is_postgres


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_stock_news_sentiment_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS stock_news_sentiment (
        id {id_type},
        stock_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        news_count INTEGER NOT NULL DEFAULT 0,
        sentiment TEXT NOT NULL,
        sentiment_score REAL NOT NULL DEFAULT 0,
        news_titles TEXT,
        analysis_reason TEXT,
        created_at TEXT NOT NULL,
        macro_sentiment_cn REAL NOT NULL DEFAULT 0,
        macro_sentiment_global REAL NOT NULL DEFAULT 0,
        industry_sentiment REAL NOT NULL DEFAULT 0,
        final_sentiment_score REAL NOT NULL DEFAULT 0,
        score_breakdown TEXT,
        UNIQUE(stock_code, trade_date)
    )
    """
    if conn is not None:
        conn.execute(ddl)
        return
    with engine.begin() as db:
        db.execute(text(ddl))


def _json_load(value, fallback):
    try:
        return json.loads(value or fallback)
    except json.JSONDecodeError:
        return json.loads(fallback)


def _decode_row(row):
    if row is None:
        return None
    result = dict(row._mapping)
    result["news_titles"] = _json_load(result.get("news_titles"), "[]")
    result["score_breakdown"] = _json_load(result.get("score_breakdown"), "{}")
    return result


def sentiment_exists(stock_code, trade_date):
    init_stock_news_sentiment_table()
    with engine.connect() as db:
        row = db.execute(text("""
        SELECT 1
        FROM stock_news_sentiment
        WHERE stock_code = :stock_code AND trade_date = :trade_date
        """), {
            "stock_code": str(stock_code).zfill(6),
            "trade_date": trade_date,
        }).fetchone()
    return row is not None


def get_existing_sentiment_status(stock_code, trade_date):
    init_stock_news_sentiment_table()
    with engine.connect() as db:
        row = db.execute(text("""
        SELECT sentiment, news_count, score_breakdown, analysis_reason
        FROM stock_news_sentiment
        WHERE stock_code = :stock_code AND trade_date = :trade_date
        """), {
            "stock_code": str(stock_code).zfill(6),
            "trade_date": trade_date,
        }).fetchone()
    if row is None:
        return None
    return {
        "sentiment": row[0],
        "news_count": int(row[1] or 0),
        "has_breakdown": bool(row[2]),
        "analysis_reason": row[3] or "",
    }


def save_news_sentiment(item):
    init_stock_news_sentiment_table()
    with engine.begin() as db:
        result = db.execute(text("""
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
        VALUES (
            :stock_code,
            :trade_date,
            :news_count,
            :sentiment,
            :sentiment_score,
            :news_titles,
            :analysis_reason,
            :created_at
        )
        ON CONFLICT(stock_code, trade_date) DO NOTHING
        """), {
            "stock_code": str(item["stock_code"]).zfill(6),
            "trade_date": item["trade_date"],
            "news_count": int(item.get("news_count", 0)),
            "sentiment": item.get("sentiment", "无数据"),
            "sentiment_score": float(item.get("sentiment_score", 0)),
            "news_titles": json.dumps(item.get("news_titles", []), ensure_ascii=False),
            "analysis_reason": item.get("analysis_reason", ""),
            "created_at": item.get("created_at") or now_text(),
        })
    return (result.rowcount or 0) > 0


def upsert_news_sentiment(item):
    init_stock_news_sentiment_table()
    with engine.begin() as db:
        db.execute(text("""
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
        VALUES (
            :stock_code,
            :trade_date,
            :news_count,
            :sentiment,
            :sentiment_score,
            :news_titles,
            :analysis_reason,
            :macro_sentiment_cn,
            :macro_sentiment_global,
            :industry_sentiment,
            :final_sentiment_score,
            :score_breakdown,
            :created_at
        )
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
        """), {
            "stock_code": str(item["stock_code"]).zfill(6),
            "trade_date": item["trade_date"],
            "news_count": int(item.get("news_count", 0)),
            "sentiment": item.get("sentiment", "无数据"),
            "sentiment_score": float(item.get("sentiment_score", 0)),
            "news_titles": json.dumps(item.get("news_titles", []), ensure_ascii=False),
            "analysis_reason": item.get("analysis_reason", ""),
            "macro_sentiment_cn": float(item.get("macro_sentiment_cn", 0)),
            "macro_sentiment_global": float(item.get("macro_sentiment_global", 0)),
            "industry_sentiment": float(item.get("industry_sentiment", 0)),
            "final_sentiment_score": float(item.get("final_sentiment_score", item.get("sentiment_score", 0))),
            "score_breakdown": json.dumps(item.get("score_breakdown", {}), ensure_ascii=False),
            "created_at": item.get("created_at") or now_text(),
        })
    return True


def get_news_sentiment(stock_code, trade_date):
    init_stock_news_sentiment_table()
    with engine.connect() as db:
        row = db.execute(text("""
        SELECT *
        FROM stock_news_sentiment
        WHERE stock_code = :stock_code AND trade_date = :trade_date
        """), {
            "stock_code": str(stock_code).zfill(6),
            "trade_date": trade_date,
        }).fetchone()
    return _decode_row(row)


def list_latest_news_sentiments(stock_code=None, limit=50):
    init_stock_news_sentiment_table()
    sql = """
    SELECT *
    FROM stock_news_sentiment
    """
    params = {"limit": int(limit)}
    if stock_code:
        sql += " WHERE stock_code = :stock_code"
        params["stock_code"] = str(stock_code).zfill(6)
    sql += " ORDER BY trade_date DESC, created_at DESC LIMIT :limit"
    with engine.connect() as db:
        rows = db.execute(text(sql), params).fetchall()
    return [_decode_row(row) for row in rows]


def list_news_sentiments_by_date(trade_date, stock_code=None, user_id=None):
    init_stock_news_sentiment_table()
    sql = """
    SELECT
        s.*,
        COALESCE(w.stock_name, '') AS stock_name
    FROM stock_news_sentiment s
    LEFT JOIN watchlist w
        ON w.stock_code = s.stock_code
    WHERE s.trade_date = :trade_date
    """
    params = {"trade_date": trade_date}
    if user_id is not None:
        sql = sql.replace(
            "ON w.stock_code = s.stock_code",
            "ON w.stock_code = s.stock_code AND w.user_id = :user_id",
        )
        params["user_id"] = int(user_id)
    if stock_code:
        sql += " AND s.stock_code = :stock_code"
        params["stock_code"] = str(stock_code).zfill(6)
    sql += " ORDER BY s.sentiment_score DESC, s.stock_code"
    with engine.connect() as db:
        rows = db.execute(text(sql), params).fetchall()
    return [_decode_row(row) for row in rows]


def list_news_sentiments(stock_code, start_date=None, end_date=None, limit=30):
    init_stock_news_sentiment_table()
    sql = """
    SELECT *
    FROM stock_news_sentiment
    WHERE stock_code = :stock_code
    """
    params = {
        "stock_code": str(stock_code).zfill(6),
        "limit": int(limit),
    }
    if start_date:
        sql += " AND trade_date >= :start_date"
        params["start_date"] = start_date
    if end_date:
        sql += " AND trade_date <= :end_date"
        params["end_date"] = end_date
    sql += " ORDER BY trade_date DESC, created_at DESC LIMIT :limit"
    with engine.connect() as db:
        rows = db.execute(text(sql), params).fetchall()
    return list(reversed([_decode_row(row) for row in rows]))
