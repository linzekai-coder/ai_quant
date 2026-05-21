import os
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape

import feedparser
import pandas as pd
import requests
from openai import OpenAI

from database.news_raw import has_recent_fetch, list_news_raw, now_text, set_fetch_state, upsert_news_raw
from database.news_sentiment import get_existing_sentiment_status, get_news_sentiment, upsert_news_sentiment
from database.watchlist import list_all_enabled_watchlist
from data.tushare_provider import find_stock
from news.sentiment import analyze_sentiment
from stock_pool import STOCK_METADATA
from utils.logger import logger


INDIVIDUAL_RSS_SOURCES = (
    ("东方财富", "http://feed.eastmoney.com/rss/news.xml"),
    ("新浪财经", "http://rss.sina.com.cn/finance/"),
    ("证券时报", "http://www.stcn.com/rss/news.xml"),
)
MACRO_CN_RSS_SOURCES = (
    ("财联社", "https://www.cls.cn/rss"),
    ("人民日报", "http://www.people.com.cn/rss/politics.xml"),
    ("新华社", "http://www.xinhuanet.com/politics/news_politics.xml"),
)
NEWSAPI_URL = "https://newsapi.org/v2/everything"
NEWSAPI_QUERY = "Trump OR Fed OR tariff OR China trade OR interest rate"
NEWSAPI_KEYWORDS = {
    "关税风险": ("trump", "tariff"),
    "美联储政策": ("fed", "rate", "interest rate"),
    "中美贸易": ("china trade",),
    "能源价格": ("oil", "energy"),
}
RSS_TIMEOUT = 10
RSS_RETRY = 2
INDIVIDUAL_REFRESH_HOURS = 4
MACRO_CN_KEYWORDS = ("央行", "降准", "降息", "财政部", "国务院", "政策", "刺激", "救市", "监管", "证监会", "限购", "解禁")
MARKET_KEYWORDS = ("A股", "沪指", "深成指", "大盘", "牛市", "熊市", "暴跌", "暴涨", "外资", "北向资金", "融资融券")
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


def score_to_label(score):
    if score > 0.3:
        return "正面"
    if score < -0.3:
        return "负面"
    return "中性"


def build_stock_keywords(stock_code, stock_name):
    code = str(stock_code).zfill(6)
    name = str(stock_name or "").strip()
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
    return infer_industry_from_name(stock_name)


def industry_keywords(stock_code, stock_name=""):
    industry = stock_industry(stock_code, stock_name)
    keywords = set()
    for key, values in INDUSTRY_KEYWORDS.items():
        if industry and (key in industry or industry in key):
            keywords.update(values)
    if industry:
        keywords.add(industry)
    return list(keywords)


def filter_keyword_news(news_df, keywords, limit=10):
    if news_df.empty or not keywords:
        return []
    matched = []
    for _, row in news_df.iterrows():
        title = str(row.get("title", "") or "")
        content = str(row.get("content", "") or "")
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


def combine_scores(stock_score, industry_score, macro_cn_score, macro_global_score, has_stock_news):
    if has_stock_news:
        weights = {"stock": 0.5, "industry": 0.2, "macro_cn": 0.2, "macro_global": 0.1}
    else:
        weights = {"stock": 0, "industry": 0.7, "macro_cn": 0.2, "macro_global": 0.1}
    final = (
        stock_score * weights["stock"]
        + industry_score * weights["industry"]
        + macro_cn_score * weights["macro_cn"]
        + macro_global_score * weights["macro_global"]
    )
    return max(-1, min(1, final)), weights


def parse_entry_time(entry):
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        try:
            return datetime.fromtimestamp(time.mktime(parsed))
        except Exception:
            pass
    for key in ("published", "updated"):
        value = entry.get(key)
        if not value:
            continue
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception:
            continue
    return datetime.now()


