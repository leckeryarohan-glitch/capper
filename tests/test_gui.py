from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.gui import build_simple_gui_argv, run_gui_discovery
from lead_research.models import Lead, SearchResult
from lead_research.search import SearchProviderError


class FakeProvider:
    providers = ["osm"]

    def search(self, category: str, location: str, limit: int, countries=()) -> list[SearchResult]:
        return [SearchResult(title="Hotel Beispiel", url="https://hotel.example")]


class FakeCrawler:
    def __init__(self, config, on_page=None, on_lead=None):
        self.config = config
        self.on_page = on_page
        self.on_lead = on_lead

    def crawl_result(self, result: SearchResult, category: str) -> list[Lead]:
        if self.on_page:
            self.on_page("https://hotel.example/kontakt")
        lead = Lead(
            category=category,
            source_url=result.url,
            website="https://hotel.example/kontakt",
            email="info@hotel.example",
            company_name="Hotel Beispiel",
        )
        duplicate = Lead(
            category=category,
            source_url=result.url,
            website="https://hotel.example/impressum",
            email="info@hotel.example",
            company_name="Hotel Beispiel",
        )
        return [lead, duplicate]


class GuiArgumentTests(unittest.TestCase):
    def test_builds_simple_common_source_discover_command(self) -> None:
        argv = build_simple_gui_argv(
            {
                "category": "hotel",
                "location": "Berlin",
                "output": "leads.csv",
                "suppression_file": "examples/suppression.txt",
            }
        )

        self.assertEqual(argv[0], "discover")
        self.assertIn("--category", argv)
        self.assertIn("hotel", argv)
        self.assertIn("--provider", argv)
        self.assertIn("all", argv)
        self.assertIn("--workers", argv)
        self.assertIn("--max-leads", argv)
        self.assertIn("--limit", argv)
        self.assertIn("--location", argv)
        self.assertIn("Berlin", argv)
        self.assertIn("--suppression-file", argv)

    def test_allows_only_category_as_required_input(self) -> None:
        argv = build_simple_gui_argv({"category": "restaurant"})

        self.assertIn("restaurant", argv)
        self.assertIn("leads.csv", argv)
        self.assertNotIn("--location", argv)

    def test_rejects_missing_category(self) -> None:
        with self.assertRaises(ValueError):
            build_simple_gui_argv({"category": "  "})

    def test_run_gui_discovery_emits_progress_stats_and_dedupes(self) -> None:
        events: "queue.Queue[tuple]" = queue.Queue()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.gui.combined_provider", return_value=FakeProvider()), patch(
                "lead_research.pipeline.LeadCrawler", FakeCrawler
            ):
                exit_code = run_gui_discovery(
                    {
                        "category": "hotel",
                        "location": "Berlin",
                        "output": str(output),
                    },
                    events,
                )

        self.assertEqual(exit_code, 0)
        emitted = []
        while not events.empty():
            emitted.append(events.get())

        kinds = [event[0] for event in emitted]
        self.assertIn(("total", 1), emitted)
        self.assertIn("progress", kinds)
        self.assertIn("lead", kinds)

        finished = [event for event in emitted if event[0] == "finished"]
        self.assertEqual(len(finished), 1)
        stats = finished[0][1]
        self.assertEqual(stats.leads_found, 1)
        self.assertEqual(stats.duplicates_skipped, 1)

    def test_run_gui_discovery_passes_source_toggles(self) -> None:
        events: "queue.Queue[tuple]" = queue.Queue()
        captured: dict[str, object] = {}

        def fake_combined_provider(**kwargs):
            captured.update(kwargs)
            return FakeProvider()

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.gui.combined_provider", side_effect=fake_combined_provider), patch(
                "lead_research.pipeline.LeadCrawler", FakeCrawler
            ):
                run_gui_discovery(
                    {
                        "category": "hotel",
                        "output": str(output),
                        "use_osm": False,
                        "use_duckduckgo": True,
                        "use_directories": True,
                        "use_zenrows_google": False,
                        "use_serpapi": False,
                        "zenrows_key": "zr-key",
                    },
                    events,
                )

        self.assertFalse(captured["use_osm"])
        self.assertTrue(captured["use_duckduckgo"])
        self.assertTrue(captured["use_directories"])
        self.assertFalse(captured["use_zenrows_google"])
        self.assertFalse(captured["use_serpapi"])

    def test_run_gui_discovery_requires_zenrows_key_for_directories(self) -> None:
        events: "queue.Queue[tuple]" = queue.Queue()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with self.assertRaises(SearchProviderError):
                run_gui_discovery(
                    {
                        "category": "hotel",
                        "output": str(output),
                        "use_osm": True,
                        "use_directories": True,
                        "use_zenrows_google": False,
                        "zenrows_key": "",
                    },
                    events,
                )


if __name__ == "__main__":
    unittest.main()
