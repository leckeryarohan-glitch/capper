from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .extract import normalized_host
from .http import format_request_error, read_response_text, urlopen
from .extract import extract_emails, normalize_email
from .locations import DEFAULT_COUNTRIES, cities_for_mass_web_search
from .models import SearchResult

DIRECTORY_USER_AGENT = "Mozilla/5.0 (compatible; capper-lead-research/0.1; +compliance-review)"
DIRECTORY_REQUEST_DELAY_SECONDS = 0.35
DIRECTORY_ZENROWS_DELAY_SECONDS = 0.5
DIRECTORY_ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"
DIRECTORY_ZENROWS_STEALTH_MODE = "auto"
DIRECTORY_ZENROWS_TIMEOUT_SECONDS = 60
DIRECTORY_MAX_RESULTS_PER_SOURCE = 120
DIRECTORY_MAX_DETAIL_FETCHES = 30


def cap_directory_source_limit(limit: int) -> int:
    return max(1, min(limit, DIRECTORY_MAX_RESULTS_PER_SOURCE))


def cap_directory_detail_fetches(limit: int) -> int:
    return max(1, min(limit, DIRECTORY_MAX_DETAIL_FETCHES))

DIRECTORY_HOST_SUFFIXES = (
    "gelbeseiten.de",
    "dasoertliche.de",
    "11880.com",
    "auskunft.de",
    "telefonbuch.de",
    "dastelefonbuch.de",
    "cylex.de",
    "cylex-international.com",
    "hotfrog.de",
    "hotfrog.com",
    "centralindex.com",
    "werkenntdenbesten.de",
    "goyellow.de",
    "wlw.de",
    "europages.",
    "kompass.com",
    "firmenabc.",
    "brownbook.net",
    "manta.com",
    "yalwa.",
    "yelp.",
    "meinestadt.de",
    "firmenwissen.de",
    "branchenbuch.net",
    "tripadvisor.",
    "booking.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "pinterest.com",
    "google.",
    "youtube.com",
    "vimeo.com",
    "golocal.de",
    "ekomi.de",
    "consentmanager.net",
    "yext-wrap.com",
    "holidaycheck.",
    "expedia.",
    "googletagservices.com",
    "googletagmanager.com",
    "google-analytics.com",
    "googlesyndication.com",
    "doubleclick.net",
    "wkdb.h5v.eu",
    "h5v.eu",
    "postleitzahlen.de",
    "wirfindendeinenjob.de",
    "cleverb2b.de",
    "cdn.11880.com",
    "hrs.de",
    "treatwell.de",
    "apps.apple.com",
    "play.google.com",
    "bootstrapcdn.com",
    "cloudfront.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "gstatic.com",
    "schema.org",
    "w3.org",
    "ogp.me",
    "yelpcdn.com",
    "yelp.com",
    "manta.com",
    "manta-r3.com",
    "hotjar.com",
    "visable.com",
    "meinungsmeister.de",
    "locanto.de",
    "locanto.info",
    "cookielaw.org",
    "ksales.ai",
    "btloader.com",
    "crsspxl.com",
    "chivalrouscord.com",
    "pitchbook.com",
    "pitchbook.",
    "indeed.com",
    "indeed.de",
    "hiringlab.org",
    "hrtechprivacy.com",
    "deloi.tt",
    "adjust.com",
    "ogp.me",
    "datadoghq.",
    "go-mpulse.net",
    "optimizely.com",
    "gehalt.de",
    "onelink.me",
    "indeed.onelink.me",
    "jameda.de",
    "docplanner.",
    "sanego.de",
    "restaurantguru.com",
    "openstreetmap.org",
    "docfinder.at",
    "youcanbook.me",
    "anwaltauskunft.de",
    "steuerberaterverzeichnis.berufs-org.de",
    "verzeichnis-steuerberater.de",
    "bstbk.de",
    "berufs-org.de",
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
    email: str = ""
    phone: str = ""


def normalize_directory_email(value: str) -> str:
    cleaned = normalize_email(value)
    if not cleaned or "@" not in cleaned:
        return ""
    local, _, domain = cleaned.partition("@")
    if not local or not domain or "." not in domain:
        return ""
    return cleaned


def pick_directory_email(page_html: str, *explicit_values: str) -> str:
    for value in explicit_values:
        email = normalize_directory_email(value)
        if email:
            return email
    for email in extract_emails(page_html):
        normalized = normalize_directory_email(email)
        if normalized:
            return normalized
    return ""


def directory_entry_dedupe_key(entry: DirectoryEntry) -> str:
    website = normalize_result_url(entry.website)
    if website and is_external_business_url(website):
        return f"url:{website.lower().rstrip('/')}"
    email = normalize_directory_email(entry.email)
    if email:
        return f"email:{email}"
    return ""


def directory_location_plans(
    location: str,
    countries: tuple[str, ...] = DEFAULT_COUNTRIES,
) -> list[str]:
    if location.strip():
        return [location.strip()]
    plans = [city for city, _country in cities_for_mass_web_search(countries)]
    return plans or ["Berlin"]


def slug_for_directory_path(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    return urllib.parse.quote(cleaned, safe="")


def title_case_phrase(value: str) -> str:
    parts = re.split(r"(\s+|-)", value.strip())
    return "".join(part[:1].upper() + part[1:] if part and not part.isspace() and part != "-" else part for part in parts)


def name_from_url_slug(value: str) -> str:
    cleaned = value.strip().strip("/")
    cleaned = re.sub(r"\.html?$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"--[^/]+$", "", cleaned)
    cleaned = cleaned.split("/")[-1]
    return title_case_phrase(cleaned.replace("-", " "))


def directory_hyphen_slug(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        return ""
    return "-".join(title_case_phrase(part) for part in cleaned.split(" "))


def directory_lower_hyphen_slug(value: str) -> str:
    return directory_hyphen_slug(value).lower()


def sanego_path_segment(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        return ""
    return urllib.parse.quote(title_case_phrase(cleaned), safe="+")


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
        email = normalize_directory_email(entry.email)
        phone = entry.phone.strip()
        dedupe_key = directory_entry_dedupe_key(entry)
        if not dedupe_key or dedupe_key in seen:
            continue

        if website and is_external_business_url(website):
            seen.add(dedupe_key)
            if email:
                seen.add(f"email:{email}")
            results.append(
                SearchResult(
                    title=entry.name or website,
                    url=website,
                    snippet=entry.snippet or f"Branchenverzeichnis: {entry.source_url}",
                    directory_email=email,
                    directory_phone=phone,
                    directory_source_url=entry.source_url,
                )
            )
        elif email:
            seen.add(dedupe_key)
            results.append(
                SearchResult(
                    title=entry.name or email,
                    url="",
                    snippet=entry.snippet or f"Branchenverzeichnis: {entry.source_url}",
                    directory_email=email,
                    directory_phone=phone,
                    directory_source_url=entry.source_url,
                )
            )
        else:
            continue

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


def fetch_directory_post(
    url: str,
    data: Mapping[str, str],
    *,
    timeout: int = DIRECTORY_ZENROWS_TIMEOUT_SECONDS,
) -> str:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    config = _fetch_config
    if config.zenrows_api_key:
        params = urllib.parse.urlencode(
            {
                "apikey": config.zenrows_api_key,
                "mode": DIRECTORY_ZENROWS_STEALTH_MODE,
                "proxy_country": config.proxy_country,
                "url": url,
            }
        )
        request = urllib.request.Request(
            f"{DIRECTORY_ZENROWS_ENDPOINT}?{params}",
            data=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/json",
                "User-Agent": DIRECTORY_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                page_html = read_response_text(response)
        except OSError as exc:
            message = format_request_error(exc)
            raise DirectoryFetchError(f"ZenRows directory POST failed for {url}: {message}") from exc
        time.sleep(DIRECTORY_ZENROWS_DELAY_SECONDS)
        return page_html
    if config.allow_direct_fallback:
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/json",
                "Accept-Language": "de-DE,de;q=0.9",
                "User-Agent": DIRECTORY_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=min(timeout, 20)) as response:
                return read_response_text(response)
        except OSError as exc:
            raise DirectoryFetchError(f"Directory POST failed for {url}: {format_request_error(exc)}") from exc
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
            if website and not is_external_business_url(website):
                website = ""
            name = html.unescape(str(row[14] or "").strip())
            listing_url = normalize_result_url(str(row[15] or "")) or source_url
            email = normalize_directory_email(str(row[17] or ""))
            phone = str(row[11] or "").strip()
            if not website and not email:
                continue
            snippet_parts = ["Das Örtliche"]
            if phone:
                snippet_parts.append(phone)
            key = directory_entry_dedupe_key(
                DirectoryEntry(name=name, website=website, source_url=listing_url, email=email)
            )
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=" | ".join(snippet_parts),
                    email=email,
                    phone=phone,
                )
            )

    if entries:
        return entries

    for block in extract_json_ld_blocks(page_html):
        for item in iter_json_ld_business_items(block):
            if not isinstance(item, dict):
                continue
            website = website_from_business_item(item)
            name = html.unescape(str(item.get("name", "")).strip())
            listing_url = normalize_result_url(str(item.get("url", ""))) or source_url
            phone = str(item.get("telephone", "")).strip()
            email = normalize_directory_email(str(item.get("email", "")))
            if not website and not email:
                continue
            snippet = "Das Örtliche"
            if phone:
                snippet = f"{snippet} | {phone}"
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=snippet,
                    email=email,
                    phone=phone,
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

        phone_match = re.search(r'href="tel:([^"]+)"', block)
        phone = phone_match.group(1).strip() if phone_match else ""
        email = pick_directory_email(block)
        if not website and not email:
            continue
        snippet = "auskunft.de"
        if phone:
            snippet = f"{snippet} | {phone}"
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=listing_url,
                snippet=snippet,
                email=email,
                phone=phone,
            )
        )

    if entries:
        return entries

    # Fallback for slightly different markup between result blocks.
    for block in re.findall(r'<div class="resultItemContainer[^"]*">.*?(?=<div class="resultItemContainer|$)', page_html, re.DOTALL):
        name_match = re.search(r'<h2 class="resultHeader"><a[^>]*>([^<]+)</a>', block)
        name = html.unescape(name_match.group(1).strip()) if name_match else ""
        website = next((link for link in extract_external_links(block)), "")
        email = pick_directory_email(block)
        if not website and not email:
            continue
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=source_url,
                snippet="auskunft.de",
                email=email,
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
            email = normalize_directory_email(str(item.get("email", "")))
            phone = str(item.get("telephone", "")).strip()
            snippet_parts = ["11880.com"]
            if phone:
                snippet_parts.append(phone)
            if not website and not email:
                continue
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=" | ".join(snippet_parts),
                    email=email,
                    phone=phone,
                )
            )
    return entries


