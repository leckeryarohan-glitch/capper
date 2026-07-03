from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .extract import normalized_host
from .http import format_request_error, read_response_text, urlopen
from .locations import SUPPORTED_COUNTRIES, ZENROWS_LOCALE, cities_for_mass_web_search, country_label, top_cities_for_web_search
from .models import SearchResult


GOOGLE_MAPS_ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
GOOGLE_MAPS_DEFAULT_SCROLL_STEPS = 2
GOOGLE_MAPS_MAX_SCROLL_STEPS = 5
GOOGLE_MAPS_DEFAULT_TIMEOUT_SECONDS = 240
GOOGLE_MAPS_DEFAULT_PARALLEL = 12
GOOGLE_MAPS_MAX_PARALLEL = 40
GOOGLE_MAPS_DEFAULT_PLACES_PER_CITY = 25
GOOGLE_MAPS_MAX_PLACES_PER_CITY = 60
GOOGLE_MAPS_DETAIL_PARALLEL = 4
GOOGLE_MAPS_INITIAL_WAIT_MS = 5000
GOOGLE_MAPS_FALLBACK_WAIT_MS = 10000
GOOGLE_MAPS_SIDEBAR_SCROLL_WAIT_MS = 2000
GOOGLE_MAPS_SIDEBAR_SCROLL_JS = (
    "var el=document.querySelectorAll('.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde.ecceSd')[1]; "
    "if(el){ el.scrollTop += el.scrollHeight; }"
)
GOOGLE_MAPS_LISTING_CSS_EXTRACTOR = {"place_urls": "a.hfpxzc @href"}
GOOGLE_MAPS_DETAIL_CSS_EXTRACTOR = {
    "name": "h1.DUwDvf.lfPIob",
    "website": ".RcCsl:nth-child(4) a.CsEnBe @href",
}
GOOGLE_MAPS_HOST_MARKERS = (
    "google.com",
    "google.de",
    "google.at",
    "gstatic.com",
    "googleusercontent.com",
    "ggpht.com",
    "schema.org",
    "googleapis.com",
    "g.page",
)

_PLACE_PATH_RE = re.compile(r"/maps/place/([^/?#\"]+)")
_ARIA_WEBSITE_RE = re.compile(r'aria-label="(?:Website|Webseite):\s*([^"]+)"', re.IGNORECASE)
_WEBSITE_HREF_RE = re.compile(r'href="(https?://[^"]+)"', re.IGNORECASE)
_JSON_WEBSITE_RE = re.compile(
    r'"(?:website|url)":"(https?://(?![^"]*(?:google|gstatic|ggpht))[^"]+)"',
    re.IGNORECASE,
)


class GoogleMapsFetchError(RuntimeError):
    pass


def is_valid_maps_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_maps_result_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("//"):
        cleaned = "https:" + cleaned
    if not re.match(r"^https?://", cleaned, re.IGNORECASE):
        cleaned = "https://" + cleaned.lstrip("/")
    return cleaned.rstrip("/")


def build_google_maps_search_url(category: str, location: str, *, country_code: str = "DE") -> str:
    query = " ".join(part for part in (category.strip(), location.strip()) if part)
    encoded = urllib.parse.quote_plus(query or "unternehmen")
    _locale_country, tld = ZENROWS_LOCALE.get(country_code, ZENROWS_LOCALE["DE"])
    host = f"www.google{tld}"
    return f"https://{host}/maps/search/{encoded}"


def google_maps_scroll_steps() -> int:
    import os

    raw = os.getenv("GOOGLE_MAPS_SCROLL_STEPS", "").strip()
    if not raw:
        return GOOGLE_MAPS_DEFAULT_SCROLL_STEPS
    try:
        return max(0, min(int(raw), GOOGLE_MAPS_MAX_SCROLL_STEPS))
    except ValueError:
        return GOOGLE_MAPS_DEFAULT_SCROLL_STEPS


