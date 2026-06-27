from __future__ import annotations

import html
import itertools
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Callable

from .http import format_request_error, read_response_text, urlopen
from .concurrency import recommended_workers
from .locations import (
    DEFAULT_COUNTRIES,
    SUPPORTED_COUNTRIES,
    ZENROWS_LOCALE,
    cities_for_mass_web_search,
    country_label,
    top_cities_for_web_search,
)
from .models import SearchResult


COMMON_SOURCE_DOMAINS = (
    "gelbeseiten.de",
    "dasoertliche.de",
    "11880.com",
    "auskunft.de",
    "telefonbuch.de",
    "dastelefonbuch.de",
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

# Fewer city queries for small paid SERP runs to reduce rate limits and cost.
ZENROWS_CITIES_PER_COUNTRY = 12
# so multi-city runs are not throttled to a tiny share each.
OSM_MIN_PER_LOCATION = 100

# Synonyms broaden discovery for categories where one keyword misses many businesses.
CATEGORY_SEARCH_VARIANTS: dict[str, tuple[str, ...]] = {
    "logistik": ("logistik", "spedition", "transport", "lager", "fracht", "kurier"),
    "spedition": ("spedition", "logistik", "transport", "fracht"),
    "transport": ("transport", "spedition", "logistik", "fracht"),
    "lager": ("lager", "logistik", "spedition", "warehouse"),
    "hotel": ("hotel", "gasthof", "pension"),
    "restaurant": ("restaurant", "gaststätte", "gasthaus"),
    "handwerk": ("handwerk", "handwerker", "meisterbetrieb"),
    "it": ("it", "software", "edv"),
    "software": ("software", "it", "edv"),
    "elektriker": ("elektriker", "elektro", "elektroinstallation"),
    "immobilien": ("immobilien", "immobilienmakler", "makler"),
    "makler": ("makler", "immobilienmakler", "immobilien"),
    "bau": ("bau", "bauunternehmen", "baufirma"),
    "kfz": ("kfz", "autowerkstatt", "werkstatt"),
    "werkstatt": ("werkstatt", "kfz", "autowerkstatt"),
    "friseur": ("friseur", "friseursalon", "haarsalon"),
    "fitness": ("fitness", "fitnessstudio", "fitnesscenter"),
    "supermarkt": ("supermarkt", "lebensmittel", "markt"),
}

ZENROWS_MASS_MODE_LIMIT = 500
ZENROWS_MAX_PARALLEL_REQUESTS = 6
ZENROWS_DEEP_PAGINATION_START = 90
ZENROWS_MEDIUM_PAGINATION_START = 40
ZENROWS_LIGHT_PAGINATION_START = 10


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
    "DirectorySearchProvider": "Branchenverzeichnisse",
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


@dataclass
class ZenRowsResumeState:
    results: list[SearchResult]
    seen_urls: set[str]
    completed_plans: set[str]


class ZenRowsSearchProvider(SearchProvider):
    """Google SERP via ZenRows Universal API with Adaptive Stealth (mode=auto) + autoparse."""

    universal_endpoint = "https://api.zenrows.com/v1/"
    stealth_mode = "auto"
    request_timeout_seconds = 180
    request_delay_seconds = 0.4
    page_delay_seconds = 1.5

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
        *,
        resume_state: ZenRowsResumeState | None = None,
        on_plan_complete: Callable[[ZenRowsResumeState], None] | None = None,
        parallel_workers: int = 1,
    ) -> list[SearchResult]:
        if limit < 1:
            return []
        results: list[SearchResult] = list(resume_state.results) if resume_state else []
        seen: set[str] = set(resume_state.seen_urls) if resume_state else set()
        completed_plans: set[str] = set(resume_state.completed_plans) if resume_state else set()
        mass_mode = limit >= ZENROWS_MASS_MODE_LIMIT
        plans = zenrows_query_plans(category, location, countries, limit)
        max_start = zenrows_max_pagination_start(len(plans), mass_mode)
        page_delay = self.page_delay_seconds if limit > 10 else self.request_delay_seconds
        workers = zenrows_parallel_workers(parallel_workers, mass_mode, len(plans))
        remaining_plans = sum(
            1 for query_text, country_code in plans if zenrows_plan_key(query_text, country_code) not in completed_plans
        )

        if resume_state and results:
            self._report(
                f"ZenRows: Fortsetzung — {len(results)} Websites, "
                f"{len(completed_plans)} erledigte Anfragen, {remaining_plans} offen."
            )
        elif limit > 10:
            mode_hint = "Massenmodus — " if mass_mode else ""
            parallel_hint = f", {workers} parallele Anfragen" if workers > 1 else ""
            self._report(
                f"ZenRows: bis zu {limit} Websites — {mode_hint}"
                f"{len(plans)} Suchanfragen{parallel_hint}, "
                f"bis zu {max_start // 10 + 1} Seiten pro Anfrage."
            )

        if workers > 1:
            return self._search_plans_parallel(
                plans=plans,
                limit=limit,
                max_start=max_start,
                page_delay=page_delay,
                results=results,
                seen=seen,
                completed_plans=completed_plans,
                on_plan_complete=on_plan_complete,
                parallel_workers=workers,
            )

        request_failures = 0
        progress_interval = 25 if mass_mode else 0
        for plan_index, (query_text, country_code) in enumerate(plans):
            if len(results) >= limit:
                break
            plan_key = zenrows_plan_key(query_text, country_code)
            if plan_key in completed_plans:
                continue
            if progress_interval and plan_index > 0 and plan_index % progress_interval == 0:
                self._report(f"ZenRows: {len(results)}/{limit} Websites — Anfrage {plan_index}/{len(plans)} ...")

            plan_results, auth_error = self._execute_zenrows_plan(
                query_text,
                country_code,
                max_start=max_start,
                page_delay=page_delay,
            )
            if auth_error:
                self._report(
                    "ZenRows: Suche abgebrochen. Bitte den API-Key im ZenRows-Dashboard pruefen "
                    "(https://app.zenrows.com) und im Feld 'ZenRows Key' neu eintragen."
                )
                return results
            if not plan_results and mass_mode:
                request_failures += 1

            limit_reached = self._merge_plan_results(plan_results, results, seen, limit)
            completed_plans.add(plan_key)
            if on_plan_complete:
                on_plan_complete(
                    ZenRowsResumeState(
                        results=list(results),
                        seen_urls=set(seen),
                        completed_plans=set(completed_plans),
                    )
                )
            if limit_reached:
                self._report(f"ZenRows: {len(results)} Websites gefunden")
                return results

        self._report(f"ZenRows: {len(results)} Websites gefunden")
        if not results and request_failures:
            self._report(
                "ZenRows: keine Ergebnisse nach API-Fehlern. "
                "Pruefe API-Key/Guthaben oder aktiviere zusaetzlich OpenStreetMap."
            )
        return results

    def _merge_plan_results(
        self,
        plan_results: list[SearchResult],
        results: list[SearchResult],
        seen: set[str],
        limit: int,
    ) -> bool:
        for result in plan_results:
            key = result.url.lower().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            results.append(result)
            if len(results) >= limit:
                return True
        return False

    def _execute_zenrows_plan(
        self,
        query_text: str,
        country_code: str,
        *,
        max_start: int,
        page_delay: float,
    ) -> tuple[list[SearchResult], bool]:
        locale_country, tld = ZENROWS_LOCALE.get(country_code, ZENROWS_LOCALE["DE"])
        plan_results: list[SearchResult] = []
        start = 0
        while start <= max_start:
            request = urllib.request.Request(
                build_zenrows_api_request_url(self.api_key, query_text, start, locale_country, tld),
                headers={"Accept": "application/json", "User-Agent": "capper-lead-research/0.1"},
            )
            try:
                page_results = zenrows_items_to_results(
                    _read_json_with_retry(
                        request,
                        timeout=self.request_timeout_seconds,
                        retries=3,
                        backoff_seconds=3.0,
                    )
                )
            except SearchProviderError as exc:
                self._report(f"ZenRows: Anfrage fehlgeschlagen fuer '{query_text}' (Start {start}): {exc}")
                auth_error = (
                    "API-Key ungueltig" in str(exc)
                    or "HTTP Error 401" in str(exc)
                    or "HTTP Error 403" in str(exc)
                )
                return plan_results, auth_error
            if not page_results:
                break
            plan_results.extend(page_results)
            start += 10
            if start <= max_start:
                time.sleep(page_delay)
        return plan_results, False

    def _search_plans_parallel(
        self,
        *,
        plans: list[tuple[str, str]],
        limit: int,
        max_start: int,
        page_delay: float,
        results: list[SearchResult],
        seen: set[str],
        completed_plans: set[str],
        on_plan_complete: Callable[[ZenRowsResumeState], None] | None,
        parallel_workers: int,
    ) -> list[SearchResult]:
        pending = [
            (zenrows_plan_key(query_text, country_code), query_text, country_code)
            for query_text, country_code in plans
            if zenrows_plan_key(query_text, country_code) not in completed_plans
        ]
        state_lock = threading.Lock()
        stop = threading.Event()
        completed_count = 0

        def run_plan(plan_key: str, query_text: str, country_code: str) -> tuple[str, list[SearchResult], bool]:
            if stop.is_set():
                return plan_key, [], False
            self._report(f"ZenRows (Stealth): '{query_text}' ({country_code}) ...")
            return plan_key, *self._execute_zenrows_plan(
                query_text,
                country_code,
                max_start=max_start,
                page_delay=page_delay,
            )

        with ThreadPoolExecutor(max_workers=parallel_workers, thread_name_prefix="capper-zenrows") as executor:
            futures = [
                executor.submit(run_plan, plan_key, query_text, country_code)
                for plan_key, query_text, country_code in pending
            ]
            for future in as_completed(futures):
                if stop.is_set():
                    break
                plan_key, plan_results, auth_error = future.result()
                if auth_error:
                    stop.set()
                    self._report(
                        "ZenRows: Suche abgebrochen. Bitte den API-Key im ZenRows-Dashboard pruefen "
                        "(https://app.zenrows.com) und im Feld 'ZenRows Key' neu eintragen."
                    )
                    break
                with state_lock:
                    limit_reached = self._merge_plan_results(plan_results, results, seen, limit)
                    completed_plans.add(plan_key)
                    completed_count += 1
                    if on_plan_complete:
                        on_plan_complete(
                            ZenRowsResumeState(
                                results=list(results),
                                seen_urls=set(seen),
                                completed_plans=set(completed_plans),
                            )
                        )
                    if completed_count % 25 == 0:
                        self._report(
                            f"ZenRows: {len(results)}/{limit} Websites — "
                            f"{completed_count}/{len(pending)} Anfragen erledigt ..."
                        )
                if limit_reached:
                    stop.set()
                    break

        self._report(f"ZenRows: {len(results)} Websites gefunden")
        return results


