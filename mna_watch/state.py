"""Small JSON state: which items we've processed and which deals we've already emailed."""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata

KEEP_SEEN_DAYS = 21
KEEP_DEALS_DAYS = 365


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(plc|inc|incorporated|corp|corporation|ltd|limited|ag|se|sa|s a|spa|s p a|nv|n v|bv|"
               r"holdings?|group|company|co|the|kgaa|gmbh|asa|ab|oyj)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class State:
    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, "state.json")
        self.data = {"seen": {}, "deals": {}, "llm_by_subject": {}, "initialised_sources": []}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        self.dirty = False

    # --- items ---------------------------------------------------------
    def seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark_seen(self, key: str, note: str = ""):
        self.data["seen"][key] = [int(time.time()), note]
        self.dirty = True

    def source_initialised(self, source: str) -> bool:
        return source in self.data["initialised_sources"]

    def set_source_initialised(self, source: str):
        if source not in self.data["initialised_sources"]:
            self.data["initialised_sources"].append(source)
            self.dirty = True

    # --- deals ---------------------------------------------------------
    def deal_key(self, target: str, stage: str) -> str:
        # Collapse the "firm" family so the FR and EN versions of the same release, or an 8-K
        # plus a DEFA14A, don't trigger twice.
        family = {"definitive_agreement": "firm", "tender_offer": "firm"}.get(stage, stage)
        return f"{norm_name(target)}|{family}"

    def already_alerted(self, target: str, stage: str) -> bool:
        if not norm_name(target):
            return False
        return self.deal_key(target, stage) in self.data["deals"]

    def record_alert(self, target: str, stage: str, info: dict):
        self.data["deals"][self.deal_key(target, stage)] = {"ts": int(time.time()), **info}
        self.dirty = True

    def known_target(self, target: str) -> bool:
        n = norm_name(target)
        return bool(n) and any(k.split("|")[0] == n for k in self.data["deals"])

    # --- LLM throttling for repetitive filers (e.g. dozens of SEC 425s per deal) -----
    def recently_checked_subject(self, subject: str, days: int = 14) -> bool:
        ts = self.data["llm_by_subject"].get(subject)
        return bool(ts) and time.time() - ts < days * 86400

    def mark_subject_checked(self, subject: str):
        self.data["llm_by_subject"][subject] = int(time.time())
        self.dirty = True

    # --- persistence ---------------------------------------------------
    def prune(self):
        now = time.time()
        def keep(k, v):
            # Takeover Panel rows stay in the table for the whole offer period (can be > 1 year)
            days = 730 if k.startswith("uk_panel:") else KEEP_SEEN_DAYS
            return now - v[0] < days * 86400
        self.data["seen"] = {k: v for k, v in self.data["seen"].items() if keep(k, v)}
        self.data["deals"] = {k: v for k, v in self.data["deals"].items() if now - v["ts"] < KEEP_DEALS_DAYS * 86400}
        self.data["llm_by_subject"] = {k: v for k, v in self.data["llm_by_subject"].items() if now - v < 60 * 86400}

    def save(self, force: bool = False):
        if not (self.dirty or force):
            return
        self.prune()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, self.path)
        self.dirty = False
