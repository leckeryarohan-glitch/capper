from __future__ import annotations

import unittest

from lead_research.models import SearchResult
from lead_research.search import CommonSourcesSearchProvider, SearchProvider, google_items_to_results


class RecordingProvider(SearchProvider):
    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        self.calls.append((category, location, limit))
        domain = category.split("site:", 1)[-1]
        return [SearchResult(title=domain, url=f"https://{domain}/kontakt")]


class SearchTests(unittest.TestCase):
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
