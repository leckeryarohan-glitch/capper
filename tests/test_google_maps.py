from __future__ import annotations

import unittest
from unittest.mock import patch

from lead_research.google_maps import (
    build_google_maps_search_url,
    build_zenrows_google_maps_request_url,
    google_maps_location_plans,
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
