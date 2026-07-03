from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .extract import normalized_host
from .http import format_request_error, read_response_text, urlopen
from .locations import SUPPORTED_COUNTRIES, ZENROWS_LOCALE, cities_for_mass_web_search, top_cities_for_web_search
from .models import SearchResult


@dataclass(frozen=True)
class GoogleMapsDiscoveryStats:
    place_urls: int = 0
    details_checked: int = 0
    websites_found: int = 0
    page_hint: str = ""


GOOGLE_MAPS_ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
GOOGLE_MAPS_DEFAULT_SCROLL_STEPS = 2
GOOGLE_MAPS_MAX_SCROLL_STEPS = 5
GOOGLE_MAPS_DEFAULT_TIMEOUT_SECONDS = 240
GOOGLE_MAPS_DEFAULT_PARALLEL = 12
GOOGLE_MAPS_MAX_PARALLEL = 40
GOOGLE_MAPS_DEFAULT_PLACES_PER_CITY = 25
GOOGLE_MAPS_MAX_PLACES_PER_CITY = 60
GOOGLE_MAPS_DETAIL_PARALLEL = 4
GOOGLE_MAPS_INITIAL_WAIT_MS = 10000
GOOGLE_MAPS_FALLBACK_WAIT_MS = 15000
GOOGLE_MAPS_SIDEBAR_SCROLL_WAIT_MS = 2000
GOOGLE_MAPS_CONSENT_JS = (
    "(function(){"
    "var s=['#L2AGLb','button[jsname=\"V67aGc\"]','form[action*=\"consent\"] button',"
    "'button[aria-label*=\"Alle akzeptieren\"]','button[aria-label*=\"Accept all\"]'];"
    "for(var i=0;i<s.length;i++){var e=document.querySelector(s[i]);if(e){e.click();return;}}"
    "})();"
)
GOOGLE_MAPS_SIDEBAR_SCROLL_JS = (
    "var el=document.querySelectorAll('.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde.ecceSd')[1]; "
    "if(el){ el.scrollTop += el.scrollHeight; }"
)
GOOGLE_MAPS_LISTING_COLLECT_JS = (
    "(function(){"
    "var u=[];"
    "document.querySelectorAll('a.hfpxzc').forEach(function(a){if(a.href)u.push(a.href);});"
    "if(u.length){document.documentElement.setAttribute('data-cap-place-urls',u.join('\\n'));}"
    "})();"
)
GOOGLE_MAPS_LISTING_CSS_EXTRACTOR = {
    "place_urls": "a.hfpxzc @href",
    "url": "a.hfpxzc @href",
    "js_place_urls": "html @data-cap-place-urls",
}
GOOGLE_MAPS_DETAIL_CSS_EXTRACTOR = {
    "name": "h1.DUwDvf",
    "website": 'a[data-item-id="authority"] @href',
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
_PLACE_HREF_RE = re.compile(r'href="(https?://[^"]+/maps/place/[^"]+)"', re.IGNORECASE)
_HFPXZC_HREF_RE = re.compile(
    r'<a[^>]*class="[^"]*hfpxzc[^"]*"[^>]*href="([^"]+)"',
    re.IGNORECASE,
)
_AUTHORITY_HREF_RE = re.compile(
    r'data-item-id="authority"[^>]*href="([^"]+)"',
    re.IGNORECASE,
)
_AUTHORITY_HREF_RE_ALT = re.compile(
    r'href="([^"]+)"[^>]*data-item-id="authority"',
    re.IGNORECASE,
)
_LOOSE_PLACE_URL_RE = re.compile(
    r"https?://(?:www\.)?google\.[a-z.]+/maps/place/[^\s\"'<>\\]+",
    re.IGNORECASE,
)
_RELATIVE_PLACE_PATH_RE = re.compile(
    r'"(/maps/place/[^"]+)"',
    re.IGNORECASE,
)
_ZENROWS_JSON_ENVELOPE_KEYS = frozenset(
    {
        "html",
        "xhr",
        "js_instructions_report",
        "metadata",
        "status_code",
        "headers",
        "cookies",
        "screenshot",
        "cost",
    }
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
    hl = "de" if country_code == "AT" else "de"
    return f"https://{host}/maps/search/{encoded}?hl={hl}"


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


def google_maps_bootstrap_instructions() -> list[dict[str, object]]:
    return [
        {"evaluate": GOOGLE_MAPS_CONSENT_JS},
        {"wait": 3000},
    ]


def maps_js_instructions(scroll_steps: int) -> list[dict[str, object]]:
    instructions = google_maps_bootstrap_instructions()
    instructions.extend(sidebar_scroll_instructions(scroll_steps))
    instructions.append({"evaluate": GOOGLE_MAPS_LISTING_COLLECT_JS})
    instructions.append({"wait": 1000})
    return instructions


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
    json_response: bool = True,
) -> str:
    params: dict[str, str] = {
        "apikey": api_key,
        "js_render": "true",
        "premium_proxy": "true",
        "proxy_country": proxy_country,
        "custom_headers": "true",
    }
    if json_response:
        params["json_response"] = "true"
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


def parse_zenrows_full_response(raw_text: str) -> tuple[dict, str, list[str]]:
    """Parse ZenRows css_extractor JSON or json_response envelope."""
    text = raw_text.strip()
    if not text:
        return {}, "", []
    if text.startswith("<"):
        return {}, text, []

    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return {}, text, []

    if not isinstance(envelope, dict):
        return {}, text, []

    if "html" in envelope or "xhr" in envelope:
        html = str(envelope.get("html") or "")
        xhr_bodies: list[str] = []
        for entry in envelope.get("xhr") or []:
            if not isinstance(entry, dict):
                continue
            body = entry.get("body") or entry.get("response") or ""
            if body:
                xhr_bodies.append(str(body))
        css_payload = {
            key: value
            for key, value in envelope.items()
            if key not in _ZENROWS_JSON_ENVELOPE_KEYS
        }
        return css_payload, html, xhr_bodies

    return envelope, "", []


def css_extractor_place_urls(payload: dict) -> list[str]:
    urls = css_extractor_values(payload, "place_urls", "url")
    js_urls = payload.get("js_place_urls")
    if isinstance(js_urls, str) and js_urls.strip():
        urls.extend(line.strip() for line in js_urls.splitlines() if line.strip())
    elif isinstance(js_urls, list):
        urls.extend(str(item).strip() for item in js_urls if str(item).strip())
    return urls


def css_payload_has_extractor_hits(payload: dict, css_extractor: dict[str, str] | None) -> bool:
    if not css_extractor or not payload:
        return False
    if css_extractor_place_urls(payload):
        return True
    for key in css_extractor:
        value = payload.get(key)
        if isinstance(value, list) and any(str(item).strip() for item in value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def diagnose_maps_page(page_html: str) -> str:
    lower = page_html.lower()
    if "consent.google" in lower or "bevor sie zu google weitergehen" in lower:
        return "consent_wall"
    if "unusual traffic" in lower or "captcha" in lower or "/sorry/" in lower:
        return "captcha"
    if "hfpxzc" in lower or "/maps/place/" in lower:
        return "listings_present"
    if "maps/search" in lower or ("suche" in lower and "maps" in lower):
        return "search_loaded_no_listings"
    if not page_html.strip():
        return "empty"
    return "unknown"


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


def extract_place_urls_from_text(text: str) -> list[str]:
    text = text.replace("\\/", "/").replace("\\u002f", "/")
    seen: set[str] = set()
    urls: list[str] = []

    def add(candidate: str) -> None:
        cleaned = candidate.strip().replace("\\/", "/").replace("\\u0026", "&")
        if not cleaned:
            return
        if cleaned.startswith("/maps/place/"):
            cleaned = "https://www.google.com" + cleaned
        if "/maps/place/" not in cleaned:
            return
        key = cleaned.lower().split("?", 1)[0]
        if key in seen:
            return
        seen.add(key)
        urls.append(cleaned)

    for pattern in (_HFPXZC_HREF_RE, _PLACE_HREF_RE, _LOOSE_PLACE_URL_RE):
        for match in pattern.finditer(text):
            add(match.group(1) if pattern is not _LOOSE_PLACE_URL_RE else match.group(0))
    for match in _RELATIVE_PLACE_PATH_RE.finditer(text):
        add(match.group(1))
    return urls


def extract_place_urls_from_html(page_html: str) -> list[str]:
    return extract_place_urls_from_text(page_html)


def parse_website_from_detail_html(page_html: str) -> str:
    for pattern in (_AUTHORITY_HREF_RE, _AUTHORITY_HREF_RE_ALT, _ARIA_WEBSITE_RE, _JSON_WEBSITE_RE):
        match = pattern.search(page_html)
        if not match:
            continue
        website = normalize_maps_website(match.group(1))
        if website:
            return website
    for match in _WEBSITE_HREF_RE.finditer(page_html):
        website = normalize_maps_website(match.group(1))
        if website:
            return website
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
    json_response: bool = True,
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
        json_response=json_response,
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


def _fetch_zenrows_maps_with_fallbacks(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str,
    country_code: str,
    css_extractor: dict[str, str] | None = None,
    scroll_steps: int = 0,
    wait_ms: int = GOOGLE_MAPS_INITIAL_WAIT_MS,
) -> str:
    """Fetch Maps HTML/JSON without wait_for (wait_for causes 422 when selectors are absent)."""
    instructions = maps_js_instructions(scroll_steps)
    profiles: list[tuple[dict[str, str] | None, list[dict[str, object]] | None, int, bool]] = [
        (css_extractor, instructions, wait_ms, True),
        (css_extractor, instructions, GOOGLE_MAPS_FALLBACK_WAIT_MS, True),
        (css_extractor, google_maps_bootstrap_instructions(), GOOGLE_MAPS_FALLBACK_WAIT_MS, True),
        (None, instructions, GOOGLE_MAPS_FALLBACK_WAIT_MS, True),
        (None, google_maps_bootstrap_instructions(), GOOGLE_MAPS_FALLBACK_WAIT_MS, True),
        (css_extractor, instructions, wait_ms, False),
        (None, None, GOOGLE_MAPS_FALLBACK_WAIT_MS, False),
    ]
    last_error: GoogleMapsFetchError | None = None
    seen: set[tuple[bool, bool, int, bool]] = set()
    for css, js, wait, use_json in profiles:
        key = (css is not None, js is not None, wait, use_json)
        if key in seen:
            continue
        seen.add(key)
        try:
            return _fetch_zenrows_text_once(
                api_key,
                target_url,
                proxy_country=proxy_country,
                country_code=country_code,
                css_extractor=css,
                js_instructions=js,
                wait_ms=wait,
                wait_for="",
                json_response=use_json,
            )
        except GoogleMapsFetchError as exc:
            last_error = exc
            if "HTTP Error 422" not in str(exc):
                raise
    if last_error is not None:
        raise last_error
    raise GoogleMapsFetchError(f"Google Maps ZenRows request failed for {target_url}")


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
    del wait_for, timeout  # Maps fetch uses unified fallback helper without wait_for.
    raw_text = _fetch_zenrows_maps_with_fallbacks(
        api_key,
        target_url,
        proxy_country=proxy_country,
        country_code=country_code,
        css_extractor=css_extractor,
        scroll_steps=scroll_steps,
        wait_ms=wait_ms,
    )
    payload, html_text, xhr_bodies = parse_zenrows_full_response(raw_text)
    if css_payload_has_extractor_hits(payload, css_extractor):
        return payload
    if html_text.strip():
        payload = dict(payload)
        payload["_html"] = html_text
        return payload
    if css_extractor and raw_text.strip().startswith("<"):
        return {"_html": raw_text}
    # Keep xhr bodies for callers that parse place URLs from network payloads.
    if xhr_bodies:
        payload = dict(payload)
        payload["_xhr"] = xhr_bodies
    return payload


def fetch_google_maps_search_html(
    api_key: str,
    search_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
    scroll_steps: int | None = None,
) -> str:
    steps = google_maps_scroll_steps() if scroll_steps is None else max(0, min(scroll_steps, GOOGLE_MAPS_MAX_SCROLL_STEPS))
    return _fetch_zenrows_maps_with_fallbacks(
        api_key,
        search_url,
        proxy_country=proxy_country,
        country_code=country_code,
        css_extractor=None,
        scroll_steps=steps,
    )


def _collect_place_urls_from_payload(payload: dict) -> list[str]:
    urls = css_extractor_place_urls(payload)
    html = payload.get("_html")
    if isinstance(html, str) and html.strip():
        urls.extend(extract_place_urls_from_text(html))
    xhr_bodies = payload.get("_xhr")
    if isinstance(xhr_bodies, list):
        for body in xhr_bodies:
            if isinstance(body, str) and body.strip():
                urls.extend(extract_place_urls_from_text(body))
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


def fetch_google_maps_place_urls(
    api_key: str,
    search_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
    scroll_steps: int | None = None,
) -> tuple[list[str], str]:
    steps = google_maps_scroll_steps() if scroll_steps is None else max(0, min(scroll_steps, GOOGLE_MAPS_MAX_SCROLL_STEPS))
    page_hint = ""
    try:
        payload = fetch_zenrows_css_payload(
            api_key,
            search_url,
            proxy_country=proxy_country,
            country_code=country_code,
            css_extractor=GOOGLE_MAPS_LISTING_CSS_EXTRACTOR,
            scroll_steps=steps,
        )
    except GoogleMapsFetchError as exc:
        if "HTTP Error 422" in str(exc):
            return [], "zenrows_422"
        raise
    unique = _collect_place_urls_from_payload(payload)
    html = payload.get("_html")
    if isinstance(html, str) and html.strip():
        page_hint = diagnose_maps_page(html)
    if not unique:
        if not isinstance(html, str) or not html.strip():
            html = fetch_google_maps_search_html(
                api_key,
                search_url,
                proxy_country=proxy_country,
                country_code=country_code,
                scroll_steps=steps,
            )
        unique = extract_place_urls_from_text(html)
        page_hint = diagnose_maps_page(html)
    return unique, page_hint


def fetch_google_maps_place_result(
    api_key: str,
    place_url: str,
    *,
    proxy_country: str = "de",
    country_code: str = "DE",
) -> SearchResult | None:
    try:
        payload = fetch_zenrows_css_payload(
            api_key,
            place_url,
            proxy_country=proxy_country,
            country_code=country_code,
            css_extractor=GOOGLE_MAPS_DETAIL_CSS_EXTRACTOR,
            scroll_steps=0,
            wait_ms=5000,
        )
    except GoogleMapsFetchError:
        payload = {}
    result = search_result_from_detail_payload(place_url, payload)
    if result is not None:
        return result
    html_parts: list[str] = []
    html = payload.get("_html")
    if isinstance(html, str) and html.strip():
        html_parts.append(html)
    if not html_parts:
        try:
            html_parts.append(
                _fetch_zenrows_maps_with_fallbacks(
                    api_key,
                    place_url,
                    proxy_country=proxy_country,
                    country_code=country_code,
                    css_extractor=None,
                    scroll_steps=0,
                    wait_ms=5000,
                )
            )
        except GoogleMapsFetchError:
            return None
    for html_text in html_parts:
        website = parse_website_from_detail_html(html_text)
        if website:
            return SearchResult(
                title=place_name_from_url(place_url) or normalized_host(website),
                url=website,
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
) -> tuple[list[SearchResult], GoogleMapsDiscoveryStats]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    place_urls, page_hint = fetch_google_maps_place_urls(
        api_key,
        search_url,
        proxy_country=proxy_country,
        country_code=country_code,
        scroll_steps=scroll_steps,
    )
    place_urls = place_urls[: max(places_limit, 0)]
    stats = GoogleMapsDiscoveryStats(place_urls=len(place_urls), page_hint=page_hint)
    if not place_urls:
        return [], stats

    results: list[SearchResult] = []
    details_checked = 0
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
            details_checked += 1
            result = future.result()
            if result is not None and result.url.strip():
                results.append(result)
    stats = GoogleMapsDiscoveryStats(
        place_urls=len(place_urls),
        details_checked=details_checked,
        websites_found=len(results),
        page_hint=page_hint,
    )
    return results, stats


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
