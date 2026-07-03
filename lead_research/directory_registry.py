from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

DirectoryScraper = Callable[[str, str, int], list]


@dataclass(frozen=True)
class DirectorySourceSpec:
    id: str
    label: str
    category: str
    scraper: DirectoryScraper | None = None
    default_enabled: bool = True
    implemented: bool = False
    unavailable: bool = False


DIRECTORY_CATEGORIES: tuple[str, ...] = (
    "Firmenverzeichnisse",
    "Unternehmensdatenbanken",
    "Handelsregister",
    "IHK / HWK",
    "Verbaende",
    "Hersteller",
    "Franchise",
    "Messen",
    "Jobboersen",
    "Immobilien",
    "Gastronomie",
    "Hotels",
    "Aerzte",
    "Handwerker",
    "Marktplaetze",
    "Lieferanten",
    "Startups",
    "Software",
    "SaaS",
    "Bewertungen",
    "Social Media",
    "Google",
    "Bing",
    "Apple",
    "Branchen",
    "Oeffentliche Daten",
    "E-Commerce",
    "Reise",
    "Logistik",
    "News",
    "Ausschreibungen",
    "Universitaeten",
    "Foerderprogramme",
    "GitHub",
    "APIs",
    "Lokale Portale",
    "Sonstige",
)


def _source_id(category: str, id_suffix: str) -> str:
    return f"{category.casefold().replace(' ', '_').replace('/', '_')}_{id_suffix}"


def _label_slug(label: str) -> str:
    return label.lower().replace(" ", "_").replace("&", "and").replace(".", "").replace("-", "_")


def _planned(category: str, id_suffix: str, label: str) -> DirectorySourceSpec:
    return DirectorySourceSpec(
        id=_source_id(category, id_suffix),
        label=label,
        category=category,
        scraper=None,
        default_enabled=False,
        implemented=False,
        unavailable=False,
    )


def _unavailable(category: str, id_suffix: str, label: str) -> DirectorySourceSpec:
    return DirectorySourceSpec(
        id=_source_id(category, id_suffix),
        label=label,
        category=category,
        scraper=None,
        default_enabled=False,
        implemented=False,
        unavailable=True,
    )


