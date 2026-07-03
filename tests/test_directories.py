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

    def test_parse_stepstone_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_stepstone_url,
            parse_stepstone_detail_name,
            parse_stepstone_detail_website,
            parse_stepstone_listing_html,
        )

        self.assertEqual(
            build_stepstone_url("Steuerberater", "Berlin", 2),
            "https://www.stepstone.de/jobs/steuerberater/in-berlin?page=2&action=paging_next",
        )
        listings = parse_stepstone_listing_html(
            """
            "companyName":"Demo Steuer GmbH","companyUrl":"https://www.stepstone.de/cmp/de/demo-steuer-gmbh-123/jobs"
            <a href="https://www.stepstone.de/cmp/de/fallback-buero-456/jobs">Fallback</a>
            """
        )
        self.assertEqual(
            listings[0],
            ("Demo Steuer GmbH", "https://www.stepstone.de/cmp/de/demo-steuer-gmbh-123/jobs"),
        )
        website = parse_stepstone_detail_website('"website":"https://www.demo-steuer.example"')
        self.assertEqual(website, "https://www.demo-steuer.example")
        name = parse_stepstone_detail_name("<title>12 Aktuelle Jobs bei Demo Steuer GmbH | Stepstone</title>")
        self.assertEqual(name, "Demo Steuer GmbH")

    def test_parse_arbeitsagentur_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_arbeitsagentur_url,
            parse_arbeitsagentur_detail_html,
            parse_arbeitsagentur_detail_website,
            parse_arbeitsagentur_listing_html,
        )

        self.assertIn(
            "was=Steuerberater&wo=Berlin&page=2",
            build_arbeitsagentur_url("Steuerberater", "Berlin", 2),
        )
        listings = parse_arbeitsagentur_listing_html(
            """
            <script id="ng-state" type="application/json">{
              "suchergebnis": {
                "ergebnisliste": [
                  {
                    "firma": "Demo Steuer GmbH",
                    "referenznummer": "10000-123-S",
                    "arbeitgeberKundennummerHash": "hash-1"
                  },
                  {
                    "firma": "Demo Steuer GmbH",
                    "referenznummer": "10000-124-S",
                    "arbeitgeberKundennummerHash": "hash-1"
                  }
                ]
              }
            }</script>
            """
        )
        self.assertEqual(
            listings[0],
            ("Demo Steuer GmbH", "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-123-S"),
        )
        self.assertEqual(len(listings), 1)
        detail_html = """
        <script id="ng-state" type="application/json">{
          "jobdetail": {"firma": "Demo Steuer GmbH"},
          "arbeitgeberdarstellung": {
            "firma": "Demo Steuer GmbH",
            "links": [{"url": "https://www.demo-steuer.example", "art": "Homepage"}],
            "kontaktinformationen": "E-Mail: bewerbung@demo-steuer.example"
          }
        }</script>
        """
        website = parse_arbeitsagentur_detail_website(detail_html)
        self.assertEqual(website, "https://www.demo-steuer.example")
        entry = parse_arbeitsagentur_detail_html(
            detail_html,
            name="Demo Steuer GmbH",
            source_url="https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-123-S",
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.email, "bewerbung@demo-steuer.example")

    def test_parse_treatwell_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_treatwell_url,
            parse_treatwell_detail_html,
            parse_treatwell_listing_html,
            treatwell_location_slug,
        )

        self.assertEqual(treatwell_location_slug("München"), "muenchen")
        self.assertEqual(
            build_treatwell_url("Friseur", "Köln", 2),
            "https://www.treatwell.de/orte/friseur/angebot-typ-lokal/in-koeln-de/seite-2/",
        )
        listings = parse_treatwell_listing_html(
            """
            <a href="https://www.treatwell.de/ort/berlin/">City</a>
            <a href="https://www.treatwell.de/ort/demo-salon-berlin/">Salon</a>
            <a href="https://www.treatwell.de/ort/demo-salon-berlin/?serviceIds=1">Salon</a>
            """,
            location="Berlin",
        )
        self.assertEqual(
            listings[0],
            ("Demo Salon Berlin", "https://www.treatwell.de/ort/demo-salon-berlin/"),
        )
        entry = parse_treatwell_detail_html(
            '"email":"kontakt@demo-salon.example"\n<title>Demo Salon | Treatwell</title>',
            name="Demo Salon Berlin",
            source_url="https://www.treatwell.de/ort/demo-salon-berlin/",
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.email, "kontakt@demo-salon.example")
        self.assertEqual(entry.name, "Demo Salon")

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

    def test_parse_anwaltauskunft_json(self) -> None:
        from lead_research.directories import build_anwaltauskunft_url, parse_anwaltauskunft_json

        self.assertIn(
            "location=Berlin&specialty=Steuerrecht",
            build_anwaltauskunft_url("Steuerrecht", "Berlin"),
        )
        entries = parse_anwaltauskunft_json(
            """
            {
              "count": 1,
              "data": [{
                "id": "demo-1",
                "vorname": "Max",
                "nachname": "Muster",
                "internetadresse_1": "www.demo-kanzlei.example",
                "e_mail_1": "info@demo-kanzlei.example",
                "telefon_1": "+49 30 123",
                "organisation": {"name": "Demo Kanzlei GbR"}
              }]
            }
            """,
            source_url="https://anwaltauskunft.de/wp-json/search/v1/query?location=Berlin",
        )
        self.assertEqual(entries[0].name, "Demo Kanzlei GbR")
        self.assertEqual(entries[0].website, "https://www.demo-kanzlei.example")
        self.assertEqual(entries[0].email, "info@demo-kanzlei.example")

    def test_parse_herold_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_herold_url,
            parse_herold_detail_html,
            parse_herold_detail_name,
            parse_herold_detail_website,
            parse_herold_listing_html,
        )

        self.assertEqual(
            build_herold_url("Steuerberater", "Wien"),
            "https://www.herold.at/gelbe-seiten/wien/steuerberater/",
        )
        listings = parse_herold_listing_html(
            """
            <a href="/gelbe-seiten/wien/TLhrv/steuerberatung-weiss/"
               data-ht-label="company_name" data-ht-value="2026962">
              <picture alt="Logo von Steuerberatung Weiß"></picture>
              <span><!--t=at-->Steuerberatung Weiß<!----></span>
            </a>
            """
        )
        self.assertEqual(
            listings[0],
            (
                "Steuerberatung Weiß",
                "https://www.herold.at/gelbe-seiten/wien/TLhrv/steuerberatung-weiss/",
            ),
        )
        website = parse_herold_detail_website(
            '<a href="https://www.demo-steuerberatung.example" data-ht-label="use_other_contact_info">'
            '<i class="icon icon-internet"></i></a>'
            '<a href="https://www.facebook.com/demo">Facebook</a>'
        )
        self.assertEqual(website, "https://www.demo-steuerberatung.example")
        name = parse_herold_detail_name(
            "<title>Steuerberatung Weiß in 1010 Wien 1 (Innere Stadt) | herold.at</title>"
        )
        self.assertEqual(name, "Steuerberatung Weiß")
        entry = parse_herold_detail_html(
            '<a href="mailto:office@demo-steuerberatung.example">Mail</a>'
            '<a href="https://www.demo-steuerberatung.example" data-ht-label="use_other_contact_info">Web</a>',
            name="Steuerberatung Weiß",
            source_url="https://www.herold.at/gelbe-seiten/wien/TLhrv/demo/",
        )
        assert entry is not None
        self.assertEqual(entry.email, "office@demo-steuerberatung.example")
        self.assertEqual(entry.website, "https://www.demo-steuerberatung.example")

    def test_parse_wko_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_wko_url,
            parse_wko_detail_html,
            parse_wko_listing_html,
        )

        self.assertEqual(
            build_wko_url("Steuerberater", "Wien", 2),
            "https://firmen.wko.at/steuerberater/wien/?page=2",
        )
        ready, pending = parse_wko_listing_html(
            """
            <article class='search-result-article'>
              <a class="title-link" href="/demo-steuerberatung/wien/?firmaid=1">
                <h3>Demo Steuerberatung GmbH</h3>
              </a>
              <a data-gtm-event="kontaktinfo-mail-click" href='mailto:office@demo-steuerberatung.example'>
                <span>office@demo-steuerberatung.example</span>
              </a>
              <a data-gtm-event="kontaktinfo-web-click" href='https://www.demo-steuerberatung.example/'>
                <span>https://www.demo-steuerberatung.example/</span>
              </a>
            </article>
            <article class='search-result-article'>
              <a class="title-link" href="/ohne-kontakt/wien/?firmaid=2"><h3>Ohne Kontakt KG</h3></a>
            </article>
            """,
            source_url="https://firmen.wko.at/steuerberater/wien/",
        )
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].email, "office@demo-steuerberatung.example")
        self.assertEqual(ready[0].website.rstrip("/"), "https://www.demo-steuerberatung.example")
        self.assertEqual(
            pending[0],
            ("Ohne Kontakt KG", "https://firmen.wko.at/ohne-kontakt/wien/?firmaid=2"),
        )
        entry = parse_wko_detail_html(
            "<h1 class='detail-heading h3'>Ohne Kontakt KG</h1>"
            "<a data-gtm-event='kontaktinfo-mail-click' href='mailto:kontakt@ohne-kontakt.example'>",
            name="Ohne Kontakt KG",
            source_url="https://firmen.wko.at/ohne-kontakt/wien/",
        )
        assert entry is not None
        self.assertEqual(entry.email, "kontakt@ohne-kontakt.example")

    def test_parse_golocal_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_golocal_url,
            parse_golocal_detail_name,
            parse_golocal_detail_website,
            parse_golocal_listing_html,
        )

        self.assertEqual(
            build_golocal_url("Steuerberater", "Berlin", 2),
            "https://www.golocal.de/berlin/steuerberater/?p=2",
        )
        listings = parse_golocal_listing_html(
            """
            <li class="listEntry ">
              <meta itemprop="name" content="Demo Steuerberatung" />
              <h2 class="title">
                <a href="https://www.golocal.de/berlin/steuerberater/demo-steuerberatung-abc/">
                  Demo Steuerberatung
                </a>
              </h2>
            </li>
            <li class="listEntry ui-droppable gl-adsbygoogle"></li>
            """
        )
        self.assertEqual(
            listings[0],
            (
                "Demo Steuerberatung",
                "https://www.golocal.de/berlin/steuerberater/demo-steuerberatung-abc/",
            ),
        )
        website = parse_golocal_detail_website(
            '<a href="https://www.demo-steuerberatung.example" itemprop=url target=_blank>Homepage</a>'
        )
        self.assertEqual(website, "https://www.demo-steuerberatung.example")
        name = parse_golocal_detail_name('<meta itemprop="name" content="Demo Steuerberatung" />')
        self.assertEqual(name, "Demo Steuerberatung")

    def test_parse_wlw_listing_and_detail(self) -> None:
        from lead_research.directories import (
            build_wlw_url,
            parse_wlw_detail_html,
            parse_wlw_detail_website,
            parse_wlw_listing_html,
        )

        self.assertIn(
            "qs=Steuerberater&ort=Berlin&page=2",
            build_wlw_url("Steuerberater", "Berlin", 2),
        )
        listings = parse_wlw_listing_html(
            """
            <script type="application/ld+json">
            {"@context":"https://schema.org","@graph":[{"@type":"ItemList","itemListElement":[
              {"@type":"ListItem","position":1,"item":{
                "@type":"Organization","name":"Demo Supplier GmbH",
                "url":"https://www.wlw.de/de/firma/demo-supplier-gmbh-123456"
              }}
            ]}]}
            </script>
            """
        )
        self.assertEqual(
            listings[0],
            (
                "Demo Supplier GmbH",
                "https://www.wlw.de/de/firma/demo-supplier-gmbh-123456",
            ),
        )
        website = parse_wlw_detail_website('"homepage":"https://www.demo-supplier.example"')
        self.assertEqual(website, "https://www.demo-supplier.example")
        entry = parse_wlw_detail_html(
            '"homepage":"https://www.demo-supplier.example" "email":"kontakt@demo-supplier.example"',
            name="Demo Supplier GmbH",
            source_url="https://www.wlw.de/de/firma/demo-supplier-gmbh-123456",
        )
        assert entry is not None
        self.assertEqual(entry.email, "kontakt@demo-supplier.example")
        self.assertEqual(entry.website, "https://www.demo-supplier.example")

    def test_parse_steuerberater_filters_and_detail(self) -> None:
        from lead_research.directories import (
            parse_steuerberater_company_filters,
            parse_steuerberater_detail_html,
            parse_steuerberater_detail_link,
        )

        companies = parse_steuerberater_company_filters(
            """
            <select name="nachnameOrFirmennameFilter">
              <option value=""></option>
              <option title="A&amp;C Steuerberatung GmbH" value="QSZD">A&amp;C Steuerberatung GmbH</option>
            </select>
            """
        )
        self.assertEqual(companies[0][0], "A&C Steuerberatung GmbH")
        link = parse_steuerberater_detail_link(
            '<a class="link-to-detail" href="details/F7-A5-89-CB/?lang=de">'
        )
        self.assertEqual(
            link,
            "https://steuerberaterverzeichnis.berufs-org.de/details/F7-A5-89-CB/?lang=de",
        )
        entry = parse_steuerberater_detail_html(
            '<a href="mailto:h.demo@steuerberatung-ac.de">Mail</a> www.steuerberatung-ac.de',
            name="A&C Steuerberatung GmbH",
            source_url=link,
        )
        assert entry is not None
        self.assertEqual(entry.email, "h.demo@steuerberatung-ac.de")
        self.assertEqual(entry.website, "https://www.steuerberatung-ac.de")

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