def zenrows_plan_key(query_text: str, country_code: str) -> str:
    return f"{country_code}\t{query_text}"


def zenrows_parallel_workers(requested: int, mass_mode: bool, plan_count: int) -> int:
    if not mass_mode or plan_count <= 30 or requested <= 1:
        return 1
    return max(1, min(ZENROWS_MAX_PARALLEL_REQUESTS, requested))


def find_zenrows_provider(provider: SearchProvider) -> ZenRowsSearchProvider | None:
    if isinstance(provider, ZenRowsSearchProvider):
        return provider
    if isinstance(provider, MultiSourceProvider):
        for sub_provider in provider.providers:
            found = find_zenrows_provider(sub_provider)
            if found is not None:
                return found
    if isinstance(provider, CommonSourcesSearchProvider):
        return find_zenrows_provider(provider.provider)
    return None


def is_zenrows_only_provider(provider: SearchProvider) -> bool:
    if isinstance(provider, ZenRowsSearchProvider):
        return True
    if isinstance(provider, MultiSourceProvider):
        return len(provider.providers) == 1 and isinstance(provider.providers[0], ZenRowsSearchProvider)
    return False


def build_google_search_url(query_text: str, start: int, locale_country: str, tld: str = "") -> str:
    """Build a Google search URL for ZenRows Universal API (same pattern as ZenRows docs)."""
    del tld  # locale is controlled via hl/gl and proxy_country, not the host TLD
    return "https://www.google.com/search?" + urllib.parse.urlencode(
        {
            "q": query_text,
            "num": 10,
            "start": start,
            "hl": locale_country,
            "gl": locale_country,
        }
    )


