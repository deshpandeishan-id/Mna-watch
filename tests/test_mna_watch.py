"""Offline tests: parsers run against saved samples of each real feed format; pipeline runs with fake sources."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mna_watch import notify
from mna_watch.config import Settings
from mna_watch.keywords import score
from mna_watch.models import Alert, Deal, Item
from mna_watch.pipeline import Runner, is_candidate
from mna_watch.sources import bse, eqs, rss, sec, uk_panel
from mna_watch.state import State, norm_name

FX = Path(__file__).parent / "fixtures"


def fx(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


class ParserTests(unittest.TestCase):
    def test_uk_panel(self):
        cases = uk_panel.parse(fx("uk_panel.xml"))
        self.assertEqual(len(cases), 3)
        items = uk_panel.to_items(cases)
        # 3 case rows + 3 offeror rows
        self.assertEqual(len(items), 6)
        by_uid = {i.uid: i for i in items}
        # Case with no named offeror alerts on its own
        d = uk_panel.structured_deal(by_uid["case:1602"])
        self.assertEqual(d.stage, "possible_offer")
        self.assertEqual(d.target_name, "Aptitude Software Group plc")
        # Case with an offeror: the case row is suppressed, the offeror row alerts
        self.assertIsNone(uk_panel.structured_deal(by_uid["case:1645"]))
        d = uk_panel.structured_deal(by_uid["case:1645:offeror:Metals X Limited:possible"])
        self.assertEqual((d.stage, d.acquirer_name), ("possible_offer", "Metals X Limited"))
        self.assertIn("06-Nov-2026", d.summary)
        # Two bidders -> competing offer
        d = uk_panel.structured_deal(by_uid["case:1595:offeror:DNO Bidco AS:firm"])
        self.assertEqual(d.stage, "competing_offer")

    def test_globenewswire(self):
        items = rss.parse("globenewswire", fx("globenewswire.xml"), rss.FEEDS["globenewswire"])
        self.assertEqual(len(items), 3)
        self.assertTrue(all(i.meta["mna_feed"] for i in items))
        self.assertIn("Mergers and Acquisitions", items[0].meta["categories"])

    def test_euronext(self):
        items = rss.parse("euronext", fx("euronext.xml"), rss.FEEDS["euronext"])
        self.assertEqual(len(items), 2)
        rexel, opa = items
        self.assertEqual(rexel.meta["topic"], "")
        self.assertEqual(opa.meta["topic"], "Takeover bids")
        self.assertEqual(opa.meta["issuer_isins"], ["FR0000000001"])
        self.assertIn("12,50 euros", opa.text)

    def test_eqs(self):
        items = eqs.parse(fx("eqs.html"))
        cats = {i.meta["category"] for i in items}
        self.assertNotIn("voting-rights", cats)  # noise dropped
        wp = [i for i in items if i.meta["category"] == "wpueg"][0]
        self.assertTrue(wp.meta["auto_candidate"])
        self.assertIn("PSI Software", wp.title)

    def test_sec(self):
        items = sec.parse_feed(fx("sec_425.xml"), "425")
        self.assertEqual(len(items), 2)  # both party rows; fetch() collapses them
        subj = [i for i in items if i.meta["role"] == "Subject"][0]
        self.assertEqual(subj.meta["cik"], 1234567)
        self.assertEqual(subj.uid, "0001234567-26-000001")
        eight_k = sec.parse_feed(fx("sec_8k.xml"), "8-K")
        self.assertEqual([i.meta["company"] for i in eight_k], ["Widget Co"])  # only Item 1.01 kept

    def test_bse(self):
        items = bse.parse(json.loads(fx("bse.json")))
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].url.endswith("abc.pdf"))
        self.assertTrue(items[0].meta["mna_feed"])


class KeywordTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(score("Acme to be acquired by Big Co for $42.00 per share in cash")[0], "strong")
        self.assertEqual(score("Statement re Possible Offer for Foo plc")[0], "strong")
        self.assertEqual(score("EQS-WpÜG: Übernahmeangebot / Zielgesellschaft: X AG")[0], "strong")
        self.assertEqual(score("Dépôt d'un projet d'offre publique d'achat visant Exemple")[0], "strong")
        self.assertEqual(score("Comunicato: offerta pubblica di acquisto totalitaria su Alfa S.p.A.")[0], "strong")
        self.assertEqual(score("Open offer for acquisition of 26% shares of Example Ltd")[0], "strong")
        self.assertEqual(score("Diazyme Acquires Alfa Wassermann")[0], "medium")
        self.assertEqual(score("Form 8.3 - Tate & Lyle plc", "possible offer")[0], "none")
        self.assertEqual(score("Q3 results ahead of expectations")[0], "none")
        # Boilerplate deep in the body should not trigger
        self.assertEqual(score("Annual report", "x " * 2000 + "this is not an offre publique d'achat")[0], "none")

    def test_candidates(self):
        gnw = rss.parse("globenewswire", fx("globenewswire.xml"), rss.FEEDS["globenewswire"])
        verdicts = [is_candidate(i)[0] for i in gnw]
        self.assertEqual(verdicts, [True, False, True])  # private acquisition still goes to AI; Form 8.3 dropped


class FakeExtractor:
    provider = "fake"

    def __init__(self):
        self.calls = 0

    def budget_left(self):
        return True

    def reset_budget(self):
        pass

    def classify(self, item, body):
        self.calls += 1
        if "Acme" in item.title:
            return Deal(is_mna=True, target_is_public=True, target_name="Acme Robotics, Inc.", target_ticker="ACME",
                        acquirer_name="Big Holdings", stage="definitive_agreement", price_per_share="42.00",
                        currency="USD", summary="Big Holdings to buy Acme.")
        return Deal(is_mna=True, target_is_public=False, stage="other")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"STATE_DIR": self.tmp.name, "SOURCES": "globenewswire,uk_panel",
                                                "LLM_PROVIDER": "none", "SMTP_USER": "", "EMAIL_TO": ""})
        self.env.start()
        self.no_net = mock.patch.object(sec, "listed_tickers", return_value={})
        self.no_net.start()
        self.gnw = rss.parse("globenewswire", fx("globenewswire.xml"), rss.FEEDS["globenewswire"])
        self.uk = uk_panel.to_items(uk_panel.parse(fx("uk_panel.xml")))

    def tearDown(self):
        self.env.stop()
        self.no_net.stop()
        self.tmp.cleanup()

    def _runner(self, bootstrap: bool):
        s = Settings()
        s.bootstrap_silently = bootstrap
        r = Runner(s)
        r.extractor = FakeExtractor()
        r.sources = {
            "globenewswire": (lambda _s: self.gnw, lambda it, _s: it.text),
            "uk_panel": (lambda _s: self.uk, lambda it, _s: it.text),
        }
        return r

    def test_bootstrap_is_silent_then_alerts_on_new(self):
        r = self._runner(bootstrap=True)
        self.assertEqual(r.cycle(), [])
        # a new UK bidder appears
        new_case = uk_panel.parse(fx("uk_panel.xml").replace("caseid=\"1602\"", "caseid=\"1700\"")
                                  .replace("Aptitude Software Group plc", "Newco Target plc"))
        self.uk = uk_panel.to_items(new_case)
        r2 = self._runner(bootstrap=True)
        alerts = r2.cycle()
        self.assertEqual([a.deal.target_name for a in alerts], ["Newco Target plc"])

    def test_end_to_end_and_dedupe(self):
        r = self._runner(bootstrap=False)
        alerts = r.cycle()
        names = sorted(a.deal.target_name for a in alerts)
        # Acme (AI says public, definitive), Aptitude (no offeror), First Tin (possible), Capricorn (2 bidders -> competing, once)
        self.assertEqual(names, ["Acme Robotics, Inc.", "Aptitude Software Group plc", "Capricorn Energy plc", "First Tin plc"])
        self.assertEqual(r.extractor.calls, 2)  # Form 8.3 never reached the AI
        # Same release again (e.g. French version) -> no duplicate alert
        dup = Item(source="globenewswire", uid="other-language-version",
                   title="Acme Robotics conclut un accord définitif - to be acquired by Big Holdings", url="u")
        self.gnw = [dup]
        r2 = self._runner(bootstrap=False)
        self.assertEqual(r2.cycle(), [])
        # State persisted to disk
        st = json.loads(Path(self.tmp.name, "state.json").read_text())
        self.assertIn("acme robotics|firm", st["deals"])


class MiscTests(unittest.TestCase):
    def test_norm_name(self):
        self.assertEqual(norm_name("Tate & Lyle PLC"), norm_name("Tate & Lyle plc"))
        self.assertEqual(norm_name("PSI Software SE"), "psi software")

    def test_email_render(self):
        it = Item(source="sec", uid="x", title="425 – Acme", url="https://sec.gov/x", market="US")
        d = Deal(is_mna=True, target_is_public=True, target_name="Acme Robotics, Inc.", target_ticker="ACME",
                 acquirer_name="Big Holdings", stage="definitive_agreement", price_per_share="42.00", currency="USD",
                 summary="<script>x</script> Big Holdings to buy Acme.")
        subj = notify.subject_for(Alert(item=it, deal=d))
        self.assertIn("Acme Robotics", subj)
        self.assertIn("USD 42.00", subj)
        text, html_body = notify.body_for(Alert(item=it, deal=d))
        self.assertNotIn("<script>", html_body)  # escaped
        self.assertIn("https://sec.gov/x", text)

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = State(d)
            s.mark_seen("a:1")
            s.record_alert("Foo plc", "possible_offer", {})
            s.save()
            s2 = State(d)
            self.assertTrue(s2.seen("a:1"))
            self.assertTrue(s2.already_alerted("FOO PLC", "possible_offer"))
            self.assertFalse(s2.already_alerted("Foo plc", "definitive_agreement"))


class ExtractorTests(unittest.TestCase):
    def test_gemini_reply_parsed(self):
        from mna_watch.extract import Extractor
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k", "LLM_PROVIDER": "auto"}):
            ex = Extractor(Settings())
        self.assertEqual(ex.provider, "gemini")
        reply = {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "is_mna": True, "target_is_public": True, "target_name": "PSI Software SE", "acquirer_name": "Zest Bidco",
            "stage": "definitive_agreement", "price_per_share": "45.00", "currency": "EUR", "confidence": "high"})}]}}]}
        resp = mock.Mock(status_code=200, json=lambda: reply, raise_for_status=lambda: None)
        with mock.patch("mna_watch.extract.requests.post", return_value=resp) as post:
            d = ex.classify(Item(source="eqs", uid="1", title="EQS-WpÜG", url="u"), "text")
        self.assertEqual((d.target_name, d.stage, d.price_per_share), ("PSI Software SE", "definitive_agreement", "45.00"))
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["generationConfig"]["responseMimeType"], "application/json")

    def test_llm_failure_falls_back_to_keywords(self):
        from mna_watch.extract import Extractor
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "", "LLM_PROVIDER": "auto"}):
            ex = Extractor(Settings())
        self.assertEqual(ex.provider, "anthropic")
        with mock.patch("mna_watch.extract.requests.post", side_effect=RuntimeError("down")):
            d = ex.classify(Item(source="x", uid="1", title="Recommended cash offer for Foo plc", url="u"), "")
        self.assertEqual(d.method, "keywords")


if __name__ == "__main__":
    unittest.main()
