"""UK: Takeover Panel Disclosure Table (official XML).

Every UK Takeover Code company that enters an "offer period" (possible or firm offer) is added
to this table, along with every named offeror. It is updated through the day as announcements
are made, so diffing it gives a near-authoritative, free list of new UK public-target situations:

  * new offeree (case)            -> new possible/firm offer for a UK listed company
  * new offeror on existing case  -> competing bidder

Feed: https://www.thetakeoverpanel.org.uk/new/disclosureTable/v3/disclosuretable.xml
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from ..models import Deal, Item
from ..net import get

log = logging.getLogger(__name__)

URL = "https://www.thetakeoverpanel.org.uk/new/disclosureTable/v3/disclosuretable.xml"
PAGE = "https://www.thetakeoverpanel.org.uk/disclosure/disclosure-table"


def parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    cases: dict[str, dict] = {}
    for case in root.iter("case"):
        cid = case.get("caseid", "")
        offeree = case.find("offeree")
        if offeree is None or not cid:
            continue
        isins = [l.findtext("ISIN") for l in offeree.iter("twoten_line") if l.findtext("ISIN")]
        offerors = []
        for o in case.findall("offeror"):
            offerors.append({
                "name": o.get("name", ""),
                "identified": o.get("offeror_identified", ""),
                "rule_26_deadline": o.get("rule_26_deadline", ""),
                "cash_only_hint": o.find("empty_2.10") is not None,
            })
        cases[cid] = {
            "caseid": cid,
            "offeree": offeree.get("name", ""),
            "lei": offeree.get("lei", ""),
            "offer_period_commenced": offeree.get("offer_period_commenced", ""),
            "isins": isins,
            "offerors": offerors,
            "no_named_offeror": case.find("empty_offeror") is not None,
        }
    return list(cases.values())


def to_items(cases: list[dict]) -> list[Item]:
    """One item per (case) and one per (case, offeror) so a newly named/competing bidder is a new item."""
    items: list[Item] = []
    for c in cases:
        base_meta = {"case": c, "auto_candidate": True, "structured": True}
        items.append(Item(
            source="uk_panel",
            uid=f"case:{c['caseid']}",
            title=f"UK offer period: {c['offeree']}",
            url=PAGE,
            published=c["offer_period_commenced"],
            text=_describe(c),
            market="UK",
            meta={**base_meta, "kind": "case"},
        ))
        for o in c["offerors"]:
            # Include the firm/possible state in the id, so a possible offer turning firm
            # (Rule 2.6 deadline disappears) is picked up as a new event.
            state = "firm" if (o.get("rule_26_deadline") or "N/A") == "N/A" else "possible"
            items.append(Item(
                source="uk_panel",
                uid=f"case:{c['caseid']}:offeror:{o['name']}:{state}",
                title=f"UK offeror identified for {c['offeree']}: {o['name']}",
                url=PAGE,
                published=o["identified"],
                text=_describe(c),
                market="UK",
                meta={**base_meta, "kind": "offeror", "offeror": o},
            ))
    return items


def _describe(c: dict) -> str:
    bidders = ", ".join(o["name"] for o in c["offerors"]) or "no named offeror yet"
    return (f"Offeree: {c['offeree']} (ISIN {', '.join(c['isins']) or 'n/a'}). "
            f"Offer period commenced {c['offer_period_commenced']}. Offeror(s): {bidders}.")


def structured_deal(item: Item) -> Deal | None:
    """Turn a table row into a Deal. Returns None for rows that shouldn't alert on their own."""
    c = item.meta["case"]
    if item.meta["kind"] == "case" and c["offerors"]:
        # The offeror row(s) carry the useful detail; alert from those instead.
        return None
    confidence = "high"
    if item.meta["kind"] == "offeror":
        o = item.meta["offeror"]
        n_bidders = len(c["offerors"])
        stage = "competing_offer" if n_bidders > 1 else "possible_offer"
        acquirer = o["name"]
        deadline = o.get("rule_26_deadline") or "N/A"
        note = (f"Rule 2.6 'put up or shut up' deadline: {deadline}." if deadline != "N/A"
                else "No Rule 2.6 deadline listed (usually means a firm offer or recommended deal has been announced).")
        if deadline == "N/A":
            # Inferred: no PUSU deadline normally means a Rule 2.7 firm offer has been announced.
            stage = "competing_offer" if n_bidders > 1 else "definitive_agreement"
            confidence = "medium"
    else:
        acquirer = ", ".join(o["name"] for o in c["offerors"]) or "Not yet named"
        stage = "possible_offer"
        note = "Offer period started; offeror not yet named." if c["no_named_offeror"] else ""
    consideration = "cash (per Panel table: offeror dealings disclosure not required)" if (
        item.meta["kind"] == "offeror" and item.meta["offeror"].get("cash_only_hint")) else ""
    return Deal(
        is_mna=True,
        target_is_public=True,
        target_name=c["offeree"],
        target_ticker=", ".join(c["isins"]),
        target_exchange="UK (Takeover Code company)",
        target_country="UK",
        acquirer_name=acquirer,
        stage=stage,
        consideration=consideration,
        summary=f"{_describe(c)} {note}".strip(),
        confidence=confidence,
        method="structured",
        listing_check="Listed on the Takeover Panel Disclosure Table (official UK source).",
    )


def fetch(settings) -> list[Item]:
    try:
        r = get(URL, min_interval=1)
    except Exception as e:  # pragma: no cover - network
        log.warning("Takeover Panel table failed: %s", e)
        return []
    return to_items(parse(r.text))
