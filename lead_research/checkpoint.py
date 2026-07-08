from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import ConsentStatus, Lead, SearchResult, search_result_crawl_key


CHECKPOINT_VERSION = 1
LARGE_CHECKPOINT_RESULT_COUNT = 5000
CHECKPOINT_SAVE_MIN_SECONDS = 90


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
    _crawled_url_set: set[str] | None = field(default=None, repr=False, compare=False)

    @property
    def crawled_url_set(self) -> set[str]:
        if self._crawled_url_set is None:
            self._crawled_url_set = {url.lower().rstrip("/") for url in self.crawled_urls}
        return self._crawled_url_set

    def remember_crawled_url(self, url: str) -> None:
        normalized = url.lower().rstrip("/")
        if normalized in self.crawled_url_set:
            return
        self.crawled_urls.append(url)
        self.crawled_url_set.add(normalized)

    def search_result_objects(self) -> list[SearchResult]:
        return [search_result_from_dict(item) for item in self.search_results]

    def directory_search_result_objects(self) -> list[SearchResult]:
        return [search_result_from_dict(item) for item in self.directory_partial_results]

    def lead_objects(self) -> list[Lead]:
        return [lead_from_dict(item) for item in self.leads]


def search_result_crawl_key_from_dict(item: dict[str, str]) -> str:
    url = str(item.get("url", "")).strip()
    if url:
        return url.lower().rstrip("/")
    email = str(item.get("directory_email", "")).strip().lower()
    if email:
        return f"directory-email:{email}"
    source = str(item.get("directory_source_url", "")).strip().lower().rstrip("/")
    if source:
        return f"directory-source:{source}"
    title = str(item.get("title", "")).strip().lower()
    return f"directory-title:{title}" if title else "directory-empty"


def checkpoint_search_results_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}-search{path.suffix}")


def checkpoint_backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


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


def checkpoint_progress_summary(
    checkpoint: DiscoveryCheckpoint,
    *,
    search_results: int | None = None,
    crawled_urls: int | None = None,
    leads: int | None = None,
    directory_locations: int | None = None,
) -> str:
    parts = [
        f"{search_results if search_results is not None else len(checkpoint.search_results)} Websites",
        f"{crawled_urls if crawled_urls is not None else len(checkpoint.crawled_urls)} gecrawlt",
        f"{leads if leads is not None else len(checkpoint.leads)} Leads",
    ]
    completed_count = (
        directory_locations
        if directory_locations is not None
        else len(checkpoint.directory_completed_locations)
    )
    if completed_count and not checkpoint.search_complete:
        completed = checkpoint.directory_completed_locations
        if directory_locations is not None and not completed:
            parts.append(f"{completed_count} Branchenorte")
        elif len(completed) <= 3:
            parts.append(f"{len(completed)} Branchenorte ({', '.join(completed)})")
        else:
            parts.append(f"{len(completed)} Branchenorte")
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


def load_discovery_checkpoint(
    path: Path | None,
    *,
    on_status: Callable[[str], None] | None = None,
) -> DiscoveryCheckpoint | None:
    if path is None:
        return None
    if on_status:
        on_status(f"Lese Checkpoint-Datei {path.name} ...")
    payload = _read_checkpoint_payload(path)
    if payload is None:
        return None
    search_results = list(payload.get("search_results", []))
    if not search_results and payload.get("search_results_external"):
        sidecar = checkpoint_search_results_path(path)
        if sidecar.exists():
            if on_status:
                size_mb = sidecar.stat().st_size / (1024 * 1024)
                on_status(f"Lese Suchergebnisse aus {sidecar.name} ({size_mb:.0f} MB) ...")
            sidecar_payload = _read_checkpoint_payload(sidecar)
            if isinstance(sidecar_payload, dict):
                search_results = list(sidecar_payload.get("search_results", []))
    checkpoint = DiscoveryCheckpoint(
        version=int(payload.get("version", CHECKPOINT_VERSION)),
        config=dict(payload.get("config", {})),
        search_complete=bool(payload.get("search_complete", False)),
        search_results=search_results,
        zenrows_completed_plans=list(payload.get("zenrows_completed_plans", [])),
        directory_completed_locations=list(payload.get("directory_completed_locations", [])),
        directory_partial_results=list(payload.get("directory_partial_results", [])),
        directory_seen_keys=list(payload.get("directory_seen_keys", [])),
        crawled_urls=list(payload.get("crawled_urls", [])),
        leads=list(payload.get("leads", [])),
    )
    checkpoint.crawled_url_set  # build cache once after load
    return checkpoint