def parse_goyellow_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for seourl in re.findall(r'data-seourl="(/home/[^"]+\.html)"', page_html):
        detail_url = f"https://www.goyellow.de{html.unescape(seourl)}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(seourl), detail_url))
    return listings


def parse_kompass_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in re.findall(r'href="(/c/[^/]+/de\d+/)"', page_html):
        detail_url = f"https://de.kompass.com{html.unescape(path)}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        slug = path.strip("/").split("/")[1] if path.count("/") >= 2 else path
        listings.append((name_from_url_slug(slug), detail_url))
    return listings


def parse_europages_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href in re.findall(r'company-tile[\s\S]{0,800}?href="([^"]+)"', page_html, re.IGNORECASE):
        candidate = html.unescape(href)
        if candidate.startswith("/"):
            detail_url = f"https://www.europages.de{candidate}"
        elif candidate.startswith("https://www.europages.de/"):
            detail_url = candidate
        else:
            continue
        if detail_url in seen or "/de/suche" in detail_url or "/company/legal" in detail_url:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(candidate), detail_url))
    return listings


def parse_yelp_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in re.findall(r'href="(/biz/[^"?]+)', page_html):
        detail_url = f"https://www.yelp.de{html.unescape(path)}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(path.replace("/biz/", "")), detail_url))
    return listings


