"""HTTP + text helpers."""
from __future__ import annotations

import html
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_session = requests.Session()
_last_call: dict[str, float] = {}


def get(url: str, *, headers: dict | None = None, timeout: int = 25, min_interval: float = 0.0,
        params: dict | None = None, retries: int = 2) -> requests.Response:
    """GET with polite per-host spacing and small retry/backoff."""
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    hdrs = {"User-Agent": BROWSER_UA, "Accept-Language": "en-GB,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        wait = _last_call.get(host, 0) + min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = _session.get(url, headers=hdrs, timeout=timeout, params=params)
            _last_call[host] = time.time()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last_exc = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def html_to_text(markup: str, limit: int = 15000) -> str:
    """Strip HTML (handles double-escaped HTML seen in some RSS feeds) to compact text."""
    if not markup:
        return ""
    s = markup
    # Some feeds (Euronext) contain HTML that is itself HTML-escaped inside the description.
    for _ in range(2):
        if "&lt;" in s or "&amp;lt;" in s:
            s = html.unescape(s)
    soup = BeautifulSoup(s, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def page_text(url: str, *, headers: dict | None = None, limit: int = 15000, min_interval: float = 0.5) -> str:
    try:
        r = get(url, headers=headers, min_interval=min_interval)
    except Exception as e:  # pragma: no cover - network
        log.warning("Could not fetch %s: %s", url, e)
        return ""
    return html_to_text(r.text, limit)


NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def parse_rss(xml_text: str) -> list[dict]:
    """Minimal RSS 2.0 / Atom parser -> list of dicts (title, link, guid, published, description, categories)."""
    # Some feeds put zero-width chars or junk before the XML declaration
    xml_text = xml_text.lstrip("﻿​ \r\n\t")
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out: list[dict] = []
    if root.tag.endswith("feed"):  # Atom
        for e in root.findall("atom:entry", NS):
            link_el = e.find("atom:link", NS)
            out.append({
                "title": (e.findtext("atom:title", default="", namespaces=NS) or "").strip(),
                "link": link_el.get("href", "") if link_el is not None else "",
                "guid": (e.findtext("atom:id", default="", namespaces=NS) or "").strip(),
                "published": (e.findtext("atom:updated", default="", namespaces=NS) or "").strip(),
                "description": e.findtext("atom:summary", default="", namespaces=NS) or "",
                "categories": [c.get("term", "") for c in e.findall("atom:category", NS)],
            })
        return out
    for it in root.iter("item"):
        cats = [c.text or "" for c in it.findall("category")]
        cats += [c.text or "" for c in it.findall("{http://purl.org/dc/elements/1.1/}subject")]
        cats += [c.text or "" for c in it.findall("{http://purl.org/dc/documents/1.1/}subject")]
        # GlobeNewswire uses a non-standard dc namespace URI
        cats += [c.text or "" for c in it.iter() if c.tag.endswith("}subject") and (c.text or "") not in cats]
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "guid": (it.findtext("guid") or it.findtext("link") or "").strip(),
            "published": (it.findtext("pubDate") or "").strip(),
            "description": it.findtext("description") or "",
            "categories": [c.strip() for c in cats if c and c.strip()],
        })
    return out