def build_zenrows_api_request_url(
    api_key: str,
    query_text: str,
    start: int,
    locale_country: str,
    tld: str = "",
) -> str:
    """Build a ZenRows Universal API URL with a fully URL-encoded Google target."""
    google_url = build_google_search_url(query_text, start, locale_country, tld)
    encoded_target = urllib.parse.quote(google_url, safe="")
    params = urllib.parse.urlencode(
        {
            "apikey": api_key,
            "mode": ZenRowsSearchProvider.stealth_mode,
            "autoparse": "true",
            "proxy_country": locale_country,
        }
    )
    return f"{ZenRowsSearchProvider.universal_endpoint}?{params}&url={encoded_target}"


def category_search_variants(category: str) -> tuple[str, ...]:
    normalized = category.strip().casefold()
    for keyword, variants in CATEGORY_SEARCH_VARIANTS.items():
        if keyword in normalized:
            return variants
    return (category.strip(),) if category.strip() else ()


def zenrows_cities_budget(limit: int) -> int | None:
    """How many cities per country to query. None means all cached cities (1600+ for DE)."""
    if limit >= 3000:
        return None
    if limit >= 1000:
        return 800
    if limit >= 500:
        return 200
    if limit >= 100:
        return 40
    return ZENROWS_CITIES_PER_COUNTRY


