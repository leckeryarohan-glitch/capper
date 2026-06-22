from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from math import ceil
from pathlib import Path

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
    "hotel": (("tourism", "hotel"), ("tourism", "guest_house"), ("tourism", "hostel")),
    "restaurant": (("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food")),
    "lager": (("building", "warehouse"), ("landuse", "industrial"), ("industrial", "warehouse")),
    "logistik": (("office", "logistics"), ("industrial", "logistics"), ("landuse", "industrial")),
    "elektronik": (("shop", "electronics"),),
    "friseur": (("shop", "hairdresser"),),
    "arzt": (("amenity", "doctors"),),
    "zahnarzt": (("amenity", "dentist"),),
    "auto": (("shop", "car_repair"), ("shop", "car"), ("amenity", "car_rental")),
}


class SearchProviderError(RuntimeError):
    pass


class SearchProvider(ABC):
    @abstractmethod
    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


class FileSearchProvider(SearchProvider):
    """Reads seed URLs from a plain text file, one URL per line."""

    def __init__(self, path: Path):
        self.path = path

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
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

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
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

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
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

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        query = build_query(category, location)
        params = urllib.parse.urlencode({"engine": "google", "q": query, "api_key": self.api_key})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={"Accept": "application/json", "User-Agent": "capper-lead-research/0.1"},
        )
        data = _read_json(request)
        organic_results = data.get("organic_results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
            )
            for item in organic_results
            if item.get("link")
        ][:limit]


class GoogleCustomSearchProvider(SearchProvider):
    endpoint = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str | None = None, search_engine_id: str | None = None):
        self.api_key = api_key or os.getenv("GOOGLE_SEARCH_API_KEY")
        self.search_engine_id = search_engine_id or os.getenv("GOOGLE_SEARCH_ENGINE_ID")
        if not self.api_key:
            raise SearchProviderError("GOOGLE_SEARCH_API_KEY is required for Google search.")
        if not self.search_engine_id:
            raise SearchProviderError("GOOGLE_SEARCH_ENGINE_ID is required for Google search.")

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
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
        if item.get("link")
    ]


class OpenStreetMapSearchProvider(SearchProvider):
    endpoint = "https://overpass-api.de/api/interpreter"

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        if limit < 1:
            return []
        query = build_overpass_query(category, location, limit)
        request = urllib.request.Request(
            self.endpoint,
            data=query.encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "text/plain; charset=utf-8",
                "User-Agent": "capper-lead-research/0.1",
            },
            method="POST",
        )
        data = _read_json(request)
        return osm_elements_to_results(data, limit)


def build_overpass_query(category: str, location: str, limit: int) -> str:
    selectors = osm_selectors_for_category(category)
    scoped_selectors = []
    area_setup = ""
    location_name = location.strip()
    if location_name:
        escaped_location = escape_overpass_string(location_name)
        area_setup = (
            f'area["name"="{escaped_location}"]["boundary"="administrative"]->.searchArea;\n'
        )
        scoped_selectors = [f'nwr{selector}(area.searchArea);' for selector in selectors]
    else:
        scoped_selectors = [f"nwr{selector};" for selector in selectors]

    count = max(limit * 5, limit)
    return (
        "[out:json][timeout:25];\n"
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
            selectors.extend(f'["{key}"="{value}"]' for key, value in tag_pairs)
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


class CommonSourcesSearchProvider(SearchProvider):
    """Searches common business directory and marketplace domains through an API provider."""

    def __init__(self, provider: SearchProvider, domains: tuple[str, ...] = COMMON_SOURCE_DOMAINS):
        self.provider = provider
        self.domains = domains

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
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
    raise SearchProviderError(f"Unsupported provider: {name}")


def auto_provider() -> SearchProvider:
    return OpenStreetMapSearchProvider()


def api_provider() -> SearchProvider:
    if os.getenv("GOOGLE_SEARCH_API_KEY") and os.getenv("GOOGLE_SEARCH_ENGINE_ID"):
        return GoogleCustomSearchProvider()
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        return BraveSearchProvider()
    if os.getenv("BING_SEARCH_API_KEY"):
        return BingSearchProvider()
    if os.getenv("SERPAPI_API_KEY"):
        return SerpApiSearchProvider()
    raise SearchProviderError(
        "No search API key found. Set GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID, "
        "BRAVE_SEARCH_API_KEY, BING_SEARCH_API_KEY, or SERPAPI_API_KEY before starting the GUI."
    )


def with_source_profile(provider: SearchProvider, source_profile: str) -> SearchProvider:
    normalized = source_profile.lower()
    if normalized == "web":
        return provider
    if normalized == "common":
        return CommonSourcesSearchProvider(provider)
    raise SearchProviderError(f"Unsupported source profile: {source_profile}")


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise SearchProviderError(f"Search provider request failed: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SearchProviderError("Search provider returned invalid JSON.") from exc
