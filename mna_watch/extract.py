"""Classify an announcement and extract deal terms with an LLM (or keywords when no LLM is configured)."""
from __future__ import annotations

import json
import logging
import re
import time

import requests

from .keywords import score
from .models import Deal, Item

log = logging.getLogger(__name__)

PROMPT = """You screen corporate announcements for a merger-arbitrage desk.

Decide whether this document ANNOUNCES or MATERIALLY ADVANCES an acquisition of a PUBLICLY LISTED
target company (whole company or a control stake / mandatory offer). Listed means its shares trade on
a stock exchange (NYSE, Nasdaq, LSE/AIM, Euronext, Xetra, Borsa Italiana, BSE/NSE, etc.).

NOT relevant: acquisitions of private companies, subsidiaries, business units or assets; dealing
disclosures (Form 8.3 etc.); buy-backs; routine acceptance-level updates; completed deals;
joint ventures; minority investments with no control; generic disclaimers.

Stages:
- rumour: company confirms press speculation / approach
- possible_offer: possible offer / approach / non-binding proposal publicly disclosed
- definitive_agreement: signed merger agreement, firm offer (e.g. UK Rule 2.7), decision to launch an offer (e.g. §10 WpÜG), open offer triggered (India SAST)
- tender_offer: tender/exchange offer formally launched
- revised_offer: bidder raises or changes terms
- competing_offer: a rival bidder appears
- update: procedural/progress update on a known deal (approvals, acceptance levels, documents posted)
- completed: deal closed / delisting done
- other: not an M&A announcement for a listed target

Return ONLY a JSON object with these keys:
{"is_mna": bool, "target_is_public": bool, "target_name": str, "target_ticker": str,
 "target_exchange": str, "target_country": str, "acquirer_name": str, "stage": str,
 "consideration": "cash"|"stock"|"mixed"|"unknown", "price_per_share": str, "currency": str,
 "premium": str, "deal_value": str, "summary": str (max 2 sentences, English),
 "confidence": "high"|"medium"|"low"}
Use "" for unknown strings. The document may be in any language; answer in English.

SOURCE: {source}
TITLE: {title}
DOCUMENT:
{body}
"""

_JSON_RE = re.compile(r"\{.*\}", re.S)


def _parse_json(s: str) -> dict:
    m = _JSON_RE.search(s or "")
    if not m:
        raise ValueError("no JSON in model reply")
    return json.loads(m.group(0))


def _to_deal(d: dict, method: str) -> Deal:
    def g(k):
        v = d.get(k, "")
        return "" if v is None else str(v)
    return Deal(
        is_mna=bool(d.get("is_mna")),
        target_is_public=bool(d.get("target_is_public")),
        target_name=g("target_name"), target_ticker=g("target_ticker"), target_exchange=g("target_exchange"),
        target_country=g("target_country"), acquirer_name=g("acquirer_name"),
        stage=(g("stage") or "other").lower(), consideration=g("consideration"),
        price_per_share=g("price_per_share"), currency=g("currency"), premium=g("premium"),
        deal_value=g("deal_value"), summary=g("summary"), confidence=(g("confidence") or "medium").lower(),
        method=method,
    )


class Extractor:
    def __init__(self, settings):
        self.s = settings
        self.provider = settings.resolved_llm()
        self.calls = 0

    def budget_left(self) -> bool:
        return self.provider == "none" or self.calls < self.s.max_llm_calls_per_cycle

    def reset_budget(self):
        self.calls = 0

    def classify(self, item: Item, body: str) -> Deal | None:
        if self.provider == "none":
            return keyword_deal(item, body)
        prompt = PROMPT.replace("{source}", f"{item.source} ({item.market or 'global'})") \
                       .replace("{title}", item.title).replace("{body}", body[:14000])
        self.calls += 1
        try:
            raw = self._gemini(prompt) if self.provider == "gemini" else self._anthropic(prompt)
            return _to_deal(_parse_json(raw), "llm")
        except Exception as e:
            log.warning("LLM failed for %s (%s); falling back to keywords", item.key, e)
            return keyword_deal(item, body)

    # --- providers -------------------------------------------------------
    def _gemini(self, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.s.gemini_model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        for attempt in range(4):
            r = requests.post(url, params={"key": self.s.gemini_api_key}, json=body, timeout=60)
            if r.status_code == 429 and attempt < 3:  # free-tier rate limit
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        raise RuntimeError("Gemini rate limited")

    def _anthropic(self, prompt: str) -> str:
        for attempt in range(4):
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.s.anthropic_api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.s.anthropic_model, "max_tokens": 700, "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            if r.status_code in (429, 529) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        raise RuntimeError("Anthropic rate limited")


def keyword_deal(item: Item, body: str) -> Deal | None:
    """Fallback with no LLM: alert on strong takeover phrases, clearly marked as unverified."""
    level, hits = score(item.title, body)
    if level != "strong" and not item.meta.get("auto_candidate"):
        return None
    matched = ", ".join(h.replace("\\b", "") for h in hits[:3]) or item.meta.get("form", "filing type")
    return Deal(
        is_mna=True,
        target_is_public=True,
        target_name=item.meta.get("company", ""),
        stage="possible_offer",
        summary=f"Keyword match ({matched}). Not verified by AI - open the source to confirm.",
        confidence="low",
        method="keywords",
    )
