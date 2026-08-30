"""Market snapshot worker for MrBiznes News Bot.

Every few hours posts a clean price card to the channel:
- Crypto summary: BTC dominance, global market cap, USDT/TMN
- Iran gold & coin prices (toman only)

Sources:
- CoinGecko public API  (dominance, market cap)
- Nobitex public API    (USDT to rial -> toman)
- arzdigital.com/gold/  (gold & coin toman prices)

The worker never crashes on a single failed source; it only
omits that section and logs a warning instead.
"""

import logging
import os
import re

from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from telegram.ext import (
    ContextTypes,
)


logger = logging.getLogger("MrBiznesNews")

CHANNEL_USERNAME = "@MrBiznesMarket"

COINGECKO_GLOBAL_URL = (
    "https://api.coingecko.com/api/v3/global"
)
NOBITEX_STATS_URL = (
    "https://apiv2.nobitex.ir/market/stats"
    "?srcCurrency=usdt&dstCurrency=rls"
)
ARZDIGITAL_GOLD_URL = (
    "https://arzdigital.com/gold/"
)

REQUEST_TIMEOUT = 25
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; MrBiznesNewsBot/1.0)"
    )
}

_last_pinned_message_id: Optional[int] = None

# Bubbles first: their names also contain the
# base asset names, so they must match earlier.
GOLD_ITEMS: List[Dict[str, str]] = [
    {
        "match": "حباب طلای ۱۸",
        "label": "حباب طلای ۱۸ عیار",
    },
    {
        "match": "حباب نیم سکه",
        "label": "حباب نیم سکه بهار آزادی",
    },
    {
        "match": "حباب ربع سکه",
        "label": "حباب ربع سکه بهار آزادی",
    },
    {
        "match": "حباب سکه امامی",
        "label": "حباب سکه امامی",
    },
    {
        "match": "حباب سکه گرمی",
        "label": "حباب سکه گرمی",
    },
    {
        "match": "حباب سکه بهار آزادی",
        "label": "حباب سکه بهار آزادی",
    },
    {
        "match": "طلای ۱۸",
        "label": "طلای ۱۸عیار",
    },
    {
        "match": "اونس طلا",
        "label": "اونس طلا",
    },
    {
        "match": "مثقال طلای آبشده",
        "label": "مثقال طلای آبشده",
    },
    {
        "match": "ربع سکه",
        "label": "ربع سکه بهار آزادی",
    },
    {
        "match": "سکه امامی",
        "label": "سکه امامی",
    },
    {
        "match": "نیم سکه",
        "label": "نیم سکه بهار آزادی",
    },
    {
        "match": "سکه تمام بهار آزادی",
        "label": "سکه تمام بهار آزادی",
    },
    {
        "match": "سکه گرمی",
        "label": "سکه گرمی",
    },
]

# A toman price looks like ۲۱,۷۶۴,۶۲۰ ت
_PRICE_RE = re.compile(
    r"([0-9۰-۹٠-٩]{1,3}"
    r"(?:[,٬][0-9۰-۹٠-٩]{3})+)"
    r"\s*ت"
)

_FA_DIGITS = str.maketrans(
    {
        "0": "۰", "1": "۱", "2": "۲",
        "3": "۳", "4": "۴", "5": "۵",
        "6": "۶", "7": "۷", "8": "۸",
        "9": "۹", ",": "٬",
    }
)


def _fa_num(value: str) -> str:
    """Convert ASCII digits/commas to Persian."""
    return value.translate(_FA_DIGITS)


def _normalize(text: str) -> str:
    return (
        text.replace("\u200c", "")
        .replace(" ", "")
        .replace("‌", "")
    )


def _fetch_json(url: str) -> Any:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    return response.json()


def _crypto_lines() -> List[str]:
    lines: List[str] = []

    try:
        payload = _fetch_json(COINGECKO_GLOBAL_URL)
        data = payload["data"]
        btc_dom = float(
            data["market_cap_percentage"]["btc"]
        )
        mcap_usd = float(
            data["total_market_cap"]["usd"]
        )
        lines.append(
            "🪙 دامیننس بیت‌کوین: "
            + _fa_num(f"{btc_dom:.2f}")
            + "٪"
        )
        lines.append(
            "🌐 ارزش بازار: "
            + _fa_num(f"{mcap_usd / 1e12:.2f}")
            + " تریلیون دلار"
        )
    except Exception:
        logger.warning(
            "Crypto summary fetch failed",
            exc_info=True,
        )

    try:
        payload = _fetch_json(NOBITEX_STATS_URL)
        latest_rial = float(
            payload["stats"]["usdt-rls"]["latest"]
        )
        toman = int(round(latest_rial / 10.0))
        lines.append(
            "💵 قیمت تتر: "
            + _fa_num(f"{toman:,}")
            + " تومان"
        )
    except Exception:
        logger.warning(
            "Tether price fetch failed",
            exc_info=True,
        )

    return lines


