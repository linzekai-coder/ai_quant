from strategy.score import calculate_score, get_stock_name, score_level

stocks = [
    "000001",
    "600519",
    "000858",
    "002415"
]

for stock in stocks:
    score = calculate_score(stock)
    name = get_stock_name(stock)
    print(stock, name, score, score_level(score))