def clean_text(value, limit=None):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def parse_rss_source(source_name, url, max_entries=50):
    for attempt in range(RSS_RETRY + 1):
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=RSS_TIMEOUT)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            feed = feedparser.parse(response.content)
            if getattr(feed, "bozo", False) and not getattr(feed, "entries", None):
                if "xml" not in content_type and "rss" not in response.text[:500].lower():
                    return parse_html_news_links(source_name, response)
                raise RuntimeError(getattr(feed, "bozo_exception", "RSS解析失败"))
            rows = []
            for entry in list(feed.entries)[:max_entries]:
                published_time = parse_entry_time(entry)
                rows.append({
                    "source_name": source_name,
                    "title": clean_text(entry.get("title", "")),
                    "summary": clean_text(entry.get("summary", entry.get("description", "")), limit=200),
                    "url": entry.get("link", ""),
                    "published_dt": published_time,
                    "published_time": published_time.strftime("%Y-%m-%d %H:%M:%S"),
                })
            return rows
        except Exception as exc:
            logger.warning("RSS解析失败 source=%s attempt=%s url=%s: %s", source_name, attempt + 1, url, exc)
            if attempt < RSS_RETRY:
                time.sleep(1 + attempt)
    return []


def parse_html_news_links(source_name, response, max_entries=50):
    response.encoding = response.apparent_encoding or response.encoding
    html = response.text
    base_url = response.url
    rows = []
    seen = set()
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.I | re.S):
        href = match.group(1).strip()
        title = clean_text(re.sub(r"<[^>]+>", " ", unescape(match.group(2))))
        if len(title) < 8 or len(title) > 90:
            continue
        if any(word in title for word in ("APP", "下载", "开户", "广告", "主力增仓", "自选股")):
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            root = re.match(r"^(https?://[^/]+)", base_url)
            href = (root.group(1) if root else "") + href
        elif not href.startswith("http"):
            continue
        if any(part in href for part in ("acttg.", "/pub/", "download", "passport", "login")):
            continue
        if source_name == "东方财富" and not re.search(r"eastmoney\.com/a/\d+\.html", href):
            continue
        if source_name == "新浪财经" and "finance.sina" not in href:
            continue
        if source_name == "证券时报" and "stcn.com/article/detail/" not in href:
            continue
        if source_name == "财联社" and "cls.cn/detail/" not in href:
            continue
        key = (title, href)
        if key in seen:
            continue
        seen.add(key)
        published_time = datetime.now()
        rows.append({
            "source_name": source_name,
            "title": title,
            "summary": "",
            "url": href,
            "published_dt": published_time,
            "published_time": published_time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(rows) >= max_entries:
            break
    logger.info("%s HTML兜底抽取新闻链接=%s", source_name, len(rows))
    return rows


def fetch_rss_pool(sources, within_hours=48, max_entries=50):
    cutoff = datetime.now() - timedelta(hours=within_hours)
    items = []
    for source_name, url in sources:
        try:
            for item in parse_rss_source(source_name, url, max_entries=max_entries):
                if item["published_dt"] >= cutoff:
                    items.append(item)
        except Exception as exc:
            logger.warning("RSS源跳过 source=%s url=%s: %s", source_name, url, exc)
    items = sorted(items, key=lambda item: item["published_dt"], reverse=True)
    return items


def stock_match(item, stock_code, stock_name):
    title = item.get("title", "")
    summary = item.get("summary", "")
    code = str(stock_code).zfill(6)
    name = str(stock_name or "").strip()
    if name and name in title:
        return True
    if code and code in title:
        return True
    if name and name in summary and is_finance_title(title):
        return True
    return False


def is_finance_title(title):
    finance_keywords = (
        "A股", "股票", "上市", "公司", "财报", "业绩", "融资", "投资", "基金", "券商", "证券",
        "股价", "公告", "监管", "市场", "港股", "美股", "资本", "并购", "减持", "增持"
    )
    return any(keyword in str(title or "") for keyword in finance_keywords)


def to_raw_item(item, source_type, stock_code=None, source_name=None, title=None, summary=None):
    return {
        "source_type": source_type,
        "source_name": source_name or item.get("source_name", ""),
        "stock_code": str(stock_code).zfill(6) if stock_code else None,
        "title": clean_text(title or item.get("title", "")),
        "summary": clean_text(summary if summary is not None else item.get("summary", ""), limit=200),
        "published_time": item.get("published_time") or now_text(),
        "url": item.get("url", ""),
        "created_at": now_text(),
    }


def fetch_individual_news(watchlist):
    since = (datetime.now() - timedelta(hours=INDIVIDUAL_REFRESH_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    cache_key = datetime.now().strftime("%Y-%m-%d-%H")
    if has_recent_fetch("individual", "rolling", since):
        logger.info("个股RSS新闻4小时抓取状态命中，跳过抓取")
        return 0

    try:
        pool = fetch_rss_pool(INDIVIDUAL_RSS_SOURCES, within_hours=48, max_entries=50)
        raw_items = []
        for stock in watchlist:
            stock_code = str(stock["stock_code"]).zfill(6)
            stock_name = stock["stock_name"]
            matched = [
                item for item in pool
                if stock_match(item, stock_code, stock_name)
            ][:10]
            raw_items.extend(
                to_raw_item(item, "individual", stock_code=stock_code)
                for item in matched
            )
            logger.info("%s %s RSS个股新闻匹配=%s", stock_code, stock_name, len(matched))
            for item in matched:
                logger.info("%s -> 命中新闻标题: %s", stock_name, item.get("title", ""))
        inserted = upsert_news_raw(raw_items)
        set_fetch_state("individual", "rolling", "ok", f"pool={len(pool)} inserted={inserted} key={cache_key}")
        return inserted
    except Exception as exc:
        set_fetch_state("individual", "rolling", "failed", str(exc))
        raise


def fetch_macro_cn_news():
    today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    today_key = datetime.now().strftime("%Y-%m-%d")
    if has_recent_fetch("macro_cn", today_key, today_start):
        logger.info("国内宏观RSS新闻今日抓取状态命中，跳过抓取")
        return 0

    try:
        pool = fetch_rss_pool(MACRO_CN_RSS_SOURCES, within_hours=48, max_entries=50)
        matched = []
        keywords = tuple(set(MACRO_CN_KEYWORDS + MARKET_KEYWORDS + (
            "加息", "货币政策", "财政政策", "经济", "GDP", "通胀", "改革"
        )))
        for item in pool:
            text = f"{item.get('title', '')}\n{item.get('summary', '')}"
            if any(keyword in text for keyword in keywords):
                matched.append(item)
        raw_items = [to_raw_item(item, "macro_cn") for item in matched[:20]]
        logger.info("国内宏观RSS新闻匹配=%s", len(raw_items))
        inserted = upsert_news_raw(raw_items)
        set_fetch_state("macro_cn", today_key, "ok", f"pool={len(pool)} inserted={inserted}")
        return inserted
    except Exception as exc:
        set_fetch_state("macro_cn", today_key, "failed", str(exc))
        raise


def newsapi_key():
    token = os.getenv("NEWSAPI_KEY", "").strip()
    if token:
        return token
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, "r", encoding="utf-8-sig") as file_obj:
            for line in file_obj:
                key, _, value = line.strip().partition("=")
                if key == "NEWSAPI_KEY" and value:
                    return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def translate_global_summary(title, description):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8-sig") as file_obj:
                    for line in file_obj:
                        key, _, value = line.strip().partition("=")
                        if key == "DEEPSEEK_API_KEY" and value:
                            api_key = value.strip().strip('"').strip("'")
                            break
            except OSError:
                api_key = ""
    if not api_key:
        return clean_text(description, limit=200)
    try:
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        response = client.chat.completions.create(
            model=os.getenv("AI_REASON_MODEL", "deepseek-chat"),
            messages=[{
                "role": "user",
                "content": (
                    "请把下面英文财经新闻标题和摘要翻译成简洁中文，最多120字，只输出译文。\n"
                    f"Title: {title}\nDescription: {description}"
                ),
            }],
            temperature=0.1,
            max_tokens=180,
        )
        return clean_text(response.choices[0].message.content, limit=200)
    except Exception as exc:
        logger.warning("国际新闻摘要翻译失败，使用英文摘要: %s", exc)
        return clean_text(description, limit=200)


def classify_newsapi_label(title, description):
    text = f"{title} {description}".lower()
    labels = []
    for label, keywords in NEWSAPI_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            labels.append(label)
    return "、".join(labels) if labels else "国际宏观"


def fetch_newsapi_articles():
    api_key = newsapi_key()
    if not api_key:
        logger.warning("未配置 NEWSAPI_KEY，跳过国际宏观新闻抓取")
        return None
    newsapi_from = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    params = {
        "q": NEWSAPI_QUERY,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": api_key,
        "from": newsapi_from,
    }
    for attempt in range(RSS_RETRY + 1):
        try:
            response = requests.get(NEWSAPI_URL, params=params, timeout=RSS_TIMEOUT)
            if response.status_code == 429:
                logger.warning("NewsAPI免费额度或频率达到限制，今日使用缓存/昨日数据")
                return None
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "ok":
                raise RuntimeError(payload.get("message", "NewsAPI返回异常"))
            return payload.get("articles", [])
        except Exception as exc:
            logger.warning("NewsAPI请求失败 attempt=%s: %s", attempt + 1, exc)
            if attempt < RSS_RETRY:
                time.sleep(1 + attempt)
    return None


def fetch_macro_global_news():
    today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    today_key = datetime.now().strftime("%Y-%m-%d")
    if has_recent_fetch("macro_global", today_key, today_start):
        logger.info("国际宏观新闻今日抓取状态命中，跳过抓取")
        return 0

    articles = fetch_newsapi_articles()
    if articles is None:
        yesterday_start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        yesterday_rows = list_news_raw("macro_global", start_time=yesterday_start, end_time=today_start, limit=20)
        logger.info("NewsAPI不可用，昨日国际宏观缓存=%s条", len(yesterday_rows))
        set_fetch_state("macro_global", today_key, "skipped", f"NewsAPI unavailable, yesterday={len(yesterday_rows)}")
        return 0

    raw_items = []
    for article in articles[:20]:
        title = clean_text(article.get("title", ""))
        description = clean_text(article.get("description", ""), limit=160)
        label = classify_newsapi_label(title, description)
        translated_summary = translate_global_summary(title, description)
        published_at = article.get("publishedAt", "")
        try:
            published_dt = pd.to_datetime(published_at, errors="coerce")
            if pd.isna(published_dt):
                published_time = now_text()
            else:
                if getattr(published_dt, "tzinfo", None):
                    published_dt = published_dt.tz_convert(None)
                published_time = published_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            published_time = now_text()
        raw_items.append({
            "source_type": "macro_global",
            "source_name": "NewsAPI",
            "stock_code": None,
            "title": f"{label}：{title}",
            "summary": translated_summary,
            "published_time": published_time,
            "url": article.get("url", ""),
            "created_at": now_text(),
        })
    logger.info("NewsAPI国际宏观新闻=%s", len(raw_items))
    inserted = upsert_news_raw(raw_items)
    set_fetch_state("macro_global", today_key, "ok", f"inserted={inserted}")
    return inserted


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


def analyze_news_items(news_items, empty_reason="未匹配到相关新闻"):
    if not news_items:
        return {
            "sentiment": "无数据",
            "score": 0,
            "reason": empty_reason,
            "titles": [],
        }
    joined_news = "\n\n".join(
        f"标题：{item.get('title', '')}\n摘要：{item.get('summary', '')}"
        for item in news_items[:20]
    )
    ai_result = analyze_sentiment(joined_news)
    sentiment, score = normalize_ai_sentiment(ai_result)
    return {
        "sentiment": sentiment,
        "score": score,
        "reason": ai_result.get("原因", ""),
        "titles": [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
            }
            for item in news_items
            if item.get("title")
        ],
    }


def rows_to_filter_frame(rows):
    if not rows:
        return pd.DataFrame(columns=["datetime", "title", "content", "source"])
    return pd.DataFrame([
        {
            "datetime": row.get("published_time"),
            "title": row.get("title", ""),
            "content": row.get("summary", ""),
            "source": row.get("source_name", ""),
        }
        for row in rows
    ])


def fetch_and_cache_news(watchlist):
    inserted = 0
    inserted += fetch_individual_news(watchlist)
    inserted += fetch_macro_cn_news()
    inserted += fetch_macro_global_news()
    return inserted


def analyze_watchlist_news(target_date=None, force=False):
    trade_date = target_date or datetime.now().strftime("%Y-%m-%d")
    watchlist = list_all_enabled_watchlist()
    if not watchlist:
        return {"ok": True, "saved": 0, "skipped": 0, "failed": 0, "message": "没有启用中的自选股"}

    failed = 0
    try:
        raw_inserted = fetch_and_cache_news(watchlist)
    except Exception as exc:
        raw_inserted = 0
        logger.exception("免费新闻抓取失败，继续使用已有缓存: %s", exc)

    end_time = f"{trade_date} 23:59:59"
    start_time = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d 00:00:00")
    macro_cn_rows = list_news_raw("macro_cn", start_time=start_time, end_time=end_time, limit=20)
    macro_global_rows = list_news_raw("macro_global", start_time=start_time, end_time=end_time, limit=20)
    individual_rows = list_news_raw("individual", start_time=start_time, end_time=end_time, limit=500)

    macro_cn_result = analyze_news_items(macro_cn_rows, empty_reason="未匹配到国内宏观新闻")
    macro_global_result = analyze_news_items(macro_global_rows, empty_reason="未匹配到国际宏观新闻")
    macro_cn_score = macro_cn_result["score"]
    macro_global_score = macro_global_result["score"]
    industry_pool_df = rows_to_filter_frame(individual_rows + macro_cn_rows)

    saved = 0
    analyzed = 0
    skipped = 0
    for row in watchlist:
        stock_code = str(row["stock_code"]).zfill(6)
        stock_name = row["stock_name"]
        existing = get_existing_sentiment_status(stock_code, trade_date)
        stale_ai_result = existing and "未配置AI情绪分析密钥" in str(existing.get("analysis_reason", ""))
        if existing and existing["sentiment"] != "无数据" and existing.get("has_breakdown") and not stale_ai_result and not force:
            skipped += 1
            continue
        try:
            stock_rows = [
                item for item in individual_rows
                if str(item.get("stock_code", "")).zfill(6) == stock_code
            ][:10]
            stock_result = analyze_news_items(stock_rows, empty_reason="当日未匹配到相关新闻")
            ind_keywords = industry_keywords(stock_code, stock_name)
            ind_news_items = filter_keyword_news(industry_pool_df, ind_keywords, limit=10)
            industry_rows = [
                {
                    "title": item.get("title", ""),
                    "summary": item.get("content", ""),
                    "url": "",
                    "published_time": item.get("datetime", ""),
                }
                for item in ind_news_items
            ]
            industry_result = analyze_news_items(industry_rows, empty_reason="当日未匹配到行业相关新闻")
            final_score, weights = combine_scores(
                stock_result["score"],
                industry_result["score"],
                macro_cn_score,
                macro_global_score,
                has_stock_news=bool(stock_rows),
            )
            analysis_reason = stock_result["reason"]
            if not stock_rows:
                analysis_reason = "当日无个股新闻，综合情绪仅反映宏观市场环境"
            upsert_news_sentiment({
                "stock_code": stock_code,
                "trade_date": trade_date,
                "news_count": len(stock_rows),
                "sentiment": score_to_label(final_score),
                "sentiment_score": stock_result["score"],
                "news_titles": stock_result["titles"],
                "analysis_reason": analysis_reason,
                "macro_sentiment_cn": macro_cn_score,
                "macro_sentiment_global": macro_global_score,
                "industry_sentiment": industry_result["score"],
                "final_sentiment_score": final_score,
                "score_breakdown": {
                    "stock": {"score": stock_result["score"], "sentiment": stock_result["sentiment"], "count": len(stock_rows)},
                    "industry": {"score": industry_result["score"], "sentiment": industry_result["sentiment"], "count": len(industry_rows), "keywords": ind_keywords},
                    "macro_cn": {"score": macro_cn_score, "sentiment": macro_cn_result["sentiment"], "count": len(macro_cn_rows)},
                    "market": {"score": macro_cn_score, "sentiment": macro_cn_result["sentiment"], "count": len(macro_cn_rows)},
                    "macro_global": {"score": macro_global_score, "sentiment": macro_global_result["sentiment"], "count": len(macro_global_rows)},
                    "weights": weights,
                },
                "created_at": now_text(),
            })
            analyzed += 1
            if not existing:
                saved += 1
        except Exception as exc:
            failed += 1
            logger.exception("%s %s 免费新闻情绪分析失败: %s", stock_code, stock_name, exc)

    return {
        "ok": failed == 0,
        "saved": saved,
        "analyzed": analyzed,
        "skipped": skipped,
        "failed": failed,
        "raw_inserted": raw_inserted,
        "message": f"免费新闻情绪分析完成：新增 {saved}，分析 {analyzed}，跳过 {skipped}，失败 {failed}，原始新闻新增 {raw_inserted}",
    }


def get_saved_news_sentiment(stock_code, trade_date):
    return get_news_sentiment(stock_code, trade_date)
