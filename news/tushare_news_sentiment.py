import os
import json
from datetime import datetime, timedelta

import pandas as pd

from data.tushare_provider import find_stock, fetch_cctv_news as fetch_tushare_cctv_news, get_tushare_client
from database.news_sentiment import (
    get_existing_sentiment_status,
    get_news_sentiment,
    now_text,
    upsert_news_sentiment,
)
from database.watchlist import list_all_enabled_watchlist
from news.sentiment import analyze_sentiment
from stock_pool import STOCK_METADATA
from utils.logger import logger


NEWS_SOURCES = ("", "sina", "eastmoney", "wallstreetcn")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "news_cache")
NEWS_CACHE_VERSION = "v2"
NEWS_FAILURE_COOLDOWN_MINUTES = 60
MACRO_CN_KEYWORDS = ("央行", "降准", "降息", "财政部", "国务院", "政策", "刺激", "救市", "监管", "证监会", "限购", "解禁")
MARKET_KEYWORDS = ("A股", "沪指", "深成指", "大盘", "牛市", "熊市", "暴跌", "暴涨", "外资", "北向资金", "融资融券")
GLOBAL_KEYWORDS = ("美联储", "加息", "降息", "特朗普", "关税", "贸易战", "地缘", "俄乌", "中东", "石油", "美元", "人民币汇率", "纳斯达克", "标普500", "美股")
INDUSTRY_KEYWORDS = {
    "新能源": ("宁德", "比亚迪", "光伏", "储能", "锂电", "充电桩", "电池"),
    "电池": ("宁德", "比亚迪", "光伏", "储能", "锂电", "充电桩", "电池"),
    "金融": ("银行", "券商", "利率", "存款", "信贷", "北向资金", "融资融券"),
    "银行": ("银行", "利率", "存款", "信贷"),
    "券商": ("券商", "证券", "融资融券", "沪指", "A股"),
    "科技": ("芯片", "半导体", "AI", "算力", "英伟达", "制裁", "CPO"),
    "半导体": ("芯片", "半导体", "AI", "算力", "英伟达", "制裁"),
    "医疗": ("医保", "集采", "创新药", "FDA", "医药"),
    "医药": ("医保", "集采", "创新药", "FDA", "医药"),
    "电力": ("电力", "发电", "火电", "水电", "风电", "光伏", "电网", "煤价", "用电量"),
    "能源": ("能源", "电力", "发电", "煤炭", "石油", "天然气", "新能源"),
}


