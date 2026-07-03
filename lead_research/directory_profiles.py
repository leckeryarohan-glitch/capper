from __future__ import annotations

import re
import unicodedata

from .directory_registry import default_enabled_directory_source_ids
from .directories import build_directory_source_registry

_DEFAULT_REGISTRY = None


def _registry():
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = build_directory_source_registry()
    return _DEFAULT_REGISTRY


def normalize_category_key(category: str) -> str:
    normalized = unicodedata.normalize("NFKD", category.strip().casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("ß", "ss")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


DEFAULT_MASS_DIRECTORY_SOURCES: tuple[str, ...] = (
    "gelbeseiten",
    "herold",
    "11880",
    "das_oertliche",
    "telefonbuch",
    "auskunft",
    "cylex",
    "goyellow",
    "wko",
)

CATEGORY_SOURCE_PROFILES: dict[str, tuple[str, ...]] = {
    "steuerberater": (
        "steuerberater",
        "branchen_steuerberater",
        "gelbeseiten",
        "herold",
        "11880",
        "wko",
        "wlw",
        "europages",
        "kompass",
    ),
    "restaurant": (
        "branchen_restaurants",
        "restaurantguru",
        "golocal",
        "gelbeseiten",
        "herold",
        "yelp",
        "goyellow",
        "werkenntdenbesten",
        "hotfrog",
    ),
    "hotel": (
        "branchen_hotels",
        "golocal",
        "gelbeseiten",
        "herold",
        "yelp",
        "11880",
        "hotfrog",
    ),
    "arzt": (
        "jameda",
        "sanego",
        "docfinder",
        "herold",
        "gelbeseiten",
        "11880",
    ),
    "aerzte": (
        "jameda",
        "sanego",
        "docfinder",
        "herold",
        "gelbeseiten",
        "11880",
    ),
    "zahnarzt": (
        "branchen_zahnaerzte",
        "docfinder_zahn",
        "jameda_zahn",
        "jameda",
        "sanego",
        "herold",
    ),
    "physiotherapeut": (
        "branchen_physiotherapeuten",
        "jameda_physio",
        "jameda",
        "sanego",
        "gelbeseiten",
        "herold",
    ),
    "anwalt": (
        "anwaltauskunft",
        "branchen_anwaelte",
        "gelbeseiten",
        "herold",
        "11880",
        "wko",
    ),
    "friseur": (
        "branchen_friseure",
        "treatwell",
        "golocal",
        "gelbeseiten",
        "herold",
        "yelp",
    ),
    "fitnessstudio": (
        "branchen_fitnessstudios",
        "treatwell",
        "golocal",
        "gelbeseiten",
        "herold",
    ),
    "handwerker": (
        "gelbeseiten",
        "wlw",
        "europages",
        "lieferanten_kompass",
        "kompass",
        "herold",
        "11880",
        "wko",
        "werkenntdenbesten",
    ),
    "elektriker": (
        "branchen_elektriker",
        "gelbeseiten",
        "wlw",
        "europages",
        "lieferanten_kompass",
        "herold",
        "wko",
    ),
    "installateur": (
        "branchen_instalateure",
        "gelbeseiten",
        "wlw",
        "europages",
        "herold",
        "wko",
    ),
    "maler": (
        "branchen_maler",
        "gelbeseiten",
        "wlw",
        "herold",
        "wko",
    ),
    "dachdecker": (
        "branchen_dachdecker",
        "gelbeseiten",
        "wlw",
        "herold",
        "wko",
    ),
    "bauunternehmen": (
        "branchen_bauunternehmen",
        "gelbeseiten",
        "wlw",
        "europages",
        "lieferanten_kompass",
        "herold",
        "wko",
    ),
    "werkstatt": (
        "branchen_werkstaetten",
        "gelbeseiten",
        "herold",
        "11880",
        "goyellow",
    ),
    "autohaus": (
        "branchen_autohaeuser",
        "gelbeseiten",
        "herold",
        "11880",
        "goyellow",
    ),
    "immobilienmakler": (
        "branchen_immobilienmakler",
        "gelbeseiten",
        "herold",
        "golocal",
        "11880",
    ),
    "versicherung": (
        "branchen_versicherungen",
        "gelbeseiten",
        "herold",
        "11880",
        "wko",
    ),
    "lieferant": (
        "wlw",
        "lieferanten_europages",
        "lieferanten_kompass",
        "alibaba",
        "india_mart",
        "made_in_china",
        "kompass",
        "europages",
    ),
    "hersteller": (
        "wlw",
        "lieferanten_europages",
        "lieferanten_kompass",
        "kompass",
        "europages",
        "gelbeseiten",
    ),
    "versand": (
        "wlw",
        "lieferanten_europages",
        "lieferanten_kompass",
        "kompass",
        "europages",
        "gelbeseiten",
        "herold",
        "11880",
        "das_oertliche",
        "telefonbuch",
        "auskunft",
        "goyellow",
        "wko",
        "indeed",
        "stepstone",
        "arbeitsagentur",
    ),
    "logistik": (
        "wlw",
        "lieferanten_europages",
        "lieferanten_kompass",
        "kompass",
        "europages",
        "gelbeseiten",
        "herold",
        "11880",
        "das_oertliche",
        "telefonbuch",
        "auskunft",
        "goyellow",
        "wko",
        "indeed",
        "stepstone",
        "arbeitsagentur",
    ),
    "spedition": (
        "wlw",
        "lieferanten_europages",
        "lieferanten_kompass",
        "kompass",
        "europages",
        "gelbeseiten",
        "herold",
        "11880",
        "das_oertliche",
        "telefonbuch",
        "auskunft",
        "goyellow",
        "wko",
    ),
}


def _implemented_source_ids() -> set[str]:
    return default_enabled_directory_source_ids(_registry())


def _filter_valid_source_ids(source_ids: tuple[str, ...] | set[str]) -> set[str]:
    valid = _implemented_source_ids()
    return {source_id for source_id in source_ids if source_id in valid}


def match_category_profile_key(category: str) -> str | None:
    key = normalize_category_key(category)
    if not key:
        return None
    if key in CATEGORY_SOURCE_PROFILES:
        return key
    for profile_key in sorted(CATEGORY_SOURCE_PROFILES, key=len, reverse=True):
        if profile_key in key:
            return profile_key
    tokens = key.split()
    for profile_key in CATEGORY_SOURCE_PROFILES:
        profile_tokens = profile_key.split()
        if any(token in profile_tokens for token in tokens):
            return profile_key
    return None


def resolve_mass_directory_sources(category: str) -> set[str]:
    profile_key = match_category_profile_key(category)
    if profile_key is not None:
        sources = _filter_valid_source_ids(CATEGORY_SOURCE_PROFILES[profile_key])
        if sources:
            return sources
    return _filter_valid_source_ids(DEFAULT_MASS_DIRECTORY_SOURCES)


def resolve_category_directory_sources(
    category: str,
    selected: set[str] | None = None,
) -> set[str]:
    """Return directory source IDs for a category, optionally intersected with GUI selection."""
    profile = resolve_mass_directory_sources(category)
    if selected is None:
        return profile
    if match_category_profile_key(category):
        filtered = selected & profile
        return filtered if filtered else profile
    return selected
