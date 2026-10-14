"""Germany / DACH (plus some French issuers): EQS News, the main German regulatory newswire.

German takeover law (WpÜG) requires a bidder to publish its decision to make an offer
(§10 WpÜG) or its attainment of control (§35 WpÜG) - these appear on EQS as "EQS-WpÜG"
announcements. Ad-hoc (MAR Art. 17) releases from targets also appear here.

We read the server-rendered latest-news list on the EQS News homepage (about 30 newest items),
so polling every 1-2 minutes is needed to avoid gaps at busy times.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Item
from ..net import get, page_text

log = logging.getLogger(__name__)

URLS = ["https://www.eqs-news.com/", "https://www.eqs-news.com/de/"]
_LINK_RE = re.compile(r"^https://www\.eqs-news\.com/(?:de/)?news/([a-z0-9\-]+)/[^/]+/([0-9a-f\-]{36})_([a-z]{2})/?$")
# Categories worth a look; everything else (voting rights, directors' dealings...) is noise for us.
CATEGORIES = {"wpueg", "wp-g", "ad-hoc", "corporate", "fr-regulatory", "regulatory", "takeover", "adhoc"}
MNA_CATEGORIES = {"wpueg", "takeover"}


def parse(html_text: str) -> list[Item]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[Item] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = _LINK_RE.match(a["href"])
        if not m:
            continue
        cat, uuid, lang = m.groups()
        if uuid in seen:
            continue
        seen.add(uuid)
        if cat not in CATEGORIES and not cat.startswith("wp"):
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        items.append(Item(
            source="eqs",
            uid=uuid,
            title=title,
            url=a["href"],
            text=title,
            market="EU",
            meta={"category": cat, "lang": lang,
                  "mna_feed": cat in MNA_CATEGORIES or cat.startswith("wp"),
                  "auto_candidate": cat in MNA_CATEGORIES or cat.startswith("wp")},
        ))
    return items


def fetch(settings) -> list[Item]:
    out: list[Item] = []
    for url in URLS:
        try:
            r = get(url, min_interval=1)
            out.extend(parse(r.text))
        except Exception as e:  # pragma: no cover - network
            log.warning("EQS %s failed: %s", url, e)
    uniq = {i.uid: i for i in out}
    return list(uniq.values())


def full_text(item: Item, settings) -> str:
    return page_text(item.url, limit=15000) or item.text