def _extract_price(text: str) -> Optional[str]:
    match = _PRICE_RE.search(text)
    if match:
        return match.group(1)
    return None


def _gold_rows(html_text: str) -> List[str]:
    soup = BeautifulSoup(html_text, "html.parser")

    units: List[str] = []
    rows = soup.find_all("tr")
    if rows:
        units = [
            row.get_text(" ", strip=True)
            for row in rows
        ]
    else:
        for element in soup.find_all(
            ["li", "div", "article"]
        ):
            text = element.get_text(" ", strip=True)
            if 0 < len(text) <= 250:
                units.append(text)

    normalized_units = [
        (unit, _normalize(unit)) for unit in units
    ]

    results: Dict[str, str] = {}

    for item in GOLD_ITEMS:
        needle = _normalize(item["match"])
        label = item["label"]
        if label in results:
            continue
        for unit, normalized in normalized_units:
            if needle not in normalized:
                continue
            price = _extract_price(unit)
            if price:
                results[label] = price
                break

    ordered: List[str] = []
    display_order = [
        "طلای ۱۸عیار",
        "اونس طلا",
        "مثقال طلای آبشده",
        "حباب طلای ۱۸ عیار",
        "ربع سکه بهار آزادی",
        "سکه امامی",
        "نیم سکه بهار آزادی",
        "سکه تمام بهار آزادی",
        "سکه گرمی",
        "حباب سکه بهار آزادی",
        "حباب سکه گرمی",
        "حباب نیم سکه بهار آزادی",
        "حباب ربع سکه بهار آزادی",
        "حباب سکه امامی",
    ]
    for label in display_order:
        if label in results:
            ordered.append(
                "🔸 "
                + label
                + ": "
                + _fa_num(results[label])
                + " ت"
            )

    return ordered


def _gold_lines() -> List[str]:
    response = requests.get(
        ARZDIGITAL_GOLD_URL,
        timeout=REQUEST_TIMEOUT,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    return _gold_rows(response.text)


async def market_snapshot_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    global _last_pinned_message_id

    raw_channel_id = os.getenv(
        "NEWS_CHANNEL_ID", ""
    ).strip()

    if not raw_channel_id:
        logger.error(
            "NEWS_CHANNEL_ID is missing"
        )
        return

    channel_id = int(raw_channel_id)

    crypto_lines = _crypto_lines()

    gold_lines: List[str] = []
    try:
        gold_lines = _gold_lines()
    except Exception:
        logger.warning(
            "Gold page fetch failed",
            exc_info=True,
        )

    if not crypto_lines and not gold_lines:
        logger.error(
            "Market snapshot skipped; "
            "no source returned data"
        )
        return

    parts = [
        "📊 جدول قیمت‌ها | نبض بازار",
        "",
    ]

    if crypto_lines:
        parts.extend(crypto_lines)
        parts.append("")

    if gold_lines:
        parts.append("💰 طلا و سکه")
        parts.append("")
        parts.extend(gold_lines)
        parts.append("")

    parts.append("☕️ " + CHANNEL_USERNAME)

    text = "\n".join(parts)

    message = await context.bot.send_message(
        chat_id=channel_id,
        text=text,
        disable_web_page_preview=True,
    )

    logger.info(
        "Market snapshot posted (%s gold rows)",
        len(gold_lines),
    )

    # Keep the latest card pinned
    if _last_pinned_message_id is not None:
        try:
            await context.bot.unpin_chat_message(
                chat_id=channel_id,
                message_id=(
                    _last_pinned_message_id
                ),
            )
        except Exception:
            logger.info(
                "Previous pinned snapshot "
                "could not be unpinned"
            )

    try:
        await context.bot.pin_chat_message(
            chat_id=channel_id,
            message_id=message.message_id,
            disable_notification=True,
        )
        _last_pinned_message_id = (
            message.message_id
        )
        logger.info(
            "Market snapshot pinned"
        )
    except Exception:
        logger.warning(
            "Pinning snapshot failed",
            exc_info=True,
        )
