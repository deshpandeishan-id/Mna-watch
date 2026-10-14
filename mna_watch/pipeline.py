"""Poll sources -> pre-filter -> classify -> verify -> dedupe -> email."""
from __future__ import annotations

import logging
import os
import subprocess
import time

from . import notify
from .config import Settings
from .extract import Extractor
from .keywords import score
from .models import Alert, Deal, Item
from .sources import registry, rss, sec, uk_panel
from .state import State

log = logging.getLogger(__name__)

# SEC forms that get filed many times per deal; only re-check the same company every 14 days.
SEC_THROTTLED_FORMS = {"425", "DEFA14A", "SC 14D9-C", "8-K"}


def is_candidate(item: Item) -> tuple[bool, str]:
    if item.meta.get("auto_candidate"):
        return True, "auto"
    body = item.text[item.meta.get("body_start", 0):]
    level, _ = score(item.title, body)
    if level == "strong":
        return True, "strong keywords"
    if level == "medium" and item.meta.get("mna_feed"):
        return True, "M&A feed + keywords"
    return False, f"filtered ({level})"


def verify_listing(item: Item, deal: Deal, settings) -> None:
    """Fill deal.listing_check with how we know the target is listed (best effort)."""
    if deal.listing_check:
        return
    if item.source == "sec":
        tick = sec.listed_tickers(settings).get(item.meta.get("cik"))
        subject_is_target = item.meta.get("role", "").lower().startswith("subject")
        if tick and subject_is_target:
            deal.listing_check = f"SEC company ticker list: {tick['ticker']} ({tick['title']})"
            deal.target_ticker = deal.target_ticker or tick["ticker"]
            return
        if item.meta.get("form") in {"SC TO-T", "SC 14D9", "SC TO-C", "SC 14D9-C"}:
            deal.listing_check = f"Subject of an SEC tender-offer filing ({item.meta.get('form')})"
            return
    if item.market in ("US", "") and deal.target_name:
        hit = sec.lookup_by_name(deal.target_name, settings)
        if hit:
            deal.listing_check = f"SEC company ticker list: {hit['ticker']}"
            deal.target_ticker = deal.target_ticker or hit["ticker"]
            return
    if item.source == "euronext" and item.meta.get("issuer_isins"):
        deal.listing_check = (f"Released by a Euronext-listed issuer (ISIN {', '.join(item.meta['issuer_isins'][:2])}); "
                              "check whether the issuer is the target or the bidder")
        return
    if item.source == "bse":
        deal.listing_check = f"Filed on BSE under scrip {item.meta.get('scrip')} ({item.meta.get('company')})"
        return
    if item.source == "eqs" and item.meta.get("category", "").startswith("wp"):
        deal.listing_check = "German takeover-law (WpÜG) announcement - only made for listed targets"
        return
    deal.listing_check = "Per AI reading of the announcement (not independently verified)"


