from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.history import (
    KeyLedger,
    LeadLedger,
    SiteLedger,
    lead_history_path_for,
    site_history_path_for,
)
from lead_research.models import Lead, SearchResult
from lead_research.pipeline import DiscoveryConfig, run_discovery
from lead_research.suppression import SuppressionList


def make_lead(email: str, website: str) -> Lead:
    return Lead(
        category="hotel",
        source_url=website,
        website=website,
        email=email,
        company_name="Example",
    )


class KeyLedgerTests(unittest.TestCase):
    def test_persists_and_reloads_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.txt"
            ledger = KeyLedger(path, flush_every=2)
            self.assertTrue(ledger.add(["a"]))
            self.assertTrue(ledger.add(["b"]))  # triggers flush at 2
            ledger.add(["c"])
            ledger.flush()

            reloaded = KeyLedger(path)
            self.assertIn("a", reloaded)
            self.assertIn("c", reloaded)
            self.assertNotIn("z", reloaded)
            self.assertEqual(len(reloaded), 3)

    def test_add_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.txt"
            ledger = KeyLedger(path)
            self.assertTrue(ledger.add(["x"]))
            self.assertFalse(ledger.add(["x"]))

    def test_add_failure_never_raises(self) -> None:
        ledger = KeyLedger(Path("/nonexistent-dir/keys.txt"), flush_every=1)
        with patch.object(Path, "mkdir", side_effect=OSError("denied")):
            # Must not raise even though the append cannot be written.
            ledger.add(["a"])
        self.assertIn("a", ledger)


class LeadLedgerTests(unittest.TestCase):
    def test_records_email_and_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leads.txt"
            ledger = LeadLedger(path)
            ledger.record(make_lead("info@hotel-a.example", "https://hotel-a.example"))
            ledger.flush()

            reloaded = LeadLedger(path)
            self.assertTrue(reloaded.is_known(make_lead("info@hotel-a.example", "https://hotel-a.example")))
            # Same domain, different mailbox counts as known too.
            self.assertTrue(reloaded.is_known(make_lead("sales@hotel-a.example", "https://hotel-a.example")))
            self.assertFalse(reloaded.is_known(make_lead("info@hotel-b.example", "https://hotel-b.example")))


class SiteLedgerTests(unittest.TestCase):
    def test_records_normalized_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sites.txt"
            ledger = SiteLedger(path)
            ledger.record("hotel-a.example")
            ledger.flush()

            reloaded = SiteLedger(path)
            self.assertTrue(reloaded.is_known("hotel-a.example"))
            self.assertFalse(reloaded.is_known("hotel-b.example"))


class HistoryPathTests(unittest.TestCase):
    def test_paths_follow_checkpoint_directory(self) -> None:
        checkpoint = Path("/data/run/capper-checkpoint.json")
        output = Path("/other/leads.csv")
        self.assertEqual(
            lead_history_path_for(output, checkpoint),
            Path("/data/run/capper-known-leads.txt"),
        )
        self.assertEqual(
            site_history_path_for(output, checkpoint),
            Path("/data/run/capper-known-sites.txt"),
        )


class OnlyNewLeadsPipelineTests(unittest.TestCase):
    def _run(self, tmp: Path, *, only_new: bool, skip_sites: bool):
        results = [
            SearchResult(title="A", url="https://a.example/"),
            SearchResult(title="B", url="https://b.example/"),
        ]

        class FakeProvider:
            def search(self, category, location, limit, countries=()):
                return list(results)

        class FakeCrawler:
            def __init__(self, config, on_page=None, on_lead=None):
                self.on_page = on_page

            def crawl_result(self, result: SearchResult, category: str):
                if result.url == "https://a.example/":
                    return [make_lead("info@a.example", "https://a.example/")]
                return [make_lead("info@b.example", "https://b.example/")]

        output = tmp / "leads.csv"
        checkpoint = tmp / "checkpoint.json"
        with patch("lead_research.pipeline.LeadCrawler", FakeCrawler):
            return run_discovery(
                provider=FakeProvider(),
                config=DiscoveryConfig(
                    category="hotel",
                    limit=2,
                    max_leads=10,
                    workers=1,
                    delay=0.0,
                    only_new_leads=only_new,
                    skip_known_sites=skip_sites,
                ),
                suppression=SuppressionList(None),
                output=output,
                checkpoint=checkpoint,
                resume=False,
            )

    def test_second_run_excludes_previously_found_leads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            first = self._run(tmp, only_new=True, skip_sites=False)
            self.assertEqual(first.leads_found, 2)
            self.assertEqual(first.known_skipped, 0)

            second = self._run(tmp, only_new=True, skip_sites=False)
            self.assertEqual(second.leads_found, 0)
            self.assertEqual(second.known_skipped, 2)

            ledger_file = tmp / "capper-known-leads.txt"
            self.assertTrue(ledger_file.exists())

    def test_skip_known_sites_avoids_recrawl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            first = self._run(tmp, only_new=False, skip_sites=True)
            self.assertEqual(first.websites_done, 2)

            second = self._run(tmp, only_new=False, skip_sites=True)
            self.assertEqual(second.sites_skipped_known, 2)
            self.assertEqual(second.websites_done, 0)


if __name__ == "__main__":
    unittest.main()
