"""Persistent, cross-run memory of already-found leads and crawled sites.

A single discovery run dedupes in memory (LeadDeduplicator) and skips sites
already recorded in its checkpoint. Neither survives a fresh run, so re-running
the same category rediscovers the same businesses. These append-only ledgers
add that missing cross-run memory:

- LeadLedger (feature A): every exported lead's email + domain, so repeat runs
  can exclude leads found in earlier runs and only surface genuinely new ones.
- SiteLedger (feature B): every crawled site host, so repeat runs skip sites
  already visited and spend their budget on unseen websites.

Both use a plain one-key-per-line text file (same spirit as the opt-out list),
which is human-readable, easy to merge, and robust against partial writes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable

from .models import Lead


LEAD_HISTORY_FILENAME = "capper-known-leads.txt"
SITE_HISTORY_FILENAME = "capper-known-sites.txt"


def lead_history_path_for(output: Path | None, checkpoint: Path | None = None) -> Path:
    base = checkpoint if checkpoint is not None else output
    if base is not None:
        return base.parent / LEAD_HISTORY_FILENAME
    return Path(LEAD_HISTORY_FILENAME)


def site_history_path_for(output: Path | None, checkpoint: Path | None = None) -> Path:
    base = checkpoint if checkpoint is not None else output
    if base is not None:
        return base.parent / SITE_HISTORY_FILENAME
    return Path(SITE_HISTORY_FILENAME)


class KeyLedger:
    """Append-only set of normalized string keys persisted one per line."""

    def __init__(self, path: Path | None, *, flush_every: int = 50) -> None:
        self.path = path
        self._flush_every = max(1, flush_every)
        self._known: set[str] = set()
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        if path is not None and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                self._known.add(stripped)

    def __contains__(self, key: str) -> bool:
        if not key:
            return False
        with self._lock:
            return key in self._known

    def contains_any(self, keys: Iterable[str]) -> bool:
        with self._lock:
            return any(key and key in self._known for key in keys)

    def add(self, keys: Iterable[str]) -> bool:
        """Record keys. Returns True if at least one key was newly added."""
        added = False
        with self._lock:
            for key in keys:
                if not key or key in self._known:
                    continue
                self._known.add(key)
                self._buffer.append(key)
                added = True
            if len(self._buffer) >= self._flush_every:
                self._flush_unlocked()
        return added

    def flush(self) -> None:
        with self._lock:
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        if not self._buffer or self.path is None:
            self._buffer.clear()
            return
        batch = self._buffer
        self._buffer = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for key in batch:
                    handle.write(key)
                    handle.write("\n")
        except OSError:
            # History is best-effort memory; a failed append must not abort a run.
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._known)


def lead_keys(lead: Lead) -> tuple[str, ...]:
    keys: list[str] = []
    email = lead.email_key
    if email:
        keys.append(email)
    domain = lead.domain
    if domain:
        keys.append(f"@{domain}")
    return tuple(keys)


def lead_dict_keys(item: dict[str, object]) -> tuple[str, ...]:
    email = str(item.get("email", "")).strip().lower()
    keys: list[str] = []
    if email:
        keys.append(email)
        domain = email.split("@", 1)[-1]
        if domain:
            keys.append(f"@{domain}")
    return tuple(keys)


class LeadLedger:
    """Feature A: remembers every exported lead across runs (email + domain)."""

    def __init__(self, path: Path | None) -> None:
        self._ledger = KeyLedger(path)
        self.path = path

    def is_known(self, lead: Lead) -> bool:
        return self._ledger.contains_any(lead_keys(lead))

    def record(self, lead: Lead) -> bool:
        return self._ledger.add(lead_keys(lead))

    def record_dicts(self, items: Iterable[dict[str, object]]) -> None:
        for item in items:
            self._ledger.add(lead_dict_keys(item))

    def flush(self) -> None:
        self._ledger.flush()

    def __len__(self) -> int:
        return len(self._ledger)


def site_key(host: str) -> str:
    return host.strip().lower().lstrip(".")


class SiteLedger:
    """Feature B: remembers every crawled site host across runs."""

    def __init__(self, path: Path | None) -> None:
        self._ledger = KeyLedger(path)
        self.path = path

    def is_known(self, host: str) -> bool:
        key = site_key(host)
        return bool(key) and key in self._ledger

    def record(self, host: str) -> bool:
        key = site_key(host)
        if not key:
            return False
        return self._ledger.add((key,))

    def flush(self) -> None:
        self._ledger.flush()

    def __len__(self) -> int:
        return len(self._ledger)
