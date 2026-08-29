import html
import logging
import os
import re
import xml.etree.ElementTree as ET

from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from telegram.ext import ContextTypes


logger = logging.getLogger(__name__)


RSS_URL = "https://arzdigital.com/breaking/feed/"

CHANNEL_USERNAME = "@MrBiznesMarket"

REQUEST_TIMEOUT = 20
MAX_ITEMS_PER_RUN = 5

# Telegram photo captions are limited, so keep the
# extracted paragraph reasonably compact.
MAX_PARAGRAPH_LENGTH = 700


_last_seen_link: Optional[str] = None
_initialized = False


# ============================================================
# CHANNEL
# ============================================================

def _channel_id() -> Optional[int]:

    value = os.getenv(
        "NEWS_CHANNEL_ID",
        "",
    ).strip()

    if not value:
        return None

    try:
        return int(value)

    except ValueError:
        return None


# ============================================================
# TEXT
# ============================================================

def _clean_text(
    value: Optional[str],
) -> str:

    if not value:
        return ""

    text = html.unescape(
        str(value)
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def _complete_sentences(
    text: str,
    limit: int = MAX_PARAGRAPH_LENGTH,
) -> str:
    """
    Keep source text only through a complete
    sentence. Never invent or complete text.
    """

    text = _clean_text(
        text
    )

    if not text:
        return ""

    # If short enough and already ends properly,
    # return the complete source paragraph.
    if (
        len(text) <= limit
        and text.endswith(
            (".", "؟", "!")
        )
    ):
        return text

    candidate = text[
        :limit
    ].strip()

    last_end = max(
        candidate.rfind("."),
        candidate.rfind("؟"),
        candidate.rfind("!"),
    )

    if last_end >= 0:
        return candidate[
            :last_end + 1
        ].strip()

    # We do not publish incomplete text.
    return ""


def _short_summary(
    value: Optional[str],
) -> str:
    """
    RSS fallback only.

    ArzDigital RSS descriptions may already
    be truncated. Never expose a truncated
    sentence.
    """

    text = _clean_text(
        value
    )

    if not text:
        return ""

    if re.search(
        r"(?:\.{3}|…)\s*$",
        text,
    ):
        return ""

    return _complete_sentences(
        text
    )


# ============================================================
# XML
# ============================================================

def _find_text(
    element: ET.Element,
    names: List[str],
) -> str:

    wanted = {
        name.lower()
        for name in names
    }

    for child in element:

        tag = child.tag.split(
            "}"
        )[-1].lower()

        if tag in wanted:
            return (
                child.text
                or ""
            )

    return ""


# ============================================================
# ARTICLE PAGE
# ============================================================

def _fetch_article_data(
    article_url: str,
) -> Dict[str, Optional[str]]:
    """
    Fetch the original ArzDigital page once.

    Extract:
    - first real article paragraph
    - og:image
    """

    result: Dict[
        str,
        Optional[str]
    ] = {
        "paragraph": None,
        "image_url": None,
    }

    if not article_url:
        return result

    try:

        response = requests.get(
            article_url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; "
                    "Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/120 Safari/537.36"
                ),
            },
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------
        # IMAGE
        # ----------------------------

        og_image = soup.find(
            "meta",
            attrs={
                "property": "og:image",
            },
        )

        if og_image:

            image_url = (
                og_image.get(
                    "content"
                )
                or ""
            ).strip()

            if image_url.startswith(
                (
                    "http://",
                    "https://",
                )
            ):
                result[
                    "image_url"
                ] = image_url

        # ----------------------------
        # FIRST ARTICLE PARAGRAPH
        # ----------------------------

        # In current ArzDigital Breaking
        # pages, the first <p> is the actual
        # opening paragraph. We still filter
        # obvious site boilerplate.
        for paragraph in soup.find_all(
            "p"
        ):

            text = _clean_text(
                paragraph.get_text(
                    " ",
                    strip=True,
                )
            )

            if not text:
                continue

            if len(text) < 80:
                continue

            lowered = text.lower()

            ignored = (
                "لطفا در صورت مشاهده دیدگاه",
                "قیمت بیت کوین، اتریوم",
                "مجموعه ارزدیجیتال",
                "مسئولیت کامل تمامی معاملات",
            )

            if any(
                phrase in lowered
                for phrase in ignored
            ):
                continue

            complete = (
                _complete_sentences(
                    text
                )
            )

            if complete:

                result[
                    "paragraph"
                ] = complete

                break

    except Exception:

        logger.exception(
            "Article page fetch failed: %s",
            article_url,
        )

    return result


