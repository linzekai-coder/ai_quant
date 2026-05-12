from datetime import datetime
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from database.db import save_score
from strategy.score import calculate_score, get_stock_name, score_level


stocks = [
    "000001",
    "600519",
    "000858",
    "002415"
]

results = []

for stock in stocks:
    score = calculate_score(stock)
    level = score_level(score)
    name = get_stock_name(stock)

    results.append({
        "stock": stock,
        "name": name,
        "score": score,
        "level": level
    })

    save_score(stock, score, level)

results = sorted(results, key=lambda x: x["score"], reverse=True)

report = []

report.append(f"AI投资日报 - {datetime.now().strftime('%Y-%m-%d')}")
report.append("=" * 40)
report.append("今日重点关注")
report.append("-" * 20)

top3 = results[:3]
for idx, item in enumerate(top3, 1):
    report.append(f"{idx}. {item['stock']} {item['name']}（{item['score']}）")
    report.append(f"建议: {item['level']}")

report.append("=" * 40)
report.append("推荐股票排行榜")
report.append("-" * 20)

for idx, item in enumerate(results, 1):
    reason = ""

    if item["score"] >= 80:
        reason = "趋势强，风险低，市场情绪积极"
    elif item["score"] >= 60:
        reason = "具备一定机会，可持续观察"
    else:
        reason = "当前风险偏高"

    report.append(f"{idx}. {item['stock']} {item['name']}")
    report.append(f"评分: {item['score']}")
    report.append(f"建议: {item['level']}")
    report.append(f"原因: {reason}")
    report.append("-" * 20)

content = "\n".join(report)

output_dir = os.path.join(PROJECT_ROOT, "report", "output")
os.makedirs(output_dir, exist_ok=True)

filename = os.path.join(output_dir, f"report_{datetime.now().strftime('%Y%m%d')}.txt")

with open(filename, "w", encoding="utf-8") as f:
    f.write(content)

print(content)
print(f"\n日报已保存: {filename}")
