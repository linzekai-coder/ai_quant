import os
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from dynamic_pool_pipeline import run_dynamic_pool_scan
from news.free_news_sentiment import analyze_watchlist_news
from utils.logger import logger


LOCK_PATH = Path("logs") / "scheduler.lock"
_LOCK_HANDLE = None


def acquire_scheduler_lock():
    global _LOCK_HANDLE
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        if hasattr(os, "lockf"):
            os.lockf(_LOCK_HANDLE.fileno(), os.F_TLOCK, 0)
            return True
    except Exception:
        pass
    try:
        import msvcrt

        msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except Exception:
        logger.info("定时任务锁已被其他进程持有，本进程不启动调度器")
        return False


def run_news_sentiment_job():
    result = analyze_watchlist_news()
    logger.info("定时新闻情绪分析结果: %s", result)
    return result


def run_dynamic_pool_scan_job():
    result = run_dynamic_pool_scan()
    logger.info("定时全市场股票池扫描结果: %s", result)
    return result


def create_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_dynamic_pool_scan_job,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone="Asia/Shanghai"),
        id="dynamic_pool_scan_daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
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
    if not acquire_scheduler_lock():
        print("定时任务已在其他进程运行，本进程退出")
        return
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("定时任务已启动：交易日 17:30 全市场扫描，18:00 新闻情绪")
    print("定时任务已启动：交易日 17:30 全市场扫描，18:00 新闻情绪，按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("新闻情绪定时任务已停止")


if __name__ == "__main__":
    main()
