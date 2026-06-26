from __future__ import annotations

import html
import itertools
import json
import os
import re
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Callable

from .locations import (
    DEFAULT_COUNTRIES,
    SUPPORTED_COUNTRIES,
    ZENROWS_LOCALE,
    country_label,
    top_cities_for_web_search,
)
from .models import SearchResult


COMMON_SOURCE_DOMAINS = (
    "gelbeseiten.de",
    "dasoertliche.de",
    "11880.com",
    "meinestadt.de",
    "werkenntdenbesten.de",
    "wlw.de",
    "firmenwissen.de",
    "tripadvisor.de",
    "yelp.de",
    "booking.com",
)

OSM_CATEGORY_TAGS = {
    "hotel": (("tourism", "hotel"), ("tourism", "guest_house"), ("tourism", "hostel"), ("tourism", "motel")),
    "pension": (("tourism", "guest_house"), ("tourism", "hotel")),
    "restaurant": (("amenity", "restaurant"),),
    "cafe": (("amenity", "cafe"),),
    "kaffee": (("amenity", "cafe"),),
    "bar": (("amenity", "bar"), ("amenity", "pub")),
    "imbiss": (("amenity", "fast_food"),),
    "baeckerei": (("shop", "bakery"),),
    "bäckerei": (("shop", "bakery"),),
    "metzger": (("shop", "butcher"),),
    "lager": (("building", "warehouse"), ("landuse", "industrial"), ("industrial", "warehouse")),
    "logistik": (("office", "logistics"), ("industrial", "logistics"), ("landuse", "industrial")),
    "spedition": (("office", "logistics"), ("industrial", "logistics")),
    "elektronik": (("shop", "electronics"),),
    "elektriker": (("craft", "electrician"),),
    "it": (("office", "it"), ("office", "company")),
    "software": (("office", "it"), ("office", "company")),
    "friseur": (("shop", "hairdresser"),),
    "kosmetik": (("shop", "beauty"), ("shop", "cosmetics")),
    "arzt": (("amenity", "doctors"),),
    "zahnarzt": (("amenity", "dentist"),),
    "apotheke": (("amenity", "pharmacy"),),
    "tierarzt": (("amenity", "veterinary"),),
    "physio": (("healthcare", "physiotherapist"), ("amenity", "clinic")),
    "auto": (("shop", "car_repair"), ("shop", "car"), ("amenity", "car_rental")),
    "kfz": (("shop", "car_repair"), ("shop", "car")),
    "werkstatt": (("shop", "car_repair"), ("craft", "")),
    "handwerk": (("craft", ""),),
    "maler": (("craft", "painter"),),
    "tischler": (("craft", "carpenter"), ("craft", "joiner")),
    "schreiner": (("craft", "carpenter"), ("craft", "joiner")),
    "sanitaer": (("craft", "plumber"),),
    "sanitär": (("craft", "plumber"),),
    "klempner": (("craft", "plumber"),),
    "dachdecker": (("craft", "roofer"),),
    "bau": (("craft", "builder"), ("office", "construction")),
    "immobilien": (("office", "estate_agent"),),
    "makler": (("office", "estate_agent"),),
    "anwalt": (("office", "lawyer"),),
    "rechtsanwalt": (("office", "lawyer"),),
    "steuerberater": (("office", "tax_advisor"), ("office", "accountant")),
    "versicherung": (("office", "insurance"),),
    "fitness": (("leisure", "fitness_centre"),),
    "supermarkt": (("shop", "supermarket"),),
    "moebel": (("shop", "furniture"),),
    "möbel": (("shop", "furniture"),),
    "blumen": (("shop", "florist"),),
    "florist": (("shop", "florist"),),
    "optiker": (("shop", "optician"),),
    "buero": (("office", "company"),),
    "büro": (("office", "company"),),
    "firma": (("office", "company"),),
}

@dataclass(frozen=True)
class OsmSearchTarget:
    label: str
    country_code: str | None = None

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"