def zenrows_max_pagination_start(num_plans: int, mass_mode: bool) -> int:
    """Balance depth per query vs. number of locations in the run."""
    if not mass_mode:
        return ZENROWS_DEEP_PAGINATION_START
    if num_plans <= 30:
        return ZENROWS_DEEP_PAGINATION_START
    if num_plans <= 150:
        return ZENROWS_MEDIUM_PAGINATION_START
    return ZENROWS_LIGHT_PAGINATION_START


def zenrows_country_plans(
    category: str,
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    *,
    broad: bool = False,
) -> list[tuple[str, str]]:
    if location.strip():
        country_code = countries[0] if countries else "DE"
        return [
            (build_query(variant, location, broad=broad), country_code)
            for variant in category_search_variants(category)
        ]
    plans: list[tuple[str, str]] = []
    for code in countries:
        if code not in SUPPORTED_COUNTRIES:
            continue
        label = country_label(code)
        for variant in category_search_variants(category):
            plans.append((build_query(variant, label, broad=broad), code))
    return plans


def zenrows_city_plans(
    category: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    *,
    limit: int = 50,
    broad: bool = False,
) -> list[tuple[str, str]]:
    city_budget = zenrows_cities_budget(limit)
    if city_budget is None:
        cities = cities_for_mass_web_search(countries)
    else:
        cities = top_cities_for_web_search(countries, per_country=city_budget)

    variants = category_search_variants(category)
    # With hundreds of cities, use the primary term first to limit API volume.
    city_variants = variants if len(cities) <= 60 else (variants[0],)
    plans: list[tuple[str, str]] = []
    for city_name, country_code in cities:
        for variant in city_variants:
            plans.append((build_query(variant, city_name, broad=broad), country_code))
    return plans


