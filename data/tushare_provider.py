import os
import sys
from functools import lru_cache

import pandas as pd
import tushare as ts


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STOCK_BASIC_CACHE = None


def get_tushare_token():
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if token:
        return token

    if sys.platform.startswith("win"):
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                token, _ = winreg.QueryValueEx(key, "TUSHARE_TOKEN")
                token = str(token).strip()
                if token:
                    return token
        except OSError:
            pass

    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return ""

    try:
        with open(env_path, "r", encoding="utf-8-sig") as file_obj:
            for line in file_obj:
                key, _, value = line.strip().partition("=")
                if key == "TUSHARE_TOKEN" and value:
                    return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


@lru_cache(maxsize=1)
def get_tushare_client():
    token = get_tushare_token()
    if not token:
        return None
    ts.set_token(token)
    return ts.pro_api(token)


def normalize_stock_code(stock_code):
    code = str(stock_code).strip().upper()
    if "." in code:
        parts = code.split(".")
        code = parts[0] if parts[0].isdigit() else parts[-1]
    code = code.replace("SH", "").replace("SZ", "")
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits.zfill(6) if digits else ""


def to_ts_code(stock_code):
    code = normalize_stock_code(stock_code)
    if not code:
        return ""
    suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


def ts_code_to_symbol(ts_code):
    code = str(ts_code).strip().upper().split(".", 1)[0]
    return normalize_stock_code(code)


def normalize_tushare_daily(price_df):
    if price_df is None or price_df.empty:
        return pd.DataFrame()

    column_map = {
        "trade_date": "日期",
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "pre_close": "前收盘",
        "vol": "成交量",
        "amount": "成交额",
        "pct_chg": "涨跌幅",
    }
    normalized = price_df.rename(columns=column_map).copy()
    keep_cols = [value for value in column_map.values() if value in normalized.columns]
    normalized = normalized[keep_cols]

    if "日期" in normalized.columns:
        normalized["日期"] = pd.to_datetime(normalized["日期"], format="%Y%m%d", errors="coerce")
    for col in ["开盘", "最高", "最低", "收盘", "前收盘", "成交量", "成交额", "涨跌幅"]:
        if col in normalized.columns:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    if "涨跌幅" not in normalized.columns and "收盘" in normalized.columns:
        normalized["涨跌幅"] = normalized["收盘"].pct_change() * 100

    normalized = normalized.dropna(subset=["日期", "收盘"]).sort_values("日期")
    return normalized.reset_index(drop=True)


def fetch_daily_price(stock_code, start_date, end_date, adjust="qfq"):
    ts_code = to_ts_code(stock_code)
    if not ts_code:
        return pd.DataFrame()

    token = get_tushare_token()
    if token:
        ts.set_token(token)

    try:
        price_df = ts.pro_bar(
            ts_code=ts_code,
            adj=adjust,
            start_date=str(start_date),
            end_date=str(end_date),
            asset="E",
            freq="D",
        )
    except Exception:
        price_df = None

    if price_df is None or price_df.empty:
        pro = get_tushare_client()
        if pro is None:
            return pd.DataFrame()
        try:
            price_df = pro.daily(
                ts_code=ts_code,
                start_date=str(start_date),
                end_date=str(end_date),
            )
        except Exception:
            return pd.DataFrame()

    return normalize_tushare_daily(price_df)


def get_stock_basic_list():
    global _STOCK_BASIC_CACHE
    if _STOCK_BASIC_CACHE is not None:
        return _STOCK_BASIC_CACHE.copy()

    pro = get_tushare_client()
    if pro is None:
        return pd.DataFrame()
    try:
        stock_df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )
    except Exception:
        return pd.DataFrame()
    if stock_df is None or stock_df.empty:
        return pd.DataFrame()
    _STOCK_BASIC_CACHE = stock_df.copy()
    return stock_df


def fetch_trade_calendar(start_date, end_date, exchange="SSE"):
    pro = get_tushare_client()
    if pro is None:
        return pd.DataFrame()
    try:
        df = pro.trade_cal(
            exchange=exchange,
            start_date=str(start_date),
            end_date=str(end_date),
            fields="exchange,cal_date,is_open,pretrade_date",
        )
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    return df.copy().sort_values("cal_date").reset_index(drop=True)


def is_trade_date(trade_date):
    cal_df = fetch_trade_calendar(trade_date, trade_date)
    if cal_df.empty:
        return False
    return int(cal_df.iloc[-1].get("is_open", 0) or 0) == 1


def latest_trade_date(date):
    end_date = pd.to_datetime(str(date), format="%Y%m%d", errors="coerce")
    if pd.isna(end_date):
        return ""
    start_date = (end_date - pd.Timedelta(days=20)).strftime("%Y%m%d")
    cal_df = fetch_trade_calendar(start_date, end_date.strftime("%Y%m%d"))
    if cal_df.empty:
        return ""
    open_dates = cal_df[cal_df["is_open"].astype(int) == 1]["cal_date"].astype(str)
    if open_dates.empty:
        return ""
    return open_dates.iloc[-1]


def fetch_all_daily(trade_date):
    pro = get_tushare_client()
    if pro is None:
        return pd.DataFrame()
    try:
        df = pro.daily(
            trade_date=str(trade_date),
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    for col in ["open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["ts_code", "trade_date", "close"]).reset_index(drop=True)


def fetch_daily_by_ts_code(ts_code, start_date, end_date, adjust="qfq"):
    code = ts_code_to_symbol(ts_code)
    return fetch_daily_price(code, start_date, end_date, adjust=adjust)


def find_stock(stock_code):
    code = normalize_stock_code(stock_code)
    if not code:
        return None
    stock_df = get_stock_basic_list()
    if stock_df.empty or "symbol" not in stock_df.columns:
        return None
    matched = stock_df[stock_df["symbol"].astype(str).str.zfill(6) == code]
    if matched.empty:
        return None
    return matched.iloc[0].to_dict()


def fetch_latest_quote(stock_code):
    basic = find_stock(stock_code)
    end_date = pd.Timestamp.today().strftime("%Y%m%d")
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=20)).strftime("%Y%m%d")
    price_df = fetch_daily_price(stock_code, start_date, end_date)
    if price_df.empty:
        return {
            "found": basic is not None,
            "name": basic.get("name") if basic else None,
            "price": pd.NA,
            "pct_change": pd.NA,
        }

    latest = price_df.iloc[-1]
    return {
        "found": True,
        "name": basic.get("name") if basic else None,
        "price": pd.to_numeric(latest.get("收盘"), errors="coerce"),
        "pct_change": pd.to_numeric(latest.get("涨跌幅"), errors="coerce"),
    }


def fetch_daily_basic(stock_code, start_date, end_date):
    pro = get_tushare_client()
    ts_code = to_ts_code(stock_code)
    if pro is None or not ts_code:
        return pd.DataFrame()
    try:
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=str(start_date),
            end_date=str(end_date),
            fields="ts_code,trade_date,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv",
        )
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
    return df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)


def fetch_news(start_date, end_date, src="sina"):
    pro = get_tushare_client()
    if pro is None:
        return pd.DataFrame()
    try:
        news_df = pro.news(
            src=src,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:
        return pd.DataFrame()
    if news_df is None or news_df.empty:
        return pd.DataFrame()
    return news_df.copy()


def fetch_cctv_news(date):
    pro = get_tushare_client()
    if pro is None:
        return pd.DataFrame()
    try:
        news_df = pro.cctv_news(date=str(date))
    except Exception:
        return pd.DataFrame()
    if news_df is None or news_df.empty:
        return pd.DataFrame()
    return news_df.copy()
