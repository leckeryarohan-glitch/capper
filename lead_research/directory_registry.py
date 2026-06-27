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


def _planned(category: str, id_suffix: str, label: str) -> DirectorySourceSpec:
    return DirectorySourceSpec(
        id=f"{category.casefold().replace(' ', '_').replace('/', '_')}_{id_suffix}",
        label=label,
        category=category,
        scraper=None,
        default_enabled=False,
        implemented=False,
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
        _planned(cat, "yelp", "Yelp"),
        _planned(cat, "europages", "Europages"),
        _planned(cat, "kompass", "Kompass"),
        _planned(cat, "wlw", "Wer liefert was (WLW)"),
        _planned(cat, "firmenabc", "FirmenABC"),
        _planned(cat, "business_branchenbuch", "Business Branchenbuch"),
        _planned(cat, "goyellow", "GoYellow"),
        _planned(cat, "brownbook", "Brownbook"),
        _planned(cat, "manta", "Manta"),
        _planned(cat, "yalwa", "Yalwa"),
        _planned(cat, "yellow_pages", "Yellow Pages"),
        _planned(cat, "192com", "192.com"),
        _planned(cat, "scoot", "Scoot"),
        _planned(cat, "businesslist", "BusinessList"),
        _planned(cat, "meinestadt", "meinestadt.de"),
        _planned(cat, "firmenwissen", "Firmenwissen"),
    ]

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
        "Lieferanten": ["Thomasnet", "GlobalSources", "IndiaMART", "Made-in-China", "Europages"],
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
        "Lokale Portale": ["Stadtportale", "Gemeindeverzeichnisse", "Tourismusportale", "Gewerbeverzeichnisse"],
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
        for label in labels:
            slug = label.lower().replace(" ", "_").replace("&", "and").replace(".", "").replace("-", "_")
            specs.append(_planned(category, slug, label))

    return tuple(specs)


def implemented_directory_sources(registry: Iterable[DirectorySourceSpec]) -> tuple[DirectorySourceSpec, ...]:
    return tuple(spec for spec in registry if spec.implemented and spec.scraper is not None)


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
