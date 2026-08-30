import asyncio
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

# Request URLs contain the bot token, so keep httpx quiet in logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("MrBiznesNews")


async def main():
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

    # Start the application WITHOUT polling for updates.
    # This service only sends outbound news messages, so it
    # never calls getUpdates. This avoids 409 Conflict with
    # any other bot or deployment using the same BOT_TOKEN.
    await application.initialize()
    await application.start()

    logger.info("MrBiznes News Bot: RUNNING")

    try:
        # Keep the service alive forever; the JobQueue keeps
        # checking for fresh news in the background.
        await asyncio.Event().wait()
    finally:
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
