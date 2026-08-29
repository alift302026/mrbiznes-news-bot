import logging
import os
from typing import Optional

from telegram.ext import ContextTypes

from app.engines.news.wallex_news_provider import fetch_wallex_news

logger = logging.getLogger(__name__)

_last_seen_link: Optional[str] = None
_initialized = False


async def wallex_news_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    global _last_seen_link, _initialized

    value = os.getenv(
        "NEWS_CHANNEL_ID",
        "",
    ).strip()

    if not value:
        logger.warning(
            "NEWS_CHANNEL_ID is missing"
        )
        return

    try:
        channel_id = int(value)
        items = fetch_wallex_news()
    except Exception:
        logger.exception(
            "Wallex RSS fetch failed"
        )
        return

    if not items:
        return

    newest_link = items[0]["link"]

    # First run establishes baseline only.
    if not _initialized:
        _last_seen_link = newest_link
        _initialized = True

        logger.info(
            "Wallex baseline initialized: %s",
            newest_link,
        )
        return

    if newest_link == _last_seen_link:
        return

    new_items = []
    baseline_found = False

    for item in items:
        if item["link"] == _last_seen_link:
            baseline_found = True
            break

        new_items.append(item)

    # Avoid flooding old articles.
    if not baseline_found:
        _last_seen_link = newest_link

        logger.warning(
            "Wallex baseline refreshed"
        )
        return

    new_items = new_items[:5]

    for item in reversed(new_items):
        text = (
            "🚨 فوری | نبض بازار\n\n"
            f"📰 {item['title']}\n\n"
            f"📌 {item.get('summary', '')[:700]}\n\n"
            "☕️ @MrBiznesMarket"
        )

        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=text,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                "Wallex news send failed: %s",
                item.get("link"),
            )

    _last_seen_link = newest_link

    logger.info(
        "Wallex News sent: %d",
        len(new_items),
    )