def parse_manta_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="(/c/[^"]+)"[^>]*>([^<]{3,120})</a>', page_html, re.IGNORECASE):
        path = html.unescape(match.group(1))
        if not path.startswith("/c/"):
            continue
        detail_url = f"https://www.manta.com{path}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        name = html.unescape(match.group(2).strip()) or name_from_url_slug(path)
        listings.append((name, detail_url))
    if listings:
        return listings
    for path in re.findall(r'href="(/c/[^"]+)"', page_html):
        detail_url = f"https://www.manta.com{html.unescape(path)}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(path), detail_url))
    return listings


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

    email = pick_directory_email(page_html)
    if not website and not email:
        return None

    title_match = re.search(r"<title>([^<|]+)", page_html)
    resolved_name = html.unescape(title_match.group(1).strip()) if title_match and title_match.group(1).strip() else name
    snippet = "Gelbe Seiten"
    return DirectoryEntry(
        name=resolved_name or name,
        website=website,
        source_url=source_url,
        snippet=snippet,
        email=email,
    )


def parse_telefonbuch_html(page_html: str, *, source_url: str) -> list[DirectoryEntry]:
    if "cloudflare" in page_html.casefold() and "blocked" in page_html.casefold():
        return []

    entries: list[DirectoryEntry] = []
    for block in extract_json_ld_blocks(page_html):
        for item in iter_json_ld_business_items(block):
            if not isinstance(item, dict):
                continue
            website = website_from_business_item(item)
            email = normalize_directory_email(str(item.get("email", "")))
            phone = str(item.get("telephone", "")).strip()
            if not website and not email:
                continue
            name = html.unescape(str(item.get("name", "")).strip())
            listing_url = normalize_result_url(str(item.get("url", ""))) or source_url
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet="Telefonbuch",
                    email=email,
                    phone=phone,
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


def build_goyellow_url(category: str, location: str, page: int) -> str:
    base = (
        f"https://www.goyellow.de/suche/"
        f"{slug_for_directory_path(category)}/{slug_for_directory_path(location)}"
    )
    if page <= 1:
        return base
    return f"{base}/seite-{page}"


def build_kompass_url(category: str, location: str) -> str:
    params = urllib.parse.urlencode({"text": category, "location": location})
    return f"https://de.kompass.com/searchCompanies?{params}"


def build_europages_url(category: str, location: str) -> str:
    category_slug = slug_for_directory_path(category).lower()
    location_slug = slug_for_directory_path(location)
    return f"https://www.europages.de/unternehmen/{category_slug}.html?loc={location_slug}"


def build_yelp_url(category: str, location: str) -> str:
    params = urllib.parse.urlencode({"find_desc": category, "find_loc": location})
    return f"https://www.yelp.de/search?{params}"


def build_manta_url(category: str, location: str) -> str:
    params = urllib.parse.urlencode({"search": category, "city": location, "country": "Germany"})
    return f"https://www.manta.com/search?{params}"


def enrich_11880_entries(entries: list[DirectoryEntry], *, max_detail_fetches: int) -> list[DirectoryEntry]:
    enriched: list[DirectoryEntry] = []
    detail_fetches = 0
    max_detail_fetches = cap_directory_detail_fetches(max_detail_fetches)
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
        website = parse_11880_detail_website(detail_html)
        email = pick_directory_email(detail_html, entry.email)
        if website:
            enriched.append(
                DirectoryEntry(
                    name=entry.name,
                    website=website,
                    source_url=entry.source_url,
                    snippet=entry.snippet,
                    email=email or entry.email,
                    phone=entry.phone,
                )
            )
        elif email or entry.email:
            enriched.append(
                DirectoryEntry(
                    name=entry.name,
                    website="",
                    source_url=entry.source_url,
                    snippet=entry.snippet,
                    email=email or entry.email,
                    phone=entry.phone,
                )
            )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return enriched


def enrich_gelbeseiten_entries(listings: list[tuple[str, str]], *, max_detail_fetches: int) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    max_detail_fetches = cap_directory_detail_fetches(max_detail_fetches)
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


def enrich_named_listing_details(
    listings: list[tuple[str, str]],
    *,
    max_detail_fetches: int,
    parse_detail_website: Callable[[str], str],
    source_name: str,
) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    max_detail_fetches = cap_directory_detail_fetches(max_detail_fetches)
    for name, detail_url in listings[:max_detail_fetches]:
        try:
            detail_html = fetch_directory_html(detail_url)
        except DirectoryFetchError:
            continue
        website = parse_detail_website(detail_html)
        email = pick_directory_email(detail_html)
        if not website and not email:
            continue
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=detail_url,
                snippet=source_name,
                email=email,
            )
        )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries


def parse_json_ld_directory_entries(page_html: str, *, source_url: str, source_name: str) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    seen: set[str] = set()
    for block in extract_json_ld_blocks(page_html):
        for item in iter_json_ld_business_items(block):
            if not isinstance(item, dict):
                continue
            name = html.unescape(str(item.get("name", "")).strip())
            listing_url = normalize_result_url(str(item.get("url", ""))) or source_url
            website = website_from_business_item(item)
            phone = str(item.get("telephone", "")).strip()
            email = normalize_directory_email(str(item.get("email", "")))
            snippet_parts = [source_name]
            if phone:
                snippet_parts.append(phone)
            key = directory_entry_dedupe_key(
                DirectoryEntry(name=name, website=website, source_url=listing_url, email=email)
            )
            if not key or key in seen:
                continue
            seen.add(key)
            if not website and not email:
                continue
            entries.append(
                DirectoryEntry(
                    name=name,
                    website=website,
                    source_url=listing_url,
                    snippet=" | ".join(snippet_parts),
                    email=email,
                    phone=phone,
                )
            )
    return entries


def parse_cylex_detail_website(page_html: str) -> str:
    match = re.search(r'"url"\s*:\s*"(https?://[^"]+)"', page_html)
    if match:
        candidate = html.unescape(match.group(1))
        if is_external_business_url(candidate):
            return normalize_result_url(candidate)
    return next((link for link in extract_external_links(page_html)), "")