# Fewer city queries for paid SERP APIs to reduce rate limits and cost.
ZENROWS_CITIES_PER_COUNTRY = 12
# so multi-city runs are not throttled to a tiny share each.
OSM_MIN_PER_LOCATION = 100


class SearchProviderError(RuntimeError):
    pass


class SearchProvider(ABC):
    on_status: "Callable[[str], None] | None" = None

    def _report(self, message: str) -> None:
        callback = getattr(self, "on_status", None)
        if callback is None:
            return
        try:
            callback(message)
        except Exception:  # noqa: BLE001 - status reporting must never break a search
            pass

    @abstractmethod
    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        raise NotImplementedError


SOURCE_LABELS = {
    "OpenStreetMapSearchProvider": "OpenStreetMap",
    "DuckDuckGoSearchProvider": "DuckDuckGo",
    "GoogleCustomSearchProvider": "Google",
    "BraveSearchProvider": "Brave",
    "BingSearchProvider": "Bing",
    "SerpApiSearchProvider": "SerpAPI",
    "ZenRowsSearchProvider": "ZenRows",
    "CommonSourcesSearchProvider": "Branchenquellen",
    "MultiSourceProvider": "Kombiniert",
    "FileSearchProvider": "Datei",
    "NominatimSearchProvider": "Nominatim",
}


def source_label(provider: SearchProvider) -> str:
    return SOURCE_LABELS.get(type(provider).__name__, type(provider).__name__)


class FileSearchProvider(SearchProvider):
    """Reads seed URLs from a plain text file, one URL per line."""

    def __init__(self, path: Path):
        self.path = path

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if not self.path.exists():
            raise SearchProviderError(f"Seed file does not exist: {self.path}")

        results: list[SearchResult] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            results.append(SearchResult(title=stripped, url=stripped))
            if len(results) >= limit:
                break
        return results


class BraveSearchProvider(SearchProvider):
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY")
        if not self.api_key:
            raise SearchProviderError("BRAVE_SEARCH_API_KEY is required for Brave search.")

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        query = build_query(category, location)
        params = urllib.parse.urlencode({"q": query, "count": min(limit, 20)})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "capper-lead-research/0.1",
            },
        )
        data = _read_json(request)
        web_results = data.get("web", {}).get("results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            )
            for item in web_results
            if item.get("url")
        ][:limit]


class BingSearchProvider(SearchProvider):
    endpoint = "https://api.bing.microsoft.com/v7.0/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("BING_SEARCH_API_KEY")
        if not self.api_key:
            raise SearchProviderError("BING_SEARCH_API_KEY is required for Bing search.")

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        query = build_query(category, location)
        params = urllib.parse.urlencode({"q": query, "count": min(limit, 50)})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={
                "Accept": "application/json",
                "Ocp-Apim-Subscription-Key": self.api_key,
                "User-Agent": "capper-lead-research/0.1",
            },
        )
        data = _read_json(request)
        web_results = data.get("webPages", {}).get("value", [])
        return [
            SearchResult(
                title=item.get("name", ""),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
            )
            for item in web_results
            if item.get("url")
        ][:limit]


