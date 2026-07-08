from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING


LIVE_STATUS_FILENAME = "capper-live-status.json"
LIVE_STATUS_MIN_INTERVAL_SECONDS = 1.0

if TYPE_CHECKING:
    from .pipeline import LeadStats


@dataclass(frozen=True)
class LiveRunStatus:
    websites_done: int
    websites_total: int
    leads_found: int
    pages_fetched: int
    unique_domains: int
    duplicates_skipped: int
    suppressed_skipped: int
    leads_per_minute: float
    phase: str
    status: str
    updated_at: float


def live_status_path_for_checkpoint(checkpoint: Path | None) -> Path:
    if checkpoint is not None:
        return checkpoint.parent / LIVE_STATUS_FILENAME
    return Path(LIVE_STATUS_FILENAME)


def write_live_status(
    path: Path,
    stats: object,
    *,
    phase: str = "crawl",
    status: str = "",
    min_interval_seconds: float = LIVE_STATUS_MIN_INTERVAL_SECONDS,
) -> None:
    now = time.monotonic()
    last_write = getattr(write_live_status, "_last_write_at", 0.0)
    if now - last_write < min_interval_seconds:
        return
    write_live_status._last_write_at = now  # type: ignore[attr-defined]
    payload = {
        "websites_done": int(getattr(stats, "websites_done", 0)),
        "websites_total": int(getattr(stats, "websites_total", 0)),
        "leads_found": int(getattr(stats, "leads_found", 0)),
        "pages_fetched": int(getattr(stats, "pages_fetched", 0)),
        "unique_domains": int(getattr(stats, "unique_domains", 0)),
        "duplicates_skipped": int(getattr(stats, "duplicates_skipped", 0)),
        "suppressed_skipped": int(getattr(stats, "suppressed_skipped", 0)),
        "leads_per_minute": float(getattr(stats, "leads_per_minute", 0.0)),
        "phase": phase,
        "status": status,
        "updated_at": time.time(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def read_live_status(path: Path) -> LiveRunStatus | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return LiveRunStatus(
        websites_done=int(payload.get("websites_done", 0)),
        websites_total=int(payload.get("websites_total", 0)),
        leads_found=int(payload.get("leads_found", 0)),
        pages_fetched=int(payload.get("pages_fetched", 0)),
        unique_domains=int(payload.get("unique_domains", 0)),
        duplicates_skipped=int(payload.get("duplicates_skipped", 0)),
        suppressed_skipped=int(payload.get("suppressed_skipped", 0)),
        leads_per_minute=float(payload.get("leads_per_minute", 0.0)),
        phase=str(payload.get("phase", "")),
        status=str(payload.get("status", "")),
        updated_at=float(payload.get("updated_at", 0.0)),
    )


def live_status_to_lead_stats(status: LiveRunStatus) -> LeadStats:
    from .pipeline import LeadStats

    stats = LeadStats()
    stats.websites_done = status.websites_done
    stats.websites_total = status.websites_total
    stats.leads_found = status.leads_found
    stats.pages_fetched = status.pages_fetched
    stats.unique_domains = status.unique_domains
    stats.duplicates_skipped = status.duplicates_skipped
    stats.suppressed_skipped = status.suppressed_skipped
    return stats