def fetch_tushare_news(start_date, end_date, src="sina"):
    pro = get_tushare_client()
    if pro is None:
        return pd.DataFrame()
    try:
        news_df = pro.news(
            src=src,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        logger.warning("Tushare news 接口失败 src=%s: %s", src, exc)
        return pd.DataFrame()
    if news_df is None or news_df.empty:
        return pd.DataFrame()
    return news_df.copy()


def cache_path_for(prefix, start_dt, end_dt, source=None):
    safe_source = (source or "all").replace(os.path.sep, "_")
    filename = f"{prefix}_{safe_source}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}_{NEWS_CACHE_VERSION}.csv"
    return os.path.join(NEWS_CACHE_DIR, filename)


def failure_cache_path(start_dt, end_dt):
    return cache_path_for("news_failure", start_dt, end_dt, "all_sources").replace(".csv", ".json")


def read_failure_cooldown(start_dt, end_dt):
    path = failure_cache_path(start_dt, end_dt)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        failed_at = datetime.strptime(payload.get("failed_at", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    expires_at = failed_at + timedelta(minutes=NEWS_FAILURE_COOLDOWN_MINUTES)
    if datetime.now() >= expires_at:
        return None
    return {
        "path": path,
        "reason": payload.get("reason", "Tushare news 接口暂不可用"),
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def write_failure_cooldown(start_dt, end_dt, reason):
    path = failure_cache_path(start_dt, end_dt)
    try:
        os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump({
                "failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reason": str(reason),
            }, file_obj, ensure_ascii=False, indent=2)
        logger.warning("新闻接口失败冷却已写入: %s", path)
    except Exception as exc:
        logger.warning("新闻接口失败冷却写入失败 %s: %s", path, exc)


def read_news_cache(cache_path):
    if not os.path.exists(cache_path):
        return pd.DataFrame()
    try:
        cached = pd.read_csv(cache_path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("读取新闻缓存失败 %s: %s", cache_path, exc)
        return pd.DataFrame()
    if cached.empty:
        return pd.DataFrame()
    if "datetime" in cached.columns:
        cached["datetime"] = pd.to_datetime(cached["datetime"], errors="coerce")
    logger.info("使用新闻缓存: %s (%s 条)", cache_path, len(cached))
    return cached


def write_news_cache(cache_path, news_df):
    if news_df is None or news_df.empty:
        return
    try:
        os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
        news_df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        logger.info("新闻缓存已更新: %s (%s 条)", cache_path, len(news_df))
    except Exception as exc:
        logger.warning("新闻缓存写入失败 %s: %s", cache_path, exc)


def normalize_news_frame(news_df, source):
    if news_df is None or news_df.empty:
        return pd.DataFrame()
    result = news_df.copy()
    if "datetime" not in result.columns:
        if "date" in result.columns:
            result["datetime"] = pd.to_datetime(result["date"], errors="coerce")
        elif "time" in result.columns:
            result["datetime"] = pd.to_datetime(result["time"], errors="coerce")
    if "title" not in result.columns:
        if "content" in result.columns:
            result["title"] = result["content"].astype(str).str.slice(0, 80)
        else:
            result["title"] = ""
    if "content" not in result.columns:
        result["content"] = ""
    result["source"] = source or "all"
    return result


def fetch_cctv_news(start_dt, end_dt):
    cache_path = cache_path_for("cctv", start_dt, end_dt, "cctv")
    cached = read_news_cache(cache_path)
    if not cached.empty:
        return cached

    frames = []
    candidate_dates = [end_dt.date()]
    if start_dt.date() != end_dt.date():
        candidate_dates.append(end_dt.date() - timedelta(days=1))
    for current in candidate_dates:
        if current < start_dt.date():
            continue
        date_text = current.strftime("%Y%m%d")
        news_df = fetch_tushare_cctv_news(date_text)
        logger.info("Tushare cctv_news date=%s 原始返回: %s 条", date_text, len(news_df))
        if not news_df.empty:
            frames.append(normalize_news_frame(news_df, "cctv"))
            break
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    if "datetime" in merged.columns:
        merged["datetime"] = pd.to_datetime(merged["datetime"], errors="coerce")
        merged = merged.dropna(subset=["datetime"])
        merged = merged[(merged["datetime"] >= start_dt) & (merged["datetime"] <= end_dt)]
    merged = merged.drop_duplicates(subset=[col for col in ["datetime", "title", "content"] if col in merged.columns])
    write_news_cache(cache_path, merged)
    return merged


def normalize_ai_sentiment(ai_result):
    raw_sentiment = ai_result.get("情绪", "中性")
    raw_score = float(ai_result.get("评分", 0) or 0)
    score = max(-1, min(1, raw_score / 100))
    if raw_sentiment == "利好" or score > 0.3:
        sentiment = "正面"
    elif raw_sentiment == "利空" or score < -0.3:
        sentiment = "负面"
    else:
        sentiment = "中性"
    return sentiment, score


def score_to_label(score):
    if score > 0.3:
        return "正面"
    if score < -0.3:
        return "负面"
    return "中性"


def build_news_window(target_date=None, days=3):
    end_dt = datetime.now() if target_date is None else datetime.strptime(target_date, "%Y-%m-%d") + timedelta(hours=23, minutes=59, seconds=59)
    start_dt = end_dt - timedelta(days=days)
    return start_dt, end_dt


def fetch_recent_market_news(start_dt, end_dt, sources=NEWS_SOURCES):
    frames = []
    start_text = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    merged_cache_path = cache_path_for("news_merged", start_dt, end_dt, "all_sources")
    cached_merged = read_news_cache(merged_cache_path)
    if not cached_merged.empty:
        return cached_merged

    cooldown = read_failure_cooldown(start_dt, end_dt)
    if cooldown:
        logger.warning(
            "Tushare news 接口处于冷却期，跳过请求；原因=%s，到期=%s",
            cooldown["reason"],
            cooldown["expires_at"],
        )
        return pd.DataFrame()

    logger.info("Tushare 新闻拉取窗口: %s -> %s", start_text, end_text)
    for source in sources:
        source_cache_path = cache_path_for("news", start_dt, end_dt, source)
        cached = read_news_cache(source_cache_path)
        if not cached.empty:
            frames.append(cached)
    if frames:
        logger.info("本轮新闻使用分来源缓存，未重新请求 Tushare news 接口")
    else:
        # Tushare news 配额较紧，首次无缓存时只拉一次全量快讯，避免同一轮连续请求多个 src 触发限流。
        source = ""
        source_cache_path = cache_path_for("news", start_dt, end_dt, source)
        news_df = fetch_tushare_news(start_text, end_text, src=source)
        logger.info("Tushare news src=%s 原始返回: %s 条, columns=%s", "all", len(news_df), list(news_df.columns) if not news_df.empty else [])
        if not news_df.empty:
            news_df = normalize_news_frame(news_df, source or "all")
            write_news_cache(source_cache_path, news_df)
            frames.append(news_df)

    if not frames:
        if not frames:
            reason = "Tushare news 接口未返回数据，可能是频率限制或权限不足"
            logger.warning(reason)
            write_failure_cooldown(start_dt, end_dt, reason)
            return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    if "datetime" in merged.columns:
        merged["datetime"] = pd.to_datetime(merged["datetime"], errors="coerce")
        merged = merged.dropna(subset=["datetime"])
        merged = merged[(merged["datetime"] >= start_dt) & (merged["datetime"] <= end_dt)]
    merged = merged.drop_duplicates(subset=[col for col in ["datetime", "title", "content"] if col in merged.columns])
    for idx, row in merged.head(3).iterrows():
        logger.info(
            "原始新闻样例 %s: title=%s content=%s",
            idx + 1,
            str(row.get("title", "") or "")[:120],
            str(row.get("content", "") or "")[:160],
        )
    write_news_cache(merged_cache_path, merged)
    return merged


def build_stock_keywords(stock_code, stock_name):
    code = str(stock_code).zfill(6)
    name = str(stock_name).strip()
    keywords = {code}
    if name:
        keywords.add(name)
        suffixes = ("股份", "集团", "银行", "证券", "科技", "精密", "发电", "能源", "医药", "控股", "电力")
        short_name = name
        for suffix in suffixes:
            if short_name.endswith(suffix) and len(short_name) > len(suffix) + 1:
                short_name = short_name[: -len(suffix)]
                break
        if len(short_name) >= 2:
            keywords.add(short_name)
        if len(name) >= 2:
            keywords.add(name[:2])

    metadata = STOCK_METADATA.get(code, {})
    industry = metadata.get("industry")
    if industry and industry not in ("未分类", "自选", "推荐池"):
        keywords.add(industry)
    return [item for item in keywords if item]


def filter_stock_news(news_df, stock_code, stock_name, limit=10):
    if news_df.empty:
        return []

    keywords = build_stock_keywords(stock_code, stock_name)
    matched = []
    for _, row in news_df.iterrows():
        title = str(row.get("title", "") or "")
        content = str(row.get("content", "") or "")
        if not title and content:
            title = content[:80]
        text = f"{title}\n{content}"
        hit_keywords = [keyword for keyword in keywords if keyword and keyword in text]
        if hit_keywords:
            matched.append({
                "datetime": row.get("datetime"),
                "title": title.strip(),
                "content": content.strip(),
                "keywords": hit_keywords,
            })

    matched = sorted(
        matched,
        key=lambda item: item["datetime"] if pd.notna(item["datetime"]) else pd.Timestamp.min,
        reverse=True,
    )
    return matched[:limit]


def filter_keyword_news(news_df, keywords, limit=10):
    if news_df.empty:
        return []
    matched = []
    for _, row in news_df.iterrows():
        title = str(row.get("title", "") or "")
        content = str(row.get("content", "") or "")
        if not title and content:
            title = content[:80]
        text = f"{title}\n{content}"
        hit_keywords = [keyword for keyword in keywords if keyword and keyword in text]
        if hit_keywords:
            matched.append({
                "datetime": row.get("datetime"),
                "title": title.strip(),
                "content": content.strip(),
                "keywords": hit_keywords,
            })
    matched = sorted(
        matched,
        key=lambda item: item["datetime"] if pd.notna(item["datetime"]) else pd.Timestamp.min,
        reverse=True,
    )
    return matched[:limit]


def analyze_news_items(news_items, empty_reason="未匹配到相关新闻"):
    if not news_items:
        return {
            "sentiment": "无数据",
            "score": 0,
            "reason": empty_reason,
            "titles": [],
        }
    joined_news = "\n\n".join(
        f"标题：{item.get('title', '')}\n内容：{item.get('content', '')}"
        for item in news_items
    )
    ai_result = analyze_sentiment(joined_news)
    sentiment, score = normalize_ai_sentiment(ai_result)
    return {
        "sentiment": sentiment,
        "score": score,
        "reason": ai_result.get("原因", ""),
        "titles": [item["title"] for item in news_items if item.get("title")],
    }


def infer_industry_from_name(stock_name):
    name = str(stock_name or "")
    if any(keyword in name for keyword in ("发电", "电力", "能源")):
        return "电力"
    if any(keyword in name for keyword in ("证券", "券商", "银行")):
        return "金融"
    if any(keyword in name for keyword in ("医药", "医疗", "药业")):
        return "医药"
    if any(keyword in name for keyword in ("半导体", "芯片", "科技")):
        return "科技"
    if any(keyword in name for keyword in ("宁德", "电池", "新能源")):
        return "新能源"
    return ""


def stock_industry(stock_code, stock_name=""):
    metadata = STOCK_METADATA.get(str(stock_code).zfill(6), {})
    industry = metadata.get("industry")
    if industry and industry not in ("未分类", "自选", "推荐池"):
        return industry
    basic = find_stock(stock_code)
    if basic and basic.get("industry"):
        return str(basic["industry"])
    inferred = infer_industry_from_name(stock_name)
    if inferred:
        return inferred
    return ""


def industry_keywords(stock_code, stock_name=""):
    industry = stock_industry(stock_code, stock_name)
    keywords = set()
    for key, values in INDUSTRY_KEYWORDS.items():
        if industry and (key in industry or industry in key):
            keywords.update(values)
    if industry:
        keywords.add(industry)
    return list(keywords)


def combine_scores(stock_score, industry_score, macro_cn_score, macro_global_score, has_stock_news):
    if has_stock_news:
        weights = {
            "stock": 0.5,
            "industry": 0.2,
            "macro_cn": 0.2,
            "macro_global": 0.1,
        }
    else:
        weights = {
            "stock": 0,
            "industry": 0.7,
            "macro_cn": 0.2,
            "macro_global": 0.1,
        }
    final = (
        stock_score * weights["stock"]
        + industry_score * weights["industry"]
        + macro_cn_score * weights["macro_cn"]
        + macro_global_score * weights["macro_global"]
    )
    return max(-1, min(1, final)), weights


def analyze_stock_news(stock_code, stock_name, news_items, trade_date):
    analyzed = analyze_news_items(news_items, empty_reason="当日未匹配到相关新闻")
    return {
        "stock_code": stock_code,
        "trade_date": trade_date,
        "news_count": len(news_items),
        "sentiment": analyzed["sentiment"],
        "sentiment_score": analyzed["score"],
        "news_titles": analyzed["titles"],
        "analysis_reason": analyzed["reason"],
        "created_at": now_text(),
    }


def analyze_watchlist_news(target_date=None, force=False):
    trade_date = target_date or datetime.now().strftime("%Y-%m-%d")
    watchlist = list_all_enabled_watchlist()
    if not watchlist:
        return {"ok": True, "saved": 0, "skipped": 0, "failed": 0, "message": "没有启用中的自选股"}

    start_dt, end_dt = build_news_window(trade_date, days=3)
    try:
        news_df = fetch_recent_market_news(start_dt, end_dt)
    except Exception as exc:
        logger.exception("Tushare 新闻获取失败: %s", exc)
        return {"ok": False, "saved": 0, "skipped": 0, "failed": len(watchlist), "message": f"Tushare 新闻获取失败: {exc}"}

    if news_df.empty:
        message = "Tushare 新闻接口未返回原始新闻，可能是接口限流或无可用缓存；本轮未更新股票情绪"
        logger.warning(message)
        return {
            "ok": False,
            "saved": 0,
            "analyzed": 0,
            "skipped": 0,
            "failed": len(watchlist),
            "message": message,
        }

    logger.info("本轮新闻总量: %s 条; 自选股: %s 只", len(news_df), len(watchlist))
    cctv_df = fetch_cctv_news(start_dt, end_dt)
    macro_cn_news = filter_keyword_news(pd.concat([news_df, cctv_df], ignore_index=True) if not cctv_df.empty else news_df, MACRO_CN_KEYWORDS, limit=10)
    market_news = filter_keyword_news(news_df, MARKET_KEYWORDS, limit=10)
    if "source" in news_df.columns:
        global_news_df = news_df[news_df["source"].astype(str).eq("wallstreetcn")]
        if global_news_df.empty:
            global_news_df = news_df
    else:
        global_news_df = news_df
    macro_global_news = filter_keyword_news(global_news_df, GLOBAL_KEYWORDS, limit=10)
    macro_cn_result = analyze_news_items(macro_cn_news, empty_reason="未匹配到国内宏观新闻")
    market_result = analyze_news_items(market_news, empty_reason="未匹配到综合市场新闻")
    macro_global_result = analyze_news_items(macro_global_news, empty_reason="未匹配到国际宏观新闻")
    macro_cn_score = macro_cn_result["score"]
    macro_global_score = macro_global_result["score"]
    logger.info(
        "宏观情绪: 国内=%s %.2f 市场=%s %.2f 国际=%s %.2f",
        macro_cn_result["sentiment"], macro_cn_score,
        market_result["sentiment"], market_result["score"],
        macro_global_result["sentiment"], macro_global_score,
    )
    saved = 0
    analyzed = 0
    skipped = 0
    failed = 0
    for row in watchlist:
        stock_code = str(row["stock_code"]).zfill(6)
        stock_name = row["stock_name"]
        existing = get_existing_sentiment_status(stock_code, trade_date)
        if existing and existing["sentiment"] != "无数据" and existing.get("has_breakdown") and not force:
            logger.info("%s %s 跳过：已有有效记录 sentiment=%s news_count=%s", stock_code, stock_name, existing["sentiment"], existing["news_count"])
            skipped += 1
            continue
        if existing and existing["sentiment"] != "无数据" and not existing.get("has_breakdown") and not force:
            logger.info("%s %s 已有旧版情绪记录，本次补充宏观/行业明细", stock_code, stock_name)
        if existing and existing["sentiment"] == "无数据" and not force:
            logger.info("%s %s 已有无数据记录，本次重新匹配", stock_code, stock_name)
        try:
            keywords = build_stock_keywords(stock_code, stock_name)
            stock_news = filter_stock_news(news_df, stock_code, stock_name)
            ind_keywords = industry_keywords(stock_code, stock_name)
            ind_news = filter_keyword_news(news_df, ind_keywords, limit=10)
            logger.info(
                "%s %s 关键词=%s 行业关键词=%s 原始新闻=%s 个股新闻=%s 行业新闻=%s",
                stock_code,
                stock_name,
                keywords,
                ind_keywords,
                len(news_df),
                len(stock_news),
                len(ind_news),
            )
            for idx, item in enumerate(stock_news[:3], 1):
                logger.info("%s %s 匹配样例%s keywords=%s title=%s", stock_code, stock_name, idx, item.get("keywords"), item.get("title", "")[:120])
            stock_result = analyze_news_items(stock_news, empty_reason="当日未匹配到相关新闻")
            industry_result = analyze_news_items(ind_news, empty_reason="当日未匹配到行业相关新闻")
            macro_cn_combined_score = (macro_cn_score * 0.6) + (market_result["score"] * 0.4)
            final_score, weights = combine_scores(
                stock_result["score"],
                industry_result["score"],
                macro_cn_combined_score,
                macro_global_score,
                has_stock_news=bool(stock_news),
            )
            result = {
                "stock_code": stock_code,
                "trade_date": trade_date,
                "news_count": len(stock_news),
                "sentiment": score_to_label(final_score),
                "sentiment_score": stock_result["score"],
                "news_titles": stock_result["titles"],
                "analysis_reason": stock_result["reason"],
                "macro_sentiment_cn": macro_cn_combined_score,
                "macro_sentiment_global": macro_global_score,
                "industry_sentiment": industry_result["score"],
                "final_sentiment_score": final_score,
                "score_breakdown": {
                    "stock": {"score": stock_result["score"], "sentiment": stock_result["sentiment"], "count": len(stock_news)},
                    "industry": {"score": industry_result["score"], "sentiment": industry_result["sentiment"], "count": len(ind_news), "keywords": ind_keywords},
                    "macro_cn": {"score": macro_cn_score, "sentiment": macro_cn_result["sentiment"], "count": len(macro_cn_news)},
                    "market": {"score": market_result["score"], "sentiment": market_result["sentiment"], "count": len(market_news)},
                    "macro_global": {"score": macro_global_score, "sentiment": macro_global_result["sentiment"], "count": len(macro_global_news)},
                    "weights": weights,
                },
                "created_at": now_text(),
            }
            upsert_news_sentiment(result)
            analyzed += 1
            if not existing:
                saved += 1
        except Exception as exc:
            failed += 1
            logger.exception("%s %s 新闻情绪分析失败: %s", stock_code, stock_name, exc)

    return {
        "ok": failed == 0,
        "saved": saved,
        "analyzed": analyzed,
        "skipped": skipped,
        "failed": failed,
        "message": f"新闻情绪分析完成：新增 {saved}，分析 {analyzed}，跳过 {skipped}，失败 {failed}",
    }


def get_saved_news_sentiment(stock_code, trade_date):
    return get_news_sentiment(stock_code, trade_date)