class SerpApiSearchProvider(SearchProvider):
    endpoint = "https://serpapi.com/search.json"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")
        if not self.api_key:
            raise SearchProviderError("SERPAPI_API_KEY is required for SerpAPI search.")

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if limit < 1:
            return []
        results: list[SearchResult] = []
        seen: set[str] = set()

        for query in expand_queries(category, location, countries):
            if len(results) >= limit:
                break
            start = 0
            while len(results) < limit and start <= 90:
                self._report(f"SerpAPI: '{query}' ab {start} ...")
                params = urllib.parse.urlencode(
                    {
                        "engine": "google",
                        "q": query,
                        "api_key": self.api_key,
                        "num": 10,
                        "start": start,
                        "hl": "de",
                        "gl": "de",
                        "google_domain": "google.de",
                    }
                )
                request = urllib.request.Request(
                    f"{self.endpoint}?{params}",
                    headers={"Accept": "application/json", "User-Agent": "capper-lead-research/0.1"},
                )
                try:
                    page_results = serpapi_items_to_results(_read_json(request))
                except SearchProviderError:
                    break
                if not page_results:
                    break
                new_in_page = 0
                for result in page_results:
                    key = result.url.lower().rstrip("/")
                    if key in seen:
                        continue
                    seen.add(key)
                    new_in_page += 1
                    results.append(result)
                    if len(results) >= limit:
                        self._report(f"SerpAPI: {len(results)} Websites gefunden")
                        return results
                start += 10
                if new_in_page == 0:
                    break

        self._report(f"SerpAPI: {len(results)} Websites gefunden")
        return results


def serpapi_items_to_results(data: dict) -> list[SearchResult]:
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in data.get("organic_results", [])
        if is_valid_lead_url(item.get("link", ""))
    ]


class ZenRowsSearchProvider(SearchProvider):
    """Google SERP scraping via the ZenRows Search Results API."""

    serp_endpoint = "https://serp.api.zenrows.com/v1/targets/google/search"
    request_delay_seconds = 0.4

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ZENROWS_API_KEY")
        if not self.api_key:
            raise SearchProviderError("ZENROWS_API_KEY is required for ZenRows search.")

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if limit < 1:
            return []
        results: list[SearchResult] = []
        seen: set[str] = set()
        request_failures = 0

        for query_text, country_code in zenrows_query_plans(category, location, countries):
            if len(results) >= limit:
                break
            locale_country, tld = ZENROWS_LOCALE.get(country_code, ZENROWS_LOCALE["DE"])
            start = 0
            while len(results) < limit and start <= 80:
                self._report(f"ZenRows: '{query_text}' ab {start} ({country_code}) ...")
                encoded_query = urllib.parse.quote(query_text)
                params = urllib.parse.urlencode(
                    {
                        "apikey": self.api_key,
                        "country": locale_country,
                        "tld": tld,
                        "start": str(start),
                    }
                )
                request = urllib.request.Request(
                    f"{self.serp_endpoint}/{encoded_query}?{params}",
                    headers={"Accept": "application/json", "User-Agent": "capper-lead-research/0.1"},
                )
                try:
                    page_results = zenrows_items_to_results(
                        _read_json_with_retry(request, timeout=60, retries=4, backoff_seconds=2.0)
                    )
                except SearchProviderError as exc:
                    request_failures += 1
                    self._report(f"ZenRows: Anfrage fehlgeschlagen fuer '{query_text}' (Start {start}): {exc}")
                    break
                if not page_results:
                    break
                new_in_page = 0
                for result in page_results:
                    key = result.url.lower().rstrip("/")
                    if key in seen:
                        continue
                    seen.add(key)
                    new_in_page += 1
                    results.append(result)
                    if len(results) >= limit:
                        self._report(f"ZenRows: {len(results)} Websites gefunden")
                        return results
                start += 10
                if new_in_page == 0:
                    break
                time.sleep(self.request_delay_seconds)

        self._report(f"ZenRows: {len(results)} Websites gefunden")
        if not results and request_failures:
            self._report(
                "ZenRows: keine Ergebnisse nach API-Fehlern. "
                "Pruefe API-Key/Guthaben oder aktiviere zusaetzlich OpenStreetMap."
            )
        return results


def zenrows_query_plans(
    category: str,
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
) -> list[tuple[str, str]]:
    if location.strip():
        country_code = countries[0] if countries else "DE"
        return [(build_query(category, location), country_code)]
    plans: list[tuple[str, str]] = []
    for country_code in countries:
        if country_code in SUPPORTED_COUNTRIES:
            plans.append((build_query(category, country_label(country_code)), country_code))
    for city_name, country_code in top_cities_for_web_search(
        countries,
        per_country=ZENROWS_CITIES_PER_COUNTRY,
    ):
        plans.append((build_query(category, city_name), country_code))
    return plans


