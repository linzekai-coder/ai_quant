DEFAULT_STOCK_POOL = [
    {"code": "000001", "market_code": "sz.000001", "name": "平安银行", "industry": "金融", "market_cap": "大盘"},
    {"code": "600519", "market_code": "sh.600519", "name": "贵州茅台", "industry": "消费", "market_cap": "大盘"},
    {"code": "000858", "market_code": "sz.000858", "name": "五粮液", "industry": "消费", "market_cap": "大盘"},
    {"code": "002415", "market_code": "sz.002415", "name": "海康威视", "industry": "科技", "market_cap": "大盘"},
    {"code": "600036", "market_code": "sh.600036", "name": "招商银行", "industry": "金融", "market_cap": "大盘"},
    {"code": "600030", "market_code": "sh.600030", "name": "中信证券", "industry": "金融", "market_cap": "大盘"},
    {"code": "300750", "market_code": "sz.300750", "name": "宁德时代", "industry": "新能源", "market_cap": "大盘"},
    {"code": "002594", "market_code": "sz.002594", "name": "比亚迪", "industry": "新能源", "market_cap": "大盘"},
    {"code": "601012", "market_code": "sh.601012", "name": "隆基绿能", "industry": "新能源", "market_cap": "大盘"},
    {"code": "000333", "market_code": "sz.000333", "name": "美的集团", "industry": "制造", "market_cap": "大盘"},
    {"code": "300760", "market_code": "sz.300760", "name": "迈瑞医疗", "industry": "医疗", "market_cap": "大盘"},
    {"code": "002475", "market_code": "sz.002475", "name": "立讯精密", "industry": "科技", "market_cap": "中盘"},
    {"code": "000063", "market_code": "sz.000063", "name": "中兴通讯", "industry": "科技", "market_cap": "大盘"},
    {"code": "002230", "market_code": "sz.002230", "name": "科大讯飞", "industry": "科技", "market_cap": "中盘"},
    {"code": "300124", "market_code": "sz.300124", "name": "汇川技术", "industry": "制造", "market_cap": "中盘"},
    {"code": "600276", "market_code": "sh.600276", "name": "恒瑞医药", "industry": "医疗", "market_cap": "大盘"},
    {"code": "601318", "market_code": "sh.601318", "name": "中国平安", "industry": "金融", "market_cap": "大盘"},
    {"code": "601166", "market_code": "sh.601166", "name": "兴业银行", "industry": "金融", "market_cap": "大盘"},
    {"code": "600309", "market_code": "sh.600309", "name": "万华化学", "industry": "材料", "market_cap": "大盘"},
    {"code": "600887", "market_code": "sh.600887", "name": "伊利股份", "industry": "消费", "market_cap": "大盘"},
    {"code": "300059", "market_code": "sz.300059", "name": "东方财富", "industry": "金融", "market_cap": "大盘"},
    {"code": "002352", "market_code": "sz.002352", "name": "顺丰控股", "industry": "物流", "market_cap": "中盘"},
]

STOCK_METADATA = {
    item["code"]: {
        "industry": item.get("industry", "未分类"),
        "market_cap": item.get("market_cap", "未分类"),
    }
    for item in DEFAULT_STOCK_POOL
}


def get_market_code(stock_code):
    code = str(stock_code).zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"sh.{code}"
    return f"sz.{code}"


def get_stock_pool(user_id=1):
    pool = []
    existing_codes = set()

    def append_stock(code, name, industry=None, market_cap=None, source="默认"):
        code = str(code).zfill(6)
        if code in existing_codes:
            return
        metadata = STOCK_METADATA.get(code, {})
        pool.append({
            "code": code,
            "market_code": get_market_code(code),
            "name": name,
            "industry": industry or metadata.get("industry", "未分类"),
            "market_cap": market_cap or metadata.get("market_cap", "未分类"),
            "source": source,
        })
        existing_codes.add(code)

    try:
        from database.watchlist import list_watchlist

        rows = list_watchlist(enabled_only=True, user_id=user_id)
    except Exception:
        rows = []

    for row in rows:
        code = row["stock_code"]
        metadata = STOCK_METADATA.get(code, {})
        append_stock(
            code,
            row["stock_name"],
            industry=metadata.get("industry", "自选"),
            market_cap=metadata.get("market_cap", "未分类"),
            source="自选",
        )

    try:
        from database.dynamic_pool import list_dynamic_pool

        dynamic_rows = list_dynamic_pool(limit=20)
    except Exception:
        dynamic_rows = []

    for item in dynamic_rows:
        code = str(item["ts_code"]).split(".", 1)[0]
        metadata = STOCK_METADATA.get(code, {})
        basic = None
        try:
            from data.tushare_provider import find_stock

            basic = find_stock(code)
        except Exception:
            basic = None
        append_stock(
            code,
            item["stock_name"],
            industry=metadata.get("industry") or (basic or {}).get("industry") or "未分类",
            market_cap=metadata.get("market_cap") or (basic or {}).get("market") or "未分类",
            source="扫描",
        )

    if not dynamic_rows and not pool:
        for item in DEFAULT_STOCK_POOL:
            append_stock(
                item["code"],
                item["name"],
                industry=item.get("industry", "未分类"),
                market_cap=item.get("market_cap", "未分类"),
                source="默认",
            )

    return pool


STOCK_POOL = get_stock_pool()
STOCK_CODES = [item["code"] for item in STOCK_POOL]


def get_fetch_stocks():
    return {
        item["code"]: item["name"]
        for item in STOCK_POOL
    }
