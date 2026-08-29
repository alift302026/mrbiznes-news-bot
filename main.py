import logging
import os

from telegram.ext import Application

from app.engines.news.arzdigital_breaking_worker import (
    arzdigital_breaking_job,
)
from app.engines.news.wallex_news_worker import (
    wallex_news_job,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("MrBiznesNews")


def main():
    token = os.getenv("BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError("BOT_TOKEN is missing")

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    if application.job_queue is None:
        raise RuntimeError("Telegram JobQueue is unavailable")

    application.job_queue.run_repeating(
        arzdigital_breaking_job,
        interval=3600,
        first=30,
        name="arzdigital-news-worker",
    )

    application.job_queue.run_repeating(
        wallex_news_job,
        interval=3600,
        first=45,
        name="wallex-news-worker",
    )

    logger.info("ArzDigital News Worker: ON")
    logger.info("Wallex News Worker: ON")
    logger.info("MrBiznes News Bot: RUNNING")

    application.run_polling()


if __name__ == "__main__":
    main()