def zenrows_items_to_results(data: dict) -> list[SearchResult]:
    items = data.get("organic_results") or data.get("organic") or []
    results: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("link") or item.get("url") or ""
        if not is_valid_lead_url(link):
            continue
        snippet = item.get("description") or item.get("snippet") or ""
        results.append(SearchResult(title=item.get("title", ""), url=link, snippet=snippet))
    return results


class GoogleCustomSearchProvider(SearchProvider):
    endpoint = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str | None = None, search_engine_id: str | None = None):
        self.api_key = api_key or os.getenv("GOOGLE_SEARCH_API_KEY")
        self.search_engine_id = search_engine_id or os.getenv("GOOGLE_SEARCH_ENGINE_ID")
        if not self.api_key:
            raise SearchProviderError("GOOGLE_SEARCH_API_KEY is required for Google search.")
        if not self.search_engine_id:
            raise SearchProviderError("GOOGLE_SEARCH_ENGINE_ID is required for Google search.")

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        query = build_query(category, location)
        results: list[SearchResult] = []
        start = 1

        while len(results) < limit and start <= 91:
            page_size = min(10, limit - len(results))
            params = urllib.parse.urlencode(
                {
                    "key": self.api_key,
                    "cx": self.search_engine_id,
                    "q": query,
                    "num": page_size,
                    "start": start,
                }
            )
            request = urllib.request.Request(
                f"{self.endpoint}?{params}",
                headers={"Accept": "application/json", "User-Agent": "capper-lead-research/0.1"},
            )
            page_results = google_items_to_results(_read_json(request))
            if not page_results:
                break
            results.extend(page_results)
            start += len(page_results)

        return results[:limit]


def google_items_to_results(data: dict) -> list[SearchResult]:
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in data.get("items", [])
        if is_valid_lead_url(item.get("link", ""))
    ]


class OpenStreetMapSearchProvider(SearchProvider):
    def __init__(self, endpoints: tuple[str, ...] = OVERPASS_ENDPOINTS):
        self.endpoints = endpoints

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if limit < 1:
            return []

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        targets = osm_location_plan(location, countries)
        per_location_limit = (
            limit
            if location.strip()
            else min(limit, max(OSM_MIN_PER_LOCATION, ceil(limit / len(targets))))
        )
        failures: list[str] = []
        has_explicit_location = bool(location.strip())

        for target in targets:
            self._report(f"OpenStreetMap: suche '{category}' in {target.label} ...")
            location_results = (
                self._search_nominatim(category, target.label, per_location_limit)
                if has_explicit_location
                else []
            )
            try:
                location_results.extend(
                    self._search_location(
                        category,
                        target.label,
                        per_location_limit,
                        country_code=target.country_code,
                    )
                )
            except SearchProviderError as exc:
                failures.append(f"{target.label}: {exc}")
            if not location_results:
                location_results = self._search_nominatim(category, target.label, per_location_limit)
            self._report(f"OpenStreetMap: {target.label} -> {len(location_results)} Treffer")
            if not location_results:
                continue
            for result in location_results:
                dedupe_key = result.url.lower().rstrip("/")
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                results.append(result)
                if len(results) >= limit:
                    return results

        if not results and failures:
            raise SearchProviderError("OpenStreetMap/Overpass search failed: " + " | ".join(failures[:3]))
        return results

    def _search_location(
        self,
        category: str,
        location: str,
        limit: int,
        country_code: str | None = None,
    ) -> list[SearchResult]:
        query = build_overpass_query(category, location, limit, country_code=country_code)
        failures: list[str] = []
        for endpoint in self.endpoints:
            request = urllib.request.Request(
                endpoint,
                data=query.encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "text/plain; charset=utf-8",
                    "User-Agent": "capper-lead-research/0.1",
                },
                method="POST",
            )
            try:
                data = _read_json(request, timeout=45)
            except SearchProviderError as exc:
                failures.append(f"{endpoint}: {exc}")
                continue
            return osm_elements_to_results(data, limit)
        raise SearchProviderError("; ".join(failures) or "all Overpass endpoints failed")

    def _search_nominatim(self, category: str, location: str, limit: int) -> list[SearchResult]:
        query = " ".join(part for part in (category.strip(), location.strip()) if part)
        if not query:
            return []
        params = urllib.parse.urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "limit": min(max(limit * 2, limit), 50),
                "extratags": 1,
                "addressdetails": 1,
            }
        )
        request = urllib.request.Request(
            f"{NOMINATIM_ENDPOINT}?{params}",
            headers={
                "Accept": "application/json",
                "User-Agent": "capper-lead-research/0.1",
            },
        )
        try:
            data = _read_json(request, timeout=30)
        except SearchProviderError:
            return []
        return nominatim_items_to_results(data, limit, location)