def google_maps_places_per_city(limit: int, num_plans: int) -> int:
    import os

    raw = os.getenv("GOOGLE_MAPS_PLACES_PER_CITY", "").strip()
    if raw:
        try:
            return max(1, min(int(raw), GOOGLE_MAPS_MAX_PLACES_PER_CITY))
        except ValueError:
            pass
    if num_plans <= 0:
        return GOOGLE_MAPS_DEFAULT_PLACES_PER_CITY
    return max(10, min(GOOGLE_MAPS_MAX_PLACES_PER_CITY, limit // num_plans + 5))


def google_maps_cities_budget(limit: int) -> int | None:
    """How many cities per country to query. None means all cached OSM cities."""
    if limit >= 3000:
        return None
    if limit >= 1000:
        return 800
    if limit >= 500:
        return 200
    if limit >= 100:
        return 40
    return 40


def google_maps_max_cities_override() -> int | None:
    import os

    raw = os.getenv("GOOGLE_MAPS_MAX_CITIES", "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def google_maps_parallel_workers() -> int:
    import os

    raw = os.getenv("GOOGLE_MAPS_PARALLEL", "").strip()
    if raw:
        try:
            return max(1, min(int(raw), GOOGLE_MAPS_MAX_PARALLEL))
        except ValueError:
            pass
    return GOOGLE_MAPS_DEFAULT_PARALLEL


def google_maps_referer_for_country(country_code: str) -> str:
    _locale_country, tld = ZENROWS_LOCALE.get(country_code, ZENROWS_LOCALE["DE"])
    return f"https://www.google{tld}/maps/"


def zenrows_error_detail(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = read_response_text(exc, max_bytes=4000).strip()
        except Exception:
            body = ""
        if body:
            return f"{format_request_error(exc)} | {body[:500]}"
    return format_request_error(exc)


def sidebar_scroll_instructions(scrolls: int, *, wait_ms: int = GOOGLE_MAPS_SIDEBAR_SCROLL_WAIT_MS) -> list[dict[str, object]]:
    if scrolls <= 0:
        return []
    instructions: list[dict[str, object]] = []
    for _ in range(scrolls):
        instructions.append({"evaluate": GOOGLE_MAPS_SIDEBAR_SCROLL_JS})
        instructions.append({"wait": wait_ms})
    return instructions


def build_zenrows_google_maps_request_url(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str = "de",
    css_extractor: dict[str, str] | None = None,
    js_instructions: list[dict[str, object]] | None = None,
    wait_ms: int = GOOGLE_MAPS_INITIAL_WAIT_MS,
    wait_for: str = "",
) -> str:
    params: dict[str, str] = {
        "apikey": api_key,
        "js_render": "true",
        "premium_proxy": "true",
        "proxy_country": proxy_country,
        "custom_headers": "true",
    }
    if wait_ms > 0:
        params["wait"] = str(wait_ms)
    if wait_for:
        params["wait_for"] = wait_for
    if css_extractor:
        params["css_extractor"] = json.dumps(css_extractor, separators=(",", ":"))
    if js_instructions:
        params["js_instructions"] = json.dumps(js_instructions, separators=(",", ":"))
    encoded_target = urllib.parse.quote(target_url, safe="")
    query = urllib.parse.urlencode(params)
    return f"{GOOGLE_MAPS_ZENROWS_ENDPOINT}?{query}&url={encoded_target}"


def parse_zenrows_css_payload(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def css_extractor_values(payload: dict, *keys: str) -> list[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
        elif isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def is_external_maps_website(url: str) -> bool:
    normalized = normalize_maps_result_url(url)
    if not normalized or not is_valid_maps_url(normalized):
        return False
    host = normalized_host(normalized).lower()
    if not host:
        return False
    return not any(marker in host for marker in GOOGLE_MAPS_HOST_MARKERS)


def decode_maps_place_name(raw_name: str) -> str:
    cleaned = urllib.parse.unquote(raw_name.replace("+", " "))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_maps_website(candidate: str) -> str:
    value = candidate.strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = "https://" + value.lstrip("/")
    normalized = normalize_maps_result_url(value)
    if is_external_maps_website(normalized):
        return normalized
    return ""


def place_name_from_url(place_url: str) -> str:
    match = _PLACE_PATH_RE.search(place_url)
    if not match:
        return ""
    return decode_maps_place_name(match.group(1).split("/")[0])


def _website_from_chunk(chunk: str) -> str:
    for pattern in (_ARIA_WEBSITE_RE, _JSON_WEBSITE_RE):
        match = pattern.search(chunk)
        if match:
            website = normalize_maps_website(match.group(1))
            if website:
                return website
    for match in _WEBSITE_HREF_RE.finditer(chunk):
        website = normalize_maps_website(match.group(1))
        if website:
            return website
    return ""


def parse_google_maps_listing_html(page_html: str) -> list[SearchResult]:
    """Legacy HTML parser kept for tests and as a last-resort fallback."""
    results: list[SearchResult] = []
    seen_websites: set[str] = set()
    seen_places: set[str] = set()

    segments = re.split(r"(?=/maps/place/)", page_html)
    for segment in segments:
        match = _PLACE_PATH_RE.search(segment)
        if not match:
            continue
        place_slug = match.group(1)
        if not place_slug or place_slug in seen_places:
            continue
        seen_places.add(place_slug)
        name = decode_maps_place_name(place_slug.split("/")[0])
        if not name:
            continue
        chunk = segment[:1200]
        place_url = f"https://www.google.com/maps/place/{place_slug}"
        website = _website_from_chunk(chunk)
        if not website:
            continue
        key = website.lower().rstrip("/")
        if key in seen_websites:
            continue
        seen_websites.add(key)
        results.append(
            SearchResult(
                title=name,
                url=website,
                snippet="Google Maps",
                directory_source_url=place_url,
            )
        )

    if results:
        return results

    for match in _WEBSITE_HREF_RE.finditer(page_html):
        website = normalize_maps_website(match.group(1))
        if not website:
            continue
        key = website.lower().rstrip("/")
        if key in seen_websites:
            continue
        seen_websites.add(key)
        results.append(
            SearchResult(
                title=normalized_host(website),
                url=website,
                snippet="Google Maps",
            )
        )
    return results


def search_result_from_detail_payload(place_url: str, payload: dict) -> SearchResult | None:
    websites = css_extractor_values(payload, "website", "website_url", "website_alt")
    website = ""
    for candidate in websites:
        website = normalize_maps_website(candidate)
        if website:
            break
    if not website:
        return None
    names = css_extractor_values(payload, "name")
    title = names[0] if names else place_name_from_url(place_url)
    return SearchResult(
        title=title or normalized_host(website),
        url=website,
        snippet="Google Maps",
        directory_source_url=place_url,
    )


def _fetch_zenrows_text_once(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str,
    country_code: str,
    css_extractor: dict[str, str] | None = None,
    js_instructions: list[dict[str, object]] | None = None,
    wait_ms: int = GOOGLE_MAPS_INITIAL_WAIT_MS,
    wait_for: str = "",
    timeout: int = GOOGLE_MAPS_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    request_url = build_zenrows_google_maps_request_url(
        api_key,
        target_url,
        proxy_country=proxy_country,
        css_extractor=css_extractor,
        js_instructions=js_instructions,
        wait_ms=wait_ms,
        wait_for=wait_for,
    )
    request = urllib.request.Request(
        request_url,
        headers={
            "Accept": "application/json,text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (compatible; capper-lead-research/0.1)",
            "Connection": "close",
            "Referer": google_maps_referer_for_country(country_code),
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return read_response_text(response)
    except OSError as exc:
        message = zenrows_error_detail(exc)
        if "HTTP Error 401" in message or "HTTP Error 403" in message:
            raise GoogleMapsFetchError(
                "ZenRows API-Key ungueltig oder ohne Berechtigung fuer Google Maps. "
                "Bitte den Key im ZenRows-Dashboard pruefen."
            ) from exc
        raise GoogleMapsFetchError(f"Google Maps ZenRows request failed for {target_url}: {message}") from exc


def fetch_zenrows_css_payload(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
    css_extractor: dict[str, str],
    scroll_steps: int = 0,
    wait_ms: int = GOOGLE_MAPS_INITIAL_WAIT_MS,
    wait_for: str = "",
    timeout: int = GOOGLE_MAPS_DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    instructions = sidebar_scroll_instructions(scroll_steps)
    try:
        raw_text = _fetch_zenrows_text_once(
            api_key,
            target_url,
            proxy_country=proxy_country,
            country_code=country_code,
            css_extractor=css_extractor,
            js_instructions=instructions or None,
            wait_ms=wait_ms,
            wait_for=wait_for,
            timeout=timeout,
        )
    except GoogleMapsFetchError as exc:
        if "HTTP Error 422" not in str(exc) or not instructions:
            raise
        raw_text = _fetch_zenrows_text_once(
            api_key,
            target_url,
            proxy_country=proxy_country,
            country_code=country_code,
            css_extractor=css_extractor,
            js_instructions=None,
            wait_ms=GOOGLE_MAPS_FALLBACK_WAIT_MS,
            wait_for=wait_for,
            timeout=timeout,
        )
    payload = parse_zenrows_css_payload(raw_text)
    if payload:
        return payload
    if css_extractor and raw_text.strip().startswith("<"):
        return {"_html": raw_text}
    return {}


def fetch_google_maps_place_urls(
    api_key: str,
    search_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
    scroll_steps: int | None = None,
) -> list[str]:
    steps = google_maps_scroll_steps() if scroll_steps is None else max(0, min(scroll_steps, GOOGLE_MAPS_MAX_SCROLL_STEPS))
    payload = fetch_zenrows_css_payload(
        api_key,
        search_url,
        proxy_country=proxy_country,
        country_code=country_code,
        css_extractor=GOOGLE_MAPS_LISTING_CSS_EXTRACTOR,
        scroll_steps=steps,
    )
    urls = css_extractor_values(payload, "place_urls", "url")
    seen: set[str] = set()
    unique: list[str] = []
    for place_url in urls:
        normalized = place_url.strip()
        if "/maps/place/" not in normalized:
            continue
        key = normalized.lower().split("?", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def fetch_google_maps_place_result(
    api_key: str,
    place_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
) -> SearchResult | None:
    payload = fetch_zenrows_css_payload(
        api_key,
        place_url,
        proxy_country=proxy_country,
        country_code=country_code,
        css_extractor=GOOGLE_MAPS_DETAIL_CSS_EXTRACTOR,
        scroll_steps=0,
        wait_ms=3000,
        wait_for="h1.DUwDvf",
    )
    result = search_result_from_detail_payload(place_url, payload)
    if result is not None:
        return result
    html = payload.get("_html")
    if isinstance(html, str):
        for parsed in parse_google_maps_listing_html(html):
            if parsed.url:
                return SearchResult(
                    title=parsed.title or place_name_from_url(place_url),
                    url=parsed.url,
                    snippet="Google Maps",
                    directory_source_url=place_url,
                )
    return None


def discover_google_maps_results(
    api_key: str,
    search_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
    scroll_steps: int | None = None,
    places_limit: int = GOOGLE_MAPS_DEFAULT_PLACES_PER_CITY,
) -> list[SearchResult]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    place_urls = fetch_google_maps_place_urls(
        api_key,
        search_url,
        proxy_country=proxy_country,
        country_code=country_code,
        scroll_steps=scroll_steps,
    )[: max(places_limit, 0)]
    if not place_urls:
        return []

    results: list[SearchResult] = []
    workers = min(GOOGLE_MAPS_DETAIL_PARALLEL, len(place_urls))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="capper-gmaps-detail") as executor:
        futures = {
            executor.submit(
                fetch_google_maps_place_result,
                api_key,
                place_url,
                proxy_country=proxy_country,
                country_code=country_code,
            ): place_url
            for place_url in place_urls
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None and result.url.strip():
                results.append(result)
    return results


def google_maps_location_plans(
    category: str,
    location: str,
    countries: tuple[str, ...],
    *,
    limit: int,
) -> list[tuple[str, str]]:
    if location.strip():
        country_code = countries[0] if countries else "DE"
        return [(location.strip(), country_code)]
    plans: list[tuple[str, str]] = []
    for country_code in countries:
        if country_code not in SUPPORTED_COUNTRIES:
            continue
        plans.append((country_label(country_code), country_code))
    city_budget = google_maps_max_cities_override()
    if city_budget is None:
        city_budget = google_maps_cities_budget(limit)
    if city_budget is None:
        city_pairs = cities_for_mass_web_search(countries)
    else:
        city_pairs = top_cities_for_web_search(countries, per_country=city_budget)
    for city_name, country_code in city_pairs:
        plans.append((city_name, country_code))
    return plans