# ============================================================
# RSS IMAGE FALLBACK
# ============================================================

def _extract_rss_image(
    item_element: ET.Element,
    description: str,
) -> Optional[str]:

    for child in item_element:

        tag = child.tag.split(
            "}"
        )[-1].lower()

        if tag in {
            "thumbnail",
            "content",
            "enclosure",
        }:

            url = (
                child.attrib.get(
                    "url",
                    "",
                )
                or child.attrib.get(
                    "href",
                    "",
                )
            ).strip()

            if url.startswith(
                (
                    "http://",
                    "https://",
                )
            ):
                return url

    match = re.search(
        (
            r'<img[^>]+'
            r'src=["\']([^"\']+)["\']'
        ),
        description or "",
        flags=re.IGNORECASE,
    )

    if match:

        url = html.unescape(
            match.group(1)
        ).strip()

        if url.startswith(
            (
                "http://",
                "https://",
            )
        ):
            return url

    return None


# ============================================================
# HASHTAGS
# ============================================================

def _hashtags(
    title: str,
    paragraph: str,
) -> List[str]:

    text = (
        f"{title} {paragraph}"
    ).lower()

    rules = [
        (
            (
                "bitcoin",
                "btc",
                "بیت کوین",
                "بیت‌کوین",
            ),
            "#بیت_کوین",
        ),
        (
            (
                "ethereum",
                "eth",
                "اتریوم",
            ),
            "#اتریوم",
        ),
        (
            (
                "xrp",
                "ripple",
                "ریپل",
            ),
            "#ریپل",
        ),
        (
            (
                "solana",
                "sol",
                "سولانا",
            ),
            "#سولانا",
        ),
        (
            (
                "usdt",
                "tether",
                "تتر",
            ),
            "#تتر",
        ),
        (
            (
                "dogecoin",
                "doge",
                "دوج کوین",
                "دوج‌کوین",
            ),
            "#دوج_کوین",
        ),
        (
            (
                "gold",
                "طلا",
            ),
            "#طلا",
        ),
        (
            (
                "oil",
                "brent",
                "wti",
                "نفت",
            ),
            "#نفت",
        ),
        (
            (
                "fed",
                "federal reserve",
                "powell",
                "فدرال رزرو",
                "پاول",
            ),
            "#فدرال_رزرو",
        ),
        (
            (
                "inflation",
                "cpi",
                "ppi",
                "تورم",
            ),
            "#تورم",
        ),
        (
            (
                "interest rate",
                "نرخ بهره",
            ),
            "#نرخ_بهره",
        ),
        (
            (
                "trump",
                "ترامپ",
            ),
            "#ترامپ",
        ),
        (
            (
                "sanction",
                "تحریم",
            ),
            "#تحریم",
        ),
        (
            (
                "exchange",
                "binance",
                "صرافی",
                "بایننس",
            ),
            "#صرافی",
        ),
        (
            (
                "hack",
                "exploit",
                "هک",
                "حمله",
            ),
            "#امنیت",
        ),
        (
            (
                "defi",
                "دیفای",
            ),
            "#دیفای",
        ),
        (
            (
                "etf",
                "صندوق قابل معامله",
            ),
            "#ETF",
        ),
        (
            (
                "forex",
                "فارکس",
            ),
            "#فارکس",
        ),
        (
            (
                "gdp",
                "unemployment",
                "tariff",
                "اقتصاد",
                "بیکاری",
                "تعرفه",
            ),
            "#اقتصاد",
        ),
    ]

    output: List[str] = []

    for keywords, hashtag in rules:

        if any(
            keyword in text
            for keyword in keywords
        ):

            if hashtag not in output:
                output.append(
                    hashtag
                )

        if len(output) >= 2:
            break

    for fallback in (
        "#کریپتو",
        "#اقتصاد",
    ):

        if len(output) >= 2:
            break

        if fallback not in output:
            output.append(
                fallback
            )

    return output[:2]


# ============================================================
# RSS
# ============================================================

