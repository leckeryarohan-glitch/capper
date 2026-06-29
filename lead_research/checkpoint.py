from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import ConsentStatus, Lead, SearchResult, search_result_crawl_key


CHECKPOINT_VERSION = 1


@dataclass
class DiscoveryCheckpoint:
    """Persisted state for resuming long discover runs (search + crawl)."""

    version: int = CHECKPOINT_VERSION
    config: dict[str, object] = field(default_factory=dict)
    search_complete: bool = False
    search_results: list[dict[str, str]] = field(default_factory=list)
    zenrows_completed_plans: list[str] = field(default_factory=list)
    directory_completed_locations: list[str] = field(default_factory=list)
    directory_partial_results: list[dict[str, str]] = field(default_factory=list)
    directory_seen_keys: list[str] = field(default_factory=list)
    crawled_urls: list[str] = field(default_factory=list)
    leads: list[dict[str, object]] = field(default_factory=list)

    @property
    def crawled_url_set(self) -> set[str]:
        return {url.lower().rstrip("/") for url in self.crawled_urls}

    def search_result_objects(self) -> list[SearchResult]:
        return [search_result_from_dict(item) for item in self.search_results]

    def directory_search_result_objects(self) -> list[SearchResult]:
        return [search_result_from_dict(item) for item in self.directory_partial_results]

    def lead_objects(self) -> list[Lead]:
        return [lead_from_dict(item) for item in self.leads]


def config_fingerprint(
    *,
    category: str,
    location: str,
    countries: tuple[str, ...],
    limit: int,
    max_leads: int,
    dedupe_by: str,
) -> dict[str, object]:
    return {
        "category": category,
        "location": location,
        "countries": list(countries),
        "limit": limit,
        "max_leads": max_leads,
        "dedupe_by": dedupe_by,
    }


def validate_checkpoint_config(checkpoint: DiscoveryCheckpoint, expected: dict[str, object]) -> None:
    stored = checkpoint.config
    mismatches = [
        key
        for key, value in expected.items()
        if stored.get(key) != value
    ]
    if mismatches:
        labels = ", ".join(mismatches)
        raise ValueError(
            "Checkpoint passt nicht zur aktuellen Suche "
            f"(abweichend: {labels}). Gleiche Kategorie, Ort, Laender und Limits verwenden "
            "oder einen neuen Checkpoint ohne --resume starten."
        )


def checkpoint_progress_summary(checkpoint: DiscoveryCheckpoint) -> str:
    parts = [
        f"{len(checkpoint.search_results)} Websites",
        f"{len(checkpoint.crawled_urls)} gecrawlt",
        f"{len(checkpoint.leads)} Leads",
    ]
    if checkpoint.directory_completed_locations and not checkpoint.search_complete:
        parts.append(f"{len(checkpoint.directory_completed_locations)} Branchenorte")
    return ", ".join(parts)


def lead_to_dict(lead: Lead) -> dict:
    item = asdict(lead)
    item["consent_status"] = lead.consent_status.value
    return item


def lead_from_dict(item: dict) -> Lead:
    restored = dict(item)
    restored["consent_status"] = ConsentStatus(restored.get("consent_status", ConsentStatus.BUSINESS_PUBLIC))
    restored["notes"] = list(restored.get("notes", []))
    return Lead(**restored)


def search_result_to_dict(result: SearchResult) -> dict[str, str]:
    payload = {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
    }
    if result.directory_email:
        payload["directory_email"] = result.directory_email
    if result.directory_phone:
        payload["directory_phone"] = result.directory_phone
    if result.directory_source_url:
        payload["directory_source_url"] = result.directory_source_url
    return payload


def search_result_from_dict(item: dict[str, str]) -> SearchResult:
    return SearchResult(
        title=item.get("title", ""),
        url=item.get("url", ""),
        snippet=item.get("snippet", ""),
        directory_email=item.get("directory_email", ""),
        directory_phone=item.get("directory_phone", ""),
        directory_source_url=item.get("directory_source_url", ""),
    )


def new_discovery_checkpoint(
    *,
    category: str,
    location: str,
    countries: tuple[str, ...],
    limit: int,
    max_leads: int,
    dedupe_by: str,
) -> DiscoveryCheckpoint:
    return DiscoveryCheckpoint(
        config=config_fingerprint(
            category=category,
            location=location,
            countries=countries,
            limit=limit,
            max_leads=max_leads,
            dedupe_by=dedupe_by,
        )
    )


def load_discovery_checkpoint(path: Path | None) -> DiscoveryCheckpoint | None:
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Ungueltiger Checkpoint: {path}")
    return DiscoveryCheckpoint(
        version=int(payload.get("version", CHECKPOINT_VERSION)),
        config=dict(payload.get("config", {})),
        search_complete=bool(payload.get("search_complete", False)),
        search_results=list(payload.get("search_results", [])),
        zenrows_completed_plans=list(payload.get("zenrows_completed_plans", [])),
        directory_completed_locations=list(payload.get("directory_completed_locations", [])),
        directory_partial_results=list(payload.get("directory_partial_results", [])),
        directory_seen_keys=list(payload.get("directory_seen_keys", [])),
        crawled_urls=list(payload.get("crawled_urls", [])),
        leads=list(payload.get("leads", [])),
    )


def save_discovery_checkpoint(path: Path | None, checkpoint: DiscoveryCheckpoint) -> None:
    if path is None:
        return
    write_discovery_checkpoint_payload(path, checkpoint_to_payload(checkpoint))


def checkpoint_to_payload(checkpoint: DiscoveryCheckpoint) -> dict[str, object]:
    return {
        "version": checkpoint.version,
        "config": checkpoint.config,
        "search_complete": checkpoint.search_complete,
        "search_results": list(checkpoint.search_results),
        "zenrows_completed_plans": list(checkpoint.zenrows_completed_plans),
        "directory_completed_locations": list(checkpoint.directory_completed_locations),
        "directory_partial_results": list(checkpoint.directory_partial_results),
        "directory_seen_keys": list(checkpoint.directory_seen_keys),
        "crawled_urls": list(checkpoint.crawled_urls),
        "leads": list(checkpoint.leads),
    }


def write_discovery_checkpoint_payload(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_lead(checkpoint: DiscoveryCheckpoint, lead: Lead) -> None:
    checkpoint.leads.append(lead_to_dict(lead))


def mark_url_crawled(checkpoint: DiscoveryCheckpoint, url: str) -> None:
    normalized = url.lower().rstrip("/")
    if normalized not in checkpoint.crawled_url_set:
        checkpoint.crawled_urls.append(url)


def mark_result_crawled(checkpoint: DiscoveryCheckpoint, result: SearchResult) -> None:
    key = search_result_crawl_key(result)
    if key not in checkpoint.crawled_url_set:
        checkpoint.crawled_urls.append(key)


def update_search_results(checkpoint: DiscoveryCheckpoint, results: list[SearchResult]) -> None:
    checkpoint.search_results = [search_result_to_dict(result) for result in results]


def update_directory_search_progress(
    checkpoint: DiscoveryCheckpoint,
    *,
    results: list[SearchResult],
    seen: set[str],
    completed_locations: set[str],
) -> None:
    checkpoint.directory_partial_results = [search_result_to_dict(result) for result in results]
    checkpoint.directory_seen_keys = sorted(seen)
    checkpoint.directory_completed_locations = sorted(completed_locations)


def clear_directory_search_progress(checkpoint: DiscoveryCheckpoint) -> None:
    checkpoint.directory_partial_results = []
    checkpoint.directory_seen_keys = []
    checkpoint.directory_completed_locations = []
