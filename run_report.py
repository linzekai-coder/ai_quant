from report.daily_candidates import generate_daily_candidates
from report.daily_report import generate_daily_report
from report.score_validation import generate_validation_report
from news.free_news_sentiment import analyze_watchlist_news
from utils.logger import logger
from utils.notify import send_message


try:
    analyze_watchlist_news()
    generate_daily_candidates()
    generate_daily_report()
    generate_validation_report()
    print("日报生成完成")
except Exception as e:
    logger.error(str(e))
    send_message(f"系统错误: {e}")
    print(f"日报生成失败: {e}")
    raise
