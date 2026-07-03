from __future__ import annotations

import unittest
from unittest.mock import patch

from lead_research.google_maps import (
    GoogleMapsFetchError,
    build_google_maps_search_url,
    build_zenrows_google_maps_request_url,
    fetch_google_maps_html,
    google_maps_cities_budget,
    google_maps_location_plans,
    is_retryable_maps_error,
    parse_google_maps_listing_html,
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


class GoogleMapsParserTests(unittest.TestCase):
    def test_build_google_maps_search_url(self) -> None:
        self.assertEqual(
            build_google_maps_search_url("versand", "Dortmund"),
            "https://www.google.com/maps/search/versand+Dortmund",
        )

    def test_build_zenrows_request_includes_js_and_proxy(self) -> None:
        url = build_zenrows_google_maps_request_url(
            "test-key",
            "https://www.google.com/maps/search/versand+Dortmund",
            proxy_country="de",
            scroll_steps=2,
        )
        self.assertIn("api.zenrows.com/v1/", url)
        self.assertIn("js_render=true", url)
        self.assertIn("premium_proxy=true", url)
        self.assertIn("proxy_country=de", url)
        self.assertIn("wait=8000", url)
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

    def test_google_maps_location_plans_include_country_and_cities(self) -> None:
        plans = google_maps_location_plans("versand", "", ("DE",), limit=500)
        labels = [location for location, _country in plans]

        self.assertIn("Deutschland", labels)
        self.assertIn("Berlin", labels)
        self.assertGreater(len(plans), 100)

    def test_google_maps_cities_budget_scales_with_limit(self) -> None:
        self.assertEqual(google_maps_cities_budget(50), 40)
        self.assertEqual(google_maps_cities_budget(500), 200)
        self.assertIsNone(google_maps_cities_budget(5000))

    def test_google_maps_location_plans_use_all_cities_for_large_limits(self) -> None:
        plans = google_maps_location_plans("versand", "", ("DE",), limit=5000)
        city_plans = [location for location, _country in plans if location != "Deutschland"]
        self.assertGreater(len(city_plans), 1000)

    def test_is_retryable_maps_error_detects_connection_drops(self) -> None:
        self.assertTrue(
            is_retryable_maps_error(
                "Google Maps ZenRows request failed: Remote end closed connection without response"
            )
        )
        self.assertFalse(is_retryable_maps_error("HTTP Error 401: Unauthorized"))

    def test_fetch_google_maps_html_retries_transient_errors(self) -> None:
        calls = {"count": 0}

        def flaky_fetch(*_args, **_kwargs) -> str:
            calls["count"] += 1
            if calls["count"] < 3:
                raise GoogleMapsFetchError(
                    "Google Maps ZenRows request failed for https://example.com: "
                    "Remote end closed connection without response"
                )
            return "<html>ok</html>"

        with patch("lead_research.google_maps._fetch_google_maps_html_once", side_effect=flaky_fetch):
            html = fetch_google_maps_html("key", "https://www.google.com/maps/search/hotel+Berlin", retries=3)

        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(calls["count"], 3)


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

    def test_provider_parses_mocked_zenrows_html(self) -> None:
        provider = GoogleMapsSearchProvider(zenrows_api_key="zr-key")

        with patch.object(provider, "_fetch_google_maps_html", return_value=MAPS_FIXTURE):
            results = provider.search("versand", "Dortmund", 10, ("DE",))

        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(any(result.url for result in results))


if __name__ == "__main__":
    unittest.main()
