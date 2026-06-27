from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .extract import normalized_host
from .http import format_request_error, read_response_text, urlopen
from .locations import DEFAULT_COUNTRIES, top_cities_for_web_search
from .models import SearchResult

DIRECTORY_USER_AGENT = "Mozilla/5.0 (compatible; capper-lead-research/0.1; +compliance-review)"
DIRECTORY_REQUEST_DELAY_SECONDS = 0.35
DIRECTORY_ZENROWS_DELAY_SECONDS = 0.5
DIRECTORY_LOCATIONS_WITHOUT_QUERY = 5
DIRECTORY_ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
DIRECTORY_ZENROWS_STEALTH_MODE = "auto"
DIRECTORY_ZENROWS_TIMEOUT_SECONDS = 60

DIRECTORY_HOST_SUFFIXES = (
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
    "tripadvisor.",
    "yelp.",
    "booking.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "google.",
    "youtube.com",
    "vimeo.com",
    "cylex.",
    "golocal.de",
    "ekomi.de",
    "consentmanager.net",
)

BLOCKED_WEBSITE_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
)


class DirectoryFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectoryFetchConfig:
    zenrows_api_key: str = ""
    proxy_country: str = "de"
    allow_direct_fallback: bool = False


_fetch_config = DirectoryFetchConfig()


def configure_directory_fetch(config: DirectoryFetchConfig) -> None:
    global _fetch_config
    _fetch_config = config


def resolve_directory_zenrows_key(explicit_key: str | None = None) -> str:
    if explicit_key is not None:
        return explicit_key.strip()
    return os.getenv("ZENROWS_API_KEY", "").strip()


def directory_fetch_requires_zenrows(config: DirectoryFetchConfig | None = None) -> bool:
    active = config or _fetch_config
    return not active.allow_direct_fallback


def build_zenrows_directory_fetch_url(
    api_key: str,
    target_url: str,
    *,
    proxy_country: str = "de",
) -> str:
    params = urllib.parse.urlencode(
        {
            "apikey": api_key,
            "mode": DIRECTORY_ZENROWS_STEALTH_MODE,
            "proxy_country": proxy_country,
        }
    )
    encoded_target = urllib.parse.quote(target_url, safe="")
    return f"{DIRECTORY_ZENROWS_ENDPOINT}?{params}&url={encoded_target}"


def is_valid_lead_url(url: str) -> bool:
    if not url:
        return False
    parsed = urllib.parse.urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    website: str
    source_url: str
    snippet: str = ""


def directory_location_plans(
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
) -> list[str]:
    if location.strip():
        return [location.strip()]
    plans = [city for city, _country in top_cities_for_web_search(countries, per_country=DIRECTORY_LOCATIONS_WITHOUT_QUERY)]
    return plans or ["Berlin"]


def slug_for_directory_path(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    return urllib.parse.quote(cleaned, safe="")


def title_case_phrase(value: str) -> str:
    parts = re.split(r"(\s+|-)", value.strip())
    return "".join(part[:1].upper() + part[1:] if part and not part.isspace() and part != "-" else part for part in parts)


def is_external_business_url(url: str) -> bool:
    normalized = normalize_result_url(url)
    if not normalized or not is_valid_lead_url(normalized):
        return False
    host = normalized_host(normalized).lower()
    if not host:
        return False
    if host.endswith(BLOCKED_WEBSITE_SUFFIXES):
        return False
    return not any(token in host for token in DIRECTORY_HOST_SUFFIXES)


def extract_json_ld_blocks(page_html: str) -> list[object]:
    blocks: list[object] = []
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page_html, re.IGNORECASE | re.DOTALL):
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def iter_json_ld_business_items(payload: object):
    if isinstance(payload, dict):
        payload_type = payload.get("@type")
        if payload_type == "ItemList":
            for element in payload.get("itemListElement", []):
                if isinstance(element, dict):
                    yield from iter_json_ld_business_items(element.get("item"))
        elif payload_type in {"SearchResultsPage", "FAQPage"}:
            main_entity = payload.get("mainEntity")
            if isinstance(main_entity, dict):
                yield from iter_json_ld_business_items(main_entity)
            elif isinstance(main_entity, list):
                for item in main_entity:
                    yield from iter_json_ld_business_items(item)
        elif payload_type == "ListItem":
            yield from iter_json_ld_business_items(payload.get("item"))
        elif payload_type in {"LocalBusiness", "Organization", "Hotel", "Store", "ProfessionalService"}:
            yield payload
        elif "@graph" in payload:
            for item in payload["@graph"]:
                yield from iter_json_ld_business_items(item)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_json_ld_business_items(item)


