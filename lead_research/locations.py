from __future__ import annotations

import json
import urllib.request
from functools import lru_cache
from pathlib import Path

from .http import format_request_error, read_response_text, urlopen

MIN_CITY_POPULATION = 5000

SUPPORTED_COUNTRIES: dict[str, str] = {
    "DE": "Deutschland",
    "AT": "Österreich",
}

DEFAULT_COUNTRIES: tuple[str, ...] = ("DE",)

ZENROWS_LOCALE: dict[str, tuple[str, str]] = {
    "DE": ("de", ".de"),
    "AT": ("at", ".at"),
}

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

CACHE_PATH = Path(__file__).resolve().parent / "data" / "osm_cities_cache.json"

# Cap web-search city expansion so providers like ZenRows stay practical.
WEB_SEARCH_CITIES_PER_COUNTRY = 40


def parse_countries(value: str | None) -> tuple[str, ...]:
    if not value or not str(value).strip():
        return DEFAULT_COUNTRIES
    codes: list[str] = []
    for part in str(value).replace(";", ",").split(","):
        code = part.strip().upper()
        if code in SUPPORTED_COUNTRIES and code not in codes:
            codes.append(code)
    return tuple(codes) if codes else DEFAULT_COUNTRIES


def parse_population(raw: object) -> int | None:
    if raw is None:
        return None
    cleaned = str(raw).strip().replace(".", "").replace(",", "").replace(" ", "")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _load_cache_file() -> dict[str, list[dict[str, object]]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache_file(data: dict[str, list[dict[str, object]]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_cities_from_overpass(country_code: str) -> list[dict[str, object]]:
    query = (
        "[out:json][timeout:120];\n"
        f'area["ISO3166-1"="{country_code}"][admin_level=2]->.country;\n'
        "(\n"
        '  node["place"~"city|town"]["population"]["name"](area.country);\n'
        '  way["place"~"city|town"]["population"]["name"](area.country);\n'
        '  relation["place"~"city|town"]["population"]["name"](area.country);\n'
        ");\n"
        "out tags;\n"
    )
    failures: list[str] = []
    for endpoint in OVERPASS_ENDPOINTS:
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
            with urlopen(request, timeout=120) as response:
                payload = json.loads(read_response_text(response))
        except OSError as exc:
            failures.append(f"{endpoint}: {format_request_error(exc)}")
            continue

        best: dict[str, int] = {}
        for element in payload.get("elements", []):
            tags = element.get("tags", {})
            name = str(tags.get("name", "")).strip()
            population = parse_population(tags.get("population"))
            if not name or population is None or population < MIN_CITY_POPULATION:
                continue
            if name not in best or population > best[name]:
                best[name] = population

        cities = [
            {"name": name, "population": population}
            for name, population in sorted(best.items(), key=lambda item: (-item[1], item[0]))
        ]
        return cities

    raise RuntimeError("; ".join(failures) or f"Overpass city lookup failed for {country_code}")


@lru_cache(maxsize=8)
def cities_for_country(country_code: str, *, refresh: bool = False) -> tuple[dict[str, object], ...]:
    normalized = country_code.strip().upper()
    if normalized not in SUPPORTED_COUNTRIES:
        return ()

    cache = _load_cache_file()
    if not refresh and normalized in cache:
        return tuple(cache[normalized])

    cities = _fetch_cities_from_overpass(normalized)
    cache[normalized] = cities
    _save_cache_file(cache)
    cities_for_country.cache_clear()
    return tuple(cities)


def cities_for_countries(countries: tuple[str, ...]) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for country_code in countries:
        for city in cities_for_country(country_code):
            name = str(city.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
    return tuple(names)


def top_cities_for_web_search(countries: tuple[str, ...], per_country: int = WEB_SEARCH_CITIES_PER_COUNTRY) -> list[tuple[str, str]]:
    """Return (city_name, country_code) pairs for web-search expansion."""
    pairs: list[tuple[str, str]] = []
    for country_code in countries:
        if country_code not in SUPPORTED_COUNTRIES:
            continue
        for city in cities_for_country(country_code)[: max(per_country, 0)]:
            name = str(city.get("name", "")).strip()
            if name:
                pairs.append((name, country_code))
    return pairs


def country_label(country_code: str) -> str:
    return SUPPORTED_COUNTRIES.get(country_code.upper(), country_code)
