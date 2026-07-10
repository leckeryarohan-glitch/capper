from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.models import Lead, LeadDeduplicator, SearchResult, dedupe_leads
from lead_research.pipeline import DiscoveryConfig, build_crawl_config, run_discovery
from lead_research.suppression import SuppressionList


def make_lead(email: str, website: str) -> Lead:
    return Lead(
        category="hotel",
        source_url=website,
        website=website,
        email=email,
        company_name="Example",
    )


class DedupTests(unittest.TestCase):
    def test_dedupe_leads_by_email_removes_cross_site_duplicates(self) -> None:
        leads = [
            make_lead("info@example.test", "https://a.test"),
            make_lead("info@example.test", "https://b.test"),
            make_lead("sales@example.test", "https://a.test"),
        ]

        unique = dedupe_leads(leads, by="email")

        self.assertEqual([lead.email for lead in unique], ["info@example.test", "sales@example.test"])

    def test_lead_deduplicator_is_new_only_once(self) -> None:
        dedup = LeadDeduplicator(by="email")
        lead = make_lead("info@example.test", "https://a.test")

        self.assertTrue(dedup.is_new(lead))
        self.assertFalse(dedup.is_new(make_lead("INFO@example.test", "https://b.test")))
        self.assertEqual(len(dedup), 1)


class FakeProvider:
    def __init__(self, results):
        self._results = results

    def search(self, category, location, limit, countries=()):
        return self._results


class FakeCrawler:
    leads_by_url: dict[str, list[Lead]] = {}

    def __init__(self, config, on_page=None, on_lead=None):
        self.on_page = on_page

    def crawl_result(self, result: SearchResult, category: str) -> list[Lead]:
        if self.on_page:
            self.on_page(result.url)
        return FakeCrawler.leads_by_url.get(result.url, [])


class PipelineTests(unittest.TestCase):
    def test_run_discovery_dedupes_and_collects_stats(self) -> None:
        results = [
            SearchResult(title="A", url="https://a.test"),
            SearchResult(title="B", url="https://b.test"),
        ]
        FakeCrawler.leads_by_url = {
            "https://a.test": [make_lead("info@a.test", "https://a.test")],
            "https://b.test": [
                # Duplicate email; keep the same website so unique_domains stays
                # deterministic regardless of which site finishes first (workers=2).
                make_lead("info@a.test", "https://a.test"),
                make_lead("kontakt@b.test", "https://b.test"),
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.pipeline.LeadCrawler", FakeCrawler):
                stats = run_discovery(
                    provider=FakeProvider(results),
                    config=DiscoveryConfig(category="hotel", workers=2, delay=0.0),
                    suppression=SuppressionList(None),
                    output=output,
                )

            with output.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(stats.leads_found, 2)
        self.assertEqual(stats.duplicates_skipped, 1)
        self.assertEqual(stats.websites_done, 2)
        self.assertEqual(stats.unique_domains, 2)
        emails = sorted(row["email"] for row in rows)
        self.assertEqual(emails, ["info@a.test", "kontakt@b.test"])

    def test_run_discovery_emits_page_and_site_events(self) -> None:
        results = [SearchResult(title="A", url="https://a.test")]
        FakeCrawler.leads_by_url = {"https://a.test": [make_lead("info@a.test", "https://a.test")]}
        events: list[tuple] = []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.pipeline.LeadCrawler", FakeCrawler):
                run_discovery(
                    provider=FakeProvider(results),
                    config=DiscoveryConfig(category="hotel", workers=1, delay=0.0),
                    suppression=SuppressionList(None),
                    output=output,
                    on_event=lambda *event: events.append(event),
                )

        kinds = [event[0] for event in events]
        self.assertIn("status", kinds)
        self.assertIn("page", kinds)
        self.assertIn("site_done", kinds)
        self.assertIn("finished", kinds)

    def test_run_discovery_respects_max_leads(self) -> None:
        results = [SearchResult(title=f"S{i}", url=f"https://s{i}.test") for i in range(5)]
        FakeCrawler.leads_by_url = {
            f"https://s{i}.test": [make_lead(f"info@s{i}.test", f"https://s{i}.test")] for i in range(5)
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.pipeline.LeadCrawler", FakeCrawler):
                stats = run_discovery(
                    provider=FakeProvider(results),
                    config=DiscoveryConfig(category="hotel", workers=1, delay=0.0, max_leads=2),
                    suppression=SuppressionList(None),
                    output=output,
                )

        self.assertLessEqual(stats.leads_found, 2)

    def test_build_crawl_config_uses_fast_resume_for_large_pending(self) -> None:
        config = DiscoveryConfig(category="hotel", delay=2.0, respect_robots=True)
        fast = build_crawl_config(config=config, resume=True, pending_sites=500)
        normal = build_crawl_config(config=config, resume=False, pending_sites=500)

        self.assertEqual(fast.delay_seconds, 0.0)
        self.assertFalse(fast.respect_robots)
        self.assertLess(fast.site_timeout_seconds, normal.site_timeout_seconds)
        self.assertLess(fast.request_timeout_seconds, normal.request_timeout_seconds)


if __name__ == "__main__":
    unittest.main()