def _read_checkpoint_payload(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    candidates = [path, path.with_suffix(path.suffix + ".tmp"), checkpoint_backup_path(path)]
    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(payload, dict):
            raise ValueError(f"Ungueltiger Checkpoint: {candidate}")
        return payload
    if last_error is not None:
        raise ValueError(
            f"Checkpoint beschädigt oder unvollständig: {path}. "
            "Falls vorhanden capper-checkpoint.json.bak oder .tmp prüfen."
        ) from last_error
    return None


def _find_json_value_start(text: str, key: str) -> int:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*', text)
    if not match:
        return -1
    return match.end()


def _find_json_array_start(text: str, key: str) -> int:
    for match in re.finditer(rf'"{re.escape(key)}"\s*:\s*', text):
        pos = match.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos < len(text) and text[pos] == "[":
            return pos
    return -1


def _parse_json_value_at(text: str, start: int) -> tuple[object, int]:
    decoder = json.JSONDecoder()
    while start < len(text) and text[start].isspace():
        start += 1
    return decoder.raw_decode(text, start)


def _count_json_array_elements(text: str, key: str) -> int:
    """Count top-level JSON array elements without deserializing objects."""
    pos = _find_json_array_start(text, key)
    if pos < 0:
        return 0
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != "[":
        return 0
    pos += 1
    count = 0
    depth = 0
    in_string = False
    escape = False
    expecting_element = True
    while pos < len(text):
        ch = text[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            pos += 1
            continue
        if ch == '"':
            in_string = True
            if depth == 0 and expecting_element:
                count += 1
                expecting_element = False
        elif ch == "{":
            if depth == 0 and expecting_element:
                count += 1
                expecting_element = False
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "]" and depth == 0:
            break
        elif ch == "," and depth == 0:
            expecting_element = True
        pos += 1
    return count


def _parse_json_string_array(text: str, key: str, *, max_items: int = 5) -> list[str]:
    pos = _find_json_array_start(text, key)
    if pos < 0:
        return []
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != "[":
        return []
    pos += 1
    values: list[str] = []
    in_string = False
    escape = False
    current: list[str] = []
    depth = 0
    while pos < len(text) and len(values) < max_items:
        ch = text[pos]
        if in_string:
            if escape:
                current.append(ch)
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                if depth == 0:
                    values.append("".join(current))
                    current = []
            else:
                current.append(ch)
            pos += 1
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            if depth == 0:
                break
            depth -= 1
        pos += 1
    return values


def load_checkpoint_gui_metadata(path: Path | None) -> dict[str, object] | None:
    """Load checkpoint settings for the GUI without deserializing large arrays."""
    if path is None or not path.exists():
        return None

    text = path.read_text(encoding="utf-8")
    config_pos = _find_json_value_start(text, "config")
    if config_pos < 0:
        return None
    config_value, _ = _parse_json_value_at(text, config_pos)
    if not isinstance(config_value, dict):
        return None
    config = dict(config_value)

    stats_pos = _find_json_value_start(text, "stats")
    if stats_pos >= 0:
        stats_value, _ = _parse_json_value_at(text, stats_pos)
        if isinstance(stats_value, dict):
            search_count = int(stats_value.get("search_results", 0))
            crawled_count = int(stats_value.get("crawled_urls", 0))
            leads_count = int(stats_value.get("leads", 0))
            directory_count = int(stats_value.get("directory_completed_locations", 0))
        else:
            search_count = _count_json_array_elements(text, "search_results")
            crawled_count = _count_json_array_elements(text, "crawled_urls")
            leads_count = _count_json_array_elements(text, "leads")
            directory_count = _count_json_array_elements(text, "directory_completed_locations")
    else:
        search_count = _count_json_array_elements(text, "search_results")
        crawled_count = _count_json_array_elements(text, "crawled_urls")
        leads_count = _count_json_array_elements(text, "leads")
        directory_count = _count_json_array_elements(text, "directory_completed_locations")

    search_complete = bool(re.search(r'"search_complete"\s*:\s*true', text))
    directory_locations = _parse_json_string_array(text, "directory_completed_locations")
    summary_checkpoint = DiscoveryCheckpoint(
        config=config,
        search_complete=search_complete,
        directory_completed_locations=directory_locations,
    )
    progress_summary = checkpoint_progress_summary(
        summary_checkpoint,
        search_results=search_count,
        crawled_urls=crawled_count,
        leads=leads_count,
        directory_locations=directory_count if not directory_locations else None,
    )

    gui_settings = dict(config.get("gui_settings", {}))
    countries = list(config.get("countries", gui_settings.get("countries", [])))
    return {
        "category": str(config.get("category", gui_settings.get("category", ""))),
        "location": str(config.get("location", gui_settings.get("location", ""))),
        "countries": countries,
        "limit": int(config.get("limit", gui_settings.get("limit", 0))),
        "max_leads": int(config.get("max_leads", gui_settings.get("max_leads", 0))),
        "workers": str(gui_settings.get("workers", "")),
        "directory_parallel": str(gui_settings.get("directory_parallel", "")),
        "directory_detail_parallel": str(gui_settings.get("directory_detail_parallel", "")),
        "use_osm": bool(gui_settings.get("use_osm", True)),
        "use_duckduckgo": bool(gui_settings.get("use_duckduckgo", True)),
        "use_directories": bool(gui_settings.get("use_directories", True)),
        "use_zenrows_google": bool(gui_settings.get("use_zenrows_google", True)),
        "use_google_maps": bool(gui_settings.get("use_google_maps", True)),
        "use_serpapi": bool(gui_settings.get("use_serpapi", False)),
        "directory_sources": list(gui_settings.get("directory_sources", [])),
        "progress_summary": progress_summary,
    }


def checkpoint_uses_sidecar(checkpoint: DiscoveryCheckpoint) -> bool:
    return (
        checkpoint.search_complete
        and len(checkpoint.search_results) >= LARGE_CHECKPOINT_RESULT_COUNT
    )


def save_discovery_checkpoint(
    path: Path | None,
    checkpoint: DiscoveryCheckpoint,
    *,
    incremental: bool = False,
) -> None:
    if path is None:
        return
    payload = checkpoint_to_payload(checkpoint, path, incremental=incremental)
    write_discovery_checkpoint_payload(
        path,
        payload,
        backup_source=path if path.exists() else None,
        create_backup=not incremental,
    )
    if (
        not incremental
        and checkpoint.search_complete
        and len(checkpoint.search_results) >= LARGE_CHECKPOINT_RESULT_COUNT
    ):
        write_search_results_sidecar(path, checkpoint.search_results)


def checkpoint_save_interval(checkpoint: DiscoveryCheckpoint) -> int:
    size = max(len(checkpoint.search_results), len(checkpoint.crawled_urls), len(checkpoint.leads))
    if size >= LARGE_CHECKPOINT_RESULT_COUNT:
        return 100
    if size >= 1000:
        return 50
    return 10


def write_search_results_sidecar(path: Path, search_results: list[dict[str, str]]) -> None:
    sidecar = checkpoint_search_results_path(path)
    payload = {"version": CHECKPOINT_VERSION, "search_results": search_results}
    write_discovery_checkpoint_payload(sidecar, payload)


def checkpoint_to_payload(
    checkpoint: DiscoveryCheckpoint,
    path: Path | None = None,
    *,
    incremental: bool = False,
) -> dict[str, object]:
    use_sidecar = (
        incremental
        and checkpoint.search_complete
        and len(checkpoint.search_results) >= LARGE_CHECKPOINT_RESULT_COUNT
    )
    payload: dict[str, object] = {
        "version": checkpoint.version,
        "config": checkpoint.config,
        "search_complete": checkpoint.search_complete,
        "stats": {
            "search_results": len(checkpoint.search_results),
            "crawled_urls": len(checkpoint.crawled_urls),
            "leads": len(checkpoint.leads),
            "directory_completed_locations": len(checkpoint.directory_completed_locations),
        },
        "zenrows_completed_plans": checkpoint.zenrows_completed_plans,
        "directory_completed_locations": checkpoint.directory_completed_locations,
        "directory_partial_results": checkpoint.directory_partial_results,
        "directory_seen_keys": checkpoint.directory_seen_keys,
        "crawled_urls": checkpoint.crawled_urls,
        "leads": checkpoint.leads,
    }
    if use_sidecar:
        payload["search_results_external"] = True
        payload["search_results"] = []
    else:
        payload["search_results"] = checkpoint.search_results
    if path is not None and use_sidecar:
        payload["search_results_path"] = checkpoint_search_results_path(path).name
    return payload


def write_discovery_checkpoint_payload(
    path: Path,
    payload: dict[str, object],
    *,
    backup_source: Path | None = None,
    create_backup: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if create_backup and backup_source is not None and backup_source.exists():
        try:
            shutil.copy2(backup_source, checkpoint_backup_path(backup_source))
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def append_lead(checkpoint: DiscoveryCheckpoint, lead: Lead) -> None:
    checkpoint.leads.append(lead_to_dict(lead))


def mark_url_crawled(checkpoint: DiscoveryCheckpoint, url: str) -> None:
    checkpoint.remember_crawled_url(url)


def mark_result_crawled(checkpoint: DiscoveryCheckpoint, result: SearchResult) -> None:
    checkpoint.remember_crawled_url(search_result_crawl_key(result))


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