def build_directory_source_registry(
    scrapers: dict[str, DirectoryScraper],
) -> tuple[DirectorySourceSpec, ...]:
    def active(category: str, source_id: str, label: str, scraper_key: str) -> DirectorySourceSpec:
        return DirectorySourceSpec(
            id=source_id,
            label=label,
            category=category,
            scraper=scrapers[scraper_key],
            default_enabled=True,
            implemented=True,
        )

    cat = "Firmenverzeichnisse"
    specs: list[DirectorySourceSpec] = [
        active(cat, "gelbeseiten", "Gelbe Seiten", "gelbeseiten"),
        active(cat, "das_oertliche", "Das Oertliche", "das_oertliche"),
        active(cat, "telefonbuch", "Das Telefonbuch", "telefonbuch"),
        active(cat, "11880", "11880", "11880"),
        active(cat, "auskunft", "auskunft.de", "auskunft"),
        active(cat, "cylex", "Cylex", "cylex"),
        active(cat, "hotfrog", "Hotfrog", "hotfrog"),
        active(cat, "werkenntdenbesten", "Wer kennt den BESTEN", "werkenntdenbesten"),
        active(cat, "goyellow", "GoYellow", "goyellow"),
        active(cat, "yelp", "Yelp", "yelp"),
        active(cat, "europages", "Europages", "europages"),
        active(cat, "kompass", "Kompass", "kompass"),
        active(cat, "manta", "Manta", "manta"),
        active(cat, "herold", "Herold", "herold"),
        active(cat, "wko", "WKO Firmen A-Z", "wko"),
        _unavailable(cat, "firmenabc", "FirmenABC"),
        _unavailable(cat, "business_branchenbuch", "Business Branchenbuch"),
        _unavailable(cat, "brownbook", "Brownbook"),
        _unavailable(cat, "yalwa", "Yalwa"),
        _unavailable(cat, "yellow_pages", "Yellow Pages"),
        _unavailable(cat, "192com", "192.com"),
        _unavailable(cat, "scoot", "Scoot"),
        _unavailable(cat, "businesslist", "BusinessList"),
        _unavailable(cat, "meinestadt", "meinestadt.de"),
        _unavailable(cat, "firmenwissen", "Firmenwissen"),
    ]

    active_category_sources: tuple[tuple[str, str, str, str], ...] = (
        ("Unternehmensdatenbanken", "pitchbook", "PitchBook", "pitchbook"),
        ("Jobboersen", "indeed", "Indeed", "indeed"),
        ("Jobboersen", "stepstone", "StepStone", "stepstone"),
        ("Aerzte", "jameda", "Jameda", "jameda"),
        ("Aerzte", "sanego", "Sanego", "sanego"),
        ("Aerzte", "docfinder", "DocFinder", "docfinder"),
        ("Gastronomie", "restaurantguru", "Restaurant Guru", "restaurantguru"),
        ("Branchen", "anwaltauskunft", "Anwaltauskunft", "anwaltauskunft"),
        ("Branchen", "branchen_anwaelte", "Anwaelte", "anwaltauskunft"),
        ("Branchen", "steuerberater", "Steuerberaterverzeichnis", "steuerberater"),
        ("Branchen", "branchen_steuerberater", "Steuerberater", "steuerberater"),
        ("Branchen", "treatwell", "Treatwell", "treatwell"),
        ("Branchen", "branchen_restaurants", "Restaurants", "restaurantguru"),
        ("Branchen", "branchen_hotels", "Hotels", "golocal"),
        ("Branchen", "branchen_immobilienmakler", "Immobilienmakler", "gelbeseiten"),
        ("Branchen", "branchen_versicherungen", "Versicherungen", "gelbeseiten"),
        ("Branchen", "branchen_autohaeuser", "Autohaeuser", "gelbeseiten"),
        ("Branchen", "branchen_werkstaetten", "Werkstaetten", "gelbeseiten"),
        ("Branchen", "branchen_bauunternehmen", "Bauunternehmen", "gelbeseiten"),
        ("Branchen", "branchen_elektriker", "Elektriker", "gelbeseiten"),
        ("Branchen", "branchen_instalateure", "Installateure", "gelbeseiten"),
        ("Branchen", "branchen_maler", "Maler", "gelbeseiten"),
        ("Branchen", "branchen_dachdecker", "Dachdecker", "gelbeseiten"),
        ("Branchen", "branchen_solarfirmen", "Solarfirmen", "gelbeseiten"),
        ("Branchen", "branchen_fitnessstudios", "Fitnessstudios", "treatwell_fitness"),
        ("Lokale Portale", "golocal", "GoLocal", "golocal"),
        ("Lieferanten", "wlw", "Wer liefert was (WLW)", "wlw"),
        ("Lieferanten", "lieferanten_europages", "Europages", "europages"),
    )
    active_slugs_by_category: dict[str, set[str]] = {}
    for category, source_id, label, scraper_key in active_category_sources:
        if scraper_key not in scrapers:
            continue
        specs.append(active(category, source_id, label, scraper_key))
        active_slugs_by_category.setdefault(category, set()).add(_label_slug(label))

    unavailable_by_category: dict[str, set[str]] = {
        "Unternehmensdatenbanken": {
            "north_data",
            "opencorporates",
            "dun_and_bradstreet",
            "crunchbase",
            "cb_insights",
            "owler",
            "apollo",
            "zoominfo",
            "rocketreach",
            "seamlessai",
            "dealroom",
            "tracxn",
        },
        "Logistik": {"hapag_lloyd", "maersk", "msc", "cma_cgm"},
        "Handelsregister": {
            "handelsregisterde",
            "bundesanzeiger",
            "companies_house",
            "sec_edgar",
            "opencorporates",
        },
        "Jobboersen": {
            "monster",
            "linkedin_jobs",
            "xing_jobs",
            "joblift",
            "glassdoor",
            "greenhouse",
            "lever",
            "workable",
        },
        "Gastronomie": {
            "opentable",
            "tripadvisor",
            "michelin_guide",
        },
        "Handwerker": {
            "myhammer",
            "houzz",
            "check24",
            "trustatrader",
        },
        "Hotels": {
            "booking",
            "expedia",
            "hotelscom",
            "tripadvisor",
        },
        "Aerzte": {
            "doctolib",
        },
        "Immobilien": {
            "idealista",
            "immobilienscout24",
            "immowelt",
            "immonet",
            "rightmove",
            "zillow",
            "loopnet",
            "realtor",
        },
        "Software": {
            "g2",
            "capterra",
            "getapp",
            "alternativeto",
        },
        "Bewertungen": {
            "trustpilot",
        },
        "Lieferanten": {
            "thomasnet",
            "global_sources",
            "india_mart",
            "made_in_china",
        },
        "Lokale Portale": {
            "locanto",
            "kennstdueinen",
        },
        "IHK / HWK": {
            "industrie_und_handelskammern",
            "handwerkskammern",
            "mitgliederverzeichnisse",
            "amtliches_verzeichnis",
        },
        "Branchen": {
            "notare",
            "architekten",
            "physiotherapeuten",
            "zahnaerzte",
        },
    }

    planned_groups: dict[str, list[str]] = {
        "Unternehmensdatenbanken": [
            "North Data",
            "OpenCorporates",
            "Dun & Bradstreet",
            "Crunchbase",
            "PitchBook",
            "CB Insights",
            "Owler",
            "Apollo",
            "ZoomInfo",
            "RocketReach",
            "Seamless.ai",
            "Dealroom",
            "Tracxn",
        ],
        "Handelsregister": [
            "Bundesanzeiger",
            "Handelsregister.de",
            "Companies House",
            "OpenCorporates",
            "SEC EDGAR",
        ],
        "IHK / HWK": [
            "Industrie- und Handelskammern",
            "Handwerkskammern",
            "Mitgliederverzeichnisse",
            "Amtliches Verzeichnis",
        ],
        "Verbaende": ["Bundesverbaende", "Fachverbaende", "Innungen", "Berufsverbaende", "Vereinslisten"],
        "Hersteller": ["Partnerfinder", "Haendlerlisten", "Distributorlisten", "Resellerlisten"],
        "Franchise": ["Franchiseportal", "Franchise Direkt", "Franchise Opportunities"],
        "Messen": ["Ausstellerlisten", "Messekataloge", "Konferenzen", "Events", "Expo-Aussteller"],
        "Jobboersen": [
            "Indeed",
            "StepStone",
            "LinkedIn Jobs",
            "XING Jobs",
            "Monster",
            "Joblift",
            "Glassdoor",
            "Greenhouse",
            "Lever",
            "Workable",
        ],
        "Immobilien": [
            "Immobilienscout24",
            "Immowelt",
            "Immonet",
            "Idealista",
            "Rightmove",
            "Zillow",
            "LoopNet",
            "Realtor",
        ],
        "Gastronomie": ["OpenTable", "Tripadvisor", "Michelin Guide", "Restaurant Guru"],
        "Hotels": ["Booking", "Expedia", "Hotels.com", "Tripadvisor"],
        "Aerzte": ["Jameda", "Doctolib", "Sanego"],
        "Handwerker": ["MyHammer", "Check24", "Houzz", "TrustATrader"],
        "Marktplaetze": [
            "Amazon Seller",
            "eBay Shops",
            "Alibaba",
            "AliExpress",
            "Faire",
            "Ankorstore",
            "Etsy",
            "Kaufland Marketplace",
        ],
        "Lieferanten": ["Thomasnet", "GlobalSources", "IndiaMART", "Made-in-China", "Europages", "Wer liefert was (WLW)"],
        "Startups": ["Crunchbase", "AngelList", "Dealroom", "Product Hunt", "Wellfound"],
        "Software": ["G2", "Capterra", "GetApp", "AlternativeTo"],
        "SaaS": ["Product Hunt", "StackShare", "SaaSHub"],
        "Bewertungen": ["Trustpilot", "Google Reviews", "Yelp", "Tripadvisor", "Glassdoor"],
        "Social Media": [
            "LinkedIn Company Pages",
            "Facebook Pages",
            "Instagram Business",
            "X",
            "TikTok Business",
            "YouTube Channels",
            "Pinterest Business",
        ],
        "Google": ["Google Maps", "Google Search", "Google Shopping", "Google Hotels"],
        "Bing": ["Bing Maps", "Bing Search"],
        "Apple": ["Apple Maps"],
        "Branchen": [
            "Architekten",
            "Steuerberater",
            "Notare",
            "Anwaelte",
            "Immobilienmakler",
            "Versicherungen",
            "Autohaeuser",
            "Werkstaetten",
            "Zahnaerzte",
            "Physiotherapeuten",
            "Fitnessstudios",
            "Hotels",
            "Restaurants",
            "Bauunternehmen",
            "Elektriker",
            "Installateure",
            "Maler",
            "Dachdecker",
            "Solarfirmen",
        ],
        "Oeffentliche Daten": ["Open Data Portale", "Data.gov", "EU Open Data", "Kommunale Verzeichnisse"],
        "E-Commerce": [
            "Amazon",
            "Otto",
            "Zalando",
            "Walmart",
            "Target",
            "MediaMarkt",
            "Saturn",
            "Kaufland",
            "Hornbach",
            "OBI",
            "IKEA",
        ],
        "Reise": ["Airbnb", "Booking", "Expedia", "Tripadvisor"],
        "Logistik": ["Hapag Lloyd", "Maersk", "MSC", "CMA CGM"],
        "News": ["Presseportale", "Pressemitteilungen", "Unternehmensnews"],
        "Ausschreibungen": ["TED Europa", "Vergabeportale", "Bund.de", "Subreport"],
        "Universitaeten": ["Partnerlisten", "Spin-offs", "Institute"],
        "Foerderprogramme": ["Exist", "EU Foerderprojekte", "Innovationsnetzwerke"],
        "GitHub": ["Organisationen", "Repositories", "Contributors"],
        "APIs": ["OpenCorporates API", "Clearbit", "People Data Labs", "FullContact"],
        "Lokale Portale": ["Stadtportale", "Gemeindeverzeichnisse", "Tourismusportale", "Gewerbeverzeichnisse", "GoLocal", "KennstDuEinen"],
        "Sonstige": [
            "Wikipedia Unternehmenslisten",
            "Verbandslisten",
            "Lieferantenlisten",
            "Partnerlisten",
            "Resellerlisten",
            "Referenzkunden",
            "Kundenlisten",
            "Case Studies",
        ],
    }

    for category, labels in planned_groups.items():
        blocked = unavailable_by_category.get(category, set())
        active_slugs = active_slugs_by_category.get(category, set())
        for label in labels:
            slug = _label_slug(label)
            if slug in active_slugs:
                continue
            if slug in blocked:
                specs.append(_unavailable(category, slug, label))
            else:
                specs.append(_planned(category, slug, label))

    return tuple(specs)


