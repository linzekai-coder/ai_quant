import os

from data.fetch_data import fetch_stock_data
from database.db import save_score
from strategy.score import calculate_score, score_level


def process_watchlist_stock(stock_code, stock_name):
    stock_code = str(stock_code).zfill(6)
    stock_name = str(stock_name).strip()

    fetch_ok = fetch_stock_data({stock_code: stock_name})
    if not fetch_ok:
        return {
            "ok": False,
            "message": "行情数据采集失败，未生成评分",
        }

    csv_path = os.path.join("data", "stocks", f"{stock_name}.csv")
    if not os.path.exists(csv_path):
        return {
            "ok": False,
            "message": f"行情采集未生成CSV文件: {csv_path}",
        }

    try:
        score = calculate_score(csv_path)
        level = score_level(score)
        save_score(stock_code, score, level)
    except Exception as exc:
        return {
            "ok": False,
            "message": f"行情已采集，但评分失败: {exc}",
        }

    return {
        "ok": True,
        "message": f"已采集并评分: {stock_code} {stock_name} score={score} level={level}",
        "score": score,
        "level": level,
    }
