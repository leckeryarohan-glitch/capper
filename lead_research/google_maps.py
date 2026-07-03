from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from .extract import normalized_host
from .http import format_request_error, read_response_text, urlopen
from .locations import SUPPORTED_COUNTRIES, country_label, top_cities_for_web_search
from .models import SearchResult


GOOGLE_MAPS_ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
GOOGLE_MAPS_DEFAULT_SCROLL_STEPS = 2
GOOGLE_MAPS_MAX_SCROLL_STEPS = 5
GOOGLE_MAPS_DEFAULT_TIMEOUT_SECONDS = 90
GOOGLE_MAPS_MAX_CITY_PLANS = 12
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
_TEL_HREF_RE = re.compile(r'href="tel:([^"]+)"', re.IGNORECASE)
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


def build_google_maps_search_url(category: str, location: str) -> str:
    query = " ".join(part for part in (category.strip(), location.strip()) if part)
    encoded = urllib.parse.quote_plus(query or "unternehmen")
    return f"https://www.google.com/maps/search/{encoded}"


def google_maps_scroll_steps() -> int:
    import os

    raw = os.getenv("GOOGLE_MAPS_SCROLL_STEPS", "").strip()
    if not raw:
        return GOOGLE_MAPS_DEFAULT_SCROLL_STEPS
    try:
        return max(0, min(int(raw), GOOGLE_MAPS_MAX_SCROLL_STEPS))
    except ValueError:
        return GOOGLE_MAPS_DEFAULT_SCROLL_STEPS


def build_zenrows_google_maps_request_url(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str = "de",
    scroll_steps: int | None = None,
) -> str:
    steps = GOOGLE_MAPS_DEFAULT_SCROLL_STEPS if scroll_steps is None else max(0, min(scroll_steps, GOOGLE_MAPS_MAX_SCROLL_STEPS))
    params: dict[str, str] = {
        "apikey": api_key,
        "js_render": "true",
        "premium_proxy": "true",
        "proxy_country": proxy_country,
    }
    if steps > 0:
        instructions: list[dict[str, int]] = []
        for _ in range(steps):
            instructions.append({"scrollY": 4000})
            instructions.append({"wait": 1500})
        params["js_instructions"] = json.dumps(instructions, separators=(",", ":"))
    encoded_target = urllib.parse.urlencode({"url": target_url})
    query = urllib.parse.urlencode(params)
    return f"{GOOGLE_MAPS_ZENROWS_ENDPOINT}?{query}&{encoded_target}"


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


def _phone_from_chunk(chunk: str) -> str:
    match = _TEL_HREF_RE.search(chunk)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1).strip())


def parse_google_maps_listing_html(page_html: str) -> list[SearchResult]:
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
        website = _website_from_chunk(chunk)
        phone = _phone_from_chunk(chunk)
        place_url = f"https://www.google.com/maps/place/{place_slug}"
        if website:
            key = website.lower().rstrip("/")
            if key in seen_websites:
                continue
            seen_websites.add(key)
            results.append(
                SearchResult(
                    title=name,
                    url=website,
                    snippet="Google Maps",
                    directory_phone=phone,
                    directory_source_url=place_url,
                )
            )
        elif phone:
            results.append(
                SearchResult(
                    title=name,
                    url="",
                    snippet="Google Maps",
                    directory_phone=phone,
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
        chunk = page_html[max(0, match.start() - 500) : match.start() + 500]
        phone = _phone_from_chunk(chunk)
        results.append(
            SearchResult(
                title=normalized_host(website),
                url=website,
                snippet="Google Maps",
                directory_phone=phone,
            )
        )
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
    city_budget = GOOGLE_MAPS_MAX_CITY_PLANS
    if limit >= 500:
        city_budget = min(40, GOOGLE_MAPS_MAX_CITY_PLANS * 2)
    for city_name, country_code in top_cities_for_web_search(countries, per_country=city_budget):
        plans.append((city_name, country_code))
    return plans


def fetch_google_maps_html(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str = "de",
    timeout: int = GOOGLE_MAPS_DEFAULT_TIMEOUT_SECONDS,
    scroll_steps: int | None = None,
) -> str:
    request_url = build_zenrows_google_maps_request_url(
        api_key,
        target_url,
        proxy_country=proxy_country,
        scroll_steps=scroll_steps,
    )
    request = urllib.request.Request(
        request_url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/json",
            "User-Agent": "Mozilla/5.0 (compatible; capper-lead-research/0.1)",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return read_response_text(response)
    except OSError as exc:
        message = format_request_error(exc)
        if "HTTP Error 401" in message or "HTTP Error 403" in message:
            raise GoogleMapsFetchError(
                "ZenRows API-Key ungueltig oder ohne Berechtigung fuer Google Maps. "
                "Bitte den Key im ZenRows-Dashboard pruefen."
            ) from exc
        raise GoogleMapsFetchError(f"Google Maps ZenRows request failed for {target_url}: {message}") from exc
