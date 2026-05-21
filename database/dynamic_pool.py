from datetime import datetime

from sqlalchemy import text

from database.connection import engine, is_postgres


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_dynamic_pool_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    pool_ddl = f"""
    CREATE TABLE IF NOT EXISTS dynamic_pool (
        id {id_type},
        ts_code TEXT NOT NULL,
        stock_name TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        close REAL,
        pct_chg REAL,
        amount REAL,
        volume_ratio REAL,
        ma20 REAL,
        ai_score REAL,
        ai_grade TEXT,
        filter_pass_reason TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(trade_date, ts_code)
    )
    """
    run_ddl = f"""
    CREATE TABLE IF NOT EXISTS dynamic_pool_scan_runs (
        id {id_type},
        trade_date TEXT NOT NULL UNIQUE,
        scanned_count INTEGER NOT NULL DEFAULT 0,
        static_pass_count INTEGER NOT NULL DEFAULT 0,
        daily_pass_count INTEGER NOT NULL DEFAULT 0,
        scored_count INTEGER NOT NULL DEFAULT 0,
        selected_count INTEGER NOT NULL DEFAULT 0,
        max_ai_score REAL,
        status TEXT NOT NULL,
        message TEXT,
        created_at TEXT NOT NULL
    )
    """
    index_sql = """
    CREATE INDEX IF NOT EXISTS idx_dynamic_pool_trade_date_score
    ON dynamic_pool(trade_date, ai_score DESC)
    """
    if conn is not None:
        conn.execute(pool_ddl)
        conn.execute(index_sql)
        conn.execute(run_ddl)
        return
    with engine.begin() as db:
        db.execute(text(pool_ddl))
        db.execute(text(index_sql))
        db.execute(text(run_ddl))


def save_dynamic_pool(rows, trade_date):
    init_dynamic_pool_table()
    timestamp = now_text()
    with engine.begin() as db:
        db.execute(text(
            "DELETE FROM dynamic_pool WHERE trade_date = :trade_date"
        ), {"trade_date": trade_date})
        for item in rows:
            db.execute(text("""
            INSERT INTO dynamic_pool (
                ts_code,
                stock_name,
                trade_date,
                close,
                pct_chg,
                amount,
                volume_ratio,
                ma20,
                ai_score,
                ai_grade,
                filter_pass_reason,
                created_at
            )
            VALUES (
                :ts_code,
                :stock_name,
                :trade_date,
                :close,
                :pct_chg,
                :amount,
                :volume_ratio,
                :ma20,
                :ai_score,
                :ai_grade,
                :filter_pass_reason,
                :created_at
            )
            """), {
                "ts_code": item["ts_code"],
                "stock_name": item["stock_name"],
                "trade_date": trade_date,
                "close": item.get("close"),
                "pct_chg": item.get("pct_chg"),
                "amount": item.get("amount"),
                "volume_ratio": item.get("volume_ratio"),
                "ma20": item.get("ma20"),
                "ai_score": item.get("ai_score"),
                "ai_grade": item.get("ai_grade"),
                "filter_pass_reason": item.get("filter_pass_reason"),
                "created_at": timestamp,
            })
    return len(rows)


def list_dynamic_pool(trade_date=None, limit=None):
    init_dynamic_pool_table()
    with engine.connect() as db:
        if trade_date is None:
            row = db.execute(text("SELECT MAX(trade_date) AS trade_date FROM dynamic_pool")).fetchone()
            trade_date = row[0] if row else None
        if trade_date is None:
            return []

        sql = """
        SELECT *
        FROM dynamic_pool
        WHERE trade_date = :trade_date
        ORDER BY ai_score DESC, ts_code
        """
        params = {"trade_date": trade_date}
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)
        rows = db.execute(text(sql), params)
        return [dict(row._mapping) for row in rows]


def get_latest_dynamic_pool_summary():
    init_dynamic_pool_table()
    with engine.connect() as db:
        row = db.execute(text("""
        SELECT *
        FROM dynamic_pool_scan_runs
        ORDER BY trade_date DESC, id DESC
        LIMIT 1
        """)).fetchone()
    if row is None:
        return None
    result = dict(row._mapping)
    result["completed_at"] = result.get("created_at")
    return result


def save_dynamic_pool_scan_run(result):
    init_dynamic_pool_table()
    timestamp = now_text()
    with engine.begin() as db:
        db.execute(text("""
        INSERT INTO dynamic_pool_scan_runs (
            trade_date,
            scanned_count,
            static_pass_count,
            daily_pass_count,
            scored_count,
            selected_count,
            max_ai_score,
            status,
            message,
            created_at
        )
        VALUES (
            :trade_date,
            :scanned_count,
            :static_pass_count,
            :daily_pass_count,
            :scored_count,
            :selected_count,
            :max_ai_score,
            :status,
            :message,
            :created_at
        )
        ON CONFLICT(trade_date) DO UPDATE SET
            scanned_count = excluded.scanned_count,
            static_pass_count = excluded.static_pass_count,
            daily_pass_count = excluded.daily_pass_count,
            scored_count = excluded.scored_count,
            selected_count = excluded.selected_count,
            max_ai_score = excluded.max_ai_score,
            status = excluded.status,
            message = excluded.message,
            created_at = excluded.created_at
        """), {
            "trade_date": result.get("trade_date"),
            "scanned_count": int(result.get("scanned_count", 0) or 0),
            "static_pass_count": int(result.get("static_pass_count", 0) or 0),
            "daily_pass_count": int(result.get("daily_pass_count", 0) or 0),
            "scored_count": int(result.get("scored_count", 0) or 0),
            "selected_count": int(result.get("selected_count", 0) or 0),
            "max_ai_score": result.get("max_ai_score"),
            "status": "skipped" if result.get("skipped") else "ok" if result.get("ok") else "failed",
            "message": result.get("message"),
            "created_at": timestamp,
        })
