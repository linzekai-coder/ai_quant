"""
news/sentiment.py
AI 新闻情绪分析器（评分制）
依赖：openai（DeepSeek 兼容接口）

返回结构：
{
    "情绪": "利好" / "利空" / "中性",
    "评分": int(-100 ~ 100),
    "原因": "一句话原因"
}

用法：
    from news.sentiment import analyze_sentiment, batch_analyze
    result = analyze_sentiment("公司签订50亿战略合作协议")
    print(result)  # {'情绪':'利好', '评分':82, '原因':'...'}
"""

from openai import OpenAI
import os
import time
import re

# ========== DeepSeek 配置 ==========
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

MODEL = "deepseek-chat"   # 也可用 deepseek-reasoner


def analyze_sentiment(news_text: str, max_retry: int = 2) -> dict:
    """
    单条新闻情绪分析（评分制）
    返回：{
        "情绪": "利好" / "利空" / "中性",
        "评分": int(-100~100),
        "原因": str
    }
    """
    prompt = f"""你是一个专业的A股市场情绪分析助手。
请分析以下新闻对相关股票价格的短期影响。

新闻内容：
{news_text}

请严格按以下格式输出（三行，不要输出其他内容）：
情绪: 利好/利空/中性
评分: 整数（-100 到 100，利好为正，利空为负，中性接近0）
原因: 一句话说明评分理由"""

    for attempt in range(max_retry + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            text = response.choices[0].message.content.strip()
            return _parse_result(text)

        except Exception as e:
            if attempt < max_retry:
                time.sleep(1 * (attempt + 1))
                continue
            print(f"[ERROR] 情绪分析失败: {e}")
            return {"情绪": "未知", "评分": 0, "原因": str(e)}


def analyze_news(news_text: str) -> str:
    result = analyze_sentiment(news_text)
    return result.get("情绪", "未知")


def _parse_result(text: str) -> dict:
    """解析 AI 返回的三行格式"""
    result = {"情绪": "中性", "评分": 0, "原因": ""}

    # 情绪
    m = re.search(r"情绪\s*[:：]\s*(利好|利空|中性)", text)
    if m:
        result["情绪"] = m.group(1)

    # 评分（支持正负号）
    m = re.search(r"评分\s*[:：]\s*(-?\d+)", text)
    if m:
        score = int(m.group(1))
        result["评分"] = max(-100, min(100, score))

    # 原因
    m = re.search(r"原因\s*[:：]\s*(.+)", text, re.DOTALL)
    if m:
        result["原因"] = m.group(1).strip().replace("\n", " ")

    return result


def batch_analyze(news_list: list, delay: float = 0.5) -> list[dict]:
    """
    批量分析新闻列表
    news_list: [{"日期":..., "标题":..., "内容":...}, ...]
    返回: [{"日期":..., "标题":..., "情绪":..., "评分":..., "原因":...}, ...]
    """
    results = []
    total = len(news_list)
    for i, item in enumerate(news_list, 1):
        text = item.get("内容") or item.get("标题", "")
        r = analyze_sentiment(text)
        results.append({
            "日期": item.get("日期", ""),
            "标题": item.get("标题", ""),
            "情绪": r["情绪"],
            "评分": r["评分"],
            "原因": r["原因"],
        })
        sign = "+" if r["评分"] >= 0 else ""
        print(f"[{i}/{total}] {item.get('标题', '')[:30]}...  {r['情绪']} {sign}{r['评分']}")
        time.sleep(delay)
    return results


def analyze_csv(csv_path: str, text_col: str = "标题", date_col: str = "日期"):
    """
    直接分析 CSV 文件中的新闻
    CSV 需包含：日期列、文本列（标题或内容）
    输出：原路径_base_sentiment.csv（含情绪/评分/原因三列）
    """
    import pandas as pd
    df = pd.read_csv(csv_path)
    news_list = df[[date_col, text_col]].rename(
        columns={date_col: "日期", text_col: "标题"}
    ).to_dict("records")

    results = batch_analyze(news_list)

    result_df = pd.DataFrame(results)
    # 合并回原 df（按索引）
    out = pd.concat([df.reset_index(drop=True), result_df[["情绪","评分","原因"]]], axis=1)
    output_path = csv_path.replace(".csv", "_sentiment.csv")
    out.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n结果已保存至: {output_path}")
    return out


def sentiment_stats(result_df):
    """统计情绪分布 & 平均评分"""
    import pandas as pd
    print("\n=== 情绪分布统计 ===")
    counts = result_df["情绪"].value_counts()
    for k, v in counts.items():
        print(f"  {k}: {v} 条 ({v/len(result_df)*100:.1f}%)")

    avg_score = result_df["评分"].mean()
    pos = result_df[result_df["评分"] > 0]["评分"].mean()
    neg = result_df[result_df["评分"] < 0]["评分"].mean()
    print(f"\n  总平均评分: {avg_score:+.1f}")
    if not pd.isna(pos):
        print(f"  利好平均评分: {pos:+.1f}")
    if not pd.isna(neg):
        print(f"  利空平均评分: {neg:+.1f}")
    return counts


if __name__ == "__main__":
    # 快速测试
    test_cases = [
        "某上市公司发布公告，成功签订50亿元战略合作协议",
        "公司高管集体减持，合计减持比例达5%",
        "今日天气晴朗，公司正常经营",
        "财报显示Q1净利润同比增长320%",
        "因涉嫌信息披露违规，公司被证监会立案调查",
    ]
    print("=== 情绪分析测试（评分制）===\n")
    for news in test_cases:
        r = analyze_sentiment(news)
        sign = "+" if r["评分"] >= 0 else ""
        print(f"新闻: {news}")
        print(f"  情绪: {r['情绪']}  评分: {sign}{r['评分']}  原因: {r['原因']}\n")
