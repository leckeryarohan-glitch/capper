from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.gui import build_simple_gui_argv, run_gui_discovery
from lead_research.models import Lead, SearchResult


class FakeProvider:
    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        return [SearchResult(title="Hotel Beispiel", url="https://hotel.example")]


class FakeCrawler:
    def __init__(self, config, on_page=None, on_lead=None):
        self.on_page = on_page
        self.on_lead = on_lead

    def crawl_result(self, result: SearchResult, category: str) -> list[Lead]:
        lead = Lead(
            category=category,
            source_url=result.url,
            website="https://hotel.example/kontakt",
            email="info@hotel.example",
            company_name="Hotel Beispiel",
        )
        if self.on_page:
            self.on_page("https://hotel.example/kontakt")
        if self.on_lead:
            self.on_lead(lead)
        return [lead]


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
        self.assertIn("osm", argv)
        self.assertNotIn("--source-profile", argv)
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

    def test_run_gui_discovery_emits_progress_and_leads(self) -> None:
        events: "queue.Queue[tuple]" = queue.Queue()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.gui.provider_from_name", return_value=FakeProvider()), patch(
                "lead_research.gui.LeadCrawler", FakeCrawler
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
        self.assertIn(("total", 1), emitted)
        self.assertTrue(any(event[0] == "progress" for event in emitted))
        self.assertTrue(any(event[0] == "lead" for event in emitted))
        self.assertTrue(any(event[0] == "finished" and event[1] == 1 for event in emitted))


if __name__ == "__main__":
    unittest.main()
