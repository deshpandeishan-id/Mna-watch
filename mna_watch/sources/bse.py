"""India: BSE corporate announcements.

Under SEBI's takeover rules (SAST Regulations), crossing 25% of a listed company's shares or
acquiring control triggers a mandatory "open offer". The Public Announcement and Detailed Public
Statement are filed with the exchanges, so they show up in the target's BSE announcements.
Schemes of amalgamation/arrangement are also announced here.

The endpoint below is the JSON API used by bseindia.com itself. It is unofficial: it needs a
browser-like User-Agent and a Referer, and BSE sometimes blocks cloud/data-centre IPs. The
pipeline treats failures as non-fatal.

Which filings count
-------------------
BSE carries thousands of routine filings a day, and most SAST paperwork is NOT a deal: shareholding
disclosures (Reg 29/31), promoter pledges (Reg 28/31), exempt inter-se transfers (Reg 10). So BSE items
are judged by their filing type (subject line, headline and BSE subcategory), never by keywords in
the attached PDF. Only these get through:

  open offer   Public Announcement / Detailed Public Statement -> definitive_agreement
               competing offer -> competing_offer; revised offer price -> revised_offer
               letters of offer, pre/post-offer ads, corrigenda, updates -> update (not emailed by default)
  delisting    promoter's intention / initial public announcement -> possible_offer
               detailed public announcement -> definitive_agreement; later steps -> update
  merger       board approval of a scheme of amalgamation/merger that isn't an internal group
               reorganisation (subsidiaries, demergers) -> sent to the AI, which checks the
               other party is listed. Court/NCLT/meeting procedure is dropped.

Buy-backs (which also use "public announcement", "letter of offer" and "tender offer") are always dropped.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from ..models import Deal, Item
from ..net import get

log = logging.getLogger(__name__)

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{}"
HEADERS = {
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
}


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(p, text) for p in patterns)


BUYBACK = (r"buy[- ]?back",)
OPEN_OFFER = (r"open offer", r"detailed public statement", r"letter of offer", r"\bdps\b",
              r"public announcement.{0,40}(?:regulation|reg\.)\s*3", r"competing offer",
              r"offer to the (?:public )?shareholders")
DELISTING = (r"delist",)
MERGER = (r"amalgamation", r"\bmerger\b", r"\bmerge\b", r"scheme of arrangement", r"composite scheme")
# Internal reorganisations and court procedure - not a takeover of a listed company.
INTRA_GROUP = (r"subsidiar", r"demerg", r"\bholding compan", r"promoter group compan", r"group compan",
               r"step[- ]down", r"fellow", r"hive[- ]?off", r"slump sale", r"reduction of (?:share )?capital")
PROCEDURAL = (r"\bnclt\b", r"tribunal", r"\bsanction", r"hearing", r"\bpetition", r"meeting of (?:the )?"
              r"(?:equity )?(?:share|creditor)", r"creditors", r"observation letter", r"no[- ]objection",
              r"\bnoc\b", r"effective date", r"record date", r"\bfinancial results\b", r"outcome of (?:the )?"
              r"(?:court|tribunal|nclt)", r"postal ballot", r"scrutini[sz]er", r"voting results", r"listing (?:and|&) "
              r"trading approval", r"allotment", r"credit of shares")
BOARD_APPROVAL = (r"approv", r"board", r"consider", r"proposed", r"proposal", r"entered into",
                  r"enter into", r"agreement")


def classify_filing(subject: str, headline: str = "", category: str = "") -> tuple[str, str] | None:
    """Return (kind, stage) for a deal-type BSE filing, or None for everything else."""
    text = " ".join([subject, headline, category]).lower()
    subj = subject.lower()
    if _has(text, *BUYBACK):
        return None

    if _has(text, *OPEN_OFFER):
        if _has(text, r"competing offer"):
            return "open_offer", "competing_offer"
        if _has(text, r"revis(?:ion|ed) (?:in |of )?(?:the )?offer price", r"upward revision", r"revised offer"):
            return "open_offer", "revised_offer"
        later = _has(text, r"pre[- ]offer", r"post[- ]offer", r"corrigendum", r"letter of offer", r"update",
                     r"closure", r"completion", r"recommendation", r"independent directors", r"opening",
                     r"advertisement", r"intimation of (?:the )?schedule", r"payment", r"withdraw", r"tendering")
        if later:
            return "open_offer", "update"
        if _has(text, r"public announcement", r"detailed public statement", r"\bdps\b",
                r"regulation[s]? 3\(1\)", r"regulation[s]? 4\b"):
            return "open_offer", "definitive_agreement"
        return "open_offer", "update"

    if _has(text, *DELISTING) and not _has(text, r"delisted from", r"suspension"):
        if _has(text, r"initial public announcement", r"intention", r"proposal", r"proposed", r"intimation of "
                r"(?:the )?(?:proposal|intent)"):
            return "delisting", "possible_offer"
        if _has(text, r"detailed public announcement", r"public announcement"):
            return "delisting", "definitive_agreement"
        return "delisting", "update"

    if _has(text, *MERGER):
        if _has(text, *INTRA_GROUP) or _has(text, *PROCEDURAL):
            return None
        # The bare "Announcement under Regulation 30 (LODR)-Scheme of Arrangement" subject with no detail is
        # mostly procedure; let it through only if the headline says something was approved/proposed.
        boiler = r"^.*?announcement under regulation 30 \(lodr\)\s*-\s*"
        detail = re.sub(boiler, "", (headline or subject).lower())
        if _has(detail, *MERGER) and _has(detail, *BOARD_APPROVAL):
            return "merger", "definitive_agreement"
        return None
    return None


_ACQ_RE = re.compile(r"\bby\s+(?:m/s\.?\s*)?(.+?)(?:\s+(?:along with|together with|\(|to acquire|for the|for acquisition|"
                     r"pursuant|under|in accordance|being)|[.;,]|$)", re.I)


def _acquirer_from(text: str) -> str:
    m = _ACQ_RE.search(text or "")
    if not m:
        return ""
    name = m.group(1).strip(" .-")
    return "" if len(name) < 3 or len(name) > 120 or name.lower().startswith(("the company", "its ", "the board")) else name


_TARGET_RES = [
    re.compile(r"(?:public )?shareholders of\s+(?:m/s\.?\s*)?(.+?\b(?:limited|ltd)\b\.?)", re.I),
    re.compile(r"(.+?\b(?:limited|ltd)\b\.?)\s*\(\s*(?:the\s+)?target(?: company)?\s*\)", re.I),
    re.compile(r"target company\s*[:-]?\s*(?:m/s\.?\s*)?(.+?\b(?:limited|ltd)\b\.?)", re.I),
]


def _target_from(text: str, filer: str) -> str:
    """Open-offer documents are sometimes filed by the acquirer; pick out the named target if there is one."""
    for rx in _TARGET_RES:
        m = rx.search(text or "")
        if m:
            name = re.sub(r"^.*\b(?:of|to|for|given to the)\s+(?=[A-Z])", "", m.group(1).strip(" .-:"))
            if 3 <= len(name) <= 120:
                return name
    return filer


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
        verdict = classify_filing(subject, headline, row.get("SUBCATNAME") or "")
        items.append(Item(
            source="bse",
            uid=nid,
            title=f"{company}: {subject}".strip(": "),
            url=ATTACH.format(attach) if attach else "https://www.bseindia.com/corporates/ann.html",
            published=str(row.get("NEWS_DT") or row.get("DT_TM") or ""),
            text=f"{subject}. {headline}. Category: {cat}. Company: {company} (BSE scrip {row.get('SCRIP_CD')}).",
            market="IN",
            meta={"company": company, "scrip": row.get("SCRIP_CD"), "category": cat, "headline": headline,
                  "deal_kind": verdict[0] if verdict else "", "rule_stage": verdict[1] if verdict else "",
                  "mna_feed": bool(verdict)},
        ))
    return items


def structured_deal(item: Item) -> Deal:
    """Deal built from the filing type alone (used when no AI is configured or the AI call fails)."""
    kind, stage = item.meta.get("deal_kind"), item.meta.get("rule_stage") or "update"
    what = {"open_offer": "SEBI SAST open offer filing", "delisting": "Delisting offer filing",
            "merger": "Board-approved scheme of amalgamation/merger"}.get(kind, "Filing")
    headline = (item.meta.get("headline") or "").strip()
    subject = item.title.split(": ", 1)[-1]
    company = item.meta.get("company", "")
    target = _target_from(f"{subject} {headline}", company) if kind == "open_offer" else company
    return Deal(
        is_mna=True, target_is_public=True, target_name=target,
        acquirer_name=_acquirer_from(headline) if kind != "merger" else "", stage=stage,
        summary=f"{what}: {headline or subject}"[:400],
        confidence="medium" if kind in ("open_offer", "delisting") else "low",
        method="structured" if kind in ("open_offer", "delisting") else "keywords",
    )


def reconcile(item: Item, deal: Deal | None) -> Deal | None:
    """Combine the AI's reading with what the filing type already proves.

    Open-offer and delisting filings are only ever made for a listed target, so the filing type sets the
    stage and the target; the AI only adds terms (acquirer, price). Merger schemes are left to the AI.
    """
    kind = item.meta.get("deal_kind")
    if not kind:
        return None
    if deal is None or deal.method == "keywords":
        return structured_deal(item)
    if kind == "merger":
        return deal
    deal.is_mna = True
    deal.target_is_public = True
    deal.stage = item.meta.get("rule_stage") or deal.stage
    deal.target_name = deal.target_name or structured_deal(item).target_name
    deal.acquirer_name = deal.acquirer_name or _acquirer_from(item.meta.get("headline", ""))
    return deal


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