class Runner:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or Settings()
        self.state = State(self.s.state_dir)
        self.extractor = Extractor(self.s)
        wanted = list(dict.fromkeys(self.s.sources + list(rss.extra_feeds())))
        self.sources = registry(wanted)
        log.info("Sources: %s | LLM: %s | email: %s", ", ".join(self.sources), self.extractor.provider,
                 "on" if self.s.email_ready() else "OFF (missing SMTP settings)")

    # ------------------------------------------------------------------
    def cycle(self) -> list[Alert]:
        self.extractor.reset_budget()
        alerts: list[Alert] = []
        for name, (fetch, full_text) in self.sources.items():
            try:
                items = fetch(self.s)
            except Exception as e:  # never let one source kill the loop
                log.warning("Source %s crashed: %s", name, e)
                continue
            if not self.state.source_initialised(name):
                if self.s.bootstrap_silently and items:
                    for it in items:
                        self.state.mark_seen(it.key, "bootstrap")
                    self.state.set_source_initialised(name)
                    log.info("%s: first run, recorded %d existing items without alerting", name, len(items))
                    continue
                if items:
                    self.state.set_source_initialised(name)
            new = [it for it in items if not self.state.seen(it.key)]
            if new:
                log.info("%s: %d items, %d new", name, len(items), len(new))
            for it in new:
                alert = self.process(it, full_text)
                if alert == "defer":
                    break  # LLM budget used up; try the rest next cycle
                if alert:
                    alerts.append(alert)
        sent = notify.send(self.s, alerts)
        if alerts:
            log.info("%d alert(s), %d email(s) sent", len(alerts), sent)
        self.state.save()
        return alerts

    def process(self, it: Item, full_text):
        # 1. structured sources need no AI
        if it.source == "uk_panel":
            deal = uk_panel.structured_deal(it)
            self.state.mark_seen(it.key, "structured")
            return self.maybe_alert(it, deal) if deal else None

        # 2. cheap pre-filter
        ok, why = is_candidate(it)
        if not ok:
            self.state.mark_seen(it.key, why)
            return None

        # 3. avoid re-checking the same SEC company dozens of times per deal
        subj = None
        if it.source == "sec" and it.meta.get("form") in SEC_THROTTLED_FORMS:
            subj = f"sec:{it.meta.get('cik')}:{it.meta.get('form')}"
            if self.state.recently_checked_subject(subj):
                self.state.mark_seen(it.key, "sec subject recently checked")
                return None

        if not self.extractor.budget_left():
            return "defer"

        body = full_text(it, self.s) or it.text
        deal = self.extractor.classify(it, body)
        self.state.mark_seen(it.key, f"classified:{deal.stage if deal else 'none'}")
        if subj:
            self.state.mark_subject_checked(subj)
        return self.maybe_alert(it, deal) if deal else None

    def maybe_alert(self, it: Item, deal: Deal) -> Alert | None:
        if not (deal.is_mna and deal.target_is_public and deal.stage in self.s.alert_stages):
            return None
        target = deal.target_name or it.meta.get("company") or it.title
        if self.state.already_alerted(target, deal.stage):
            log.info("Duplicate (already alerted): %s / %s", target, deal.stage)
            return None
        verify_listing(it, deal, self.s)
        self.state.record_alert(target, deal.stage, {"source": it.source, "url": it.url, "title": it.title,
                                                     "acquirer": deal.acquirer_name})
        return Alert(item=it, deal=deal)

    # ------------------------------------------------------------------
    def run(self, max_minutes: float = 0, commit_state: bool = False):
        """Poll forever (or for max_minutes). Commits state to git after cycles that alerted."""
        start = time.time()
        last_commit = time.time()
        while True:
            t0 = time.time()
            try:
                alerts = self.cycle()
            except Exception as e:
                log.exception("Cycle failed: %s", e)
                alerts = []
            if commit_state and (alerts or time.time() - last_commit > 20 * 60):
                commit_state_to_git()
                last_commit = time.time()
            if max_minutes and time.time() - start > max_minutes * 60:
                break
            time.sleep(max(5, self.s.poll_seconds - (time.time() - t0)))
        if commit_state:
            commit_state_to_git()


def commit_state_to_git():
    """Used on GitHub Actions so state survives between runs."""
    path = os.environ.get("STATE_DIR", "state")
    cmds = [
        ["git", "add", path],
        ["git", "-c", "user.name=mna-watch", "-c", "user.email=mna-watch@users.noreply.github.com",
         "commit", "-q", "-m", "Update state [skip ci]"],
        ["git", "pull", "-q", "--rebase", "-X", "theirs"],
        ["git", "push", "-q"],
    ]
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode != 0 and c[1] == "-c":  # nothing to commit
            return
        if r.returncode != 0:
            log.warning("git %s failed: %s", c[1], (r.stderr or r.stdout).strip()[:300])
            return
