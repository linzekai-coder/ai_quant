import os
import json
from datetime import datetime

from sqlalchemy import text

from database.connection import engine, is_postgres


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_backtest_results_table(conn=None):
    id_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS backtest_results (
        id {id_type},
        user_id INTEGER NOT NULL,
        stock_code TEXT NOT NULL,
        params TEXT,
        metrics TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """
    index_sql = """
    CREATE INDEX IF NOT EXISTS idx_backtest_results_user_created
    ON backtest_results(user_id, created_at DESC)
    """
    if conn is not None:
        conn.execute(ddl)
        conn.execute(index_sql)
        conn.commit()
        return
    with engine.begin() as db:
        db.execute(text(ddl))
        db.execute(text(index_sql))


def save_backtest_result(user_id, stock_code, params, metrics):
    init_backtest_results_table()
    with engine.begin() as db:
        db.execute(text("""
        INSERT INTO backtest_results (
            user_id,
            stock_code,
            params,
            metrics,
            created_at
        )
        VALUES (
            :user_id,
            :stock_code,
            :params,
            :metrics,
            :created_at
        )
        """), {
            "user_id": int(user_id),
            "stock_code": str(stock_code).zfill(6),
            "params": json.dumps(params or {}, ensure_ascii=False, default=str),
            "metrics": json.dumps(metrics or {}, ensure_ascii=False, default=str),
            "created_at": now_text(),
        })


def list_backtest_results(user_id, stock_code=None, limit=20):
    init_backtest_results_table()
    sql = """
    SELECT *
    FROM backtest_results
    WHERE user_id = :user_id
    """
    params = {"user_id": int(user_id), "limit": int(limit)}
    if stock_code:
        sql += " AND stock_code = :stock_code"
        params["stock_code"] = str(stock_code).zfill(6)
    sql += " ORDER BY created_at DESC LIMIT :limit"
    with engine.connect() as db:
        rows = db.execute(text(sql), params).fetchall()
    results = []
    for row in rows:
        item = dict(row._mapping)
        try:
            item["params"] = json.loads(item.get("params") or "{}")
        except json.JSONDecodeError:
            item["params"] = {}
        try:
            item["metrics"] = json.loads(item.get("metrics") or "{}")
        except json.JSONDecodeError:
            item["metrics"] = {}
        results.append(item)
    return results