def osm_location_plan(
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
) -> tuple[OsmSearchTarget, ...]:
    stripped = location.strip()
    if stripped:
        return (OsmSearchTarget(label=stripped),)
    return tuple(
        OsmSearchTarget(label=country_label(code), country_code=code)
        for code in countries
        if code in SUPPORTED_COUNTRIES
    )


def build_overpass_query(
    category: str,
    location: str,
    limit: int,
    country_code: str | None = None,
) -> str:
    selectors = osm_selectors_for_category(category)
    scoped_selectors = []
    area_setup = ""
    location_name = location.strip()
    if country_code:
        area_setup = f'area["ISO3166-1"="{country_code}"][admin_level=2]->.searchArea;\n'
        scoped_selectors = [f'nwr{selector}(area.searchArea);' for selector in selectors]
    elif location_name:
        escaped_location = escape_overpass_regex(location_name)
        area_setup = (
            f'area["name"~"^{escaped_location}$",i]["boundary"="administrative"]->.searchArea;\n'
        )
        scoped_selectors = [f'nwr{selector}(area.searchArea);' for selector in selectors]
    else:
        scoped_selectors = [f"nwr{selector};" for selector in selectors]

    count = max(limit * 10, 200)
    return (
        "[out:json][timeout:35];\n"
        f"{area_setup}"
        "(\n"
        + "\n".join(scoped_selectors)
        + "\n);\n"
        f"out tags center {count};"
    )


def osm_selectors_for_category(category: str) -> list[str]:
    normalized = category.lower()
    selectors: list[str] = []
    for keyword, tag_pairs in OSM_CATEGORY_TAGS.items():
        if keyword in normalized:
            for key, value in tag_pairs:
                selector = f'["{key}"]' if not value else f'["{key}"="{value}"]'
                if selector not in selectors:
                    selectors.append(selector)
    if selectors:
        return selectors

    escaped_category = escape_overpass_regex(category.strip())
    return [f'["name"~"{escaped_category}",i]']


