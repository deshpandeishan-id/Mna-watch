"""Cheap multilingual pre-filter. Decides which items are worth sending to the LLM.

Two tiers:
  STRONG  - phrases that almost only appear when a listed company is being bid for.
  MEDIUM  - phrases common in M&A releases generally (target may be private) -> LLM decides.
NEGATIVE patterns knock out high-volume noise (dealing disclosures, buy-backs, voting rights).
"""
from __future__ import annotations

import re
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return s.replace("’", "'").replace("`", "'")


STRONG = [
    # English / UK / US
    r"rule 2\.7", r"rule 2\.4", r"possible offer", r"firm intention to make an offer",
    r"recommended (?:final )?(?:cash |all-share |share )?(?:offer|acquisition)",
    r"(?:cash|share|securities) offer for", r"scheme of arrangement",
    r"agreement and plan of merger", r"definitive merger agreement", r"merger agreement",
    r"definitive agreement to be acquired", r"to be acquired by", r"to go private", r"take[- ]private",
    r"tender offer", r"exchange offer for", r"per share in cash", r"\$[\d.,]+ per share",
    r"unsolicited (?:proposal|offer)", r"non-binding (?:proposal|indicative|offer)",
    r"public takeover offer", r"takeover offer", r"takeover bid", r"mandatory offer",
    r"voluntary public (?:takeover|tender|purchase) offer", r"attainment of control",
    r"statement re possible offer", r"approach regarding a possible offer",
    # German
    r"übernahmeangebot", r"wpüg", r"wpueg", r"pflichtangebot", r"kontrollerlangung",
    r"öffentliches erwerbsangebot", r"delisting-?angebot", r"angebot an die aktionäre",
    # French
    r"offre publique d'achat", r"offre publique d'échange", r"offre publique de retrait",
    r"offre publique (?:mixte|alternative|simplifiée|amicale|initiée)", r"projet d'offre publique",
    r"\bopa\b", r"\bope\b", r"\bopas\b", r"retrait obligatoire",
    # Italian
    r"offerta pubblica di acquisto", r"offerta pubblica di scambio", r"offerta pubblica totalitaria",
    r"\bopas?\b totalitaria", r"art(?:icolo|\.)? ?102 (?:del )?(?:d\.? ?lgs|tuf)", r"\bops\b totalitaria",
    # Dutch / Spanish / Nordic
    r"openbaar bod", r"aanbevolen bod", r"oferta pública de adquisición", r"\bopa\b sobre",
    r"offentlig(?:t)? (?:tilbud|uppköpserbjudande|overtakelsestilbud)", r"pliktig tilbud",
    # India (SEBI SAST)
    r"open offer", r"substantial acquisition of shares and takeovers", r"\bsast\b",
    r"regulation[s]? 3\(1\)", r"regulation[s]? 4 ", r"detailed public statement", r"public announcement.*offer",
    r"delisting offer",
]

MEDIUM = [
    r"\bmerger\b", r"\bacquire[sd]?\b", r"\bacquisition of\b", r"\btakeover\b", r"business combination",
    r"\bfusion\b", r"\bfusione\b", r"\bverschmelzung\b", r"übernahme", r"rachat de", r"\bacquisizione\b",
    r"amalgamation", r"\bdeal\b",
]

NEGATIVE = [
    r"^form 8\.3", r"^form 8\.5", r"form 8\.3 -", r"form 8 \(dd\)", r"^form-8\.3",
    r"share buy-?back", r"rachat d'actions propres", r"voting rights", r"droits de vote",
    r"stimmrechtsmitteilung", r"directors'? dealings", r"transactions sur actions propres",
    r"total number of shares", r"rule 2\.9", r"rule 2\.10", r"block listing", r"net asset value",
    r"transaction in own shares", r"acquisto di azioni proprie",
]

_STRONG = [re.compile(p) for p in STRONG]
_MEDIUM = [re.compile(p) for p in MEDIUM]
_NEG = [re.compile(p) for p in NEGATIVE]


def score(title: str, body: str = "") -> tuple[str, list[str]]:
    """Return ("strong"|"medium"|"none", matched_patterns).

    Negatives are checked against the title only (bodies of real bids often mention voting rights).
    Body text is truncated to the first ~2,500 chars to avoid boilerplate disclaimers
    ("this does not constitute an offer...").
    """
    t = _norm(title)
    b = _norm(body)[:2500]
    if any(p.search(t) for p in _NEG):
        return "none", []
    hits = [p.pattern for p in _STRONG if p.search(t) or p.search(b)]
    if hits:
        return "strong", hits
    hits = [p.pattern for p in _MEDIUM if p.search(t)]
    if hits:
        return "medium", hits
    return "none", []
