import html
import re
import xml.etree.ElementTree as ET

from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup


WALLEX_RSS_URL = "https://news.wallex.ir/feed/"
REQUEST_TIMEOUT = 20


def _clean_text(value: str) -> str:
    if not value:
        return ""

    text = BeautifulSoup(
        html.unescape(value),
        "html.parser",
    ).get_text(
        " ",
        strip=True,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def _find_text(
    element: ET.Element,
    name: str,
) -> str:
    for child in element:
        if child.tag.split("}")[-1].lower() == name.lower():
            return child.text or ""

    return ""


def fetch_wallex_news() -> List[Dict[str, Any]]:
    response = requests.get(
        WALLEX_RSS_URL,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; MrBiznesNewsBot/1.0)"
            ),
        },
    )

    response.raise_for_status()

    root = ET.fromstring(
        response.content
    )

    output: List[
        Dict[str, Any]
    ] = []

    for element in root.findall(
        ".//item"
    ):
        title = _clean_text(
            _find_text(
                element,
                "title",
            )
        )

        link = _clean_text(
            _find_text(
                element,
                "link",
            )
        )

        description = _clean_text(
            _find_text(
                element,
                "description",
            )
        )

        if not title or not link:
            continue

        output.append(
            {
                "source": "wallex",
                "title": title,
                "link": link,
                "summary": description,
            }
        )

    return output