def website_from_business_item(item: dict) -> str:
    for key in ("sameAs", "url"):
        value = item.get(key)
        if isinstance(value, str) and is_external_business_url(value):
            return normalize_result_url(value)
        if isinstance(value, list):
            for candidate in value:
                if isinstance(candidate, str) and is_external_business_url(candidate):
                    return normalize_result_url(candidate)
    return ""


def directory_entries_to_results(
    entries: list[DirectoryEntry],
    *,
    limit: int,
    seen: set[str],
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for entry in entries:
        website = normalize_result_url(entry.website)
        if not website or not is_external_business_url(website):
            continue
        key = website.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        results.append(
            SearchResult(
                title=entry.name or website,
                url=website,
                snippet=entry.snippet or f"Branchenverzeichnis: {entry.source_url}",
            )
        )
        if len(results) >= limit:
            break
    return results


def fetch_directory_html_direct(url: str, *, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "de-DE,de;q=0.9",
            "User-Agent": DIRECTORY_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return read_response_text(response)
    except OSError as exc:
        raise DirectoryFetchError(f"Directory request failed for {url}: {format_request_error(exc)}") from exc


def fetch_directory_html_via_zenrows(
    url: str,
    *,
    api_key: str,
    proxy_country: str = "de",
    timeout: int = DIRECTORY_ZENROWS_TIMEOUT_SECONDS,
) -> str:
    request = urllib.request.Request(
        build_zenrows_directory_fetch_url(api_key, url, proxy_country=proxy_country),
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": DIRECTORY_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return read_response_text(response)
    except OSError as exc:
        message = format_request_error(exc)
        if "HTTP Error 401" in message or "HTTP Error 403" in message:
            raise DirectoryFetchError(
                "ZenRows API-Key ungueltig oder ohne Berechtigung fuer Branchenverzeichnisse. "
                "Bitte den Key im ZenRows-Dashboard pruefen."
            ) from exc
        raise DirectoryFetchError(f"ZenRows directory request failed for {url}: {message}") from exc


def fetch_directory_html(url: str, *, timeout: int = DIRECTORY_ZENROWS_TIMEOUT_SECONDS) -> str:
    config = _fetch_config
    if config.zenrows_api_key:
        page_html = fetch_directory_html_via_zenrows(
            url,
            api_key=config.zenrows_api_key,
            proxy_country=config.proxy_country,
            timeout=timeout,
        )
        time.sleep(DIRECTORY_ZENROWS_DELAY_SECONDS)
        return page_html
    if config.allow_direct_fallback:
        return fetch_directory_html_direct(url, timeout=min(timeout, 20))
    raise DirectoryFetchError(
        "Branchenverzeichnisse werden ueber die ZenRows API abgefragt. "
        "Setze ZENROWS_API_KEY oder nutze --provider zenrows mit --source-profile common."
    )


def extract_external_links(page_html: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r'href="(https?://[^"]+)"', page_html, re.IGNORECASE):
        url = html.unescape(match.group(1))
        if is_external_business_url(url):
            links.append(normalize_result_url(url))
    return links


def parse_dasoertliche_html(page_html: str, *, source_url: str) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    seen_keys: set[str] = set()

    handler_match = re.search(r"var handlerData\s*=\s*(\[\[.*?\]\])\s*;", page_html, re.DOTALL)
    if handler_match:
        try:
            rows = json.loads(handler_match.group(1))
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 18:
                continue
            website = normalize_result_url(str(row[3] or ""))
            if not is_external_business_url(website):
                continue
            name = html.unescape(str(row[14] or "").strip())
            listing_url = normalize_result_url(str(row[15] or "")) or source_url
            email = str(row[17] or "").strip()
            phone = str(row[11] or "").strip()
            snippet_parts = ["Das Örtliche"]
            if phone:
                snippet_parts.append(phone)
            if email:
                snippet_parts.append(email)
            key = website.lower()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=" | ".join(snippet_parts),
                )
            )

    if entries:
        return entries

    for block in extract_json_ld_blocks(page_html):
        for item in iter_json_ld_business_items(block):
            if not isinstance(item, dict):
                continue
            website = website_from_business_item(item)
            if not website:
                continue
            name = html.unescape(str(item.get("name", "")).strip())
            listing_url = normalize_result_url(str(item.get("url", ""))) or source_url
            phone = str(item.get("telephone", "")).strip()
            snippet = "Das Örtliche"
            if phone:
                snippet = f"{snippet} | {phone}"
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=snippet,
                )
            )
    return entries


