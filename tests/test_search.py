from __future__ import annotations

import unittest
import urllib.parse

from lead_research.models import SearchResult
from unittest.mock import patch

from lead_research.search import (
    CommonSourcesSearchProvider,
    MultiSourceProvider,
    OsmSearchTarget,
    SearchProvider,
    SearchProviderError,
    SerpApiSearchProvider,
    ZenRowsSearchProvider,
    _read_json_with_retry,
    build_overpass_query,
    combined_provider,
    decode_duckduckgo_href,
    duckduckgo_links_from_html,
    expand_queries,
    google_items_to_results,
    is_valid_lead_url,
    nominatim_item_matches_location,
    nominatim_items_to_results,
    osm_elements_to_results,
    osm_location_plan,
    osm_selectors_for_category,
    serpapi_items_to_results,
    source_label,
    zenrows_items_to_results,
)


class RecordingProvider(SearchProvider):
    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []

    def search(self, category: str, location: str, limit: int, countries=()) -> list[SearchResult]:
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

    def test_build_overpass_query_scopes_to_country(self) -> None:
        query = build_overpass_query("hotel", "", 10, country_code="DE")

        self.assertIn('area["ISO3166-1"="DE"]', query)
        self.assertIn('nwr["tourism"="hotel"](area.searchArea);', query)

    def test_osm_location_plan_uses_selected_countries_without_location(self) -> None:
        locations = osm_location_plan("", ("DE", "AT"))

        self.assertEqual(len(locations), 2)
        self.assertEqual(locations[0], OsmSearchTarget(label="Deutschland", country_code="DE"))
        self.assertEqual(locations[1], OsmSearchTarget(label="Österreich", country_code="AT"))

    def test_osm_location_plan_defaults_to_germany(self) -> None:
        locations = osm_location_plan("")

        self.assertEqual(locations, (OsmSearchTarget(label="Deutschland", country_code="DE"),))

    def test_osm_location_plan_uses_given_location(self) -> None:
        self.assertEqual(osm_location_plan("Bremen"), (OsmSearchTarget(label="Bremen"),))

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

            def search(self, category, location, limit, countries=()):
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

    def test_combined_provider_includes_zenrows_when_key_set(self) -> None:
        with patch.dict("os.environ", {"ZENROWS_API_KEY": "zr-key"}, clear=True):
            provider = combined_provider()

        labels = [source_label(sub) for sub in provider.providers]
        self.assertIn("ZenRows", labels)
        self.assertIn("OpenStreetMap", labels)

    def test_zenrows_items_to_results_parses_and_filters(self) -> None:
        results = zenrows_items_to_results(
            {
                "organic_results": [
                    {"title": "Good", "link": "https://good.example/", "description": "desc"},
                    {"title": "Bad", "link": "/goto?url=CAES"},
                    {"title": "Url key", "url": "https://second.example/"},
                ]
            }
        )

        urls = sorted(r.url for r in results)
        self.assertEqual(urls, ["https://good.example/", "https://second.example/"])

    def test_read_json_with_retry_retries_transient_http_errors(self) -> None:
        attempts = {"count": 0}

        def fake_read_json(request, timeout=20):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise SearchProviderError("Search provider request failed: HTTP Error 502: Bad Gateway")
            return {"organic_results": [{"title": "OK", "link": "https://ok.example/"}]}

        request = urllib.request.Request("https://example.test/")
        with patch("lead_research.search._read_json", side_effect=fake_read_json), patch(
            "lead_research.search.time.sleep"
        ):
            data = _read_json_with_retry(request, retries=4)

        self.assertEqual(data["organic_results"][0]["link"], "https://ok.example/")
        self.assertEqual(attempts["count"], 3)

    def test_read_json_with_retry_retries_timeouts(self) -> None:
        attempts = {"count": 0}

        def fake_read_json(request, timeout=20):
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise SearchProviderError("Search provider request failed: The read operation timed out")
            return {"organic_results": []}

        request = urllib.request.Request("https://example.test/")
        with patch("lead_research.search._read_json", side_effect=fake_read_json), patch(
            "lead_research.search.time.sleep"
        ):
            _read_json_with_retry(request, retries=3)

        self.assertEqual(attempts["count"], 2)

    def test_combined_provider_uses_explicit_zenrows_key_from_gui(self) -> None:
        with patch.dict("os.environ", {"ZENROWS_API_KEY": "env-key"}, clear=False):
            provider = combined_provider(use_osm=False, use_duckduckgo=False, zenrows_key="gui-key")

        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["ZenRows"])
        self.assertEqual(provider.providers[0].api_key, "gui-key")

    def test_zenrows_uses_adaptive_stealth_mode(self) -> None:
        captured_urls: list[str] = []

        def fake_read_json_with_retry(request, timeout=120, retries=3, backoff_seconds=3.0, **kwargs):
            captured_urls.append(request.full_url)
            return {"organic_results": [{"title": "Hotel", "link": "https://hotel.example/"}]}

        provider = ZenRowsSearchProvider(api_key="zr-key")
        with patch("lead_research.search._read_json_with_retry", side_effect=fake_read_json_with_retry), patch(
            "lead_research.search.time.sleep"
        ):
            results = provider.search("hotel", "Berlin", 1)

        self.assertEqual(len(results), 1)
        self.assertIn("mode=auto", captured_urls[0])

    def test_zenrows_search_pages_and_expands(self) -> None:
        captured: list[tuple[str, str]] = []

        def fake_read_json(request, timeout=20, **kwargs):
            path = urllib.parse.urlparse(request.full_url).path
            query_text = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            start = params.get("start", ["0"])[0]
            captured.append((query_text, start))
            if start != "0":
                return {"organic_results": []}
            idx = len(captured)
            return {"organic_results": [{"title": str(idx), "link": f"https://zr{idx}.example/"}]}

        provider = ZenRowsSearchProvider(api_key="zr-key")
        with patch("lead_research.search._read_json_with_retry", side_effect=fake_read_json), patch(
            "lead_research.search.time.sleep"
        ):
            results = provider.search("hotel", "", 4)

        self.assertEqual(len(results), 4)
        self.assertGreater(len({q for q, _ in captured}), 1)

    def test_combined_provider_respects_source_toggles(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            only_ddg = combined_provider(use_osm=False, use_duckduckgo=True)
            only_osm = combined_provider(use_osm=True, use_duckduckgo=False)
            none_selected = combined_provider(use_osm=False, use_duckduckgo=False)

        self.assertEqual([source_label(p) for p in only_ddg.providers], ["DuckDuckGo"])
        self.assertEqual([source_label(p) for p in only_osm.providers], ["OpenStreetMap"])
        self.assertEqual(none_selected.providers, [])

    def test_overpass_query_without_location_requests_many_results(self) -> None:
        query = build_overpass_query("hotel", "", 50)
        # out tags center <count>; count should be well above the old ~21/city cap
        self.assertRegex(query, r"out tags center \d{3,};")

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

    def test_expand_queries_adds_cities_without_location(self) -> None:
        single = expand_queries("hotel", "Berlin")
        self.assertEqual(len(single), 1)

        many = expand_queries("hotel", "", ("DE",))
        self.assertGreater(len(many), 5)
        self.assertTrue(any("Deutschland" in q for q in many))
        self.assertTrue(any("Berlin" in q for q in many))

    def test_is_valid_lead_url_rejects_relative_and_redirects(self) -> None:
        self.assertTrue(is_valid_lead_url("https://example.test/kontakt"))
        self.assertFalse(is_valid_lead_url("/goto?url=CAESabc"))
        self.assertFalse(is_valid_lead_url(""))

    def test_serpapi_items_to_results_skips_relative_links(self) -> None:
        results = serpapi_items_to_results(
            {
                "organic_results": [
                    {"title": "Good", "link": "https://good.example/"},
                    {"title": "Redirect junk", "link": "/goto?url=CAESabc"},
                ]
            }
        )
        self.assertEqual([r.url for r in results], ["https://good.example/"])

    def test_serpapi_runs_multiple_queries_without_location(self) -> None:
        captured: list[str] = []

        def fake_read_json(request, timeout=20):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            query = params.get("q", [""])[0]
            start = params.get("start", ["0"])[0]
            captured.append(query)
            if start != "0":
                return {"organic_results": []}
            idx = len(captured)
            return {"organic_results": [{"title": str(idx), "link": f"https://site{idx}.example/"}]}

        provider = SerpApiSearchProvider(api_key="test-key")
        with patch("lead_research.search._read_json", side_effect=fake_read_json):
            results = provider.search("hotel", "", 5)

        self.assertEqual(len(results), 5)
        # multiple distinct queries (cities) were used, not just one
        self.assertGreater(len(set(captured)), 1)


if __name__ == "__main__":
    unittest.main()
