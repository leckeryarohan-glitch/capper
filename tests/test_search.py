from __future__ import annotations

import unittest

from lead_research.models import SearchResult
from unittest.mock import patch

from lead_research.search import (
    CommonSourcesSearchProvider,
    MultiSourceProvider,
    SearchProvider,
    SerpApiSearchProvider,
    build_overpass_query,
    combined_provider,
    decode_duckduckgo_href,
    duckduckgo_links_from_html,
    google_items_to_results,
    nominatim_item_matches_location,
    nominatim_items_to_results,
    osm_elements_to_results,
    osm_location_plan,
    osm_selectors_for_category,
    serpapi_items_to_results,
    source_label,
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
        query = build_overpass_query("hotel", "berlin", 10)

        self.assertIn('area["name"~"^berlin$",i]["boundary"="administrative"]', query)
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

    def test_nominatim_items_to_results_extracts_extra_tag_websites(self) -> None:
        results = nominatim_items_to_results(
            [
                {
                    "display_name": "Hotel Berlin, Berlin, Deutschland",
                    "extratags": {"contact:website": "www.hotel-berlin.example"},
                    "address": {"city": "Berlin"},
                },
                {"display_name": "No website", "extratags": None},
            ],
            10,
            "berlin",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://www.hotel-berlin.example")
        self.assertIn("Nominatim", results[0].snippet)

    def test_nominatim_items_filter_out_other_cities(self) -> None:
        results = nominatim_items_to_results(
            [
                {
                    "display_name": "Hotel Berlin, Heide, Deutschland",
                    "extratags": {"website": "https://outside.example"},
                    "address": {"city": "Heide"},
                }
            ],
            10,
            "berlin",
        )

        self.assertEqual(results, [])

    def test_nominatim_item_matches_location_case_insensitively(self) -> None:
        self.assertTrue(nominatim_item_matches_location({"address": {"city": "Berlin"}}, "berlin"))
        self.assertFalse(nominatim_item_matches_location({"address": {"city": "Heide"}}, "berlin"))

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

    def test_decode_duckduckgo_href_handles_redirect_and_direct(self) -> None:
        redirect = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fhotel.example%2Fkontakt&rut=abc"
        self.assertEqual(decode_duckduckgo_href(redirect), "https://hotel.example/kontakt")
        self.assertEqual(decode_duckduckgo_href("https://direct.example/"), "https://direct.example/")
        self.assertEqual(decode_duckduckgo_href("//duckduckgo.com/about"), "")

    def test_duckduckgo_links_from_html_extracts_result_links(self) -> None:
        html_text = (
            '<a rel="nofollow" class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fhotel-a.example%2F">Hotel A</a>'
            '<a class="result__a" href="https://hotel-b.example/kontakt">Hotel B</a>'
            '<a class="other" href="https://ignore.example/">Ignore</a>'
        )

        links = duckduckgo_links_from_html(html_text)

        self.assertIn("https://hotel-a.example/", links)
        self.assertIn("https://hotel-b.example/kontakt", links)
        self.assertNotIn("https://ignore.example/", links)

    def test_multi_source_provider_merges_and_dedupes(self) -> None:
        class StaticProvider(SearchProvider):
            def __init__(self, results):
                self._results = results

            def search(self, category, location, limit):
                return self._results

        provider_a = StaticProvider([SearchResult(title="A", url="https://a.example/")])
        provider_b = StaticProvider(
            [
                SearchResult(title="A-dup", url="https://a.example"),
                SearchResult(title="B", url="https://b.example/"),
            ]
        )

        merged = MultiSourceProvider([provider_a, provider_b]).search("hotel", "", 10)
        urls = sorted(result.url for result in merged)

        self.assertEqual(urls, ["https://a.example/", "https://b.example/"])

    def test_serpapi_items_to_results_maps_organic_results(self) -> None:
        results = serpapi_items_to_results(
            {
                "organic_results": [
                    {"title": "Hotel A", "link": "https://hotel-a.example/", "snippet": "A"},
                    {"title": "No link"},
                ]
            }
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://hotel-a.example/")

    def test_combined_provider_includes_serpapi_when_key_set(self) -> None:
        with patch.dict("os.environ", {"SERPAPI_API_KEY": "test-key"}, clear=False):
            provider = combined_provider()

        labels = [source_label(sub) for sub in provider.providers]
        self.assertIn("SerpAPI", labels)
        self.assertIn("OpenStreetMap", labels)
        self.assertIn("DuckDuckGo", labels)

    def test_serpapi_paging_collects_multiple_pages(self) -> None:
        pages = [
            {"organic_results": [{"title": "1", "link": "https://one.example/"}]},
            {"organic_results": [{"title": "2", "link": "https://two.example/"}]},
            {"organic_results": []},
        ]
        provider = SerpApiSearchProvider(api_key="test-key")

        with patch("lead_research.search._read_json", side_effect=pages):
            results = provider.search("hotel", "Berlin", 10)

        urls = sorted(result.url for result in results)
        self.assertEqual(urls, ["https://one.example/", "https://two.example/"])


if __name__ == "__main__":
    unittest.main()