def parse_auskunft_html(page_html: str, *, source_url: str) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    for block in re.findall(r'<div class="resultItemContainer[^"]*">(.*?)</div>\s*<div class="resultItemContainer', page_html, re.DOTALL):
        name_match = re.search(r'<h2 class="resultHeader"><a[^>]*title="[^"]*">([^<]+)</a>', block)
        if not name_match:
            name_match = re.search(r'<h2 class="resultHeader"><a[^>]*>([^<]+)</a>', block)
        name = html.unescape(name_match.group(1).strip()) if name_match else ""
        listing_match = re.search(r'href="(/firma/[^"?]+)', block)
        listing_url = f"https://www.auskunft.de{listing_match.group(1)}" if listing_match else source_url

        website = ""
        for link_match in re.finditer(r'href="(https?://[^"]+)"', block):
            candidate = html.unescape(link_match.group(1))
            if is_external_business_url(candidate):
                website = normalize_result_url(candidate)
                break

        if not website:
            continue

        phone_match = re.search(r'href="tel:([^"]+)"', block)
        phone = phone_match.group(1).strip() if phone_match else ""
        snippet = "auskunft.de"
        if phone:
            snippet = f"{snippet} | {phone}"
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=listing_url,
                snippet=snippet,
            )
        )

    if entries:
        return entries

    # Fallback for slightly different markup between result blocks.
    for block in re.findall(r'<div class="resultItemContainer[^"]*">.*?(?=<div class="resultItemContainer|$)', page_html, re.DOTALL):
        name_match = re.search(r'<h2 class="resultHeader"><a[^>]*>([^<]+)</a>', block)
        name = html.unescape(name_match.group(1).strip()) if name_match else ""
        website = next((link for link in extract_external_links(block)), "")
        if not website:
            continue
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=source_url,
                snippet="auskunft.de",
            )
        )
    return entries


def parse_11880_html(page_html: str, *, source_url: str) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    for block in extract_json_ld_blocks(page_html):
        for item in iter_json_ld_business_items(block):
            if not isinstance(item, dict):
                continue
            name = html.unescape(str(item.get("name", "")).strip())
            listing_url = normalize_result_url(str(item.get("url", ""))) or source_url
            website = website_from_business_item(item)
            email = str(item.get("email", "")).strip()
            phone = str(item.get("telephone", "")).strip()
            snippet_parts = ["11880.com"]
            if phone:
                snippet_parts.append(phone)
            if email:
                snippet_parts.append(email)
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=" | ".join(snippet_parts),
                )
            )
    return entries


