import os

import numpy as np
import pandas as pd

from news.sentiment import analyze_news
from database.news_sentiment import get_news_sentiment


def get_news_score(news):
    result = analyze_news(news)

    if "利好" in result:
        return 30
    elif "中性" in result:
        return 15
    else:
        return 0


def get_saved_news_score(stock_code, trade_date):
    sentiment = get_news_sentiment(stock_code, trade_date)
    if not sentiment or sentiment.get("sentiment") == "无数据":
        return 0

    score = float(sentiment.get("final_sentiment_score", sentiment.get("sentiment_score", 0)) or 0)
    if score > 0.3:
        return 10
    if score < -0.3:
        return -10
    return 0


def score_level(score):
    if score >= 80:
        return "强烈关注"
    elif score >= 60:
        return "观察"
    else:
        return "回避"


def get_stock_name(stock_code):
    csv_path = _resolve_stock_csv(stock_code)
    df = pd.read_csv(csv_path, nrows=1, dtype={"代码": str})

    if "股票名称" in df.columns and not df.empty:
        return df.loc[0, "股票名称"]

    return str(stock_code)


def _resolve_stock_csv(stock_code):
    if stock_code.endswith(".csv") or os.path.sep in stock_code:
        return stock_code

    stocks_dir = os.path.join("data", "stocks")
    direct_path = os.path.join(stocks_dir, f"{stock_code}.csv")
    if os.path.exists(direct_path):
        return direct_path

    for filename in os.listdir(stocks_dir):
        if not filename.endswith(".csv"):
            continue
        csv_path = os.path.join(stocks_dir, filename)
        try:
            sample = pd.read_csv(csv_path, nrows=1, dtype={"代码": str})
        except Exception:
            continue
        if "代码" in sample.columns and str(sample.loc[0, "代码"]).zfill(6) == str(stock_code).zfill(6):
            return csv_path

    return direct_path


def calculate_score(stock_code):
    csv_path = _resolve_stock_csv(stock_code)
    df = pd.read_csv(csv_path)

    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    df["收盘"] = pd.to_numeric(df["收盘"], errors="coerce")
    df = df.dropna(subset=["收盘"])

    if len(df) < 20:
        raise ValueError("至少需要 20 个交易日数据才能计算 MA20 评分")

    # 均线
    df["MA5"] = df["收盘"].rolling(5).mean()
    df["MA20"] = df["收盘"].rolling(20).mean()

    latest = df.iloc[-1]
    score = 0

    # 技术面（40）
    if latest["MA5"] > latest["MA20"]:
        score += 40

    # 风险面（30）
    returns = df["收盘"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    volatility = returns.std()

    if volatility < 0.02:
        score += 30
    elif volatility < 0.04:
        score += 20
    else:
        score += 10

    trade_date = latest["日期"].strftime("%Y-%m-%d")
    score += get_saved_news_score(str(df.iloc[-1].get("代码", stock_code)).zfill(6), trade_date)

    return int(score)


if __name__ == "__main__":
    for stock in ["贵州茅台", "平安银行", "海康威视", "五粮液"]:
        print(stock, calculate_score(stock))
