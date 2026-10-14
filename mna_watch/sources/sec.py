"""US: SEC EDGAR "latest filings" Atom feeds, by form type.

Form types watched (all imply a public, SEC-registered company is involved):
  SC TO-C   pre-commencement tender offer communication (often the deal press release)
  SC TO-T   third-party tender offer launched
  SC 14D9   target's response to a tender offer (+ SC 14D9-C communications)
  425       communications in a stock-for-stock business combination
  DEFA14A   additional proxy material - targets in cash mergers file the deal press release here
  8-K       only kept when the feed summary mentions Item 1.01 (material definitive agreement)

EDGAR asks automated clients to send a User-Agent with a name and email and stay
under 10 requests/second. See https://www.sec.gov/os/accessing-edgar-data
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Item
from ..net import get, html_to_text, parse_rss

log = logging.getLogger(__name__)

FEED = "https://www.sec.gov/cgi-bin/browse-edgar"
FORMS = ["SC TO-C", "SC TO-T", "SC 14D9", "SC 14D9-C", "425", "DEFA14A", "8-K"]
# Forms that by themselves mean "someone is bidding for a public company"
AUTO_CANDIDATE_FORMS = {"SC TO-C", "SC TO-T", "SC 14D9", "SC 14D9-C"}
MIN_INTERVAL = 0.15  # seconds between SEC requests (< 10 req/s)

_TITLE_RE = re.compile(r"^(?P<form>.+?) - (?P<name>.+?) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)\s*$")
_ACC_RE = re.compile(r"accession-number=([\d-]+)")

_tickers_cache: dict[int, dict] | None = None


def _headers(settings) -> dict:
    ua = settings.sec_user_agent or "mna-watch (set SEC_USER_AGENT to 'Your Name your@email')"
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


def parse_feed(xml_text: str, form: str) -> list[Item]:
    items: list[Item] = []
    for e in parse_rss(xml_text):
        m = _TITLE_RE.match(e["title"])
        acc = _ACC_RE.search(e["guid"])
        if not m or not acc:
            continue
        summary = html_to_text(e["description"], 2000)
        if form == "8-K" and "1.01" not in summary:
            continue
        items.append(Item(
            source="sec",
            uid=acc.group(1),
            title=f"{m.group('form')} – {m.group('name')} ({m.group('role')})",
            url=e["link"],
            published=e["published"],
            text=summary,
            market="US",
            meta={
                "form": m.group("form").strip(),
                "company": m.group("name"),
                "cik": int(m.group("cik")),
                "role": m.group("role"),  # "Subject" (target/issuer) or "Filed by"
                "accession": acc.group(1),
                "auto_candidate": m.group("form").strip() in AUTO_CANDIDATE_FORMS,
            },
        ))
    return items


def fetch(settings) -> list[Item]:
    out: list[Item] = []
    for form in FORMS:
        params = {"action": "getcurrent", "type": form, "company": "", "dateb": "",
                  "owner": "include", "start": "0", "count": "100", "output": "atom"}
        try:
            r = get(FEED, params=params, headers=_headers(settings), min_interval=MIN_INTERVAL)
        except Exception as e:  # pragma: no cover - network
            log.warning("SEC feed %s failed: %s", form, e)
            continue
        got = parse_feed(r.text, form)
        # getcurrent returns rows for every party; keep the "Subject" row when present
        out.extend(got)
    # Collapse the "(Filed by)" / "(Subject)" duplicates for one accession, preferring Subject
    by_acc: dict[str, Item] = {}
    for it in out:
        acc = it.meta["accession"]
        if acc not in by_acc or it.meta["role"].lower().startswith("subject"):
            by_acc[acc] = it
    return list(by_acc.values())


def full_text(item: Item, settings) -> str:
    """Fetch the filing index, then the primary document, and return its text."""
    try:
        idx = get(item.url, headers=_headers(settings), min_interval=MIN_INTERVAL)
        soup = BeautifulSoup(idx.text, "html.parser")
        doc_href = None
        for a in soup.select("table.tableFile a, table a"):
            href = a.get("href", "")
            if "/Archives/edgar/data/" in href and re.search(r"\.(htm|html|txt)$", href) and "-index" not in href:
                doc_href = href
                break
        if not doc_href:
            return item.text
        if doc_href.startswith("/ix?doc="):
            doc_href = doc_href[len("/ix?doc="):]
        url = "https://www.sec.gov" + doc_href if doc_href.startswith("/") else doc_href
        doc = get(url, headers=_headers(settings), min_interval=MIN_INTERVAL)
        return html_to_text(doc.text, 15000)
    except Exception as e:  # pragma: no cover - network
        log.warning("SEC full text failed for %s: %s", item.url, e)
        return item.text


def listed_tickers(settings) -> dict[int, dict]:
    """CIK -> {ticker, title} for every company with an exchange ticker (SEC's own list)."""
    global _tickers_cache
    if _tickers_cache is None:
        try:
            r = get("https://www.sec.gov/files/company_tickers.json", headers=_headers(settings), min_interval=MIN_INTERVAL)
            data = r.json()
            _tickers_cache = {int(v["cik_str"]): {"ticker": v["ticker"], "title": v["title"]} for v in data.values()}
        except Exception as e:  # pragma: no cover - network
            log.warning("Could not load SEC ticker list: %s", e)
            _tickers_cache = {}
    return _tickers_cache


def lookup_by_name(name: str, settings) -> dict | None:
    """Very simple exact-ish name match against SEC's ticker list (used for US targets found in press releases)."""
    if not name:
        return None
    norm = _norm_co(name)
    for cik, v in listed_tickers(settings).items():
        if _norm_co(v["title"]) == norm:
            return {"cik": cik, **v}
    return None


def _norm_co(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|the|n v|nv|sa|ag|se)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()