def zenrows_synonym_city_plans(
    category: str,
    countries: tuple[str, ...],
    *,
    limit: int,
    broad: bool = False,
) -> list[tuple[str, str]]:
    """Extra city queries with synonym terms when the primary sweep is not enough."""
    variants = category_search_variants(category)
    if len(variants) <= 1:
        return []

    city_budget = zenrows_cities_budget(limit)
    if city_budget is None:
        cities = cities_for_mass_web_search(countries)[:120]
    else:
        cities = top_cities_for_web_search(countries, per_country=min(city_budget, 80))

    plans: list[tuple[str, str]] = []
    for city_name, country_code in cities:
        for variant in variants[1:]:
            plans.append((build_query(variant, city_name, broad=broad), country_code))
    return plans


def zenrows_query_plans(
    category: str,
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    limit: int = 50,
) -> list[tuple[str, str]]:
    """Country plans first, then city sweep; synonym pass for large limits without location."""
    broad = limit >= ZENROWS_MASS_MODE_LIMIT
    if location.strip():
        return zenrows_country_plans(category, location, countries, broad=broad)

    plans = zenrows_country_plans(category, location, countries, broad=broad)
    plans += zenrows_city_plans(category, countries, limit=limit, broad=broad)
    if limit >= 1000:
        plans += zenrows_synonym_city_plans(category, countries, limit=limit, broad=broad)
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


class DirectorySearchProvider(SearchProvider):
    """Discovers business websites by reading public German directory listings via ZenRows."""

    def __init__(
        self,
        zenrows_api_key: str | None = None,
        *,
        allow_direct_fetch: bool = False,
        proxy_country: str = "de",
    ):
        self.zenrows_api_key = _resolve_api_key(zenrows_api_key, "ZENROWS_API_KEY")
        self.allow_direct_fetch = allow_direct_fetch
        self.proxy_country = proxy_country

    def search(
        self,
        category: str,
        location: str,
        limit: int,
        countries: tuple[str, ...] = DEFAULT_COUNTRIES,
    ) -> list[SearchResult]:
        from .directories import (
            DIRECTORY_SCRAPERS,
            DirectoryFetchConfig,
            DirectoryFetchError,
            configure_directory_fetch,
            directory_entries_to_results,
            directory_location_plans,
        )
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from math import ceil

        if limit < 1:
            return []

        if not self.zenrows_api_key and not self.allow_direct_fetch:
            raise SearchProviderError(
                "Branchenverzeichnisse (Gelbe Seiten, Das Oertliche, auskunft.de, 11880, Telefonbuch) "
                "werden ueber die ZenRows Universal API abgefragt. Bitte ZENROWS_API_KEY setzen."
            )

        configure_directory_fetch(
            DirectoryFetchConfig(
                zenrows_api_key=self.zenrows_api_key,
                proxy_country=self.proxy_country,
                allow_direct_fallback=self.allow_direct_fetch,
            )
        )

        locations = directory_location_plans(location, countries)
        per_location_limit = max(1, ceil(limit / len(locations)))
        per_source_limit = max(1, ceil(per_location_limit / len(DIRECTORY_SCRAPERS)))
        results: list[SearchResult] = []
        seen: set[str] = set()
        fetch_mode = "ZenRows" if self.zenrows_api_key else "Direct"
        max_workers = 2 if self.zenrows_api_key else len(DIRECTORY_SCRAPERS)

        for plan_location in locations:
            if len(results) >= limit:
                break
            self._report(f"Branchenverzeichnisse ({fetch_mode}): {category} in {plan_location} ...")

            def run_scraper(scraper_item: tuple[str, object]) -> tuple[str, list]:
                label, scraper = scraper_item
                try:
                    return label, scraper(category, plan_location, per_source_limit)
                except DirectoryFetchError as exc:
                    self._report(f"{label}: {exc}")
                    return label, []
                except Exception:  # noqa: BLE001 - one directory must not abort the others
                    return label, []

            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="capper-directories") as executor:
                futures = [executor.submit(run_scraper, item) for item in DIRECTORY_SCRAPERS]
                for future in as_completed(futures):
                    label, entries = future.result()
                    added = directory_entries_to_results(entries, limit=limit - len(results), seen=seen)
                    if added:
                        self._report(f"{label}: {len(added)} Websites aus {plan_location}")
                    results.extend(added)
                    if len(results) >= limit:
                        break

        self._report(f"Branchenverzeichnisse: {len(results)} Websites gefunden")
        return results[:limit]


