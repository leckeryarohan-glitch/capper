from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import pickle
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .live_status import replace_file_with_retry
from .models import ConsentStatus, Lead, SearchResult, search_result_crawl_key


CHECKPOINT_VERSION = 1
LARGE_CHECKPOINT_RESULT_COUNT = 5000
CRAWLED_URLS_EXTERNAL_THRESHOLD = 500
CHECKPOINT_SAVE_MIN_SECONDS = 120
CHECKPOINT_BACKUP_MAX_BYTES = 2_000_000
SIDECAR_FLUSH_EVERY = 25
SIDECAR_MIGRATION_CHUNK = 500


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


def checkpoint_crawled_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}-crawled.jsonl")


def checkpoint_leads_delta_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}-leads.jsonl")


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
    directory_partial_results: int | None = None,
) -> str:
    websites = search_results if search_results is not None else len(checkpoint.search_results)
    partial = (
        directory_partial_results
        if directory_partial_results is not None
        else len(checkpoint.directory_partial_results)
    )
    # Mid-search checkpoints keep found websites in directory_partial_results
    # until the search finishes; count them so the summary never shows 0.
    if not checkpoint.search_complete:
        websites += partial
    parts = [
        f"{websites} Websites",
        f"{crawled_urls if crawled_urls is not None else len(checkpoint.crawled_urls)} gecrawlt",
        f"{leads if leads is not None else len(checkpoint.leads)} Leads",
    ]
    completed = checkpoint.directory_completed_locations
    completed_count = (
        directory_locations if directory_locations is not None else len(completed)
    )
    if completed_count and not checkpoint.search_complete:
        if completed and completed_count <= 3:
            parts.append(f"{completed_count} Branchenorte ({', '.join(completed[:3])})")
        else:
            parts.append(f"{completed_count} Branchenorte")
    if not checkpoint.search_complete:
        parts.append("Suche laeuft noch")
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
    crawled_urls = list(payload.get("crawled_urls", []))
    leads = list(payload.get("leads", []))
    if payload.get("crawled_urls_external"):
        crawled_sidecar = checkpoint_crawled_path(path)
        if crawled_sidecar.exists():
            crawled_urls.extend(_read_jsonl_strings(crawled_sidecar))
    if payload.get("leads_external"):
        leads_sidecar = checkpoint_leads_delta_path(path)
        if leads_sidecar.exists():
            leads.extend(_read_jsonl_objects(leads_sidecar))
    checkpoint = DiscoveryCheckpoint(
        version=int(payload.get("version", CHECKPOINT_VERSION)),
        config=dict(payload.get("config", {})),
        search_complete=bool(payload.get("search_complete", False)),
        search_results=search_results,
        zenrows_completed_plans=list(payload.get("zenrows_completed_plans", [])),
        directory_completed_locations=list(payload.get("directory_completed_locations", [])),
        directory_partial_results=list(payload.get("directory_partial_results", [])),
        directory_seen_keys=list(payload.get("directory_seen_keys", [])),
        crawled_urls=crawled_urls,
        leads=leads,
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

    stats_value: object = None
    stats_pos = _find_json_value_start(text, "stats")
    if stats_pos >= 0:
        stats_value, _ = _parse_json_value_at(text, stats_pos)
    if isinstance(stats_value, dict):
        search_count = int(stats_value.get("search_results", 0))
        crawled_count = int(stats_value.get("crawled_urls", 0))
        leads_count = int(stats_value.get("leads", 0))
        directory_count = int(stats_value.get("directory_completed_locations", 0))
        partial_count = int(
            stats_value.get(
                "directory_partial_results",
                _count_json_array_elements(text, "directory_partial_results"),
            )
        )
    else:
        search_count = _count_json_array_elements(text, "search_results")
        crawled_count = _count_json_array_elements(text, "crawled_urls")
        leads_count = _count_json_array_elements(text, "leads")
        directory_count = _count_json_array_elements(text, "directory_completed_locations")
        partial_count = _count_json_array_elements(text, "directory_partial_results")

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
        directory_locations=directory_count,
        directory_partial_results=partial_count,
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


def checkpoint_uses_crawled_sidecar(checkpoint: DiscoveryCheckpoint) -> bool:
    return len(checkpoint.crawled_urls) >= CRAWLED_URLS_EXTERNAL_THRESHOLD


def append_crawled_urls_sidecar(path: Path, urls: list[str]) -> None:
    if not urls:
        return
    sidecar = checkpoint_crawled_path(path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("a", encoding="utf-8") as handle:
        for url in urls:
            handle.write(json.dumps(url, ensure_ascii=False))
            handle.write("\n")


def append_leads_sidecar(path: Path, leads: list[dict[str, object]]) -> None:
    if not leads:
        return
    sidecar = checkpoint_leads_delta_path(path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("a", encoding="utf-8") as handle:
        for lead in leads:
            handle.write(json.dumps(lead, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


class BufferedSidecarWriter:
    """Append crawl progress to JSONL sidecars in small batches."""

    def __init__(
        self,
        append_fn: Callable[[Path, list], None],
        checkpoint_path: Path,
        *,
        flush_every: int = SIDECAR_FLUSH_EVERY,
    ) -> None:
        self._append_fn = append_fn
        self._checkpoint_path = checkpoint_path
        self._flush_every = max(1, flush_every)
        self._buffer: list = []
        self._lock = threading.Lock()

    def append(self, item) -> None:
        with self._lock:
            self._buffer.append(item)
            if len(self._buffer) >= self._flush_every:
                self._flush_unlocked()

    def flush(self) -> None:
        with self._lock:
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._append_fn(self._checkpoint_path, batch)


def ensure_checkpoint_sidecars(
    path: Path,
    checkpoint: DiscoveryCheckpoint,
    *,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """Migrate large in-memory crawl state to append-only sidecars before resume saves."""
    if checkpoint_uses_sidecar(checkpoint):
        search_sidecar = checkpoint_search_results_path(path)
        if not search_sidecar.exists() and checkpoint.search_results:
            if on_status:
                on_status(
                    f"Migriere {len(checkpoint.search_results)} Suchergebnisse in Sidecar-Format ..."
                )
            write_search_results_sidecar(path, checkpoint.search_results)
    if checkpoint_uses_crawled_sidecar(checkpoint):
        crawled_sidecar = checkpoint_crawled_path(path)
        if not crawled_sidecar.exists() and checkpoint.crawled_urls:
            if on_status:
                on_status(
                    f"Migriere {len(checkpoint.crawled_urls)} gecrawlte URLs in Sidecar-Format ..."
                )
            for offset in range(0, len(checkpoint.crawled_urls), SIDECAR_MIGRATION_CHUNK):
                append_crawled_urls_sidecar(
                    path,
                    checkpoint.crawled_urls[offset : offset + SIDECAR_MIGRATION_CHUNK],
                )
                time.sleep(0)
    if len(checkpoint.leads) >= CRAWLED_URLS_EXTERNAL_THRESHOLD:
        leads_sidecar = checkpoint_leads_delta_path(path)
        if not leads_sidecar.exists() and checkpoint.leads:
            if on_status:
                on_status(f"Migriere {len(checkpoint.leads)} Leads in Sidecar-Format ...")
            for offset in range(0, len(checkpoint.leads), SIDECAR_MIGRATION_CHUNK):
                append_leads_sidecar(
                    path,
                    checkpoint.leads[offset : offset + SIDECAR_MIGRATION_CHUNK],
                )
                time.sleep(0)
    if checkpoint_uses_sidecar(checkpoint) or checkpoint_uses_crawled_sidecar(checkpoint):
        if on_status:
            on_status("Optimiere Checkpoint fuer schnelle Resume-Speicherung ...")
        save_discovery_checkpoint(path, checkpoint, incremental=True)


def _read_jsonl_strings(path: Path) -> list[str]:
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        value = json.loads(stripped)
        if isinstance(value, str):
            values.append(value)
    return values


def _read_jsonl_objects(path: Path) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        value = json.loads(stripped)
        if isinstance(value, dict):
            values.append(value)
    return values


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
        return 500
    if size >= 1000:
        return 150
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
    use_crawled_sidecar = incremental and checkpoint_uses_crawled_sidecar(checkpoint)
    use_leads_sidecar = incremental and len(checkpoint.leads) >= CRAWLED_URLS_EXTERNAL_THRESHOLD
    payload: dict[str, object] = {
        "version": checkpoint.version,
        "config": checkpoint.config,
        "search_complete": checkpoint.search_complete,
        "stats": {
            "search_results": len(checkpoint.search_results),
            "crawled_urls": len(checkpoint.crawled_urls),
            "leads": len(checkpoint.leads),
            "directory_completed_locations": len(checkpoint.directory_completed_locations),
            "directory_partial_results": len(checkpoint.directory_partial_results),
        },
        "zenrows_completed_plans": checkpoint.zenrows_completed_plans,
        "directory_completed_locations": checkpoint.directory_completed_locations,
        "directory_partial_results": checkpoint.directory_partial_results,
        "directory_seen_keys": checkpoint.directory_seen_keys,
    }
    if use_crawled_sidecar:
        payload["crawled_urls_external"] = True
        payload["crawled_urls"] = []
    else:
        payload["crawled_urls"] = checkpoint.crawled_urls
    if use_leads_sidecar:
        payload["leads_external"] = True
        payload["leads"] = []
    else:
        payload["leads"] = checkpoint.leads
    if incremental and checkpoint.search_complete:
        payload["directory_partial_results"] = []
        payload["directory_seen_keys"] = []
    if use_sidecar:
        payload["search_results_external"] = True
        payload["search_results"] = []
    else:
        payload["search_results"] = checkpoint.search_results
    if path is not None and use_sidecar:
        payload["search_results_path"] = checkpoint_search_results_path(path).name
    return payload


_CHECKPOINT_WRITE_SCRIPT = """
import json
import os
import pickle
import sys
import time
from pathlib import Path

pickle_path = Path(sys.argv[1])
dest = Path(sys.argv[2])
payload = pickle.loads(pickle_path.read_bytes())
tmp = dest.with_suffix(dest.suffix + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
for attempt in range(5):
    try:
        os.replace(tmp, dest)
        break
    except OSError:
        if attempt == 4:
            raise
        time.sleep(0.05 * (attempt + 1))
try:
    pickle_path.unlink()
except OSError:
    pass
"""


def _write_payload_subprocess(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pkl") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        pickle_path = Path(handle.name)
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHECKPOINT_WRITE_SCRIPT, str(pickle_path), str(path)],
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", errors="replace") or "checkpoint subprocess failed")
    except Exception:
        pickle_path.unlink(missing_ok=True)
        raise


def write_discovery_checkpoint_payload(
    path: Path,
    payload: dict[str, object],
    *,
    backup_source: Path | None = None,
    create_backup: bool = True,
    use_subprocess: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if (
        create_backup
        and backup_source is not None
        and backup_source.exists()
        and backup_source.stat().st_size <= CHECKPOINT_BACKUP_MAX_BYTES
    ):
        try:
            shutil.copy2(backup_source, checkpoint_backup_path(backup_source))
        except OSError:
            pass
    if use_subprocess:
        _write_payload_subprocess(path, payload)
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    replace_file_with_retry(tmp, path)


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