def parse_gelbeseiten_listing_html(page_html: str, *, source_url: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for article in re.findall(r'<article class="mod mod-Treffer"[^>]*>(.*?)</article>', page_html, re.DOTALL):
        name_match = re.search(r'class="mod-Treffer__name"[^>]*>([^<]+)', article)
        link_match = re.search(r'href="(https://www\.gelbeseiten\.de/gsbiz/[^"]+)"', article)
        if not link_match:
            continue
        detail_url = html.unescape(link_match.group(1))
        if detail_url in seen:
            continue
        seen.add(detail_url)
        name = html.unescape(name_match.group(1).strip()) if name_match else detail_url
        listings.append((name, detail_url))
    return listings


def parse_gelbeseiten_detail_html(page_html: str, *, name: str, source_url: str) -> DirectoryEntry | None:
    website = ""
    for pattern in (
        r'contains-icon-big-homepage[\s\S]*?href="(https?://[^"]+)"',
        r'icon-homepage[\s\S]*?href="(https?://[^"]+)"',
        r'data-link="mailto:[^"]+"[\s\S]*?href="(https?://[^"]+)"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if match:
            candidate = html.unescape(match.group(1))
            if is_external_business_url(candidate):
                website = normalize_result_url(candidate)
                break
    if not website:
        website = next((link for link in extract_external_links(page_html)), "")

    if not website:
        return None

    title_match = re.search(r"<title>([^<|]+)", page_html)
    resolved_name = html.unescape(title_match.group(1).strip()) if title_match and title_match.group(1).strip() else name
    phone_match = re.search(r'data-prg="[^"]+"', page_html)
    snippet = "Gelbe Seiten"
    if phone_match:
        snippet = f"{snippet} | {source_url}"
    return DirectoryEntry(name=resolved_name or name, website=website, source_url=source_url, snippet=snippet)


def parse_telefonbuch_html(page_html: str, *, source_url: str) -> list[DirectoryEntry]:
    if "cloudflare" in page_html.casefold() and "blocked" in page_html.casefold():
        return []

    entries: list[DirectoryEntry] = []
    for block in extract_json_ld_blocks(page_html):
        for item in iter_json_ld_business_items(block):
            if not isinstance(item, dict):
                continue
            website = website_from_business_item(item)
            if not website:
                continue
            name = html.unescape(str(item.get("name", "")).strip())
            listing_url = normalize_result_url(str(item.get("url", ""))) or source_url
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet="Telefonbuch",
                )
            )

    if entries:
        return entries

    for name, website in re.findall(
        r'<h\d[^>]*class="[^"]*name[^"]*"[^>]*>\s*([^<]+)\s*</h\d>[\s\S]{0,2500}?href="(https?://[^"]+)"',
        page_html,
        re.IGNORECASE,
    ):
        candidate = html.unescape(website)
        if not is_external_business_url(candidate):
            continue
        entries.append(
            DirectoryEntry(
                name=html.unescape(name.strip()),
                website=normalize_result_url(candidate),
                source_url=source_url,
                snippet="Telefonbuch",
            )
        )
    return entries


def build_dasoertliche_url(category: str, location: str, page: int) -> str:
    category_slug = title_case_phrase(category)
    location_slug = title_case_phrase(location)
    if page <= 1:
        return f"https://www.dasoertliche.de/Themen/{slug_for_directory_path(category_slug)}/{slug_for_directory_path(location_slug)}.html"
    return (
        f"https://www.dasoertliche.de/Themen/{slug_for_directory_path(category_slug)}/"
        f"{slug_for_directory_path(location_slug)}-Seite-{page}.html"
    )


def build_auskunft_url(category: str, location: str) -> str:
    query = urllib.parse.urlencode({"search": f"{category} {location}".strip()})
    return f"https://www.auskunft.de/Suche?{query}"


