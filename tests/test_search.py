from __future__ import annotations

import unittest

from lead_research.models import SearchResult
from lead_research.search import (
    CommonSourcesSearchProvider,
    SearchProvider,
    build_overpass_query,
    google_items_to_results,
    osm_elements_to_results,
    osm_location_plan,
    osm_selectors_for_category,
)


class RecordingProvider(SearchProvider):
    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        self.calls.append((category, location, limit))
        domain = category.split("site:", 1)[-1]
        return [SearchResult(title=domain, url=f"https://{domain}/kontakt")]


class SearchTests(unittest.TestCase):
    def test_osm_selectors_map_common_categories(self) -> None:
        self.assertIn('["tourism"="hotel"]', osm_selectors_for_category("hotel"))
        self.assertIn('["shop"="electronics"]', osm_selectors_for_category("elektronik"))

    def test_build_overpass_query_scopes_to_location(self) -> None:
        query = build_overpass_query("hotel", "Berlin", 10)

        self.assertIn('area["name"="Berlin"]["boundary"="administrative"]', query)
        self.assertIn('nwr["tourism"="hotel"](area.searchArea);', query)
        self.assertIn("out tags center", query)

    def test_osm_location_plan_uses_default_cities_without_location(self) -> None:
        locations = osm_location_plan("")

        self.assertIn("Berlin", locations)
        self.assertIn("Hamburg", locations)
        self.assertGreater(len(locations), 3)

    def test_osm_location_plan_uses_given_location(self) -> None:
        self.assertEqual(osm_location_plan("Bremen"), ("Bremen",))

    def test_osm_elements_to_results_extracts_websites(self) -> None:
        results = osm_elements_to_results(
            {
                "elements": [
                    {
                        "tags": {
                            "name": "Hotel Beispiel",
                            "website": "hotel-beispiel.test",
                            "addr:city": "Berlin",
                        }
                    },
                    {"tags": {"name": "No website"}},
                ]
            },
            10,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Hotel Beispiel")
        self.assertEqual(results[0].url, "https://hotel-beispiel.test")
        self.assertIn("Berlin", results[0].snippet)

    def test_google_items_to_results_maps_custom_search_response(self) -> None:
        results = google_items_to_results(
            {
                "items": [
                    {
                        "title": "Hotel Beispiel",
                        "link": "https://hotel.example/kontakt",
                        "snippet": "Kontakt Hotel Beispiel",
                    },
                    {"title": "Missing link"},
                ]
            }
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Hotel Beispiel")
        self.assertEqual(results[0].url, "https://hotel.example/kontakt")
        self.assertEqual(results[0].snippet, "Kontakt Hotel Beispiel")

    def test_common_sources_searches_site_limited_queries(self) -> None:
        provider = RecordingProvider()
        common_provider = CommonSourcesSearchProvider(provider, domains=("gelbeseiten.de", "wlw.de"))

        results = common_provider.search("hotel", "Berlin", 2)

        self.assertEqual(len(results), 2)
        self.assertEqual(provider.calls[0][0], "hotel site:gelbeseiten.de")
        self.assertEqual(provider.calls[0][1], "Berlin")
        self.assertEqual(provider.calls[1][0], "hotel site:wlw.de")


if __name__ == "__main__":
    unittest.main()