def osm_elements_to_results(data: dict, limit: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        url = first_present(tags, ("website", "contact:website", "url", "contact:url"))
        if not url:
            continue
        normalized_url = normalize_result_url(url)
        if not normalized_url:
            continue
        dedupe_key = normalized_url.lower().rstrip("/")
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        title = tags.get("name", normalized_url)
        snippet = build_osm_snippet(tags)
        results.append(SearchResult(title=title, url=normalized_url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def nominatim_items_to_results(data: list[dict], limit: int, location: str = "") -> list[SearchResult]:
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for item in data:
        if location and not nominatim_item_matches_location(item, location):
            continue
        tags = item.get("extratags") or {}
        url = first_present(tags, ("website", "contact:website", "url", "contact:url"))
        if not url:
            continue
        normalized_url = normalize_result_url(url)
        if not normalized_url:
            continue
        dedupe_key = normalized_url.lower().rstrip("/")
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        title = item.get("name") or item.get("display_name") or normalized_url
        snippet = item.get("display_name", "OpenStreetMap/Nominatim result")
        results.append(SearchResult(title=title, url=normalized_url, snippet=f"Nominatim: {snippet}"))
        if len(results) >= limit:
            break
    return results


def nominatim_item_matches_location(item: dict, location: str) -> bool:
    expected = location.strip().casefold()
    if not expected:
        return True
    address = item.get("address") or {}
    address_values = [
        str(address.get(key, "")).casefold()
        for key in ("city", "town", "village", "municipality", "county", "state")
        if address.get(key)
    ]
    return any(expected == value or expected in value for value in address_values)


def first_present(mapping: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key, "")
        if value:
            return str(value)
    return ""


def normalize_result_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return ""
    parsed = urllib.parse.urlparse(stripped)
    if not parsed.scheme:
        stripped = "https://" + stripped
        parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return stripped


def build_osm_snippet(tags: dict) -> str:
    parts = []
    for key in ("addr:street", "addr:housenumber", "addr:postcode", "addr:city"):
        if tags.get(key):
            parts.append(str(tags[key]))
    return "OpenStreetMap: " + " ".join(parts).strip() if parts else "OpenStreetMap result"


def escape_overpass_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def escape_overpass_regex(value: str) -> str:
    escaped = escape_overpass_string(value)
    for char in ".+*?^$()[]{}|":
        escaped = escaped.replace(char, "\\" + char)
    return escaped


class DuckDuckGoSearchProvider(SearchProvider):
    """No-key web search using the DuckDuckGo HTML endpoint, like a normal user."""

    endpoint = "https://html.duckduckgo.com/html/"

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if limit < 1:
            return []
        results: list[SearchResult] = []
        seen: set[str] = set()

        for query in expand_queries(category, location, countries):
            if len(results) >= limit:
                break
            offset = 0
            page_num = 0
            while len(results) < limit and offset <= 200:
                page_num += 1
                self._report(f"DuckDuckGo: '{query}' Seite {page_num} ...")
                data = urllib.parse.urlencode({"q": query, "s": offset, "kl": "de-de"}).encode("utf-8")
                request = urllib.request.Request(
                    self.endpoint,
                    data=data,
                    headers={
                        "Accept": "text/html",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "Mozilla/5.0 (compatible; capper-lead-research/0.1)",
                    },
                    method="POST",
                )
                try:
                    html_text = _read_text(request, timeout=20)
                except SearchProviderError:
                    break

                links = duckduckgo_links_from_html(html_text)
                if not links:
                    break
                new_in_page = 0
                for url in links:
                    normalized_url = normalize_result_url(url)
                    if not normalized_url or not is_valid_lead_url(normalized_url):
                        continue
                    key = normalized_url.lower().rstrip("/")
                    if key in seen:
                        continue
                    seen.add(key)
                    new_in_page += 1
                    results.append(SearchResult(title=normalized_url, url=normalized_url, snippet="DuckDuckGo result"))
                    if len(results) >= limit:
                        self._report(f"DuckDuckGo: {len(results)} Websites gefunden")
                        return results
                offset += len(links)
                if new_in_page == 0:
                    break
                time.sleep(0.4)

        self._report(f"DuckDuckGo: {len(results)} Websites gefunden")
        return results


def duckduckgo_links_from_html(html_text: str) -> list[str]:
    links: list[str] = []
    for attrs in re.findall(r"<a\b([^>]*)>", html_text, re.IGNORECASE):
        if "result__a" not in attrs and "result__url" not in attrs:
            continue
        match = re.search(r'href="([^"]+)"', attrs, re.IGNORECASE)
        if not match:
            continue
        url = decode_duckduckgo_href(html.unescape(match.group(1)))
        if url:
            links.append(url)
    return links


def decode_duckduckgo_href(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        return target or ""
    if parsed.scheme in {"http", "https"} and "duckduckgo.com" not in parsed.netloc:
        return href
    return ""


class MultiSourceProvider(SearchProvider):
    """Aggregates several providers concurrently and merges deduplicated results."""

    def __init__(self, providers: list[SearchProvider]):
        self.providers = [provider for provider in providers if provider is not None]

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if limit < 1 or not self.providers:
            return []

        labels = ", ".join(source_label(provider) for provider in self.providers)
        self._report(f"Kombiniere {len(self.providers)} Quellen: {labels}")
        for provider in self.providers:
            try:
                provider.on_status = self.on_status
            except Exception:  # noqa: BLE001
                pass

        grouped: list[list[SearchResult]] = []
        with ThreadPoolExecutor(max_workers=len(self.providers)) as executor:
            futures = {
                executor.submit(self._safe_search, provider, category, location, limit, countries): provider
                for provider in self.providers
            }
            for future in as_completed(futures):
                provider = futures[future]
                group = future.result()
                self._report(f"{source_label(provider)}: {len(group)} Websites geliefert")
                grouped.append(group)

        merged: list[SearchResult] = []
        seen: set[str] = set()
        for group in itertools.zip_longest(*grouped):
            for result in group:
                if result is None:
                    continue
                key = result.url.lower().rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(result)
                if len(merged) >= limit:
                    return merged
        return merged

    @staticmethod
    def _safe_search(
        provider: SearchProvider,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...],
    ) -> list[SearchResult]:
        try:
            return provider.search(category, location, limit, countries)
        except SearchProviderError:
            return []
        except Exception:  # noqa: BLE001 - one failing source must not abort the others
            return []


class CommonSourcesSearchProvider(SearchProvider):
    """Searches common business directory and marketplace domains through an API provider."""

    def __init__(self, provider: SearchProvider, domains: tuple[str, ...] = COMMON_SOURCE_DOMAINS):
        self.provider = provider
        self.domains = domains

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        if limit < 1:
            return []

        per_domain_limit = max(1, ceil(limit / len(self.domains)))
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for domain in self.domains:
            site_category = f"{category} site:{domain}"
            for result in self.provider.search(site_category, location, per_domain_limit):
                normalized_url = result.url.lower().rstrip("/")
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
                results.append(result)
                if len(results) >= limit:
                    return results
        return results


def build_query(category: str, location: str) -> str:
    parts = [category, "Unternehmen", "Kontakt", "E-Mail"]
    if location:
        parts.insert(1, location)
    return " ".join(part for part in parts if part).strip()


def expand_query_plans(
    category: str,
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
) -> list[tuple[str, str]]:
    """Build search queries with the country code used for localized web search."""
    if location.strip():
        country_code = countries[0] if countries else "DE"
        return [(build_query(category, location), country_code)]
    plans: list[tuple[str, str]] = []
    for country_code in countries:
        plans.append((build_query(category, country_label(country_code)), country_code))
    for city_name, country_code in top_cities_for_web_search(countries):
        plans.append((build_query(category, city_name), country_code))
    return plans


def expand_queries(
    category: str,
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
) -> list[str]:
    """Build multiple search queries so engines that cap a single query (e.g.
    ~100 Google results) still yield many websites. Without a location, the query
    is expanded across major cities."""
    return [query for query, _ in expand_query_plans(category, location, countries)]


def is_valid_lead_url(url: str) -> bool:
    if not url:
        return False
    parsed = urllib.parse.urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def provider_from_name(name: str, seed_file: Path | None = None, source_profile: str = "web") -> SearchProvider:
    normalized = name.lower()
    if normalized == "file":
        if seed_file is None:
            raise SearchProviderError("--seed-file is required when --provider=file")
        return FileSearchProvider(seed_file)
    if normalized == "auto":
        provider = auto_provider()
        if isinstance(provider, OpenStreetMapSearchProvider):
            return provider
        return with_source_profile(provider, source_profile)
    if normalized == "all":
        return combined_provider()
    if normalized in {"duckduckgo", "ddg"}:
        return DuckDuckGoSearchProvider()
    if normalized == "google":
        return with_source_profile(GoogleCustomSearchProvider(), source_profile)
    if normalized == "osm":
        return OpenStreetMapSearchProvider()
    if normalized == "brave":
        return with_source_profile(BraveSearchProvider(), source_profile)
    if normalized == "bing":
        return with_source_profile(BingSearchProvider(), source_profile)
    if normalized == "serpapi":
        return with_source_profile(SerpApiSearchProvider(), source_profile)
    if normalized == "zenrows":
        return with_source_profile(ZenRowsSearchProvider(), source_profile)
    raise SearchProviderError(f"Unsupported provider: {name}")


def auto_provider() -> SearchProvider:
    return combined_provider()


def combined_provider(use_osm: bool = True, use_duckduckgo: bool = True) -> SearchProvider:
    """Combine the selected no-key and key-based sources for maximum coverage."""
    providers: list[SearchProvider] = []
    if use_osm:
        providers.append(OpenStreetMapSearchProvider())
    if use_duckduckgo:
        providers.append(DuckDuckGoSearchProvider())
    if os.getenv("GOOGLE_SEARCH_API_KEY") and os.getenv("GOOGLE_SEARCH_ENGINE_ID"):
        providers.append(GoogleCustomSearchProvider())
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        providers.append(BraveSearchProvider())
    if os.getenv("BING_SEARCH_API_KEY"):
        providers.append(BingSearchProvider())
    if os.getenv("SERPAPI_API_KEY"):
        providers.append(SerpApiSearchProvider())
    if os.getenv("ZENROWS_API_KEY"):
        providers.append(ZenRowsSearchProvider())
    return MultiSourceProvider(providers)


def api_provider() -> SearchProvider:
    if os.getenv("GOOGLE_SEARCH_API_KEY") and os.getenv("GOOGLE_SEARCH_ENGINE_ID"):
        return GoogleCustomSearchProvider()
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        return BraveSearchProvider()
    if os.getenv("BING_SEARCH_API_KEY"):
        return BingSearchProvider()
    if os.getenv("SERPAPI_API_KEY"):
        return SerpApiSearchProvider()
    if os.getenv("ZENROWS_API_KEY"):
        return ZenRowsSearchProvider()
    raise SearchProviderError(
        "No search API key found. Set GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID, "
        "BRAVE_SEARCH_API_KEY, BING_SEARCH_API_KEY, SERPAPI_API_KEY, or ZENROWS_API_KEY before starting the GUI."
    )


def with_source_profile(provider: SearchProvider, source_profile: str) -> SearchProvider:
    normalized = source_profile.lower()
    if normalized == "web":
        return provider
    if normalized == "common":
        return CommonSourcesSearchProvider(provider)
    raise SearchProviderError(f"Unsupported source profile: {source_profile}")


def _transient_http_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _read_json_with_retry(
    request: urllib.request.Request,
    timeout: int = 20,
    retries: int = 4,
    backoff_seconds: float = 2.0,
) -> dict:
    last_error: SearchProviderError | None = None
    for attempt in range(retries):
        try:
            return _read_json(request, timeout=timeout)
        except SearchProviderError as exc:
            last_error = exc
            message = str(exc)
            retryable = "HTTP Error" in message and any(
                f"HTTP Error {code}:" in message for code in (408, 425, 429, 500, 502, 503, 504)
            )
            if retryable and attempt + 1 < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise
    if last_error is not None:
        raise last_error
    raise SearchProviderError("Search provider request failed after retries.")


def _read_text(request: urllib.request.Request, timeout: int = 20) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except OSError as exc:
        raise SearchProviderError(f"Search provider request failed: {exc}") from exc


def _read_json(request: urllib.request.Request, timeout: int = 20) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise SearchProviderError(f"Search provider request failed: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SearchProviderError("Search provider returned invalid JSON.") from exc
