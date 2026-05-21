from datetime import datetime

from sqlalchemy import text

from database.connection import engine, is_postgres


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_daily_candidates_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
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
    """
    if conn is not None:
        conn.execute(ddl)
        return
    with engine.begin() as db:
        db.execute(text(ddl))


def save_daily_candidates(candidates):
    init_daily_candidates_table()
    timestamp = now_text()
    rows = [
        {
            "trade_date": item["trade_date"],
            "stock_code": item["stock_code"],
            "stock_name": item["stock_name"],
            "score": item["score"],
            "reason": item.get("reason"),
            "source": item.get("source", "rule"),
            "enabled": int(item.get("enabled", 1)),
            "created_at": timestamp,
        }
        for item in candidates
    ]
    if not rows:
        return 0

    with engine.begin() as db:
        db.execute(text("""
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
        VALUES (
            :trade_date,
            :stock_code,
            :stock_name,
            :score,
            :reason,
            :source,
            :enabled,
            :created_at
        )
        ON CONFLICT(trade_date, stock_code) DO UPDATE SET
            stock_name = excluded.stock_name,
            score = excluded.score,
            reason = excluded.reason,
            source = excluded.source,
            enabled = excluded.enabled,
            created_at = excluded.created_at
        """), rows)
    return len(rows)


def list_latest_candidates(enabled_only=True):
    init_daily_candidates_table()
    with engine.connect() as db:
        latest_date = db.execute(text(
            "SELECT MAX(trade_date) AS latest_date FROM daily_candidates"
        )).scalar()
        if latest_date is None:
            return []

        sql = """
        SELECT *
        FROM daily_candidates
        WHERE trade_date = :trade_date
        """
        params = {"trade_date": latest_date}
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY score DESC, stock_code"

        rows = db.execute(text(sql), params)
        return [dict(row._mapping) for row in rows]
