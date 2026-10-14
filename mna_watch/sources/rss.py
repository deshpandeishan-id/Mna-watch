"""Press-release RSS feeds.

  euronext       Euronext company regulated news (Paris, Amsterdam, Brussels, Lisbon, Dublin, Oslo,
                 plus some Milan issuers). Each item carries the issuer's ISIN and a "topic"
                 such as "Takeover bids" or "Mergers, Acquisitions, Transfers".
  globenewswire  GlobeNewswire "Mergers and Acquisitions" subject feed (US + European regulatory news)
  prnewswire     PR Newswire "Acquisitions, Mergers and Takeovers" feed (global, multilingual)

Extra feeds can be added with the EXTRA_RSS_FEEDS env var:  name|market|url;name2|market|url2
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

from ..models import Item
from ..net import get, html_to_text, page_text, parse_rss

log = logging.getLogger(__name__)

FEEDS = {
    "euronext": {
        "url": "https://live.euronext.com/rss/company-pr-release",
        "market": "EU",
        "mna_feed": False,  # general feed: rely on topic + keywords
    },
    "globenewswire": {
        "url": "https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions/"
               "feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions",
        "market": "",
        "mna_feed": True,
    },
    "prnewswire": {
        "url": "https://www.prnewswire.com/rss/financial-services-latest-news/acquisitions-mergers-and-takeovers-list.rss",
        "market": "",
        "mna_feed": True,
    },
}

EURONEXT_MNA_TOPICS = ("Takeover bids", "Mergers, Acquisitions, Transfers", "Mergers, Acquiqitions, Transferts")
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")


def extra_feeds() -> dict:
    out = {}
    for chunk in (os.environ.get("EXTRA_RSS_FEEDS") or "").split(";"):
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) == 3 and parts[2].startswith("http"):
            out[parts[0]] = {"url": parts[2], "market": parts[1], "mna_feed": False}
    return out


def parse(name: str, xml_text: str, cfg: dict) -> list[Item]:
    items: list[Item] = []
    for e in parse_rss(xml_text):
        desc_raw = e["description"] or ""
        text = html_to_text(desc_raw, 6000)
        meta: dict = {"categories": e["categories"], "mna_feed": cfg.get("mna_feed", False)}
        if name == "euronext":
            topic = next((t for t in EURONEXT_MNA_TOPICS if t in text), "")
            meta["topic"] = topic
            meta["mna_feed"] = bool(topic)
            isins = _ISIN_RE.findall(text[:600])
            meta["issuer_isins"] = sorted(set(isins))
            # Skip the long metadata preamble when keyword-scoring
            m = re.search(r"Language \w+", text)
            meta["body_start"] = m.end() if m else 0
        uid = e["guid"] or e["link"] or hashlib.sha1(e["title"].encode()).hexdigest()
        items.append(Item(
            source=name,
            uid=uid,
            title=e["title"],
            url=e["link"],
            published=e["published"],
            text=text,
            market=cfg.get("market", ""),
            meta=meta,
        ))
    return items


def fetch_feed(name: str, settings) -> list[Item]:
    cfg = {**FEEDS, **extra_feeds()}.get(name)
    if not cfg:
        return []
    try:
        r = get(cfg["url"], min_interval=1, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    except Exception as e:  # pragma: no cover - network
        log.warning("Feed %s failed: %s", name, e)
        return []
    try:
        return parse(name, r.text, cfg)
    except Exception as e:
        log.warning("Feed %s could not be parsed: %s", name, e)
        return []


def full_text(item: Item, settings) -> str:
    # Euronext items already include the full release in the feed.
    if item.source == "euronext" and len(item.text) > 1500:
        return item.text[item.meta.get("body_start", 0):][:15000] or item.text
    txt = page_text(item.url, limit=15000)
    return txt or item.text