def implemented_directory_sources(registry: Iterable[DirectorySourceSpec]) -> tuple[DirectorySourceSpec, ...]:
    return tuple(spec for spec in registry if spec.implemented and spec.scraper is not None)


def unavailable_directory_sources(registry: Iterable[DirectorySourceSpec]) -> tuple[DirectorySourceSpec, ...]:
    return tuple(spec for spec in registry if spec.unavailable)


def planned_directory_sources(registry: Iterable[DirectorySourceSpec]) -> tuple[DirectorySourceSpec, ...]:
    return tuple(spec for spec in registry if not spec.implemented and not spec.unavailable)


def resolve_active_scrapers(
    registry: Iterable[DirectorySourceSpec],
    enabled_ids: set[str] | None = None,
) -> tuple[tuple[str, DirectoryScraper], ...]:
    implemented = implemented_directory_sources(registry)
    if enabled_ids is None:
        selected = [spec for spec in implemented if spec.default_enabled]
    else:
        selected = [spec for spec in implemented if spec.id in enabled_ids]
    return tuple((spec.label, spec.scraper) for spec in selected if spec.scraper is not None)


def default_enabled_directory_source_ids(registry: Iterable[DirectorySourceSpec]) -> set[str]:
    return {spec.id for spec in implemented_directory_sources(registry) if spec.default_enabled}


def directory_sources_by_category(
    registry: Iterable[DirectorySourceSpec],
) -> dict[str, tuple[DirectorySourceSpec, ...]]:
    grouped: dict[str, list[DirectorySourceSpec]] = {}
    for spec in registry:
        grouped.setdefault(spec.category, []).append(spec)
    return {category: tuple(grouped.get(category, ())) for category in DIRECTORY_CATEGORIES if grouped.get(category)}
