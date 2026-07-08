from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.checkpoint import (
    DiscoveryCheckpoint,
    append_crawled_urls_sidecar,
    checkpoint_crawled_path,
    checkpoint_to_payload,
    load_checkpoint_gui_metadata,
    load_discovery_checkpoint,
    mark_result_crawled,
    new_discovery_checkpoint,
    save_discovery_checkpoint,
    validate_checkpoint_config,
    write_discovery_checkpoint_payload,
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

    def test_checkpoint_roundtrip_directory_progress(self) -> None:
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
            checkpoint.directory_completed_locations = ["Berlin", "Hamburg"]
            checkpoint.directory_partial_results = [
                {"title": "Berlin", "url": "https://berlin.example", "snippet": ""},
            ]
            checkpoint.directory_seen_keys = ["url:https://berlin.example"]
            save_discovery_checkpoint(path, checkpoint)
            loaded = load_discovery_checkpoint(path)

        assert loaded is not None
        self.assertEqual(loaded.directory_completed_locations, ["Berlin", "Hamburg"])
        self.assertEqual(loaded.directory_search_result_objects()[0].url, "https://berlin.example")

    def test_mark_result_crawled_reuses_cached_set(self) -> None:
        checkpoint = new_discovery_checkpoint(
            category="hotel",
            location="Berlin",
            countries=("DE",),
            limit=10,
            max_leads=10,
            dedupe_by="email",
        )
        first = SearchResult(title="A", url="https://a.example/")
        second = SearchResult(title="B", url="https://b.example/")
        mark_result_crawled(checkpoint, first)
        crawled_set_id = id(checkpoint.crawled_url_set)
        mark_result_crawled(checkpoint, second)
        self.assertIs(checkpoint.crawled_url_set, checkpoint.crawled_url_set)
        self.assertEqual(id(checkpoint.crawled_url_set), crawled_set_id)
        self.assertEqual(len(checkpoint.crawled_urls), 2)

    def test_load_checkpoint_gui_metadata_reads_config_without_full_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = new_discovery_checkpoint(
                category="hotel",
                location="Berlin",
                countries=("DE",),
                limit=5000,
                max_leads=20000,
                dedupe_by="email",
            )
            checkpoint.config["gui_settings"] = {"workers": "16", "use_google_maps": False}
            checkpoint.search_complete = True
            checkpoint.search_results = [
                {"title": f"Hotel {idx}", "url": f"https://hotel{idx}.example/", "snippet": ""}
                for idx in range(250)
            ]
            checkpoint.crawled_urls = [f"https://done{idx}.example/" for idx in range(120)]
            checkpoint.leads = [
                {
                    "category": "hotel",
                    "source_url": "https://done0.example/",
                    "website": "https://done0.example/",
                    "email": f"lead{idx}@example.com",
                    "company_name": "Hotel",
                    "phone": "",
                    "page_title": "",
                    "consent_status": "business_public",
                    "notes": [],
                    "discovered_at": "2026-01-01T00:00:00+00:00",
                }
                for idx in range(15)
            ]
            save_discovery_checkpoint(path, checkpoint)
            metadata = load_checkpoint_gui_metadata(path)
            loaded = load_discovery_checkpoint(path)

        assert metadata is not None
        assert loaded is not None
        self.assertEqual(metadata["category"], "hotel")
        self.assertEqual(metadata["location"], "Berlin")
        self.assertEqual(metadata["workers"], "16")
        self.assertFalse(metadata["use_google_maps"])
        self.assertIn("250 Websites", str(metadata["progress_summary"]))
        self.assertIn("120 gecrawlt", str(metadata["progress_summary"]))
        self.assertIn("15 Leads", str(metadata["progress_summary"]))
        self.assertEqual(len(loaded.search_results), 250)

    def test_large_checkpoint_uses_search_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = new_discovery_checkpoint(
                category="hotel",
                location="Berlin",
                countries=("DE",),
                limit=6000,
                max_leads=20000,
                dedupe_by="email",
            )
            checkpoint.search_complete = True
            checkpoint.search_results = [
                {"title": f"Hotel {idx}", "url": f"https://hotel{idx}.example/", "snippet": ""}
                for idx in range(5100)
            ]
            checkpoint.crawled_urls = ["https://done0.example/"]
            save_discovery_checkpoint(path, checkpoint)
            sidecar = path.with_name(f"{path.stem}-search{path.suffix}")
            self.assertTrue(sidecar.exists())

            incremental_payload = {
                "version": 1,
                "config": checkpoint.config,
                "search_complete": True,
                "search_results_external": True,
                "search_results": [],
                "stats": {
                    "search_results": 5100,
                    "crawled_urls": 1,
                    "leads": 0,
                    "directory_completed_locations": 0,
                },
                "zenrows_completed_plans": [],
                "directory_completed_locations": [],
                "directory_partial_results": [],
                "directory_seen_keys": [],
                "crawled_urls": ["https://done0.example/"],
                "leads": [],
            }
            path.write_text(__import__("json").dumps(incremental_payload), encoding="utf-8")
            loaded = load_discovery_checkpoint(path)

        assert loaded is not None
        self.assertEqual(len(loaded.search_results), 5100)

    def test_incremental_checkpoint_loads_crawled_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = new_discovery_checkpoint(
                category="hotel",
                location="Berlin",
                countries=("DE",),
                limit=6000,
                max_leads=20000,
                dedupe_by="email",
            )
            checkpoint.search_complete = True
            checkpoint.search_results = [
                {"title": f"Hotel {idx}", "url": f"https://hotel{idx}.example/", "snippet": ""}
                for idx in range(5100)
            ]
            append_crawled_urls_sidecar(path, ["https://done0.example/", "https://done1.example/"])
            payload = checkpoint_to_payload(checkpoint, path, incremental=True)
            payload["crawled_urls_external"] = True
            write_discovery_checkpoint_payload(path, payload, create_backup=False)
            loaded = load_discovery_checkpoint(path)

            assert loaded is not None
            self.assertEqual(
                loaded.crawled_urls,
                ["https://done0.example/", "https://done1.example/"],
            )
            self.assertTrue(checkpoint_crawled_path(path).exists())

    def test_load_discovery_checkpoint_recovers_from_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = new_discovery_checkpoint(
                category="hotel",
                location="Berlin",
                countries=("DE",),
                limit=10,
                max_leads=10,
                dedupe_by="email",
            )
            checkpoint.search_results = [{"title": "Hotel", "url": "https://hotel.example/", "snippet": ""}]
            save_discovery_checkpoint(path, checkpoint)
            save_discovery_checkpoint(path, checkpoint)
            path.write_text("{broken", encoding="utf-8")
            loaded = load_discovery_checkpoint(path)

        assert loaded is not None
        self.assertEqual(loaded.search_result_objects()[0].url, "https://hotel.example/")


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

    def test_directory_search_resume_skips_completed_locations(self) -> None:
        from lead_research.directories import DirectoryEntry
        from lead_research.search import DirectoryResumeState, DirectorySearchProvider

        cities = ["Berlin", "Hamburg", "Muenchen"]
        visited: list[str] = []

        def fake_scraper(category: str, location: str, limit: int) -> list[DirectoryEntry]:
            visited.append(location)
            slug = location.lower()
            return [
                DirectoryEntry(
                    name=f"Firma {location}",
                    website=f"https://{slug}.example",
                    source_url=f"https://source/{slug}",
                )
            ]

        provider = DirectorySearchProvider(zenrows_api_key="test-key", allow_direct_fetch=True)
        resume_state = DirectoryResumeState(
            results=[SearchResult(title="Berlin", url="https://berlin.example")],
            seen={"url:https://berlin.example"},
            completed_locations={"Berlin", "Hamburg"},
        )
        with patch("lead_research.directories.get_directory_scrapers", return_value=(("Test", fake_scraper),)), patch(
            "lead_research.directories.directory_location_plans",
            return_value=cities,
        ), patch("lead_research.directories.configure_directory_fetch"):
            results = provider.search("logistik", "", 10, ("DE",), resume_state=resume_state)

        self.assertEqual(visited, ["Muenchen"])
        self.assertEqual(len(results), 2)
        self.assertEqual({result.url for result in results}, {"https://berlin.example", "https://muenchen.example"})


if __name__ == "__main__":
    unittest.main()
