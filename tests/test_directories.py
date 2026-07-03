from __future__ import annotations

import unittest
from unittest.mock import patch

from lead_research.directories import (
    DirectoryFetchConfig,
    build_auskunft_url,
    build_dasoertliche_url,
    build_gelbeseiten_url,
    build_zenrows_directory_fetch_url,
    cap_directory_detail_fetches,
    cap_directory_source_limit,
    configure_directory_fetch,
    directory_entries_to_results,
    directory_location_plans,
    enrich_gelbeseiten_entries,
    fetch_directory_html,
    is_external_business_url,
    parse_11880_html,
    parse_auskunft_html,
    parse_dasoertliche_html,
    parse_gelbeseiten_detail_html,
    parse_gelbeseiten_listing_html,
)
from lead_research.search import DirectorySearchProvider, SearchProviderError, combined_provider, provider_from_name, source_label


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

GELBESEITEN_EMAIL_ONLY_FIXTURE = """
<title>Spedition Demo in 10115 Berlin</title>
<a href="mailto:info@spedition-demo.example">E-Mail senden</a>
"""

DASOERTLICHE_EMAIL_ONLY_FIXTURE = """
<script>
var handlerData =[["1","","","","Berlin","","2","1126","10115","Musterstr.","1","030 123456","0","2239","Spedition Demo","https://www.dasoertliche.de/Themen/Spedition-Demo-Berlin","1","info@spedition-demo.example"]];
</script>
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

    def test_parse_gelbeseiten_detail_accepts_email_without_website(self) -> None:
        detail = parse_gelbeseiten_detail_html(
            GELBESEITEN_EMAIL_ONLY_FIXTURE,
            name="Spedition Demo",
            source_url="https://www.gelbeseiten.de/gsbiz/demo",
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.email, "info@spedition-demo.example")
        self.assertEqual(detail.website, "")

    def test_parse_dasoertliche_accepts_email_without_website(self) -> None:
        entries = parse_dasoertliche_html(DASOERTLICHE_EMAIL_ONLY_FIXTURE, source_url="https://example.test")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].email, "info@spedition-demo.example")
        self.assertEqual(entries[0].website, "")

    def test_parse_11880_html_stores_email(self) -> None:
        entries = parse_11880_html(E11880_FIXTURE, source_url="https://example.test")

        self.assertEqual(entries[0].email, "demo@example.de")

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

    def test_directory_entries_to_results_includes_email_only(self) -> None:
        from lead_research.directories import DirectoryEntry

        entries = [
            DirectoryEntry(
                name="Spedition Demo",
                website="",
                source_url="https://www.gelbeseiten.de/gsbiz/demo",
                email="info@spedition-demo.example",
            )
        ]
        results = directory_entries_to_results(entries, limit=5, seen=set())

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].directory_email, "info@spedition-demo.example")
        self.assertEqual(results[0].url, "")
        self.assertEqual(results[0].directory_source_url, "https://www.gelbeseiten.de/gsbiz/demo")

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

    def test_build_zenrows_directory_fetch_url(self) -> None:
        request_url = build_zenrows_directory_fetch_url(
            "test-key",
            "https://www.gelbeseiten.de/branchen/hotel/Berlin",
            proxy_country="de",
        )

        self.assertIn("api.zenrows.com/v1/", request_url)
        self.assertIn("apikey=test-key", request_url)
        self.assertIn("mode=auto", request_url)
        self.assertIn("proxy_country=de", request_url)
        self.assertIn("gelbeseiten.de", request_url)

    def test_parse_11880_detail_website(self) -> None:
        from lead_research.directories import parse_11880_detail_website

        website = parse_11880_detail_website(
            '<link itemprop="url" content="http://www.pension-goldkopf-berlin.de">'
            '<a class="website-link" href="https://www.googletagservices.com">Ad</a>'
        )
        self.assertEqual(website, "http://www.pension-goldkopf-berlin.de")

    def test_parse_werkenntdenbesten_detail_website(self) -> None:
        from lead_research.directories import parse_werkenntdenbesten_detail_website

        website = parse_werkenntdenbesten_detail_website(
            '<a href="https://wkdb.h5v.eu">Track</a>'
            '<a title="Homepage" href="http://www.palace.de/" target="_blank">Web</a>'
        )
        self.assertEqual(website, "http://www.palace.de/")

    def test_parse_hotfrog_redirect_websites(self) -> None:
        from lead_research.directories import parse_hotfrog_redirect_websites

        websites = parse_hotfrog_redirect_websites(
            'href="https://x.yext-wrap.com/plclick?continue=https%3A%2F%2Fwww.example-hotel.de%2F"'
        )
        self.assertEqual(websites, ["https://www.example-hotel.de/"])

    def test_parse_goyellow_listing_html(self) -> None:
        from lead_research.directories import parse_goyellow_listing_html

        listings = parse_goyellow_listing_html(
            '<div data-seourl="/home/hotel-demo-berlin--abc123.html"></div>'
        )
        self.assertEqual(listings[0][1], "https://www.goyellow.de/home/hotel-demo-berlin--abc123.html")

    def test_parse_yelp_listing_and_detail(self) -> None:
        from lead_research.directories import parse_yelp_detail_website, parse_yelp_listing_html

        listings = parse_yelp_listing_html(
            '<a href="/biz/demo-hotel-berlin?osq=hotel">Demo Hotel</a>'
        )
        self.assertEqual(listings[0][1], "https://www.yelp.de/biz/demo-hotel-berlin")
        website = parse_yelp_detail_website(
            '<a href="/biz_redir?url=http%3A%2F%2Fwww.demo-hotel.example&amp;cachebuster=1">Web</a>'
        )
        self.assertEqual(website, "http://www.demo-hotel.example")

    def test_parse_kompass_listing_html(self) -> None:
        from lead_research.directories import parse_kompass_listing_html

        listings = parse_kompass_listing_html(
            '<a href="/c/demo-gmbh/de123456/">Demo GmbH</a>'
        )
        self.assertEqual(listings[0][1], "https://de.kompass.com/c/demo-gmbh/de123456/")

    def test_parse_europages_listing_html(self) -> None:
        from lead_research.directories import parse_europages_listing_html

        listings = parse_europages_listing_html(
            '<div class="company-tile"><a href="/de/firma/demo-gmbh-1234567">Demo</a></div>'
        )
        self.assertEqual(listings[0][1], "https://www.europages.de/de/firma/demo-gmbh-1234567")

    def test_parse_manta_listing_html(self) -> None:
        from lead_research.directories import parse_manta_listing_html

        listings = parse_manta_listing_html(
            '<a href="/c/m1demo/demo-hotel-berlin">Demo Hotel Berlin</a>'
        )
        self.assertEqual(listings[0], ("Demo Hotel Berlin", "https://www.manta.com/c/m1demo/demo-hotel-berlin"))

    def test_parse_pitchbook_listing_and_detail(self) -> None:
        from lead_research.directories import (
            parse_pitchbook_detail_name,
            parse_pitchbook_detail_website,
            parse_pitchbook_listing_html,
        )

        listings = parse_pitchbook_listing_html(
            '<a href="/profiles/company/531082-81">Demo Hotel</a>'
        )
        self.assertEqual(listings[0][1], "https://pitchbook.com/profiles/company/531082-81")
        website = parse_pitchbook_detail_website(
            'Website <a href="http://www.demo-hotel.example">Link</a>'
        )
        self.assertEqual(website, "http://www.demo-hotel.example")
        name = parse_pitchbook_detail_name("<title>Demo Hotel 2026 Company Profile: Valuation</title>")
        self.assertEqual(name, "Demo Hotel")

    def test_parse_indeed_listing_and_detail(self) -> None:
        from lead_research.directories import (
            parse_indeed_detail_name,
            parse_indeed_detail_website,
            parse_indeed_listing_html,
        )

        listings = parse_indeed_listing_html(
            '<a href="/cmp/demo-hotel-berlin/faq">Demo Hotel</a>'
        )
        self.assertEqual(listings[0][1], "https://de.indeed.com/cmp/demo-hotel-berlin")
        website = parse_indeed_detail_website(
            'Besuche uns unter https://www.demo-hotel.example/jobs und mehr.'
        )
        self.assertEqual(website, "https://www.demo-hotel.example/jobs")
        name = parse_indeed_detail_name("<title>Beruf und Karriere bei Demo Hotel | Indeed.de</title>")
        self.assertEqual(name, "Demo Hotel")

    def test_parse_jameda_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_jameda_url,
            parse_jameda_detail_name,
            parse_jameda_detail_website,
            parse_jameda_listing_html,
        )

        self.assertEqual(build_jameda_url("Hausarzt", "Berlin"), "https://www.jameda.de/hausarzt/berlin")
        listings = parse_jameda_listing_html(
            """
            <a href="https://www.jameda.de/hausarzt/berlin">Listing</a>
            <a href="https://www.jameda.de/philipp-lindemann-2/allgemeinmediziner-hausarzt-internist-hausarzt/berlin">Profil</a>
            <a href="https://www.jameda.de/gesundheitseinrichtungen/demo-klinik-berlin">Klinik</a>
            """,
            location="Berlin",
        )
        self.assertEqual(
            listings[0][1],
            "https://www.jameda.de/philipp-lindemann-2/allgemeinmediziner-hausarzt-internist-hausarzt/berlin",
        )
        self.assertEqual(listings[1][1], "https://www.jameda.de/gesundheitseinrichtungen/demo-klinik-berlin")
        website = parse_jameda_detail_website(
            '<a href="https://www.praxis-demo.example" data-patient-app-event-name="dp-doctor-website">Webseite</a>'
        )
        self.assertEqual(website, "https://www.praxis-demo.example")
        name = parse_jameda_detail_name(
            "<title>Dr. med. Demo Arzt - Hausarzt in Berlin | jameda</title>"
        )
        self.assertEqual(name, "Dr. med. Demo Arzt - Hausarzt")

    def test_parse_sanego_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_sanego_url,
            parse_sanego_detail_name,
            parse_sanego_detail_phone,
            parse_sanego_detail_website,
            parse_sanego_listing_html,
        )

        self.assertEqual(build_sanego_url("Hausarzt", "Berlin"), "https://www.sanego.de/Arzt/Berlin/Hausarzt/")
        listings = parse_sanego_listing_html(
            '<a href="/Arzt/Berlin/2701-Berlin/Allgemeinmedizin/52130-Dr-med-Demo-Arzt/">Profil</a>'
        )
        self.assertEqual(
            listings[0][1],
            "https://www.sanego.de/Arzt/Berlin/2701-Berlin/Allgemeinmedizin/52130-Dr-med-Demo-Arzt/",
        )
        website = parse_sanego_detail_website(
            '<div class="website"><a href="https://www.praxis-demo.example">Homepage</a></div>'
        )
        self.assertEqual(website, "https://www.praxis-demo.example")
        phone = parse_sanego_detail_phone('<a href="tel:030123456">Anrufen</a>')
        self.assertEqual(phone, "030123456")
        name = parse_sanego_detail_name("<title>Dr. med. Demo, Hausarzt in Berlin | sanego</title>")
        self.assertEqual(name, "Dr. med. Demo")

    def test_parse_restaurantguru_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_restaurantguru_url,
            parse_restaurantguru_detail_name,
            parse_restaurantguru_detail_website,
            parse_restaurantguru_listing_html,
        )

        self.assertEqual(
            build_restaurantguru_url("Restaurant", "Berlin"),
            "https://de.restaurantguru.com/Restaurant-Berlin",
        )
        listings = parse_restaurantguru_listing_html(
            """
            <a href="https://de.restaurantguru.com/April-Berlin">April</a>
            <a href="https://de.restaurantguru.com/Berlin">City</a>
            """,
            location="Berlin",
        )
        self.assertEqual(listings[0][1], "https://de.restaurantguru.com/April-Berlin")
        website = parse_restaurantguru_detail_website(
            """
            <div class="website">
                <a rel="nofollow" href="https://de.restaurantguru.com/link/123">demo-restaurant.example</a>
            </div>
            """
        )
        self.assertEqual(website, "https://demo-restaurant.example")
        name = parse_restaurantguru_detail_name("<title>Demo Restaurant, Berlin - Speisekarte</title>")
        self.assertEqual(name, "Demo Restaurant")

    def test_parse_docfinder_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_docfinder_url,
            parse_docfinder_detail_email,
            parse_docfinder_detail_name,
            parse_docfinder_detail_website,
            parse_docfinder_listing_html,
        )

        self.assertEqual(
            build_docfinder_url("Hausarzt", "Wien"),
            "https://www.docfinder.at/suche/hausarzt/wien",
        )
        listings = parse_docfinder_listing_html(
            """
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"SearchResultsPage","mainEntity":{"@type":"ItemList",
            "itemListElement":[{"@type":"ListItem","position":1,
            "url":"https://www.docfinder.at/praktischer-arzt/1090-wien/dr-demo-arzt","name":"Dr. Demo Arzt"}]}}
            </script>
            """
        )
        self.assertEqual(
            listings[0],
            ("Dr. Demo Arzt", "https://www.docfinder.at/praktischer-arzt/1090-wien/dr-demo-arzt"),
        )
        website = parse_docfinder_detail_website(
            '<a data-t-action="homepage" data-t-params="https://www.praxis-demo.example/arzt/demo">Homepage</a>'
            '<a data-t-action="homepage" data-t-params="https://demo.youcanbook.me/">Buchen</a>'
        )
        self.assertEqual(website, "https://www.praxis-demo.example/arzt/demo")
        email = parse_docfinder_detail_email(
            '<a data-t-action="email" data-t-params="kontakt@praxis-demo.example">Mail</a>'
        )
        self.assertEqual(email, "kontakt@praxis-demo.example")
        name = parse_docfinder_detail_name(
            "<title>Dr. Demo Arzt | Praktischer Arzt in 1090 Wien - DocFinder.at</title>"
        )
        self.assertEqual(name, "Dr. Demo Arzt")

    def test_fetch_directory_html_requires_zenrows_by_default(self) -> None:
        configure_directory_fetch(DirectoryFetchConfig())
        with self.assertRaisesRegex(Exception, "ZenRows"):
            fetch_directory_html("https://www.gelbeseiten.de/branchen/hotel/Berlin")


class DirectoryProviderTests(unittest.TestCase):
    def test_provider_from_name_supports_directories(self) -> None:
        provider = provider_from_name("directories")

        self.assertIsInstance(provider, DirectorySearchProvider)
        self.assertEqual(source_label(provider), "Branchenverzeichnisse")

    def test_directory_provider_requires_zenrows_key(self) -> None:
        provider = DirectorySearchProvider(zenrows_api_key="")
        with self.assertRaises(SearchProviderError):
            provider.search("hotel", "Berlin", 5)

    def test_combined_provider_includes_directories_with_zenrows_key(self) -> None:
        provider = combined_provider(
            use_osm=False,
            use_duckduckgo=False,
            use_directories=True,
            use_zenrows_google=False,
            zenrows_key="test-key",
        )

        labels = [source_label(sub) for sub in provider.providers]
        self.assertEqual(labels, ["Branchenverzeichnisse"])
        self.assertEqual(provider.providers[0].zenrows_api_key, "test-key")

    def test_combined_provider_skips_directories_without_zenrows_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            provider = combined_provider(use_osm=False, use_duckduckgo=False, use_directories=True)

        self.assertEqual(provider.providers, [])


class DirectoryLimitCapTests(unittest.TestCase):
    def test_caps_per_source_limit(self) -> None:
        self.assertEqual(cap_directory_source_limit(250_000), 120)
        self.assertEqual(cap_directory_source_limit(10), 10)

    def test_directory_location_plans_without_location_uses_all_cities(self) -> None:
        plans = directory_location_plans("", ("DE",))

        self.assertGreater(len(plans), 500)
        self.assertIn("Berlin", plans)

    def test_caps_detail_fetches_in_enrichment(self) -> None:
        listings = [(f"Firma {index}", f"https://example.test/{index}") for index in range(50)]
        fetch_calls = 0

        def fake_fetch(_url: str) -> str:
            nonlocal fetch_calls
            fetch_calls += 1
            return "<html></html>"

        with patch("lead_research.directories.fetch_directory_html", side_effect=fake_fetch), patch(
            "lead_research.directories.parse_gelbeseiten_detail_html", return_value=None
        ), patch("lead_research.directories.time.sleep"):
            enrich_gelbeseiten_entries(listings, max_detail_fetches=250_000)

        self.assertEqual(fetch_calls, cap_directory_detail_fetches(250_000))


if __name__ == "__main__":
    unittest.main()