def build_11880_url(category: str, location: str, page: int) -> str:
    category_slug = slug_for_directory_path(category)
    location_slug = slug_for_directory_path(location)
    if page <= 1:
        return f"https://www.11880.com/suche/{category_slug}/{location_slug}"
    return f"https://www.11880.com/suche/{category_slug}/{location_slug}?page={page}"


def build_gelbeseiten_url(category: str, location: str) -> str:
    return f"https://www.gelbeseiten.de/branchen/{slug_for_directory_path(category)}/{slug_for_directory_path(location)}"


def build_telefonbuch_url(category: str, location: str) -> str:
    return f"https://www.telefonbuch.de/Suche/{slug_for_directory_path(category)}/{slug_for_directory_path(location)}"


def enrich_11880_entries(entries: list[DirectoryEntry], *, max_detail_fetches: int) -> list[DirectoryEntry]:
    enriched: list[DirectoryEntry] = []
    detail_fetches = 0
    for entry in entries:
        if entry.website:
            enriched.append(entry)
            continue
        if detail_fetches >= max_detail_fetches or not entry.source_url:
            continue
        try:
            detail_html = fetch_directory_html(entry.source_url)
        except DirectoryFetchError:
            continue
        detail_fetches += 1
        website = next((link for link in extract_external_links(detail_html)), "")
        if website:
            enriched.append(
                DirectoryEntry(
                    name=entry.name,
                    website=website,
                    source_url=entry.source_url,
                    snippet=entry.snippet,
                )
            )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return enriched


def enrich_gelbeseiten_entries(listings: list[tuple[str, str]], *, max_detail_fetches: int) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    for name, detail_url in listings[:max_detail_fetches]:
        try:
            detail_html = fetch_directory_html(detail_url)
        except DirectoryFetchError:
            continue
        parsed = parse_gelbeseiten_detail_html(detail_html, name=name, source_url=detail_url)
        if parsed is not None:
            entries.append(parsed)
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries


def scrape_dasoertliche(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    page = 1
    while len(entries) < limit and page <= 5:
        source_url = build_dasoertliche_url(category, location, page)
        page_html = fetch_directory_html(source_url)
        page_entries = parse_dasoertliche_html(page_html, source_url=source_url)
        if not page_entries:
            break
        entries.extend(page_entries)
        if "rel=\"next\"" not in page_html and f"-Seite-{page + 1}.html" not in page_html:
            break
        page += 1
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries


def scrape_auskunft(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_auskunft_url(category, location)
    page_html = fetch_directory_html(source_url)
    return parse_auskunft_html(page_html, source_url=source_url)[:limit]


def scrape_11880(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    page = 1
    while len(entries) < limit and page <= 3:
        source_url = build_11880_url(category, location, page)
        page_html = fetch_directory_html(source_url)
        page_entries = parse_11880_html(page_html, source_url=source_url)
        if not page_entries:
            break
        entries.extend(page_entries)
        page += 1
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return enrich_11880_entries(entries, max_detail_fetches=limit)


def scrape_gelbeseiten(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_gelbeseiten_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_gelbeseiten_listing_html(page_html, source_url=source_url)
    return enrich_gelbeseiten_entries(listings, max_detail_fetches=limit)


def scrape_telefonbuch(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_telefonbuch_url(category, location)
    try:
        page_html = fetch_directory_html(source_url)
    except DirectoryFetchError:
        return []
    if "cloudflare" in page_html.casefold() and "blocked" in page_html.casefold():
        return []
    return parse_telefonbuch_html(page_html, source_url=source_url)[:limit]


DIRECTORY_SCRAPERS: tuple[tuple[str, callable], ...] = (
    ("Das Örtliche", scrape_dasoertliche),
    ("auskunft.de", scrape_auskunft),
    ("Gelbe Seiten", scrape_gelbeseiten),
    ("11880.com", scrape_11880),
    ("Telefonbuch", scrape_telefonbuch),
)
