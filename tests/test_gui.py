from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_research.gui import (
    apply_gui_settings,
    build_simple_gui_argv,
    checkpoint_settings_for_gui,
    collect_gui_settings,
    run_gui_discovery,
)
from lead_research.checkpoint import new_discovery_checkpoint, save_discovery_checkpoint
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
                exit_code =                 run_gui_discovery(
                    {
                        "category": "hotel",
                        "location": "Berlin",
                        "output": str(output),
                        "use_osm": True,
                        "use_duckduckgo": False,
                        "use_directories": False,
                        "use_zenrows_google": False,
                        "use_google_maps": False,
                        "use_serpapi": False,
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
        self.assertEqual(captured["directory_parallel_requests"], 40)

    def test_run_gui_discovery_passes_directory_parallel_requests(self) -> None:
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
                        "use_directories": True,
                        "use_zenrows_google": False,
                        "use_serpapi": False,
                        "zenrows_key": "zr-key",
                        "directory_parallel": "50",
                    },
                    events,
                )

        self.assertEqual(captured["directory_parallel_requests"], 50)

    def test_collect_and_apply_gui_settings_roundtrip(self) -> None:
        original = {
            "category": "logistik",
            "location": "Berlin",
            "max_leads": "12000",
            "limit": "3000",
            "workers": "8",
            "directory_parallel": "40",
            "use_osm": False,
            "use_duckduckgo": True,
            "use_directories": True,
            "use_zenrows_google": False,
            "use_serpapi": False,
            "country_de": True,
            "country_at": True,
            "dir_source_gelbeseiten": True,
            "dir_source_cylex": False,
        }
        collected = collect_gui_settings(original)
        target: dict[str, object] = {}
        apply_gui_settings(target, collected)
        self.assertEqual(target["category"], "logistik")
        self.assertEqual(target["location"], "Berlin")
        self.assertEqual(target["limit"], "3000")
        self.assertEqual(target["max_leads"], "12000")
        self.assertEqual(target["workers"], "8")
        self.assertEqual(target["directory_parallel"], "40")
        self.assertFalse(target["use_osm"])
        self.assertFalse(target["dir_source_cylex"])
        self.assertTrue(target["dir_source_gelbeseiten"])

    def test_checkpoint_settings_for_gui_reads_saved_config(self) -> None:
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
            checkpoint.config["gui_settings"] = collect_gui_settings(
                {
                    "category": "logistik",
                    "location": "",
                    "limit": "5000",
                    "max_leads": "20000",
                    "workers": "12",
                    "directory_parallel": "50",
                    "use_osm": True,
                    "use_duckduckgo": False,
                    "use_directories": True,
                    "use_zenrows_google": True,
                    "use_serpapi": False,
                    "country_de": True,
                    "country_at": False,
                    "dir_source_gelbeseiten": True,
                }
            )
            checkpoint.directory_completed_locations = ["Berlin"]
            save_discovery_checkpoint(path, checkpoint)
            settings = checkpoint_settings_for_gui(path)

        assert settings is not None
        self.assertEqual(settings["category"], "logistik")
        self.assertEqual(settings["workers"], "12")
        self.assertEqual(settings["directory_parallel"], "50")
        self.assertFalse(settings["use_duckduckgo"])
        self.assertIn("Berlin", str(settings["progress_summary"]))

    def test_run_gui_discovery_passes_directory_source_ids(self) -> None:
        from lead_research.directories import build_directory_source_registry
        from lead_research.directory_registry import implemented_directory_sources

        events: "queue.Queue[tuple]" = queue.Queue()
        captured: dict[str, object] = {}

        def fake_combined_provider(**kwargs):
            captured.update(kwargs)
            return FakeProvider()

        implemented_ids = {spec.id for spec in implemented_directory_sources(build_directory_source_registry())}
        directory_flags = {f"dir_source_{source_id}": source_id == "gelbeseiten" for source_id in implemented_ids}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch("lead_research.gui.combined_provider", side_effect=fake_combined_provider), patch(
                "lead_research.pipeline.LeadCrawler", FakeCrawler
            ):
                run_gui_discovery(
                    {
                        "category": "hotel",
                        "output": str(output),
                        "use_directories": True,
                        "use_zenrows_google": False,
                        "use_serpapi": False,
                        "zenrows_key": "zr-key",
                        **directory_flags,
                    },
                    events,
                )

        self.assertEqual(captured["enabled_directory_sources"], {"gelbeseiten"})

    def test_run_gui_discovery_filters_directory_sources_by_category_profile(self) -> None:
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
                        "category": "versand",
                        "output": str(output),
                        "use_osm": False,
                        "use_duckduckgo": False,
                        "use_directories": True,
                        "use_zenrows_google": False,
                        "use_serpapi": False,
                        "zenrows_key": "zr-key",
                    },
                    events,
                )

        enabled = captured["enabled_directory_sources"]
        self.assertIn("wlw", enabled)
        self.assertIn("gelbeseiten", enabled)
        self.assertNotIn("jameda", enabled)
        self.assertNotIn("treatwell", enabled)
        self.assertTrue(captured["directory_mass_mode"])

    def test_run_gui_discovery_uses_env_zenrows_key_when_field_empty(self) -> None:
        events: "queue.Queue[tuple]" = queue.Queue()
        captured: dict[str, object] = {}

        def fake_combined_provider(**kwargs):
            captured.update(kwargs)
            return FakeProvider()

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with patch.dict("os.environ", {"ZENROWS_API_KEY": "env-key"}, clear=False), patch(
                "lead_research.gui.combined_provider", side_effect=fake_combined_provider
            ), patch("lead_research.pipeline.LeadCrawler", FakeCrawler):
                run_gui_discovery(
                    {
                        "category": "hotel",
                        "output": str(output),
                        "use_osm": False,
                        "use_duckduckgo": False,
                        "use_directories": True,
                        "use_zenrows_google": False,
                        "use_serpapi": False,
                        "zenrows_key": "",
                    },
                    events,
                )

        self.assertEqual(captured["zenrows_key"], "env-key")

    def test_run_gui_discovery_requires_zenrows_key_for_directories(self) -> None:
        events: "queue.Queue[tuple]" = queue.Queue()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            with self.assertRaises(SearchProviderError):
                run_gui_discovery(
                    {
                        "category": "hotel",
                        "output": str(output),
                        "use_directories": True,
                        "zenrows_key": "",
                        "dir_source_gelbeseiten": True,
                    },
                    events,
                )


if __name__ == "__main__":
    unittest.main()
