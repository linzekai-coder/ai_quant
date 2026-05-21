from datetime import datetime

from sqlalchemy import text

from database.connection import engine, is_postgres


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_watchlist_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS watchlist (
        id {id_type},
        user_id INTEGER,
        stock_code TEXT NOT NULL,
        stock_name TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual',
        enabled INTEGER NOT NULL DEFAULT 1,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        group_name TEXT NOT NULL DEFAULT '默认',
        UNIQUE(user_id, stock_code)
    )
    """
    if conn is not None:
        try:
            conn.execute(ddl)
        except Exception:
            pass
        return
    with engine.begin() as db:
        db.execute(text(ddl))


def list_watchlist(enabled_only=False, user_id=None):
    clauses = []
    params = {}
    if user_id is not None:
        clauses.append("user_id = :user_id")
        params["user_id"] = int(user_id)
    if enabled_only:
        clauses.append("enabled = 1")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with engine.connect() as db:
        rows = db.execute(text(f"""
        SELECT *
        FROM watchlist
        {where_sql}
        ORDER BY pinned DESC, enabled DESC, group_name, stock_code
        """), params)
        return [dict(row._mapping) for row in rows]


def list_all_enabled_watchlist():
    return list_watchlist(enabled_only=True, user_id=None)


def upsert_watchlist_stock(stock_code, stock_name, source="manual", enabled=1, note=None, group_name="默认", pinned=0, user_id=None):
    if user_id is None:
        user_id = 1
    timestamp = now_text()
    with engine.begin() as db:
        db.execute(text("""
        INSERT INTO watchlist (
            user_id,
            stock_code,
            stock_name,
            source,
            enabled,
            note,
            group_name,
            pinned,
            created_at,
            updated_at
        )
        VALUES (
            :user_id,
            :stock_code,
            :stock_name,
            :source,
            :enabled,
            :note,
            :group_name,
            :pinned,
            :created_at,
            :updated_at
        )
        ON CONFLICT(user_id, stock_code) DO UPDATE SET
            stock_name = excluded.stock_name,
            source = excluded.source,
            enabled = excluded.enabled,
            note = excluded.note,
            group_name = excluded.group_name,
            updated_at = excluded.updated_at
        """), {
            "user_id": int(user_id),
            "stock_code": str(stock_code).zfill(6),
            "stock_name": stock_name,
            "source": source,
            "enabled": int(enabled),
            "note": note,
            "group_name": group_name or "默认",
            "pinned": int(pinned),
            "created_at": timestamp,
            "updated_at": timestamp,
        })


def set_watchlist_enabled(stock_code, enabled, user_id=None):
    if user_id is None:
        user_id = 1
    with engine.begin() as db:
        db.execute(text("""
        UPDATE watchlist
        SET enabled = :enabled, updated_at = :updated_at
        WHERE stock_code = :stock_code AND user_id = :user_id
        """), {
            "enabled": int(enabled),
            "updated_at": now_text(),
            "stock_code": str(stock_code).zfill(6),
            "user_id": int(user_id),
        })


def set_watchlist_pinned(stock_code, pinned, user_id=None):
    if user_id is None:
        user_id = 1
    with engine.begin() as db:
        db.execute(text("""
        UPDATE watchlist
        SET pinned = :pinned, updated_at = :updated_at
        WHERE stock_code = :stock_code AND user_id = :user_id
        """), {
            "pinned": int(pinned),
            "updated_at": now_text(),
            "stock_code": str(stock_code).zfill(6),
            "user_id": int(user_id),
        })


def set_watchlist_group(stock_code, group_name, user_id=None):
    if user_id is None:
        user_id = 1
    with engine.begin() as db:
        db.execute(text("""
        UPDATE watchlist
        SET group_name = :group_name, updated_at = :updated_at
        WHERE stock_code = :stock_code AND user_id = :user_id
        """), {
            "group_name": group_name or "默认",
            "updated_at": now_text(),
            "stock_code": str(stock_code).zfill(6),
            "user_id": int(user_id),
        })


def delete_watchlist_stock(stock_code, user_id=None):
    if user_id is None:
        user_id = 1
    with engine.begin() as db:
        db.execute(text("""
        DELETE FROM watchlist
        WHERE stock_code = :stock_code AND user_id = :user_id
        """), {
            "stock_code": str(stock_code).zfill(6),
            "user_id": int(user_id),
        })


def seed_default_watchlist(default_stocks, user_id=1):
    count = len(list_watchlist(user_id=user_id))
    if count > 0:
        return 0
    for item in default_stocks:
        upsert_watchlist_stock(
            item["code"],
            item["name"],
            source="default",
            enabled=1,
            note="默认股票池",
            user_id=user_id,
        )
    return len(default_stocks)
