from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from lead_research.google_maps import (
    GoogleMapsFetchError,
    build_google_maps_search_url,
    build_zenrows_google_maps_request_url,
    css_extractor_values,
    discover_google_maps_results,
    extract_place_urls_from_html,
    fetch_google_maps_place_urls,
    google_maps_cities_budget,
    google_maps_location_plans,
    google_maps_places_per_city,
    parse_google_maps_listing_html,
    parse_website_from_detail_html,
    search_result_from_detail_payload,
)
from lead_research.search import GoogleMapsSearchProvider, SearchProviderError, combined_provider, source_label


MAPS_FIXTURE = """
<div role="article">
<a href="https://www.google.com/maps/place/Spedition+Muster+GmbH/data=abc">
  <span>Spedition Muster GmbH</span>
</a>
<a href="https://www.spedition-muster.de/kontakt">Website</a>
<a href="tel:+49201123456">Anrufen</a>
</div>
<div role="article">
<a href="https://www.google.com/maps/place/Kurier+Demo+AG/data=def">
  <span>Kurier Demo AG</span>
</a>
<a aria-label="Website: www.kurier-demo.example">Web</a>
<a href="tel:+49201999888">Tel</a>
</div>
"""

LISTING_PAYLOAD = {
    "place_urls": [
        "https://www.google.de/maps/place/Hotel+Alpha/data=abc",
        "https://www.google.de/maps/place/Hotel+Beta/data=def",
    ]
}

DETAIL_PAYLOADS = {
    "https://www.google.de/maps/place/Hotel+Alpha/data=abc": {
        "name": "Hotel Alpha",
        "website": "https://www.hotel-alpha.example",
    },
    "https://www.google.de/maps/place/Hotel+Beta/data=def": {
        "name": "Hotel Beta",
        "website": "https://www.hotel-beta.example",
    },
}


