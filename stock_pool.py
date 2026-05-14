STOCK_POOL = [
    {"code": "000001", "market_code": "sz.000001", "name": "平安银行"},
    {"code": "600519", "market_code": "sh.600519", "name": "贵州茅台"},
    {"code": "000858", "market_code": "sz.000858", "name": "五粮液"},
    {"code": "002415", "market_code": "sz.002415", "name": "海康威视"},
    {"code": "600036", "market_code": "sh.600036", "name": "招商银行"},
    {"code": "600030", "market_code": "sh.600030", "name": "中信证券"},
    {"code": "300750", "market_code": "sz.300750", "name": "宁德时代"},
    {"code": "002594", "market_code": "sz.002594", "name": "比亚迪"},
    {"code": "601012", "market_code": "sh.601012", "name": "隆基绿能"},
    {"code": "000333", "market_code": "sz.000333", "name": "美的集团"},
    {"code": "300760", "market_code": "sz.300760", "name": "迈瑞医疗"},
    {"code": "002475", "market_code": "sz.002475", "name": "立讯精密"},
]

STOCK_CODES = [item["code"] for item in STOCK_POOL]


def get_fetch_stocks():
    return {
        item["code"]: item["name"]
        for item in STOCK_POOL
    }
