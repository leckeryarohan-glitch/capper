from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.checkpoint import (
    DiscoveryCheckpoint,
    load_discovery_checkpoint,
    new_discovery_checkpoint,
    save_discovery_checkpoint,
    validate_checkpoint_config,
)
from lead_research.models import Lead, SearchResult
from lead_research.pipeline import DiscoveryConfig, run_discovery
from lead_research.search import ZenRowsSearchProvider
from lead_research.suppression import SuppressionList


def make_lead(email: str, website: str) -> Lead:
    return Lead(
        category="logistik",
        source_url=website,
        website=website,
        email=email,
        company_name="Example",
    )


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = new_discovery_checkpoint(
                category="logistik",
                location="",
                countries=("DE",),
                limit=5000,
                max_leads=20000,
                dedupe_by="email",
            )
            checkpoint.search_results = [
                {
                    "title": "Spedition Demo",
                    "url": "",
                    "snippet": "Gelbe Seiten",
                    "directory_email": "info@spedition-demo.example",
                    "directory_source_url": "https://www.gelbeseiten.de/gsbiz/demo",
                },
            ]
            checkpoint.zenrows_completed_plans = ["DE\tlogistik Berlin"]
            checkpoint.crawled_urls = ["https://a.example/"]
            checkpoint.leads = [
                {
                    "category": "logistik",
                    "source_url": "https://a.example/",
                    "website": "https://a.example/",
                    "email": "info@a.example",
                    "company_name": "A",
                    "phone": "",
                    "page_title": "",
                    "consent_status": "business_public",
                    "notes": [],
                    "discovered_at": "2026-01-01T00:00:00+00:00",
                }
            ]
            save_discovery_checkpoint(path, checkpoint)
            loaded = load_discovery_checkpoint(path)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.search_results), 1)
        restored = loaded.search_result_objects()[0]
        self.assertEqual(restored.directory_email, "info@spedition-demo.example")
        self.assertEqual(restored.directory_source_url, "https://www.gelbeseiten.de/gsbiz/demo")
        self.assertEqual(loaded.zenrows_completed_plans, ["DE\tlogistik Berlin"])
        self.assertEqual(len(loaded.leads), 1)

    def test_validate_checkpoint_config_detects_mismatch(self) -> None:
        checkpoint = DiscoveryCheckpoint(
            config={
                "category": "hotel",
                "location": "",
                "countries": ["DE"],
                "limit": 100,
                "max_leads": 1000,
                "dedupe_by": "email",
            }
        )
        expected = {
            "category": "logistik",
            "location": "",
            "countries": ["DE"],
            "limit": 100,
            "max_leads": 1000,
            "dedupe_by": "email",
        }
        with self.assertRaises(ValueError):
            validate_checkpoint_config(checkpoint, expected)


class ResumableDiscoveryTests(unittest.TestCase):
    def test_run_discovery_resumes_crawl_from_checkpoint(self) -> None:
        results = [
            SearchResult(title="A", url="https://a.example/"),
            SearchResult(title="B", url="https://b.example/"),
        ]

        class FakeProvider:
            def search(self, category, location, limit, countries=()):
                raise AssertionError("search should be skipped when search_complete")

        FakeCrawler_calls: list[str] = []

        class FakeCrawler:
            def __init__(self, config, on_page=None, on_lead=None):
                self.on_page = on_page

            def crawl_result(self, result: SearchResult, category: str):
                FakeCrawler_calls.append(result.url)
                if self.on_page:
                    self.on_page(result.url)
                if result.url == "https://b.example/":
                    return [make_lead("kontakt@b.example", "https://b.example/")]
                return []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            checkpoint = Path(tmp) / "checkpoint.json"
            checkpoint_data = new_discovery_checkpoint(
                category="logistik",
                location="",
                countries=("DE",),
                limit=2,
                max_leads=10,
                dedupe_by="email",
            )
            checkpoint_data.search_complete = True
            checkpoint_data.search_results = [
                {"title": "A", "url": "https://a.example/", "snippet": ""},
                {"title": "B", "url": "https://b.example/", "snippet": ""},
            ]
            checkpoint_data.crawled_urls = ["https://a.example/"]
            checkpoint_data.leads = [
                {
                    "category": "logistik",
                    "source_url": "https://a.example/",
                    "website": "https://a.example/",
                    "email": "info@a.example",
                    "company_name": "A",
                    "phone": "",
                    "page_title": "",
                    "consent_status": "business_public",
                    "notes": [],
                    "discovered_at": "2026-01-01T00:00:00+00:00",
                }
            ]
            save_discovery_checkpoint(checkpoint, checkpoint_data)

            with patch("lead_research.pipeline.LeadCrawler", FakeCrawler):
                stats = run_discovery(
                    provider=FakeProvider(),
                    config=DiscoveryConfig(
                        category="logistik",
                        limit=2,
                        max_leads=10,
                        workers=1,
                        delay=0.0,
                    ),
                    suppression=SuppressionList(None),
                    output=output,
                    checkpoint=checkpoint,
                    resume=True,
                )

        self.assertEqual(FakeCrawler_calls, ["https://b.example/"])
        self.assertEqual(stats.leads_found, 2)
        self.assertEqual(stats.websites_done, 2)

    def test_zenrows_search_resume_skips_completed_plans(self) -> None:
        captured: list[str] = []

        def fake_read_json_with_retry(request, timeout=120, retries=3, backoff_seconds=3.0, **kwargs):
            import urllib.parse

            params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            google_url = params.get("url", [""])[0]
            google_params = urllib.parse.parse_qs(urllib.parse.urlparse(google_url).query)
            query_text = google_params.get("q", [""])[0]
            start = google_params.get("start", ["0"])[0]
            if start != "0":
                return {"organic_results": []}
            captured.append(query_text)
            idx = len(captured)
            return {"organic_results": [{"title": str(idx), "link": f"https://zr{idx}.example/"}]}

        provider = ZenRowsSearchProvider(api_key="zr-key")
        from lead_research.search import ZenRowsResumeState, zenrows_plan_key

        resume_state = ZenRowsResumeState(
            results=[SearchResult(title="1", url="https://zr0.example/")],
            seen_urls={"https://zr0.example"},
            completed_plans={zenrows_plan_key("logistik Deutschland", "DE")},
        )
        with patch("lead_research.search._read_json_with_retry", side_effect=fake_read_json_with_retry), patch(
            "lead_research.search.time.sleep"
        ), patch(
            "lead_research.search.zenrows_query_plans",
            return_value=[
                ("logistik Deutschland", "DE"),
                ("logistik Berlin", "DE"),
            ],
        ):
            results = provider.search("logistik", "", 3, ("DE",), resume_state=resume_state)

        self.assertEqual(len(results), 2)
        self.assertNotIn("logistik Deutschland", captured)


if __name__ == "__main__":
    unittest.main()
