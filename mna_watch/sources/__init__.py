"""Source registry: name -> (fetch(settings) -> list[Item], full_text(item, settings) -> str)."""
from __future__ import annotations

from functools import partial

from . import bse, eqs, rss, sec, uk_panel


def registry(names: list[str]) -> dict:
    reg = {
        "sec": (sec.fetch, sec.full_text),
        "uk_panel": (uk_panel.fetch, lambda item, s: item.text),
        "eqs": (eqs.fetch, eqs.full_text),
        "bse": (bse.fetch, bse.full_text),
    }
    for feed in {**rss.FEEDS, **rss.extra_feeds()}:
        reg[feed] = (partial(rss.fetch_feed, feed), rss.full_text)
    return {n: reg[n] for n in names if n in reg}
