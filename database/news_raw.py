from datetime import datetime, timedelta

from sqlalchemy import text

from database.connection import engine, is_postgres


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_news_raw_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS news_raw (
        id {id_type},
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
    """
    idx_type = "CREATE INDEX IF NOT EXISTS idx_news_raw_type_time ON news_raw(source_type, published_time)"
    idx_stock = "CREATE INDEX IF NOT EXISTS idx_news_raw_stock_time ON news_raw(stock_code, published_time)"
    if conn is not None:
        conn.execute(ddl)
        conn.execute(idx_type)
        conn.execute(idx_stock)
        return
    with engine.begin() as db:
        db.execute(text(ddl))
        db.execute(text(idx_type))
        db.execute(text(idx_stock))


def init_news_fetch_state_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS news_fetch_state (
        id {id_type},
        source_type TEXT NOT NULL,
        cache_key TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT,
        UNIQUE(source_type, cache_key)
    )
    """
    if conn is not None:
        conn.execute(ddl)
        return
    with engine.begin() as db:
        db.execute(text(ddl))


def ensure_tables():
    init_news_raw_table()
    init_news_fetch_state_table()


def cleanup_old_news(days=7):
    ensure_tables()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as db:
        db.execute(text(
            "DELETE FROM news_raw WHERE published_time < :cutoff"
        ), {"cutoff": cutoff})


def upsert_news_raw(items):
    if not items:
        return 0
    ensure_tables()
    rows = [
        {
            "source_type": item.get("source_type", ""),
            "source_name": item.get("source_name", ""),
            "stock_code": item.get("stock_code"),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "published_time": item.get("published_time", ""),
            "url": item.get("url", ""),
            "created_at": item.get("created_at") or now_text(),
        }
        for item in items
        if item.get("title") and item.get("published_time")
    ]
    if not rows:
        return 0

    with engine.begin() as db:
        result = db.execute(text("""
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
        VALUES (
            :source_type,
            :source_name,
            :stock_code,
            :title,
            :summary,
            :published_time,
            :url,
            :created_at
        )
        ON CONFLICT(title, published_time) DO NOTHING
        """), rows)
    cleanup_old_news(days=7)
    return max(result.rowcount or 0, 0)


def list_news_raw(source_type=None, stock_code=None, start_time=None, end_time=None, limit=200):
    ensure_tables()
    sql = """
    SELECT *
    FROM news_raw
    WHERE 1 = 1
    """
    params = {}
    if source_type:
        sql += " AND source_type = :source_type"
        params["source_type"] = source_type
    if stock_code is not None:
        sql += " AND stock_code = :stock_code"
        params["stock_code"] = str(stock_code).zfill(6)
    if start_time:
        sql += " AND published_time >= :start_time"
        params["start_time"] = start_time
    if end_time:
        sql += " AND published_time <= :end_time"
        params["end_time"] = end_time
    sql += " ORDER BY published_time DESC LIMIT :limit"
    params["limit"] = int(limit)
    with engine.connect() as db:
        rows = db.execute(text(sql), params)
        return [dict(row._mapping) for row in rows]


def has_news_since(source_type, since_time, stock_code=None):
    ensure_tables()
    sql = """
    SELECT 1
    FROM news_raw
    WHERE source_type = :source_type AND created_at >= :since_time
    """
    params = {"source_type": source_type, "since_time": since_time}
    if stock_code is not None:
        sql += " AND stock_code = :stock_code"
        params["stock_code"] = str(stock_code).zfill(6)
    sql += " LIMIT 1"
    with engine.connect() as db:
        return db.execute(text(sql), params).fetchone() is not None


def get_fetch_state(source_type, cache_key):
    init_news_fetch_state_table()
    with engine.connect() as db:
        row = db.execute(text("""
        SELECT source_type, cache_key, fetched_at, status, message
        FROM news_fetch_state
        WHERE source_type = :source_type AND cache_key = :cache_key
        """), {"source_type": source_type, "cache_key": cache_key}).fetchone()
    return dict(row._mapping) if row else None


def set_fetch_state(source_type, cache_key, status="ok", message=""):
    init_news_fetch_state_table()
    with engine.begin() as db:
        db.execute(text("""
        INSERT INTO news_fetch_state (
            source_type,
            cache_key,
            fetched_at,
            status,
            message
        )
        VALUES (
            :source_type,
            :cache_key,
            :fetched_at,
            :status,
            :message
        )
        ON CONFLICT(source_type, cache_key) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            status = excluded.status,
            message = excluded.message
        """), {
            "source_type": source_type,
            "cache_key": cache_key,
            "fetched_at": now_text(),
            "status": status,
            "message": message,
        })


def has_recent_fetch(source_type, cache_key, since_time):
    state = get_fetch_state(source_type, cache_key)
    if not state:
        return False
    if state.get("status") != "ok":
        return False
    return str(state.get("fetched_at") or "") >= since_time
