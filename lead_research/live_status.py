from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
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
    websites_per_minute: float = 0.0
    active_sites: int = 0
    current_site: str = ""
    recent_events: tuple[tuple[int, str], ...] = ()


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
    active_sites: int = 0,
    current_site: str = "",
    recent_events: list[tuple[int, str]] | None = None,
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
        "websites_per_minute": float(getattr(stats, "websites_per_minute", 0.0)),
        "phase": phase,
        "status": status,
        "active_sites": int(active_sites),
        "current_site": str(current_site),
        "recent_events": [[int(seq), str(text)] for seq, text in (recent_events or [])],
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
    raw_events = payload.get("recent_events", [])
    events: list[tuple[int, str]] = []
    if isinstance(raw_events, list):
        for item in raw_events:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    events.append((int(item[0]), str(item[1])))
                except (TypeError, ValueError):
                    continue
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
        websites_per_minute=float(payload.get("websites_per_minute", 0.0)),
        active_sites=int(payload.get("active_sites", 0)),
        current_site=str(payload.get("current_site", "")),
        recent_events=tuple(events),
    )


def live_status_to_lead_stats(status: LiveRunStatus) -> LeadStats:
    from .pipeline import LeadStats

    return LeadStats(
        websites_done=status.websites_done,
        websites_total=status.websites_total,
        leads_found=status.leads_found,
        pages_fetched=status.pages_fetched,
        unique_domains=status.unique_domains,
        duplicates_skipped=status.duplicates_skipped,
        suppressed_skipped=status.suppressed_skipped,
        leads_baseline=status.leads_found,
        websites_baseline=status.websites_done,
        session_started_at=time.monotonic(),
        display_leads_per_minute=status.leads_per_minute,
        display_websites_per_minute=status.websites_per_minute,
    )
