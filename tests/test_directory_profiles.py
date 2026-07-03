from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lead_research.directory_profiles import (
    DEFAULT_MASS_DIRECTORY_SOURCES,
    match_category_profile_key,
    resolve_mass_directory_sources,
)
from lead_research.directory_registry import default_enabled_directory_source_ids
from lead_research.directories import build_directory_source_registry
from lead_research.mass import mass_city_locations, run_mass_discovery
from lead_research.models import Lead


class DirectoryProfileTests(unittest.TestCase):
    def test_match_category_profile_key_handles_umlauts(self) -> None:
        self.assertEqual(match_category_profile_key("Steuerberater"), "steuerberater")
        self.assertEqual(match_category_profile_key("Zahnärzte Berlin"), "zahnarzt")

    def test_resolve_mass_directory_sources_for_steuerberater(self) -> None:
        sources = resolve_mass_directory_sources("Steuerberater")
        valid = default_enabled_directory_source_ids(build_directory_source_registry())

        self.assertTrue(sources)
        self.assertTrue(sources.issubset(valid))
        self.assertIn("steuerberater", sources)
        self.assertIn("gelbeseiten", sources)

    def test_resolve_mass_directory_sources_for_versand_excludes_irrelevant(self) -> None:
        sources = resolve_mass_directory_sources("versand")

        self.assertIn("wlw", sources)
        self.assertIn("gelbeseiten", sources)
        self.assertNotIn("jameda", sources)
        self.assertNotIn("treatwell", sources)
        self.assertNotIn("branchen_restaurants", sources)

    def test_resolve_category_directory_sources_intersects_gui_selection(self) -> None:
        from lead_research.directory_profiles import resolve_category_directory_sources

        sources = resolve_category_directory_sources("versand", {"gelbeseiten", "jameda", "wlw"})
        self.assertEqual(sources, {"gelbeseiten", "wlw"})

    def test_resolve_mass_directory_sources_falls_back_to_default(self) -> None:
        sources = resolve_mass_directory_sources("Unbekannte Nische XYZ")
        valid = default_enabled_directory_source_ids(build_directory_source_registry())

        self.assertTrue(sources)
        self.assertTrue(sources.issubset(valid))
        self.assertTrue(sources.intersection(DEFAULT_MASS_DIRECTORY_SOURCES))


class MassDiscoveryTests(unittest.TestCase):
    def test_mass_city_locations_returns_german_cities(self) -> None:
        locations = mass_city_locations(("DE",))

        self.assertGreater(len(locations), 1000)
        self.assertIn("Berlin", locations)

    def test_run_mass_discovery_uses_category_profile_sources(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            checkpoint = Path(tmp) / "checkpoint.json"

            class FakeProvider:
                def search(self, category: str, location: str, limit: int, **kwargs) -> list:
                    return []

            with patch("lead_research.mass.DirectorySearchProvider") as provider_cls:
                provider_cls.return_value = FakeProvider()
                run_mass_discovery(
                    category="Steuerberater",
                    countries=("DE",),
                    crawl_config=__import__("lead_research.crawl", fromlist=["CrawlConfig"]).CrawlConfig(),
                    limit_per_query=10,
                    target_leads=5,
                    output=output,
                    suppression_file=None,
                    checkpoint=checkpoint,
                    resume=False,
                    query_delay=0.0,
                    query_parallel=1,
                    directory_parallel=4,
                    directory_detail_parallel=4,
                )

            provider_cls.assert_called_once()
            kwargs = provider_cls.call_args.kwargs
            self.assertTrue(kwargs["mass_mode"])
            self.assertIn("steuerberater", kwargs["enabled_directory_sources"])

    def test_run_mass_discovery_writes_checkpointed_results(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "leads.csv"
            checkpoint = Path(tmp) / "checkpoint.json"

            class FakeProvider:
                def search(self, category: str, location: str, limit: int, **kwargs) -> list:
                    from lead_research.models import SearchResult

                    if location != "Berlin":
                        return []
                    return [
                        SearchResult(
                            title="Demo GmbH",
                            url="https://www.demo-gmbh.example",
                            snippet="",
                        )
                    ]

            with patch("lead_research.mass.mass_city_locations", return_value=["Berlin", "Hamburg"]):
                with patch("lead_research.batch.LeadCrawler.crawl_result") as crawl_result:
                    crawl_result.return_value = [
                        Lead(
                            company_name="Demo GmbH",
                            website="https://www.demo-gmbh.example",
                            email="info@demo-gmbh.example",
                            category="Steuerberater",
                            source_url="https://www.demo-gmbh.example",
                        )
                    ]
                    count = run_mass_discovery(
                        category="Steuerberater",
                        countries=("DE",),
                        crawl_config=__import__("lead_research.crawl", fromlist=["CrawlConfig"]).CrawlConfig(),
                        limit_per_query=10,
                        target_leads=10,
                        output=output,
                        suppression_file=None,
                        checkpoint=checkpoint,
                        resume=False,
                        query_delay=0.0,
                        query_parallel=1,
                        provider=FakeProvider(),
                    )

            self.assertEqual(count, 1)
            self.assertTrue(output.exists())
            self.assertTrue(checkpoint.exists())


if __name__ == "__main__":
    unittest.main()