def parse_11880_detail_website(page_html: str) -> str:
    for pattern in (
        r'itemprop="url"\s+content="(https?://[^"]+)"',
        r'class="[^"]*website[^"]*"[^>]*href="(https?://[^"]+)"',
        r'href="(https?://[^"]+)"[^>]*class="[^"]*website[^"]*"',
        r'title="[^"]*(?:Webseite|Homepage)[^"]*"[^>]*href="(https?://[^"]+)"',
        r'href="(https?://[^"]+)"[^>]*title="[^"]*(?:Webseite|Homepage)[^"]*"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if not match:
            continue
        candidate = html.unescape(match.group(1))
        if is_external_business_url(candidate):
            return normalize_result_url(candidate)
    return next((link for link in extract_external_links(page_html)), "")


def parse_hotfrog_redirect_websites(page_html: str) -> list[str]:
    websites: list[str] = []
    seen: set[str] = set()
    for encoded in re.findall(r"continue=(https[^&\"']+)", page_html):
        candidate = normalize_result_url(html.unescape(urllib.parse.unquote(encoded)))
        if not candidate or not is_external_business_url(candidate):
            continue
        key = candidate.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        websites.append(candidate)
    return websites


def parse_werkenntdenbesten_detail_website(page_html: str) -> str:
    for pattern in (
        r'title="(?:Webseite[^"]*|Homepage)"[^>]*href="(https?://[^"]+)"',
        r'href="(https?://[^"]+)"[^>]*title="(?:Webseite[^"]*|Homepage)"',
        r'class="[^"]*website[^"]*"[^>]*href="(https?://[^"]+)"',
        r'itemprop="url"\s+content="(https?://[^"]+)"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if not match:
            continue
        candidate = html.unescape(match.group(1))
        if is_external_business_url(candidate):
            return normalize_result_url(candidate)
    return next((link for link in extract_external_links(page_html)), "")


def parse_goyellow_detail_website(page_html: str) -> str:
    for pattern in (
        r'itemprop="url"\s+content="(https?://[^"]+)"',
        r'href="(https?://[^"]+)"[^>]*>[^<]*(?:Webseite|Homepage|Website)',
        r'title="[^"]*(?:Webseite|Homepage)[^"]*"[^>]*href="(https?://[^"]+)"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if not match:
            continue
        candidate = html.unescape(match.group(1))
        if is_external_business_url(candidate):
            return normalize_result_url(candidate)
    return next((link for link in extract_external_links(page_html)), "")


def parse_kompass_detail_website(page_html: str) -> str:
    match = re.search(r'Website[\s\S]{0,220}?href="(https?://[^"]+)"', page_html, re.IGNORECASE)
    if match:
        candidate = html.unescape(match.group(1))
        if is_external_business_url(candidate):
            return normalize_result_url(candidate)
    return next((link for link in extract_external_links(page_html)), "")


def parse_europages_detail_website(page_html: str) -> str:
    match = re.search(r'"website"\s*:\s*"(https?://[^"]+)"', page_html, re.IGNORECASE)
    if match:
        candidate = html.unescape(match.group(1))
        if is_external_business_url(candidate):
            return normalize_result_url(candidate)
    return next((link for link in extract_external_links(page_html)), "")


def parse_yelp_detail_website(page_html: str) -> str:
    match = re.search(r'/biz_redir\?url=([^&"]+)', page_html, re.IGNORECASE)
    if match:
        candidate = normalize_result_url(html.unescape(urllib.parse.unquote(match.group(1))))
        if is_external_business_url(candidate):
            return candidate
    return ""


def parse_manta_detail_website(page_html: str) -> str:
    candidates: list[str] = []
    for encoded in re.findall(r'redirect=(https?[^&"\']+)', page_html, re.IGNORECASE):
        candidate = normalize_result_url(html.unescape(urllib.parse.unquote(encoded)))
        if is_external_business_url(candidate):
            candidates.append(candidate)
    for match in re.finditer(r'","(https?://[^"]+)"', page_html):
        candidate = normalize_result_url(html.unescape(match.group(1)))
        if is_external_business_url(candidate):
            candidates.append(candidate)
    for pattern in (
        r'"website"\s*:\s*"(https?://[^"]+)"',
        r'Visit Web Site[\s\S]{0,150}?href="(https?://[^"]+)"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if match:
            candidate = normalize_result_url(html.unescape(match.group(1)))
            if is_external_business_url(candidate):
                candidates.append(candidate)
    for candidate in candidates:
        host = normalized_host(candidate).lower()
        if any(token in host for token in ("pinterest.", "twitter.", "facebook.", "instagram.", "linkedin.", "youtube.")):
            continue
        return candidate
    return candidates[0] if candidates else ""


def parse_pitchbook_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in re.findall(r'href="(/profiles/company/\d+-\d+)"', page_html):
        detail_url = f"https://pitchbook.com{html.unescape(path)}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(path.split("/")[-1]), detail_url))
    return listings


def parse_pitchbook_detail_name(page_html: str) -> str:
    match = re.search(r"<title>([^<|]+?)(?:\s+\d{4}\s+Company|\||$)", page_html, re.IGNORECASE)
    if match:
        return html.unescape(match.group(1).strip())
    return ""


def parse_pitchbook_detail_website(page_html: str) -> str:
    match = re.search(r'Website[\s\S]{0,300}?href="(https?://[^"]+)"', page_html, re.IGNORECASE)
    if match:
        candidate = normalize_result_url(html.unescape(match.group(1)))
        if is_external_business_url(candidate):
            return candidate
    return ""


def parse_indeed_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in re.findall(r'href="(/cmp/[^"?]+)', page_html):
        normalized_path = re.sub(r"/faq$", "", html.unescape(path.strip()))
        parts = normalized_path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "cmp":
            continue
        company_path = f"/cmp/{parts[1]}"
        detail_url = f"https://de.indeed.com{company_path}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(parts[1]), detail_url))
    return listings


def parse_indeed_detail_name(page_html: str) -> str:
    match = re.search(r"<title>(?:Beruf und Karriere bei\s+)?([^|<]+?)(?:\s*\||\s+–|\s+-)", page_html, re.IGNORECASE)
    if match:
        return html.unescape(match.group(1).strip())
    return ""


def pick_best_embedded_business_url(page_html: str) -> str:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"https?://[^\s\"\\<>]+", page_html):
        candidate = normalize_result_url(html.unescape(raw.rstrip("\\\",.;")))
        if not candidate or not is_external_business_url(candidate):
            continue
        host = normalized_host(candidate).lower()
        if len(host) < 4 or host in {"sb", "b"}:
            continue
        key = candidate.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    for candidate in candidates:
        host = normalized_host(candidate).lower()
        if any(token in host for token in ("onelink.", "adjust.com", "app.link")):
            continue
        return candidate
    return candidates[0] if candidates else ""


def parse_indeed_detail_website(page_html: str) -> str:
    for pattern in (
        r'"website"\s*:\s*"(https?://[^"]+)"',
        r'"companyWebsite"\s*:\s*"(https?://[^"]+)"',
        r'href="(https?://[^"]+)"[^>]*>[^<]*(?:Website|Webseite|Homepage)',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if match:
            candidate = normalize_result_url(html.unescape(match.group(1)))
            if is_external_business_url(candidate):
                return candidate
    return pick_best_embedded_business_url(page_html)


def parse_jameda_listing_html(page_html: str, *, location: str) -> list[tuple[str, str]]:
    location_slug = directory_lower_hyphen_slug(location)
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="(https://www\.jameda\.de/[^"#?]+)"', page_html):
        detail_url = html.unescape(match.group(1)).rstrip("/")
        path = detail_url.replace("https://www.jameda.de/", "")
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 2:
            continue
        if segments[0] in {"login", "registrierung-arzt", "social-connect", "opensearch"}:
            continue
        if len(segments) == 2 and segments[1] == location_slug:
            continue
        if detail_url in seen:
            continue
        seen.add(detail_url)
        listings.append((name_from_url_slug(segments[0]), detail_url))
    return listings


def parse_jameda_detail_name(page_html: str) -> str:
    match = re.search(r"<title>([^<|]+)", page_html)
    if not match:
        return ""
    title = html.unescape(match.group(1).strip())
    title = re.sub(r"\s+in\s+[^|]+$", "", title, flags=re.IGNORECASE)
    return title.strip(" -")


def parse_jameda_detail_website(page_html: str) -> str:
    for pattern in (
        r'data-patient-app-event-name="dp-doctor-website"[\s\S]{0,250}?href="(https?://[^"]+)"',
        r'data-avo-track="doctor-website-link"[\s\S]{0,250}?href="(https?://[^"]+)"',
        r'href="(https?://[^"]+)"[^>]*>[^<]*(?:Website|Webseite)',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if match:
            candidate = normalize_result_url(html.unescape(match.group(1)))
            if is_external_business_url(candidate):
                return candidate
    return ""


def parse_sanego_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in re.findall(r'href="(/Arzt/[^"]+/\d+-[^/]+/[^/]+/\d+-[^/]+/)"', page_html):
        detail_path = html.unescape(path)
        detail_url = f"https://www.sanego.de{detail_path}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        slug = detail_path.strip("/").split("/")[-1]
        listings.append((name_from_url_slug(slug), detail_url))
    return listings


def parse_sanego_detail_name(page_html: str) -> str:
    match = re.search(r"<title>([^<|]+)", page_html)
    if not match:
        return ""
    title = html.unescape(match.group(1).strip())
    title = re.sub(r"\s+in\s+[^|]+$", "", title, flags=re.IGNORECASE)
    title = re.sub(r",\s*[^,]+$", "", title)
    return title.strip()


def parse_sanego_detail_website(page_html: str) -> str:
    for pattern in (
        r'class="[^"]*website[^"]*"[\s\S]{0,300}?href="(https?://[^"]+)"',
        r'Homepage hinterlegen[\s\S]{0,500}?href="(https?://[^"]+)"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if match:
            candidate = normalize_result_url(html.unescape(match.group(1)))
            if is_external_business_url(candidate):
                return candidate
    return pick_best_embedded_business_url(page_html)


def parse_sanego_detail_phone(page_html: str) -> str:
    match = re.search(r'href="tel:([^"]+)"', page_html, re.IGNORECASE)
    return html.unescape(match.group(1)).strip() if match else ""


def parse_restaurantguru_listing_html(page_html: str, *, location: str) -> list[tuple[str, str]]:
    location_slug = directory_hyphen_slug(location)
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in re.findall(rf'href="(https://de\.restaurantguru\.com/[A-Za-z][^"#?]*-{re.escape(location_slug)})"', page_html):
        detail_url = html.unescape(path)
        if "/amp/" in detail_url or detail_url.endswith(f"/{location_slug}"):
            continue
        if detail_url in seen:
            continue
        seen.add(detail_url)
        slug = detail_url.rsplit("/", maxsplit=1)[-1]
        listings.append((name_from_url_slug(slug.removesuffix(f"-{location_slug}")), detail_url))
    return listings


def parse_restaurantguru_detail_name(page_html: str) -> str:
    match = re.search(r"<title>([^,<|]+)", page_html)
    if match:
        return html.unescape(match.group(1).strip())
    for block in extract_json_ld_blocks(page_html):
        if isinstance(block, dict) and block.get("@type") == "Restaurant":
            name = str(block.get("name", "")).strip()
            if name:
                return html.unescape(name)
    return ""


def parse_restaurantguru_detail_website(page_html: str) -> str:
    match = re.search(
        r'class="website"[\s\S]{0,400}?>\s*([^<\s][^<]{2,120}?)\s*</a>',
        page_html,
        re.IGNORECASE,
    )
    if match:
        domain = html.unescape(match.group(1).strip())
        if domain and "." in domain and "restaurantguru" not in domain.lower():
            candidate = normalize_result_url(domain if domain.startswith("http") else f"https://{domain}")
            if is_external_business_url(candidate):
                return candidate
    for pattern in (
        r'href="(https?://[^"]+)"[^>]*>[^<]*(?:Website|Webseite)',
        r'"url"\s*:\s*"(https?://[^"]+)"',
    ):
        match = re.search(pattern, page_html, re.IGNORECASE)
        if match:
            candidate = normalize_result_url(html.unescape(match.group(1)))
            if is_external_business_url(candidate):
                return candidate
    return ""


def parse_docfinder_listing_html(page_html: str) -> list[tuple[str, str]]:
    listings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for block in extract_json_ld_blocks(page_html):
        if not isinstance(block, dict) or block.get("@type") != "SearchResultsPage":
            continue
        main_entity = block.get("mainEntity")
        if not isinstance(main_entity, dict):
            continue
        for item in main_entity.get("itemListElement", []):
            if not isinstance(item, dict):
                continue
            detail_url = str(item.get("url", "")).strip()
            name = str(item.get("name", "")).strip()
            if not detail_url.startswith("https://www.docfinder.at/") or not name:
                continue
            if detail_url in seen:
                continue
            seen.add(detail_url)
            listings.append((html.unescape(name), detail_url))
    if listings:
        return listings
    for match in re.finditer(r'href="(https://www\.docfinder\.at/[^/]+/\d{4}-[^/]+/[^"#?]+)"', page_html):
        detail_url = html.unescape(match.group(1)).rstrip("/")
        if detail_url in seen:
            continue
        seen.add(detail_url)
        slug = detail_url.rsplit("/", maxsplit=1)[-1]
        listings.append((name_from_url_slug(slug), detail_url))
    return listings


def parse_docfinder_detail_name(page_html: str) -> str:
    match = re.search(r"<title>([^|<]+)", page_html)
    if match:
        return html.unescape(match.group(1).strip())
    return ""


def parse_docfinder_detail_website(page_html: str) -> str:
    candidates: list[str] = []
    for pattern in (
        r'data-t-action="homepage"[^>]*data-t-params="(https?://[^"]+)"',
        r'data-t-params="(https?://[^"]+)"[^>]*data-t-action="homepage"',
        r'ga-event-homepage[^>]*href="(https?://[^"]+)"',
    ):
        for match in re.finditer(pattern, page_html, re.IGNORECASE):
            candidate = normalize_result_url(html.unescape(match.group(1)))
            if candidate and is_external_business_url(candidate):
                candidates.append(candidate)
    for candidate in candidates:
        host = normalized_host(candidate).lower()
        if any(token in host for token in ("youcanbook.me", "maps.apple.com", "docfinder.at")):
            continue
        return candidate
    return ""


def parse_docfinder_detail_email(page_html: str) -> str:
    match = re.search(r'data-t-action="email"[^>]*data-t-params="([^"]+)"', page_html, re.IGNORECASE)
    if match:
        return normalize_directory_email(html.unescape(match.group(1)))
    return pick_directory_email(page_html)


def normalize_directory_website(value: str) -> str:
    candidate = normalize_result_url(value.strip())
    if candidate and is_external_business_url(candidate):
        return candidate
    if value.strip() and not value.strip().startswith("http"):
        candidate = normalize_result_url(f"https://{value.strip()}")
        if candidate and is_external_business_url(candidate):
            return candidate
    return ""


def parse_anwaltauskunft_json(page_text: str, *, source_url: str) -> list[DirectoryEntry]:
    try:
        payload = json.loads(page_text)
    except json.JSONDecodeError:
        return []
    entries: list[DirectoryEntry] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        organisation = item.get("organisation") if isinstance(item.get("organisation"), dict) else {}
        name = str(organisation.get("name") or "").strip()
        if not name:
            parts = [str(item.get("akademischer_titel", "")).strip(), str(item.get("vorname", "")).strip(), str(item.get("nachname", "")).strip()]
            name = " ".join(part for part in parts if part).strip()
        website = normalize_directory_website(
            str(item.get("internetadresse_1") or organisation.get("internetadresse_1") or "")
        )
        email = normalize_directory_email(str(item.get("e_mail_1") or organisation.get("e_mail_1") or ""))
        phone = str(item.get("telefon_1") or organisation.get("telefon_1") or "").strip()
        if not website and not email:
            continue
        profile_id = str(item.get("id", "")).strip()
        profile_url = f"https://anwaltauskunft.de/?profile={profile_id}" if profile_id else source_url
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=profile_url,
                snippet="Anwaltauskunft",
                email=email,
                phone=phone,
            )
        )
    return entries


def parse_steuerberater_company_filters(page_html: str) -> list[tuple[str, str]]:
    section = re.search(r'name="nachnameOrFirmennameFilter"[\s\S]*?</select>', page_html, re.IGNORECASE)
    if not section:
        return []
    listings: list[tuple[str, str]] = []
    for match in re.finditer(r'<option title="([^"]+)"\s+value="([^"]+)"', section.group(0)):
        value = match.group(2).strip()
        if not value:
            continue
        title = html.unescape(match.group(1).replace("<br>", " ").replace(" - ", ", "))
        listings.append((title, value))
    return listings


def parse_steuerberater_detail_link(page_html: str) -> str:
    match = re.search(r'href="(details/[A-F0-9-]+/\?lang=de)"', page_html, re.IGNORECASE)
    if not match:
        return ""
    return f"https://steuerberaterverzeichnis.berufs-org.de/{match.group(1)}"


def parse_steuerberater_detail_html(page_html: str, *, name: str, source_url: str) -> DirectoryEntry | None:
    email = pick_directory_email(page_html)
    website = ""
    for match in re.finditer(
        r"(?:href=\"|>|\s)(https?://[^\"\s<]+|www\.[a-z0-9.-]+\.[a-z]{2,})",
        page_html,
        re.IGNORECASE,
    ):
        candidate = normalize_directory_website(match.group(1))
        if candidate:
            website = candidate
            break
    if not website:
        bare = re.search(r"\b(www\.[a-z0-9.-]+\.[a-z]{2,})\b", page_html, re.IGNORECASE)
        if bare:
            website = normalize_directory_website(bare.group(1))
    if not website and not email:
        return None
    return DirectoryEntry(
        name=name,
        website=website,
        source_url=source_url,
        snippet="Steuerberaterverzeichnis",
        email=email,
    )


def enrich_detail_name_and_website(
    listings: list[tuple[str, str]],
    *,
    max_detail_fetches: int,
    parse_detail_name: Callable[[str], str],
    parse_detail_website: Callable[[str], str],
    source_name: str,
) -> list[DirectoryEntry]:
    entries: list[DirectoryEntry] = []
    max_detail_fetches = cap_directory_detail_fetches(max_detail_fetches)
    for fallback_name, detail_url in listings[:max_detail_fetches]:
        try:
            detail_html = fetch_directory_html(detail_url)
        except DirectoryFetchError:
            continue
        website = parse_detail_website(detail_html)
        email = pick_directory_email(detail_html)
        if not website and not email:
            continue
        name = parse_detail_name(detail_html) or fallback_name
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=detail_url,
                snippet=source_name,
                email=email,
            )
        )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries


def build_pitchbook_url(category: str, location: str) -> str:
    query = f"{category} {location}".strip()
    params = urllib.parse.urlencode({"q": query, "location": location.strip()})
    return f"https://pitchbook.com/profiles/search?{params}"


def build_indeed_url(category: str, location: str) -> str:
    params = urllib.parse.urlencode({"q": category, "l": location})
    return f"https://de.indeed.com/jobs?{params}"


def build_jameda_url(category: str, location: str) -> str:
    category_slug = directory_lower_hyphen_slug(category) or "arzt"
    location_slug = directory_lower_hyphen_slug(location) or "berlin"
    return f"https://www.jameda.de/{category_slug}/{location_slug}"


def build_sanego_url(category: str, location: str) -> str:
    location_seg = sanego_path_segment(location) or "Berlin"
    category_seg = sanego_path_segment(category) or "Arzt"
    return f"https://www.sanego.de/Arzt/{location_seg}/{category_seg}/"


def build_restaurantguru_url(category: str, location: str) -> str:
    location_slug = directory_hyphen_slug(location) or "Berlin"
    category = category.strip()
    if category:
        category_slug = directory_hyphen_slug(category)
        return f"https://de.restaurantguru.com/{category_slug}-{location_slug}"
    return f"https://de.restaurantguru.com/{location_slug}"


def build_docfinder_url(category: str, location: str) -> str:
    category_slug = directory_lower_hyphen_slug(category) or "arzt"
    location_slug = directory_lower_hyphen_slug(location) or "wien"
    return f"https://www.docfinder.at/suche/{category_slug}/{location_slug}"


def build_anwaltauskunft_url(category: str, location: str) -> str:
    params = urllib.parse.urlencode(
        {
            "location": location.strip() or "Berlin",
            "specialty": category.strip() or "Steuerrecht",
        }
    )
    return f"https://anwaltauskunft.de/wp-json/search/v1/query?{params}"


def build_steuerberater_url(category: str, location: str) -> str:
    _ = category
    return "https://steuerberaterverzeichnis.berufs-org.de/?lang=de"


def scrape_pitchbook(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_pitchbook_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_pitchbook_listing_html(page_html)
    return enrich_detail_name_and_website(
        listings,
        max_detail_fetches=limit,
        parse_detail_name=parse_pitchbook_detail_name,
        parse_detail_website=parse_pitchbook_detail_website,
        source_name="PitchBook",
    )[:limit]


def scrape_indeed(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_indeed_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_indeed_listing_html(page_html)
    return enrich_detail_name_and_website(
        listings,
        max_detail_fetches=limit,
        parse_detail_name=parse_indeed_detail_name,
        parse_detail_website=parse_indeed_detail_website,
        source_name="Indeed",
    )[:limit]


def scrape_jameda(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_jameda_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_jameda_listing_html(page_html, location=location)
    return enrich_detail_name_and_website(
        listings,
        max_detail_fetches=limit,
        parse_detail_name=parse_jameda_detail_name,
        parse_detail_website=parse_jameda_detail_website,
        source_name="Jameda",
    )[:limit]


def scrape_sanego(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_sanego_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_sanego_listing_html(page_html)
    max_detail_fetches = cap_directory_detail_fetches(limit)
    entries: list[DirectoryEntry] = []
    for fallback_name, detail_url in listings[:max_detail_fetches]:
        try:
            detail_html = fetch_directory_html(detail_url)
        except DirectoryFetchError:
            continue
        website = parse_sanego_detail_website(detail_html)
        email = pick_directory_email(detail_html)
        phone = parse_sanego_detail_phone(detail_html)
        if not website and not email:
            continue
        name = parse_sanego_detail_name(detail_html) or fallback_name
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=detail_url,
                snippet="Sanego",
                email=email,
                phone=phone,
            )
        )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries[:limit]


def scrape_restaurantguru(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_restaurantguru_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_restaurantguru_listing_html(page_html, location=location)
    return enrich_detail_name_and_website(
        listings,
        max_detail_fetches=limit,
        parse_detail_name=parse_restaurantguru_detail_name,
        parse_detail_website=parse_restaurantguru_detail_website,
        source_name="Restaurant Guru",
    )[:limit]


def scrape_docfinder(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_docfinder_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_docfinder_listing_html(page_html)
    max_detail_fetches = cap_directory_detail_fetches(limit)
    entries: list[DirectoryEntry] = []
    for fallback_name, detail_url in listings[:max_detail_fetches]:
        try:
            detail_html = fetch_directory_html(detail_url)
        except DirectoryFetchError:
            continue
        website = parse_docfinder_detail_website(detail_html)
        email = parse_docfinder_detail_email(detail_html)
        if not website and not email:
            continue
        name = parse_docfinder_detail_name(detail_html) or fallback_name
        entries.append(
            DirectoryEntry(
                name=name,
                website=website,
                source_url=detail_url,
                snippet="DocFinder",
                email=email,
            )
        )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries[:limit]


def scrape_anwaltauskunft(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_anwaltauskunft_url(category, location)
    page_text = fetch_directory_html(source_url)
    return parse_anwaltauskunft_json(page_text, source_url=source_url)[:limit]


def scrape_steuerberater(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    _ = category
    source_url = build_steuerberater_url(category, location)
    search_data = {
        "nachnameOrFirmenname": "",
        "ortFilter": location.strip() or "Berlin",
        "plzFilter": "",
    }
    listing_html = fetch_directory_post(source_url, search_data)
    companies = parse_steuerberater_company_filters(listing_html)
    max_detail_fetches = cap_directory_detail_fetches(limit)
    entries: list[DirectoryEntry] = []
    for company_name, company_filter in companies[:max_detail_fetches]:
        filtered_html = fetch_directory_post(
            source_url,
            {
                **search_data,
                "nachnameOrFirmennameFilter": company_filter,
            },
        )
        detail_link = parse_steuerberater_detail_link(filtered_html)
        if not detail_link:
            continue
        try:
            detail_html = fetch_directory_html(detail_link)
        except DirectoryFetchError:
            continue
        entry = parse_steuerberater_detail_html(detail_html, name=company_name, source_url=detail_link)
        if entry:
            entries.append(entry)
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return entries[:limit]


def enrich_directory_listing_details(
    entries: list[DirectoryEntry],
    *,
    max_detail_fetches: int,
    parse_detail_website: Callable[[str], str],
) -> list[DirectoryEntry]:
    enriched: list[DirectoryEntry] = []
    detail_fetches = 0
    max_detail_fetches = cap_directory_detail_fetches(max_detail_fetches)
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
        website = parse_detail_website(detail_html)
        email = pick_directory_email(detail_html, entry.email)
        if website:
            enriched.append(
                DirectoryEntry(
                    name=entry.name,
                    website=website,
                    source_url=entry.source_url,
                    snippet=entry.snippet,
                    email=email or entry.email,
                    phone=entry.phone,
                )
            )
        elif email or entry.email:
            enriched.append(
                DirectoryEntry(
                    name=entry.name,
                    website="",
                    source_url=entry.source_url,
                    snippet=entry.snippet,
                    email=email or entry.email,
                    phone=entry.phone,
                )
            )
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return enriched


def scrape_cylex(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = f"https://www.cylex.de/suche/{slug_for_directory_path(category)}/{slug_for_directory_path(location)}"
    page_html = fetch_directory_html(source_url)
    entries = parse_json_ld_directory_entries(page_html, source_url=source_url, source_name="Cylex")
    return enrich_directory_listing_details(
        entries,
        max_detail_fetches=limit,
        parse_detail_website=parse_cylex_detail_website,
    )[:limit]


def scrape_hotfrog(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = f"https://www.hotfrog.de/search/{slug_for_directory_path(location)}/{slug_for_directory_path(category)}"
    page_html = fetch_directory_html(source_url)
    entries: list[DirectoryEntry] = []
    seen: set[str] = set()
    for website in parse_hotfrog_redirect_websites(page_html):
        key = website.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            DirectoryEntry(
                name=website,
                website=website,
                source_url=source_url,
                snippet="Hotfrog",
            )
        )
        if len(entries) >= limit:
            break
    return entries


def scrape_werkenntdenbesten(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    category_slug = slug_for_directory_path(category)
    location_slug = slug_for_directory_path(location)
    source_url = f"https://www.werkenntdenbesten.de/{category_slug}/{location_slug}/"
    page_html = fetch_directory_html(source_url)
    entries = parse_json_ld_directory_entries(page_html, source_url=source_url, source_name="Wer kennt den BESTEN")
    return enrich_directory_listing_details(
        entries,
        max_detail_fetches=limit,
        parse_detail_website=parse_werkenntdenbesten_detail_website,
    )[:limit]


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


def scrape_goyellow(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    listings: list[tuple[str, str]] = []
    page = 1
    while len(listings) < limit and page <= 3:
        source_url = build_goyellow_url(category, location, page)
        page_html = fetch_directory_html(source_url)
        page_listings = parse_goyellow_listing_html(page_html)
        if not page_listings:
            break
        listings.extend(page_listings)
        if f"/seite-{page + 1}" not in page_html:
            break
        page += 1
        time.sleep(DIRECTORY_REQUEST_DELAY_SECONDS)
    return enrich_named_listing_details(
        listings,
        max_detail_fetches=limit,
        parse_detail_website=parse_goyellow_detail_website,
        source_name="GoYellow",
    )[:limit]


def scrape_kompass(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_kompass_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_kompass_listing_html(page_html)
    return enrich_named_listing_details(
        listings,
        max_detail_fetches=limit,
        parse_detail_website=parse_kompass_detail_website,
        source_name="Kompass",
    )[:limit]


def scrape_europages(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_europages_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_europages_listing_html(page_html)
    return enrich_named_listing_details(
        listings,
        max_detail_fetches=limit,
        parse_detail_website=parse_europages_detail_website,
        source_name="Europages",
    )[:limit]


def scrape_yelp(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_yelp_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_yelp_listing_html(page_html)
    return enrich_named_listing_details(
        listings,
        max_detail_fetches=limit,
        parse_detail_website=parse_yelp_detail_website,
        source_name="Yelp",
    )[:limit]


def scrape_manta(category: str, location: str, limit: int) -> list[DirectoryEntry]:
    source_url = build_manta_url(category, location)
    page_html = fetch_directory_html(source_url)
    listings = parse_manta_listing_html(page_html)
    return enrich_named_listing_details(
        listings,
        max_detail_fetches=limit,
        parse_detail_website=parse_manta_detail_website,
        source_name="Manta",
    )[:limit]


def _directory_scraper_map() -> dict[str, callable]:
    return {
        "gelbeseiten": scrape_gelbeseiten,
        "das_oertliche": scrape_dasoertliche,
        "telefonbuch": scrape_telefonbuch,
        "11880": scrape_11880,
        "auskunft": scrape_auskunft,
        "cylex": scrape_cylex,
        "hotfrog": scrape_hotfrog,
        "werkenntdenbesten": scrape_werkenntdenbesten,
        "goyellow": scrape_goyellow,
        "kompass": scrape_kompass,
        "europages": scrape_europages,
        "yelp": scrape_yelp,
        "manta": scrape_manta,
        "pitchbook": scrape_pitchbook,
        "indeed": scrape_indeed,
        "jameda": scrape_jameda,
        "sanego": scrape_sanego,
        "restaurantguru": scrape_restaurantguru,
        "docfinder": scrape_docfinder,
        "anwaltauskunft": scrape_anwaltauskunft,
        "steuerberater": scrape_steuerberater,
    }


def build_directory_source_registry():
    from .directory_registry import build_directory_source_registry as build_registry

    return build_registry(_directory_scraper_map())


def get_directory_scrapers(enabled_source_ids: set[str] | None = None) -> tuple[tuple[str, callable], ...]:
    from .directory_registry import resolve_active_scrapers

    return resolve_active_scrapers(build_directory_source_registry(), enabled_source_ids)


def default_directory_source_ids() -> set[str]:
    from .directory_registry import default_enabled_directory_source_ids

    return default_enabled_directory_source_ids(build_directory_source_registry())


# Backward compatibility for tests/imports
DIRECTORY_SCRAPERS = get_directory_scrapers()