def build_query(category: str, location: str, *, broad: bool = False) -> str:
    if broad:
        parts = [category]
        if location:
            parts.append(location)
        return " ".join(part for part in parts if part).strip()
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
    if normalized in {"directories", "directory", "verzeichnis", "branchenbuch"}:
        return DirectorySearchProvider()
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


def combined_provider(
    use_osm: bool = True,
    use_duckduckgo: bool = True,
    use_directories: bool = True,
    use_zenrows_google: bool = True,
    use_serpapi: bool = True,
    serpapi_key: str | None = None,
    zenrows_key: str | None = None,
) -> SearchProvider:
    """Combine the selected no-key and key-based sources for maximum coverage."""
    providers: list[SearchProvider] = []
    if use_osm:
        providers.append(OpenStreetMapSearchProvider())
    if use_duckduckgo:
        providers.append(DuckDuckGoSearchProvider())
    if use_directories:
        resolved_zenrows_for_directories = _resolve_api_key(zenrows_key, "ZENROWS_API_KEY")
        if resolved_zenrows_for_directories or os.getenv("DIRECTORY_ALLOW_DIRECT_FETCH") == "1":
            providers.append(
                DirectorySearchProvider(
                    zenrows_api_key=resolved_zenrows_for_directories or None,
                    allow_direct_fetch=os.getenv("DIRECTORY_ALLOW_DIRECT_FETCH") == "1",
                )
            )
    if os.getenv("GOOGLE_SEARCH_API_KEY") and os.getenv("GOOGLE_SEARCH_ENGINE_ID"):
        providers.append(GoogleCustomSearchProvider())
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        providers.append(BraveSearchProvider())
    if os.getenv("BING_SEARCH_API_KEY"):
        providers.append(BingSearchProvider())

    resolved_serpapi = _resolve_api_key(serpapi_key, "SERPAPI_API_KEY")
    if use_serpapi and resolved_serpapi:
        providers.append(SerpApiSearchProvider(api_key=resolved_serpapi))

    resolved_zenrows = _resolve_api_key(zenrows_key, "ZENROWS_API_KEY")
    if use_zenrows_google and resolved_zenrows:
        providers.append(ZenRowsSearchProvider(api_key=resolved_zenrows))

    return MultiSourceProvider(providers)


def _resolve_api_key(explicit_key: str | None, env_name: str) -> str:
    if explicit_key is not None:
        return explicit_key.strip()
    return os.getenv(env_name, "").strip()


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


def _is_retryable_search_error(message: str) -> bool:
    lowered = message.casefold()
    if any(token in lowered for token in ("timed out", "timeout", "temporarily unavailable")):
        return True
    if "HTTP Error" not in message:
        return False
    return any(f"HTTP Error {code}:" in message for code in (408, 425, 429, 500, 502, 503, 504))


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
            if _is_retryable_search_error(str(exc)) and attempt + 1 < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise
    if last_error is not None:
        raise last_error
    raise SearchProviderError("Search provider request failed after retries.")


def _read_text(request: urllib.request.Request, timeout: int = 20) -> str:
    try:
        with urlopen(request, timeout=timeout) as response:
            return read_response_text(response)
    except OSError as exc:
        raise SearchProviderError(f"Search provider request failed: {format_request_error(exc)}") from exc


def _read_json(request: urllib.request.Request, timeout: int = 20) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = read_response_text(response)
    except OSError as exc:
        raise SearchProviderError(f"Search provider request failed: {format_request_error(exc)}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SearchProviderError("Search provider returned invalid JSON.") from exc
