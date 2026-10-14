"""Core data structures."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Item:
    """One raw announcement/filing picked up from a source."""

    source: str                 # e.g. "sec", "uk_panel", "euronext"
    uid: str                    # stable unique id within the source
    title: str
    url: str
    published: str = ""         # ISO-ish timestamp string, best effort
    text: str = ""              # short text available from the feed itself
    market: str = ""            # "US", "UK", "EU", "IN"
    meta: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.uid}"


@dataclass
class Deal:
    """Structured result after classification/extraction."""

    is_mna: bool
    target_is_public: bool
    target_name: str = ""
    target_ticker: str = ""
    target_exchange: str = ""
    target_country: str = ""
    acquirer_name: str = ""
    stage: str = "other"        # rumour|possible_offer|definitive_agreement|tender_offer|revised_offer|competing_offer|update|completed|other
    consideration: str = ""     # cash|stock|mixed|unknown
    price_per_share: str = ""
    currency: str = ""
    premium: str = ""
    deal_value: str = ""
    summary: str = ""
    confidence: str = "medium"  # high|medium|low
    method: str = "llm"         # llm|structured|keywords
    listing_check: str = ""     # human-readable note on how "public target" was verified

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alert:
    item: Item
    deal: Deal
    extra_links: list = field(default_factory=list)


def clip(s: Optional[str], n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"
