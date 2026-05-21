from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from data.tushare_provider import (
    fetch_all_daily,
    fetch_daily_by_ts_code,
    get_stock_basic_list,
    is_trade_date,
    latest_trade_date,
    ts_code_to_symbol,
)
from database.dynamic_pool import save_dynamic_pool, save_dynamic_pool_scan_run
from database.db import save_score
from database.watchlist import list_watchlist
from strategy.score import calculate_score, calculate_score_from_frame, score_level
from utils.logger import logger


@dataclass
class DynamicPoolConfig:
    min_list_days: int = 180
    min_amount_yuan: float = 300_000_000
    min_close: float = 3
    min_pct_chg: float = -9.5
    max_pct_chg: float = 9.5
    volume_ratio_threshold: float = 1.2
    top_n: int = 20
    max_workers: int = 10
    score_weights: dict | None = None


def normalize_trade_date(trade_date=None):
    if trade_date:
        return pd.to_datetime(str(trade_date)).strftime("%Y%m%d")
    return datetime.now().strftime("%Y%m%d")


def stock_name_allowed(name):
    text = str(name or "").upper()
    return "ST" not in text and "退" not in text


def static_filter_stock_basic(stock_df, trade_date, config):
    if stock_df.empty:
        return pd.DataFrame()

    df = stock_df.copy()
    required_cols = {"ts_code", "symbol", "name", "list_date"}
    if not required_cols.issubset(df.columns):
        return pd.DataFrame()

    today = pd.to_datetime(trade_date, format="%Y%m%d", errors="coerce")
    df["list_date_dt"] = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce")
    df["list_days"] = (today - df["list_date_dt"]).dt.days
    symbols = df["symbol"].astype(str).str.zfill(6)
    mask = (
        df["name"].apply(stock_name_allowed)
        & df["list_days"].ge(config.min_list_days)
        & df["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
        & ~symbols.str.startswith(("200", "900"))
    )
    return df[mask].copy().reset_index(drop=True)


def merge_static_and_daily(static_df, daily_df):
    if static_df.empty or daily_df.empty:
        return pd.DataFrame()
    merged = daily_df.merge(
        static_df[["ts_code", "symbol", "name", "industry", "market"]],
        on="ts_code",
        how="inner",
    )
    return merged.reset_index(drop=True)


def first_market_filter(market_df, config):
    if market_df.empty:
        return pd.DataFrame()

    # Tushare daily amount unit is thousand yuan.
    amount_threshold = config.min_amount_yuan / 1000
    mask = (
        market_df["amount"].ge(amount_threshold)
        & market_df["pct_chg"].between(config.min_pct_chg, config.max_pct_chg, inclusive="both")
        & market_df["close"].gt(config.min_close)
    )
    return market_df[mask].copy().reset_index(drop=True)


def normalize_history_frame(history_df, ts_code, stock_name):
    if history_df.empty:
        return pd.DataFrame()
    df = history_df.copy()
    df["股票名称"] = stock_name
    df["代码"] = ts_code_to_symbol(ts_code)
    return df


def enrich_candidate_with_history(row, trade_date, config):
    ts_code = row["ts_code"]
    end_date = trade_date
    start_date = (
        pd.to_datetime(trade_date, format="%Y%m%d") - pd.Timedelta(days=90)
    ).strftime("%Y%m%d")

    history_df = fetch_daily_by_ts_code(ts_code, start_date, end_date)
    history_df = normalize_history_frame(history_df, ts_code, row["name"])
    if history_df.empty or len(history_df) < 20:
        return None

    history_df = history_df.sort_values("日期").reset_index(drop=True)
    history_df["收盘"] = pd.to_numeric(history_df["收盘"], errors="coerce")
    history_df["成交量"] = pd.to_numeric(history_df["成交量"], errors="coerce")
    latest = history_df.iloc[-1]
    ma20 = history_df["收盘"].rolling(20).mean().iloc[-1]
    avg_vol20 = history_df["成交量"].rolling(20).mean().iloc[-1]
    avg_vol5 = history_df["成交量"].tail(5).mean()
    if pd.isna(ma20) or pd.isna(avg_vol20) or avg_vol20 <= 0:
        return None

    volume_ratio = avg_vol5 / avg_vol20
    if volume_ratio <= config.volume_ratio_threshold:
        return None
    if float(latest["收盘"]) <= float(ma20):
        return None

    try:
        ai_score = calculate_score_from_frame(
            history_df,
            ts_code_to_symbol(ts_code),
            weights=config.score_weights,
        )
        ai_grade = score_level(ai_score)
    except Exception as exc:
        logger.warning("%s %s AI评分失败: %s", ts_code, row["name"], exc)
        return None

    return {
        "ts_code": ts_code,
        "stock_name": row["name"],
        "close": float(row["close"]),
        "pct_chg": float(row["pct_chg"]),
        "amount": float(row["amount"]),
        "volume_ratio": round(float(volume_ratio), 4),
        "ma20": round(float(ma20), 4),
        "ai_score": float(ai_score),
        "ai_grade": ai_grade,
        "industry": row.get("industry"),
        "market": row.get("market"),
        "filter_pass_reason": (
            f"成交额>{config.min_amount_yuan / 100000000:.1f}亿;"
            f"涨跌幅{config.min_pct_chg}%~{config.max_pct_chg}%;"
            f"收盘>{config.min_close}元;"
            f"近5日成交量/20日均量>{config.volume_ratio_threshold};"
            "收盘价站上MA20"
        ),
    }


def score_candidates(candidates_df, trade_date, config):
    rows = candidates_df.to_dict("records")
    results = []
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = [
            executor.submit(enrich_candidate_with_history, row, trade_date, config)
            for row in rows
        ]
        for future in as_completed(futures):
            item = future.result()
            if item is not None:
                results.append(item)
    return sorted(results, key=lambda item: item["ai_score"], reverse=True)


def build_final_pool_codes(dynamic_rows, user_id=1):
    watchlist_rows = list_watchlist(enabled_only=True, user_id=user_id)
    codes = []
    seen = set()
    for row in watchlist_rows:
        code = str(row["stock_code"]).zfill(6)
        if code not in seen:
            codes.append(code)
            seen.add(code)
    for row in dynamic_rows:
        code = ts_code_to_symbol(row["ts_code"])
        if code and code not in seen:
            codes.append(code)
            seen.add(code)
    return codes


def score_watchlist_stocks(user_id=1, score_weights=None):
    rows = list_watchlist(enabled_only=True, user_id=user_id)
    saved = 0
    failed = 0
    for row in rows:
        stock_code = str(row["stock_code"]).zfill(6)
        try:
            score = calculate_score(stock_code, weights=score_weights)
            save_score(stock_code, score, score_level(score))
            saved += 1
        except Exception as exc:
            logger.warning("%s %s 自选股每日评分失败: %s", stock_code, row["stock_name"], exc)
            failed += 1
    return {"saved": saved, "failed": failed}


def run_dynamic_pool_scan(trade_date=None, config=None, save=True):
    config = config or DynamicPoolConfig()
    requested_date = normalize_trade_date(trade_date)
    scan_date = requested_date
    if not is_trade_date(scan_date):
        fallback = latest_trade_date(scan_date)
        if not fallback or fallback != scan_date:
            result = {
                "ok": False,
                "skipped": True,
                "message": f"{requested_date} 非交易日，已跳过全市场扫描",
                "trade_date": requested_date,
            }
            if save:
                save_dynamic_pool_scan_run(result)
            return result

    stock_df = get_stock_basic_list()
    total_count = len(stock_df)
    static_df = static_filter_stock_basic(stock_df, scan_date, config)
    daily_df = fetch_all_daily(scan_date)
    market_df = merge_static_and_daily(static_df, daily_df)
    market_filtered = first_market_filter(market_df, config)
    scored_rows = score_candidates(market_filtered, scan_date, config)
    top_rows = scored_rows[:config.top_n]
    watchlist_score_result = score_watchlist_stocks(
        user_id=1,
        score_weights=config.score_weights,
    ) if save else {"saved": 0, "failed": 0}
    if save:
        for item in top_rows:
            save_score(ts_code_to_symbol(item["ts_code"]), item["ai_score"], item["ai_grade"])
    saved = save_dynamic_pool(top_rows, scan_date) if save else 0
    final_codes = build_final_pool_codes(top_rows, user_id=1)

    result = {
        "ok": True,
        "skipped": False,
        "trade_date": scan_date,
        "scanned_count": int(total_count),
        "static_pass_count": int(len(static_df)),
        "daily_pass_count": int(len(market_filtered)),
        "scored_count": int(len(scored_rows)),
        "selected_count": int(len(top_rows)),
        "saved": int(saved),
        "max_ai_score": max([item["ai_score"] for item in top_rows], default=None),
        "final_pool_count": len(final_codes),
        "watchlist_scored": watchlist_score_result["saved"],
        "watchlist_score_failed": watchlist_score_result["failed"],
        "message": f"全市场扫描完成，入选动态池 {len(top_rows)} 只",
    }
    logger.info("全市场动态股票池扫描结果: %s", result)
    if save:
        save_dynamic_pool_scan_run(result)
    return result


if __name__ == "__main__":
    print(run_dynamic_pool_scan())
