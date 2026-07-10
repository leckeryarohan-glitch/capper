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
    build_google_search_url,
    build_overpass_query,
    build_query,
    build_zenrows_api_request_url,
    category_search_variants,
    combined_provider,
    decode_duckduckgo_href,
    directory_parallel_workers,
    duckduckgo_links_from_html,
    duckduckgo_next_offset,
    duckduckgo_pages_per_query,
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
    zenrows_cities_budget,
    zenrows_items_to_results,
    zenrows_query_plans,
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

    def test_osm_location_plan_sweeps_cities_without_location(self) -> None:
        with patch(
            "lead_research.search.top_cities_for_web_search",
            return_value=[("Berlin", "DE"), ("Hamburg", "DE"), ("Wien", "AT")],
        ) as cities:
            locations = osm_location_plan("", ("DE", "AT"), city_budget=25)

        cities.assert_called_once_with(("DE", "AT"), per_country=25)
        # Nationwide OSM now queries city areas (fast) instead of one country area.
        self.assertEqual(
            locations,
            (
                OsmSearchTarget(label="Berlin"),
                OsmSearchTarget(label="Hamburg"),
                OsmSearchTarget(label="Wien"),
            ),
        )

    def test_osm_location_plan_falls_back_to_country_when_no_cities(self) -> None:
        with patch("lead_research.search.top_cities_for_web_search", return_value=[]):
            locations = osm_location_plan("", ("DE",))

        self.assertEqual(locations, (OsmSearchTarget(label="Deutschland", country_code="DE"),))

    def test_osm_location_plan_mass_mode_sweeps_all_cities(self) -> None:
        with patch(
            "lead_research.search.cities_for_mass_web_search",
            return_value=[("Berlin", "DE"), ("Kleinstadt", "DE")],
        ) as mass_cities, patch(
            "lead_research.search.top_cities_for_web_search"
        ) as top_cities:
            locations = osm_location_plan("", ("DE",), city_budget=None)

        mass_cities.assert_called_once_with(("DE",))
        top_cities.assert_not_called()
        self.assertEqual(
            locations,
            (OsmSearchTarget(label="Berlin"), OsmSearchTarget(label="Kleinstadt")),
        )

    def test_osm_cities_budget_scales_with_limit(self) -> None:
        from lead_research.search import osm_cities_budget

        self.assertEqual(osm_cities_budget(10), 8)
        self.assertEqual(osm_cities_budget(60), 20)
        # Mass limits sweep every city (None), not a capped top list.
        self.assertIsNone(osm_cities_budget(500))
        self.assertIsNone(osm_cities_budget(100000))

    def test_web_search_cities_budget_scales_with_limit(self) -> None:
        from lead_research.search import web_search_cities_budget

        self.assertEqual(web_search_cities_budget(50), 40)
        self.assertEqual(web_search_cities_budget(100), 60)
        self.assertEqual(web_search_cities_budget(1000), 400)
        self.assertIsNone(web_search_cities_budget(3000))

    def test_expand_forces_full_city_sweep_and_deep_pagination(self) -> None:
        from lead_research.search import (
            osm_cities_budget,
            web_search_cities_budget,
            zenrows_cities_budget,
            zenrows_max_pagination_start,
            ZENROWS_DEEP_PAGINATION_START,
        )

        # A small limit normally caps the surface; expand=True removes the cap.
        self.assertIsNotNone(web_search_cities_budget(50))
        self.assertIsNone(web_search_cities_budget(50, expand=True))
        self.assertIsNotNone(osm_cities_budget(10))
        self.assertIsNone(osm_cities_budget(10, expand=True))
        self.assertIsNotNone(zenrows_cities_budget(50))
        self.assertIsNone(zenrows_cities_budget(50, expand=True))
        # A mass run with many plans normally uses shallow pagination.
        self.assertLess(zenrows_max_pagination_start(500, True), ZENROWS_DEEP_PAGINATION_START)
        self.assertEqual(
            zenrows_max_pagination_start(500, True, expand=True),
            ZENROWS_DEEP_PAGINATION_START,
        )

    def test_expand_query_plans_adds_more_plans(self) -> None:
        from lead_research.search import expand_query_plans

        normal = expand_query_plans("hotel", "", ("DE",), limit=50)
        expanded = expand_query_plans("hotel", "", ("DE",), limit=50, expand=True)
        self.assertGreater(len(expanded), len(normal))

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

    def test_duckduckgo_next_offset_reads_form_field(self) -> None:
        html_text = '<input type="hidden" name="s" value="10">'
        self.assertEqual(duckduckgo_next_offset(html_text, 0, 20), 10)

    def test_duckduckgo_pages_per_query_scales_with_limit(self) -> None:
        self.assertEqual(duckduckgo_pages_per_query(25), 4)
        self.assertEqual(duckduckgo_pages_per_query(150), 8)
        self.assertEqual(duckduckgo_pages_per_query(500), 12)

    def test_duckduckgo_next_form_fields_parses_more_results_form(self) -> None:
        from lead_research.search import duckduckgo_next_form_fields

        html_text = (
            '<form action="/html/" method="get">'
            '<input name="q" value="hotel berlin"/></form>'
            '<div class="nav-link"><form action="/html/" method="post">'
            '<input type="hidden" name="q" value="hotel berlin"/>'
            '<input type="hidden" name="s" value="30"/>'
            '<input type="hidden" name="nextParams" value=""/>'
            '<input type="hidden" name="v" value="l"/>'
            '<input type="hidden" name="dc" value="31"/>'
            '<input type="hidden" name="vqd" value="4-123456789"/>'
            '<input type="hidden" name="kl" value="de-de"/>'
            '<input type="submit" value="Next"/></form></div>'
        )
        fields = duckduckgo_next_form_fields(html_text)
        assert fields is not None
        self.assertEqual(fields["s"], "30")
        self.assertEqual(fields["vqd"], "4-123456789")
        self.assertEqual(fields["dc"], "31")
        self.assertEqual(fields["q"], "hotel berlin")

    def test_duckduckgo_next_form_fields_returns_none_without_next(self) -> None:
        from lead_research.search import duckduckgo_next_form_fields

        html_text = '<form action="/html/"><input name="q" value="hotel"/></form>'
        self.assertIsNone(duckduckgo_next_form_fields(html_text))

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
            provider = combined_provider(
                use_osm=False,
                use_duckduckgo=False,
                use_directories=False,
                use_zenrows_google=True,
                zenrows_key="gui-key",
            )

        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["ZenRows"])
        self.assertEqual(provider.providers[0].api_key, "gui-key")

    def test_combined_provider_can_enable_directories_without_google(self) -> None:
        provider = combined_provider(
            use_osm=False,
            use_duckduckgo=False,
            use_directories=True,
            use_zenrows_google=False,
            zenrows_key="test-key",
        )

        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["Branchenverzeichnisse"])

    def test_combined_provider_can_enable_google_without_directories(self) -> None:
        provider = combined_provider(
            use_osm=False,
            use_duckduckgo=False,
            use_directories=False,
            use_zenrows_google=True,
            zenrows_key="test-key",
        )

        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["ZenRows"])

    def test_build_zenrows_api_request_url_encodes_google_target(self) -> None:
        request_url = build_zenrows_api_request_url("zr-key", "hotel berlin", 0, "de", ".de")

        self.assertIn("api.zenrows.com/v1/", request_url)
        self.assertIn("mode=auto", request_url)
        self.assertIn("autoparse=true", request_url)
        self.assertIn("url=https%3A%2F%2Fwww.google.com%2Fsearch%3F", request_url)
        self.assertIn("%26num%3D10", request_url)
        self.assertNotIn("url=https://www.google.com/search?", request_url)

    def test_build_google_search_url_localizes_domain(self) -> None:
        url = build_google_search_url("hotel berlin", 0, "de", ".de")
        self.assertIn("www.google.com/search", url)
        self.assertIn("hl=de", url)
        self.assertIn("gl=de", url)
        self.assertIn("q=hotel+berlin", url)

    def test_zenrows_uses_universal_api_with_stealth_and_autoparse(self) -> None:
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
        self.assertIn("api.zenrows.com/v1/", captured_urls[0])
        self.assertIn("mode=auto", captured_urls[0])
        self.assertIn("autoparse=true", captured_urls[0])
        self.assertIn("proxy_country=de", captured_urls[0])
        self.assertIn("www.google.com%2Fsearch", captured_urls[0])

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

        def fake_read_json_with_retry(request, timeout=120, retries=3, backoff_seconds=3.0, **kwargs):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            google_url = params.get("url", [""])[0]
            google_params = urllib.parse.parse_qs(urllib.parse.urlparse(google_url).query)
            query_text = google_params.get("q", [""])[0]
            start = google_params.get("start", ["0"])[0]
            captured.append((query_text, start))
            if start != "0":
                return {"organic_results": []}
            idx = len(captured)
            return {"organic_results": [{"title": str(idx), "link": f"https://zr{idx}.example/"}]}

        provider = ZenRowsSearchProvider(api_key="zr-key")
        with patch("lead_research.search._read_json_with_retry", side_effect=fake_read_json_with_retry), patch(
            "lead_research.search.time.sleep"
        ):
            results = provider.search("hotel", "", 4)

        self.assertEqual(len(results), 4)
        self.assertGreater(len({q for q, _ in captured}), 1)

    def test_category_search_variants_expands_logistik(self) -> None:
        variants = category_search_variants("logistik")
        self.assertIn("logistik", variants)
        self.assertIn("spedition", variants)
        self.assertGreater(len(variants), 3)

    def test_category_search_variants_cover_many_categories(self) -> None:
        # Tier 3 now broadens the other categories too, not just hotel.
        for category, expected in (
            ("restaurant", "gaststätte"),
            ("zahnarzt", "zahnarztpraxis"),
            ("rechtsanwalt", "kanzlei"),
            ("dachdecker", "dachdeckerei"),
            ("florist", "blumen"),
        ):
            variants = category_search_variants(category)
            self.assertGreater(len(variants), 1, category)
            self.assertIn(expected, variants, category)

    def test_short_keyword_matches_whole_word_only(self) -> None:
        # "it" must not fire inside "fitness".
        self.assertEqual(
            category_search_variants("fitnessstudio"),
            category_search_variants("fitness"),
        )
        self.assertIn("fitness", category_search_variants("fitnessstudio"))
        self.assertNotIn('["office"="it"]', osm_selectors_for_category("fitnessstudio"))
        # But an explicit "it" category still resolves to IT.
        self.assertIn("software", category_search_variants("it"))

    def test_longest_keyword_wins(self) -> None:
        # "kfz werkstatt" should prefer the more specific "werkstatt" mapping.
        self.assertEqual(category_search_variants("kfz werkstatt")[0], "werkstatt")

    def test_custom_category_falls_back_to_itself(self) -> None:
        self.assertEqual(category_search_variants("wasserski verleih"), ("wasserski verleih",))

    def test_zenrows_cities_budget_scales_with_limit(self) -> None:
        self.assertEqual(zenrows_cities_budget(50), 12)
        self.assertEqual(zenrows_cities_budget(500), 200)
        self.assertIsNone(zenrows_cities_budget(5000))

    def test_zenrows_query_plans_uses_many_cities_for_high_limit(self) -> None:
        small = zenrows_query_plans("logistik", "", ("DE",), limit=50)
        large = zenrows_query_plans("logistik", "", ("DE",), limit=5000)
        self.assertGreater(len(large), len(small))
        self.assertGreater(len(large), 500)

    def test_build_query_broad_mode_omits_contact_terms(self) -> None:
        narrow = build_query("logistik", "Berlin")
        broad = build_query("logistik", "Berlin", broad=True)
        self.assertIn("Kontakt", narrow)
        self.assertNotIn("Kontakt", broad)
        self.assertIn("logistik", broad)
        self.assertIn("Berlin", broad)

    def test_zenrows_mass_mode_continues_after_transient_error(self) -> None:
        calls = {"count": 0}

        def fake_read_json_with_retry(request, timeout=120, retries=3, backoff_seconds=3.0, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise SearchProviderError("Search provider request failed: timed out")
            params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            google_url = params.get("url", [""])[0]
            google_params = urllib.parse.parse_qs(urllib.parse.urlparse(google_url).query)
            start = google_params.get("start", ["0"])[0]
            if start != "0":
                return {"organic_results": []}
            idx = calls["count"]
            return {"organic_results": [{"title": str(idx), "link": f"https://zr{idx}.example/"}]}

        provider = ZenRowsSearchProvider(api_key="zr-key")
        with patch("lead_research.search._read_json_with_retry", side_effect=fake_read_json_with_retry), patch(
            "lead_research.search.time.sleep"
        ), patch(
            "lead_research.search.zenrows_query_plans",
            return_value=[("logistik Berlin", "DE"), ("logistik Hamburg", "DE")],
        ):
            results = provider.search("logistik", "", 500)

        self.assertEqual(len(results), 1)
        self.assertGreaterEqual(calls["count"], 2)

    def test_combined_provider_respects_source_toggles(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            only_ddg = combined_provider(use_osm=False, use_duckduckgo=True, use_directories=False)
            only_osm = combined_provider(use_osm=True, use_duckduckgo=False, use_directories=False)
            only_directories = combined_provider(
                use_osm=False,
                use_duckduckgo=False,
                use_directories=True,
                use_zenrows_google=False,
                zenrows_key="test-key",
            )
            none_selected = combined_provider(use_osm=False, use_duckduckgo=False, use_directories=False)

        self.assertEqual([source_label(p) for p in only_ddg.providers], ["DuckDuckGo"])
        self.assertEqual([source_label(p) for p in only_osm.providers], ["OpenStreetMap"])
        self.assertEqual([source_label(p) for p in only_directories.providers], ["Branchenverzeichnisse"])
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


class DirectoryParallelismTests(unittest.TestCase):
    def test_directory_parallel_workers_caps_zenrows_requests(self) -> None:
        self.assertEqual(directory_parallel_workers(15, use_zenrows=True), 15)
        self.assertEqual(directory_parallel_workers(15, use_zenrows=True, requested_parallel=6), 6)
        self.assertEqual(directory_parallel_workers(15, use_zenrows=True, requested_parallel=100), 15)
        self.assertEqual(directory_parallel_workers(3, use_zenrows=True), 3)
        self.assertEqual(directory_parallel_workers(15, use_zenrows=False), 15)

    def test_combined_provider_passes_directory_parallel_requests(self) -> None:
        provider = combined_provider(
            use_osm=False,
            use_duckduckgo=False,
            use_directories=True,
            use_zenrows_google=False,
            zenrows_key="test-key",
            directory_parallel_requests=50,
        )

        directory_provider = provider.providers[0]
        self.assertEqual(directory_provider.parallel_requests, 50)


if __name__ == "__main__":
    unittest.main()
