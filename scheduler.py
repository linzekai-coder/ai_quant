import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from news.free_news_sentiment import analyze_watchlist_news
from utils.logger import logger


def run_news_sentiment_job():
    result = analyze_watchlist_news()
    logger.info("定时新闻情绪分析结果: %s", result)
    return result


def create_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_news_sentiment_job,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone="Asia/Shanghai"),
        id="stock_news_sentiment_daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def main():
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("新闻情绪定时任务已启动：交易日 18:00")
    print("新闻情绪定时任务已启动：交易日 18:00，按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("新闻情绪定时任务已停止")


if __name__ == "__main__":
    main()