class GoogleMapsParserTests(unittest.TestCase):
    def test_build_google_maps_search_url(self) -> None:
        self.assertEqual(
            build_google_maps_search_url("versand", "Dortmund"),
            "https://www.google.de/maps/search/versand+Dortmund?hl=de",
        )
        self.assertEqual(
            build_google_maps_search_url("hotel", "Wien", country_code="AT"),
            "https://www.google.at/maps/search/hotel+Wien?hl=de",
        )

    def test_build_zenrows_request_uses_css_extractor_and_sidebar_scroll(self) -> None:
        url = build_zenrows_google_maps_request_url(
            "test-key",
            "https://www.google.de/maps/search/hotel+Berlin",
            css_extractor={"place_urls": "a.hfpxzc @href"},
            js_instructions=[{"evaluate": "scroll sidebar"}, {"wait": 2000}],
        )
        self.assertIn("api.zenrows.com/v1/", url)
        self.assertIn("js_render=true", url)
        self.assertIn("premium_proxy=true", url)
        self.assertIn("custom_headers=true", url)
        self.assertIn("css_extractor=", url)
        self.assertIn("hfpxzc", url)
        self.assertIn("js_instructions", url)

    def test_parse_google_maps_listing_html_extracts_websites(self) -> None:
        results = parse_google_maps_listing_html(MAPS_FIXTURE)

        self.assertGreaterEqual(len(results), 2)
        urls = {result.url for result in results if result.url}
        self.assertIn("https://www.spedition-muster.de/kontakt", urls)
        self.assertIn("https://www.kurier-demo.example", urls)
        self.assertFalse(any(result.directory_phone for result in results))

    def test_parse_google_maps_skips_phone_only_entries(self) -> None:
        html = """
        <a href="https://www.google.com/maps/place/Nur+Telefon+GmbH/data=abc">Nur Telefon GmbH</a>
        <a href="tel:+49201123456">Anrufen</a>
        """
        results = parse_google_maps_listing_html(html)
        self.assertEqual(results, [])

    def test_css_extractor_values_support_lists_and_strings(self) -> None:
        self.assertEqual(css_extractor_values({"website": "https://a.example"}, "website"), ["https://a.example"])
        self.assertEqual(css_extractor_values({"url": ["https://a.example", ""]}, "url"), ["https://a.example"])

    def test_search_result_from_detail_payload(self) -> None:
        result = search_result_from_detail_payload(
            "https://www.google.de/maps/place/Hotel+Alpha/data=abc",
            {"name": "Hotel Alpha", "website": "https://www.hotel-alpha.example"},
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.title, "Hotel Alpha")
        self.assertEqual(result.url, "https://www.hotel-alpha.example")

    def test_extract_place_urls_from_html(self) -> None:
        html = """
        <a class="hfpxzc" href="https://www.google.de/maps/place/Hotel+Alpha/data=abc">Hotel Alpha</a>
        <a href="https://www.google.de/maps/place/Hotel+Beta/data=def">Beta</a>
        """
        urls = extract_place_urls_from_html(html)
        self.assertEqual(len(urls), 2)

    def test_parse_website_from_detail_html(self) -> None:
        html = '<a data-item-id="authority" href="https://www.hotel-demo.example">Website</a>'
        self.assertEqual(parse_website_from_detail_html(html), "https://www.hotel-demo.example")

    def test_google_maps_location_plans_include_country_and_cities(self) -> None:
        plans = google_maps_location_plans("versand", "", ("DE",), limit=500)
        labels = [location for location, _country in plans]

        self.assertIn("Berlin", labels)
        self.assertNotIn("Deutschland", labels)
        self.assertGreater(len(plans), 100)

    def test_google_maps_cities_budget_scales_with_limit(self) -> None:
        self.assertEqual(google_maps_cities_budget(50), 40)
        self.assertEqual(google_maps_cities_budget(500), 200)
        self.assertIsNone(google_maps_cities_budget(5000))

    def test_google_maps_places_per_city_scales_with_limit(self) -> None:
        self.assertGreaterEqual(google_maps_places_per_city(5000, 1600), 10)
        self.assertLessEqual(google_maps_places_per_city(5000, 1600), 60)

    def test_fetch_google_maps_place_urls_returns_empty_on_422(self) -> None:
        with patch(
            "lead_research.google_maps.fetch_zenrows_css_payload",
            side_effect=GoogleMapsFetchError(
                "Google Maps ZenRows request failed: HTTP Error 422: Unprocessable Entity"
            ),
        ):
            urls = fetch_google_maps_place_urls(
                "key",
                "https://www.google.de/maps/search/hotel+Dresden?hl=de",
            )
        self.assertEqual(urls, [])

    def test_discover_google_maps_results_fetches_listings_then_details(self) -> None:
        def fake_css_payload(api_key, target_url, *, css_extractor, **_kwargs):
            if "hfpxzc" in json.dumps(css_extractor):
                return LISTING_PAYLOAD
            return DETAIL_PAYLOADS.get(target_url, {})

        with patch("lead_research.google_maps.fetch_zenrows_css_payload", side_effect=fake_css_payload):
            results, stats = discover_google_maps_results(
                "key",
                "https://www.google.de/maps/search/hotel+Berlin",
                places_limit=2,
            )

        urls = {result.url for result in results}
        self.assertEqual(urls, {"https://www.hotel-alpha.example", "https://www.hotel-beta.example"})
        self.assertEqual(stats.place_urls, 2)
        self.assertEqual(stats.websites_found, 2)


class GoogleMapsProviderTests(unittest.TestCase):
    def test_provider_requires_zenrows_key(self) -> None:
        with self.assertRaises(SearchProviderError):
            GoogleMapsSearchProvider(zenrows_api_key="")

    def test_combined_provider_includes_google_maps_when_enabled(self) -> None:
        with patch.dict("os.environ", {"ZENROWS_API_KEY": "zr-key"}, clear=True):
            provider = combined_provider(
                use_osm=False,
                use_duckduckgo=False,
                use_directories=False,
                use_zenrows_google=False,
                use_google_maps=True,
                zenrows_key="zr-key",
            )
        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["Google Maps"])

    def test_provider_uses_discover_flow(self) -> None:
        provider = GoogleMapsSearchProvider(zenrows_api_key="zr-key")
        sample = [
            __import__("lead_research.models", fromlist=["SearchResult"]).SearchResult(
                title="Hotel Alpha",
                url="https://www.hotel-alpha.example",
                snippet="Google Maps",
            )
        ]

        with patch.object(provider, "_discover_google_maps_results", return_value=(sample, __import__("lead_research.google_maps", fromlist=["GoogleMapsDiscoveryStats"]).GoogleMapsDiscoveryStats(place_urls=1, websites_found=1))):
            results = provider.search("hotel", "Berlin", 10, ("DE",))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://www.hotel-alpha.example")


if __name__ == "__main__":
    unittest.main()
