"""India: BSE corporate announcements.

Under SEBI's takeover rules (SAST Regulations), crossing 25% of a listed company's shares or
acquiring control triggers a mandatory "open offer". The Public Announcement and Detailed Public
Statement are filed with the exchanges, so they show up in the target's BSE announcements.
Schemes of amalgamation/arrangement are also announced here.

The endpoint below is the JSON API used by bseindia.com itself. It is unofficial: it needs a
browser-like User-Agent and a Referer, and BSE sometimes blocks cloud/data-centre IPs. The
pipeline treats failures as non-fatal.
"""
from __future__ import annotations

import datetime as dt
import logging

from ..models import Item
from ..net import get

log = logging.getLogger(__name__)

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{}"
HEADERS = {
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}


def parse(data: dict) -> list[Item]:
    items: list[Item] = []
    for row in data.get("Table", []) or []:
        nid = str(row.get("NEWSID") or "")
        if not nid:
            continue
        company = row.get("SLONGNAME") or ""
        headline = row.get("HEADLINE") or ""
        subject = row.get("NEWSSUB") or ""
        cat = " / ".join(x for x in [row.get("CATEGORYNAME"), row.get("SUBCATNAME")] if x)
        attach = row.get("ATTACHMENTNAME") or ""
        items.append(Item(
            source="bse",
            uid=nid,
            title=f"{company}: {subject}".strip(": "),
            url=ATTACH.format(attach) if attach else "https://www.bseindia.com/corporates/ann.html",
            published=str(row.get("NEWS_DT") or row.get("DT_TM") or ""),
            text=f"{subject}. {headline}. Category: {cat}. Company: {company} (BSE scrip {row.get('SCRIP_CD')}).",
            market="IN",
            meta={"company": company, "scrip": row.get("SCRIP_CD"), "category": cat,
                  "mna_feed": "offer" in cat.lower() or "takeover" in cat.lower() or "acquisition" in cat.lower()},
        ))
    return items


def fetch(settings) -> list[Item]:
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y%m%d")
    out: list[Item] = []
    for page in (1, 2):
        params = {"pageno": page, "strCat": "-1", "strPrevDate": today, "strScrip": "", "strSearch": "P",
                  "strToDate": today, "strType": "C", "subcategory": "-1"}
        try:
            r = get(API, params=params, headers=HEADERS, min_interval=1)
            out.extend(parse(r.json()))
        except Exception as e:  # pragma: no cover - network
            log.warning("BSE announcements failed (page %s): %s", page, e)
            break
    return out


def full_text(item: Item, settings) -> str:
    """Headline plus the first pages of the PDF attachment, when it can be read."""
    if not item.url.lower().endswith(".pdf"):
        return item.text
    try:
        import io

        from pypdf import PdfReader

        r = get(item.url, headers=HEADERS, min_interval=1)
        reader = PdfReader(io.BytesIO(r.content))
        pages = [p.extract_text() or "" for p in reader.pages[:4]]
        return (item.text + "\n\n" + "\n".join(pages))[:15000]
    except Exception as e:  # pragma: no cover - network / bad PDF
        log.info("BSE attachment unreadable (%s): %s", item.url, e)
        return item.text
