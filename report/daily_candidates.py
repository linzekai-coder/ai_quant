import os
import sys

import pandas as pd
from openai import OpenAI


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from database.daily_candidates import save_daily_candidates
from database.news_sentiment import get_news_sentiment
from stock_pool import DEFAULT_STOCK_POOL


STOCKS_DIR = os.path.join(PROJECT_ROOT, "data", "stocks")
TOP_N = 5
LLM_MODEL = os.getenv("AI_REASON_MODEL", "deepseek-chat")
LLM_BASE_URL = os.getenv("AI_REASON_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")


def _pick_column(df, candidates, fallback_index):
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return df.columns[fallback_index]


def load_stock_frame(csv_path):
    df = pd.read_csv(csv_path)
    date_col = _pick_column(df, ["日期"], 0)
    name_col = _pick_column(df, ["股票名称"], 1)
    code_col = _pick_column(df, ["代码"], 2)
    close_col = _pick_column(df, ["收盘"], 6)
    volume_col = _pick_column(df, ["成交量"], 8)

    result = df[[date_col, name_col, code_col, close_col, volume_col]].copy()
    result.columns = ["date", "stock_name", "stock_code", "close", "volume"]
    result["date"] = pd.to_datetime(result["date"])
    result["stock_code"] = result["stock_code"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce")
    result = result.dropna(subset=["date", "close", "volume"])
    return result.sort_values("date").reset_index(drop=True)


def score_candidate(df):
    if len(df) < 30:
        return None

    df = df.copy()
    df["ma20"] = df["close"].rolling(20).mean()
    df["return_5d"] = df["close"].pct_change(5)
    df["volatility_20d"] = df["close"].pct_change().rolling(20).std()
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

    latest = df.iloc[-1]
    if pd.isna(latest["ma20"]):
        return None

    score = 0
    reasons = []

    if latest["close"] > latest["ma20"]:
        score += 35
        reasons.append("收盘价站上MA20")

    if latest["return_5d"] > 0:
        score += min(25, latest["return_5d"] * 500)
        reasons.append(f"近5日上涨{latest['return_5d'] * 100:.2f}%")

    if latest["volume_ratio"] > 1.2:
        score += min(20, (latest["volume_ratio"] - 1) * 20)
        reasons.append(f"成交量放大{latest['volume_ratio']:.2f}倍")

    if latest["volatility_20d"] < 0.035:
        score += 20
        reasons.append("20日波动率可控")

    trade_date = latest["date"].strftime("%Y-%m-%d")
    stock_code = latest["stock_code"]
    news_sentiment = get_news_sentiment(stock_code, trade_date)
    if news_sentiment and news_sentiment.get("sentiment") != "无数据":
        sentiment_score = float(news_sentiment.get("final_sentiment_score", news_sentiment.get("sentiment_score", 0)) or 0)
        sentiment = news_sentiment.get("sentiment", "中性")
        if sentiment_score > 0.3:
            score += 10
        elif sentiment_score < -0.3:
            score -= 10
        reasons.append(f"新闻情绪：{sentiment}")
    else:
        reasons.append("新闻情绪：无数据")

    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": latest["stock_name"],
        "score": round(float(score), 2),
        "reason": "；".join(reasons) or "规则评分未触发明显优势",
        "rule_reasons": reasons,
        "features": {
            "close": round(float(latest["close"]), 2),
            "ma20": round(float(latest["ma20"]), 2),
            "return_5d": None if pd.isna(latest["return_5d"]) else round(float(latest["return_5d"]), 4),
            "volume_ratio": None if pd.isna(latest["volume_ratio"]) else round(float(latest["volume_ratio"]), 2),
            "volatility_20d": None if pd.isna(latest["volatility_20d"]) else round(float(latest["volatility_20d"]), 4),
        },
        "source": "rule",
        "enabled": 1,
    }


def build_ai_reason_prompt(candidate):
    features = candidate.get("features", {})
    rule_reasons = candidate.get("rule_reasons") or ["规则评分未触发明显优势"]
    return f"""
请为一个A股量化推荐结果生成面向投资者的中文解释。

股票：{candidate["stock_code"]} {candidate["stock_name"]}
交易日期：{candidate["trade_date"]}
综合评分：{candidate["score"]}
规则触发：{"；".join(rule_reasons)}
关键指标：
- 收盘价：{features.get("close")}
- MA20：{features.get("ma20")}
- 近5日收益：{features.get("return_5d")}
- 成交量放大倍数：{features.get("volume_ratio")}
- 20日波动率：{features.get("volatility_20d")}

要求：
1. 不要承诺收益，不要使用“必涨”“稳赚”等词。
2. 解释为什么当前值得关注，以及主要风险。
3. 输出 2 到 3 句中文，总字数控制在 120 字以内。
""".strip()


def generate_ai_reason(candidate):
    if not LLM_API_KEY:
        return None

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是谨慎的量化投研助手，只基于给定指标做解释，不提供确定性收益承诺。",
            },
            {
                "role": "user",
                "content": build_ai_reason_prompt(candidate),
            },
        ],
        temperature=0.3,
        max_tokens=220,
    )
    content = response.choices[0].message.content
    return content.strip() if content else None


def apply_ai_reasons(candidates):
    if not LLM_API_KEY:
        print("未配置 DEEPSEEK_API_KEY/OPENAI_API_KEY，推荐理由使用规则文本")
        return candidates

    for item in candidates:
        rule_reason = item["reason"]
        try:
            ai_reason = generate_ai_reason(item)
        except Exception as exc:
            print(f"{item['stock_code']} AI解释生成失败，使用规则理由: {exc}")
            continue

        if ai_reason:
            item["reason"] = ai_reason
            item["source"] = "ai"
            item["rule_reason"] = rule_reason

    return candidates


def generate_daily_candidates(top_n=TOP_N):
    candidates = []
    default_codes = {item["code"] for item in DEFAULT_STOCK_POOL}

    for filename in os.listdir(STOCKS_DIR):
        if not filename.endswith(".csv") or "sentiment" in filename:
            continue

        csv_path = os.path.join(STOCKS_DIR, filename)
        try:
            df = load_stock_frame(csv_path)
            item = score_candidate(df)
        except Exception as exc:
            print(f"跳过 {filename}: {exc}")
            continue

        if not item:
            continue

        # 第一版候选池先从已有CSV里生成。默认股票也可入选，但不会重复进入最终池。
        item["enabled"] = 1 if item["stock_code"] in default_codes else 1
        candidates.append(item)

    candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)[:top_n]
    candidates = apply_ai_reasons(candidates)
    save_rows = [
        {
            key: value
            for key, value in item.items()
            if key not in {"features", "rule_reasons", "rule_reason"}
        }
        for item in candidates
    ]
    saved = save_daily_candidates(save_rows)

    print(f"AI每日推荐池已生成: {saved} 只")
    for item in candidates:
        print(f"{item['stock_code']} {item['stock_name']} {item['score']} {item['reason']}")

    return candidates


if __name__ == "__main__":
    generate_daily_candidates()
