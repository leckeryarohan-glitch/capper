from __future__ import annotations

import unittest

from lead_research.directories import (
    build_auskunft_url,
    build_dasoertliche_url,
    build_gelbeseiten_url,
    directory_entries_to_results,
    is_external_business_url,
    parse_11880_html,
    parse_auskunft_html,
    parse_dasoertliche_html,
    parse_gelbeseiten_detail_html,
    parse_gelbeseiten_listing_html,
)
from lead_research.search import DirectorySearchProvider, combined_provider, provider_from_name, source_label


DASOERTLICHE_FIXTURE = """
<script type="application/ld+json">{"@context":"https://schema.org","@type":"ItemList","itemListElement":[{"@type":"ListItem","position":1,"item":{"@type":"Hotel","name":"Hotel Beispiel","url":"https://www.dasoertliche.de/Themen/Hotel-Beispiel-Berlin","telephone":"030 123456"}}]}</script>
<script>
var handlerData =[["1","","","http://www.beispiel-hotel.de","Berlin","","2","1126","10115","Musterstr.","1","030 123456","0","2239","Hotel Beispiel","https://www.dasoertliche.de/Themen/Hotel-Beispiel-Berlin","1","info@beispiel-hotel.de"]];
</script>
"""

AUSKUNFT_FIXTURE = """
<div class="resultItemContainer posRel"><div class="entryMongo"><h2 class="resultHeader"><a href="/firma/demo-hotel-berlin" title="Detailseite Demo Hotel">Demo Hotel</a></h2></div><div class="entryMongo resultTextContainer"><div class="resultAdress disFlex"><div class="phoneLinkContainer"><a href="tel:030 111111" title="Telefonnummer">030 111111</a></div></div><div class="phoneLinkContainer"><a href="https://www.demo-hotel.example" target="_blank" title="Webseite">www.demo-hotel.example</a></div></div></div>
"""

GELBESEITEN_LIST_FIXTURE = """
<article class="mod mod-Treffer"><a href="https://www.gelbeseiten.de/gsbiz/11111111-1111-1111-1111-111111111111"><h2 class="mod-Treffer__name">Demo GmbH</h2></a></article>
"""

GELBESEITEN_DETAIL_FIXTURE = """
<title>Demo GmbH in 10115 Berlin</title>
<div class="mod-Kontaktdaten__list-item contains-icon-big-homepage"><a href="https://www.demo-gmbh.example"><span>Webseite</span></a></div>
"""

E11880_FIXTURE = """
<script type="application/ld+json">{"@context":"http://schema.org","@type":"SearchResultsPage","mainEntity":{"@type":"ItemList","itemListElement":[{"@type":"ListItem","position":1,"item":{"@type":"LocalBusiness","name":"Demo Pension","url":"https://www.11880.com/branchenbuch/berlin/demo.html","email":"demo@example.de","telephone":"030 999999"}}]}}</script>
"""


class DirectoryParserTests(unittest.TestCase):
    def test_is_external_business_url_filters_directory_domains(self) -> None:
        self.assertTrue(is_external_business_url("https://www.demo-hotel.example/kontakt"))
        self.assertFalse(is_external_business_url("https://www.gelbeseiten.de/gsbiz/demo"))
        self.assertFalse(is_external_business_url("https://www.auskunft.de/firma/demo"))

    def test_parse_dasoertliche_html_reads_handler_data(self) -> None:
        entries = parse_dasoertliche_html(DASOERTLICHE_FIXTURE, source_url="https://example.test")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Hotel Beispiel")
        self.assertEqual(entries[0].website, "http://www.beispiel-hotel.de")

    def test_parse_auskunft_html_extracts_website(self) -> None:
        entries = parse_auskunft_html(AUSKUNFT_FIXTURE, source_url="https://example.test")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Demo Hotel")
        self.assertEqual(entries[0].website, "https://www.demo-hotel.example")

    def test_parse_gelbeseiten_listing_and_detail(self) -> None:
        listings = parse_gelbeseiten_listing_html(GELBESEITEN_LIST_FIXTURE, source_url="https://example.test")
        detail = parse_gelbeseiten_detail_html(
            GELBESEITEN_DETAIL_FIXTURE,
            name="Demo GmbH",
            source_url=listings[0][1],
        )

        self.assertEqual(listings, [("Demo GmbH", "https://www.gelbeseiten.de/gsbiz/11111111-1111-1111-1111-111111111111")])
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.website, "https://www.demo-gmbh.example")

    def test_parse_11880_html_reads_json_ld(self) -> None:
        entries = parse_11880_html(E11880_FIXTURE, source_url="https://example.test")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Demo Pension")
        self.assertIn("11880.com", entries[0].snippet)

    def test_directory_entries_to_results_dedupes(self) -> None:
        from lead_research.directories import DirectoryEntry

        entries = [
            DirectoryEntry(name="A", website="https://a.example/", source_url="https://source/a"),
            DirectoryEntry(name="A duplicate", website="https://a.example", source_url="https://source/b"),
            DirectoryEntry(name="B", website="https://b.example/", source_url="https://source/c"),
        ]
        results = directory_entries_to_results(entries, limit=5, seen=set())

        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result.url for result in results), ["https://a.example/", "https://b.example/"])

    def test_directory_url_builders(self) -> None:
        self.assertEqual(
            build_dasoertliche_url("hotel", "Berlin", 1),
            "https://www.dasoertliche.de/Themen/Hotel/Berlin.html",
        )
        self.assertEqual(
            build_dasoertliche_url("hotel", "Berlin", 2),
            "https://www.dasoertliche.de/Themen/Hotel/Berlin-Seite-2.html",
        )
        self.assertEqual(
            build_auskunft_url("hotel", "Berlin"),
            "https://www.auskunft.de/Suche?search=hotel+Berlin",
        )
        self.assertEqual(
            build_gelbeseiten_url("hotel", "Berlin"),
            "https://www.gelbeseiten.de/branchen/hotel/Berlin",
        )


class DirectoryProviderTests(unittest.TestCase):
    def test_provider_from_name_supports_directories(self) -> None:
        provider = provider_from_name("directories")

        self.assertIsInstance(provider, DirectorySearchProvider)
        self.assertEqual(source_label(provider), "Branchenverzeichnisse")

    def test_combined_provider_includes_directories(self) -> None:
        provider = combined_provider(use_osm=False, use_duckduckgo=False, use_directories=True)

        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["Branchenverzeichnisse"])


if __name__ == "__main__":
    unittest.main()
