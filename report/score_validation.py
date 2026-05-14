import os
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategy.score import _resolve_stock_csv


DB_NAME = os.path.join(PROJECT_ROOT, "quant.db")
HORIZONS = [1, 3, 5, 10]
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def current_local_time():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def init_validation_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS score_validation (
        score_id INTEGER PRIMARY KEY,
        stock_code TEXT NOT NULL,
        score REAL NOT NULL,
        level TEXT NOT NULL,
        score_date TEXT NOT NULL,
        base_close REAL,
        return_1d REAL,
        return_3d REAL,
        return_5d REAL,
        return_10d REAL,
        evaluated_at TEXT
    )
    """)
    conn.commit()


def _pick_column(df, candidates, fallback_index):
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return df.columns[fallback_index]


def load_price_data(stock_code):
    csv_path = _resolve_stock_csv(stock_code)
    df = pd.read_csv(csv_path)

    date_col = _pick_column(df, ["日期"], 0)
    close_col = _pick_column(df, ["收盘"], 6)

    price_df = df[[date_col, close_col]].copy()
    price_df.columns = ["date", "close"]
    price_df["date"] = pd.to_datetime(price_df["date"]).dt.normalize()
    price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce")
    price_df = price_df.dropna(subset=["date", "close"])
    price_df = price_df.sort_values("date").reset_index(drop=True)
    return price_df


def evaluate_score(score_row):
    prices = load_price_data(score_row["stock_code"])
    score_date = pd.to_datetime(score_row["created_at"]).normalize()

    future_prices = prices[prices["date"] >= score_date].reset_index(drop=True)
    if future_prices.empty:
        return None

    base_close = float(future_prices.loc[0, "close"])
    result = {
        "score_id": int(score_row["id"]),
        "stock_code": str(score_row["stock_code"]).zfill(6),
        "score": float(score_row["score"]),
        "level": str(score_row["level"]),
        "score_date": score_date.strftime("%Y-%m-%d"),
        "base_close": base_close,
    }

    for horizon in HORIZONS:
        key = f"return_{horizon}d"
        if len(future_prices) <= horizon:
            result[key] = None
            continue
        future_close = float(future_prices.loc[horizon, "close"])
        result[key] = (future_close - base_close) / base_close

    return result


def save_result(conn, result):
    conn.execute("""
    INSERT OR REPLACE INTO score_validation (
        score_id,
        stock_code,
        score,
        level,
        score_date,
        base_close,
        return_1d,
        return_3d,
        return_5d,
        return_10d,
        evaluated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["score_id"],
        result["stock_code"],
        result["score"],
        result["level"],
        result["score_date"],
        result["base_close"],
        result["return_1d"],
        result["return_3d"],
        result["return_5d"],
        result["return_10d"],
        current_local_time(),
    ))


def generate_validation_report():
    conn = sqlite3.connect(DB_NAME)
    init_validation_table(conn)

    scores = pd.read_sql("""
    SELECT *
    FROM stock_scores
    ORDER BY created_at, id
    """, conn)

    saved = 0
    skipped = 0
    for _, row in scores.iterrows():
        try:
            result = evaluate_score(row)
        except Exception as exc:
            print(f"跳过 {row['stock_code']} score_id={row['id']}: {exc}")
            skipped += 1
            continue

        if result is None:
            skipped += 1
            continue

        save_result(conn, result)
        saved += 1

    conn.commit()

    validation_df = pd.read_sql("""
    SELECT *
    FROM score_validation
    ORDER BY score_date DESC, score DESC
    """, conn)
    conn.close()

    print(f"评分验证完成: saved={saved}, skipped={skipped}")
    if validation_df.empty:
        print("暂无可验证记录")
        return validation_df

    summary_cols = ["return_1d", "return_3d", "return_5d", "return_10d"]
    print("\n=== 按评分等级统计平均收益 ===")
    print(validation_df.groupby("level")[summary_cols].mean().round(4))

    print("\n=== 最近验证记录 ===")
    print(validation_df[[
        "stock_code",
        "score",
        "level",
        "score_date",
        "return_1d",
        "return_3d",
        "return_5d",
        "return_10d",
    ]].head(20).to_string(index=False))

    return validation_df


if __name__ == "__main__":
    generate_validation_report()