def _fetch_feed() -> List[
    Dict[str, Any]
]:

    response = requests.get(
        RSS_URL,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; "
                "MrBiznesNewsBot/1.0)"
            ),
        },
    )

    response.raise_for_status()

    root = ET.fromstring(
        response.content
    )

    items: List[
        Dict[str, Any]
    ] = []

    for element in root.findall(
        ".//item"
    ):

        title = _clean_text(
            _find_text(
                element,
                ["title"],
            )
        )

        link = _clean_text(
            _find_text(
                element,
                ["link"],
            )
        )

        description_raw = (
            _find_text(
                element,
                [
                    "description",
                    "encoded",
                ],
            )
        )

        rss_summary = _clean_text(
            description_raw
        )

        if (
            not title
            or not link
        ):
            continue

        rss_image = (
            _extract_rss_image(
                element,
                description_raw,
            )
        )

        items.append(
            {
                "title": title,
                "link": link,
                "summary": rss_summary,
                "image_url": rss_image,
            }
        )

    return items


# ============================================================
# ENRICH ARTICLE
# ============================================================

def _enrich_item(
    item: Dict[str, Any],
) -> Dict[str, Any]:

    enriched = dict(
        item
    )

    article_data = (
        _fetch_article_data(
            enriched.get(
                "link",
                "",
            )
        )
    )

    paragraph = article_data.get(
        "paragraph"
    )

    if paragraph:

        enriched[
            "summary"
        ] = paragraph

    else:

        enriched[
            "summary"
        ] = _short_summary(
            enriched.get(
                "summary"
            )
        )

    article_image = (
        article_data.get(
            "image_url"
        )
    )

    if article_image:

        enriched[
            "image_url"
        ] = article_image

    return enriched


# ============================================================
# FORMAT
# ============================================================

def _format_message(
    item: Dict[str, Any],
) -> str:

    title = _clean_text(
        item.get(
            "title"
        )
    )

    paragraph = _clean_text(
        item.get(
            "summary"
        )
    )

    tags = _hashtags(
        title,
        paragraph,
    )

    parts = [
        "🚨 فوری | نبض بازار",
        "",
        f"📰 {title}",
    ]

    if paragraph:

        parts.extend(
            [
                "",
                f"📌 {paragraph}",
            ]
        )

    parts.extend(
        [
            "",
            " ".join(tags),
            "",
            f"☕️ {CHANNEL_USERNAME}",
        ]
    )

    return "\n".join(
        parts
    )


# ============================================================
# TELEGRAM
# ============================================================

async def _send_item(
    context: ContextTypes.DEFAULT_TYPE,
    channel_id: int,
    item: Dict[str, Any],
) -> None:

    enriched = _enrich_item(
        item
    )

    text = _format_message(
        enriched
    )

    image_url = enriched.get(
        "image_url"
    )

    if image_url:

        try:

            await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_url,
                caption=text,
            )

            return

        except Exception:

            logger.exception(
                "News photo send failed; "
                "falling back to text"
            )

    await context.bot.send_message(
        chat_id=channel_id,
        text=text,
        disable_web_page_preview=True,
    )


# ============================================================
# JOB
# ============================================================

async def arzdigital_breaking_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    global _initialized
    global _last_seen_link

    channel_id = _channel_id()

    if channel_id is None:

        logger.warning(
            "NEWS_CHANNEL_ID is "
            "missing or invalid"
        )

        return

    try:

        items = _fetch_feed()

    except Exception:

        logger.exception(
            "Breaking RSS fetch failed"
        )

        return

    if not items:
        return

    newest_link = items[0][
        "link"
    ]

    # First run only establishes baseline.
    if not _initialized:

        _last_seen_link = (
            newest_link
        )

        _initialized = True

        logger.info(
            "Breaking News baseline "
            "initialized: %s",
            newest_link,
        )

        return

    # Nothing new.
    if (
        newest_link
        == _last_seen_link
    ):
        return

    new_items: List[
        Dict[str, Any]
    ] = []

    baseline_found = False

    for item in items:

        if (
            item["link"]
            == _last_seen_link
        ):

            baseline_found = True
            break

        new_items.append(
            item
        )

    # Old baseline disappeared from the
    # 20-item feed. Refresh safely rather
    # than flooding old articles.
    if not baseline_found:

        _last_seen_link = (
            newest_link
        )

        logger.warning(
            "Previous RSS baseline not "
            "found; baseline refreshed"
        )

        return

    new_items = new_items[
        :MAX_ITEMS_PER_RUN
    ]

    # RSS newest first.
    # Publish oldest new item first.
    for item in reversed(
        new_items
    ):

        try:

            await _send_item(
                context,
                channel_id,
                item,
            )

        except Exception:

            logger.exception(
                "Breaking News send "
                "failed: %s",
                item.get(
                    "link"
                ),
            )

    _last_seen_link = (
        newest_link
    )

    logger.info(
        "Breaking News sent: %d",
        len(new_items),
    )