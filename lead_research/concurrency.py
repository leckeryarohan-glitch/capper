from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .checkpoint import (
    CHECKPOINT_SAVE_MIN_SECONDS,
    CRAWLED_URLS_EXTERNAL_THRESHOLD,
    DiscoveryCheckpoint,
    append_crawled_urls_sidecar,
    append_leads_sidecar,
    checkpoint_crawled_path,
    checkpoint_leads_delta_path,
    checkpoint_to_payload,
    checkpoint_uses_crawled_sidecar,
    checkpoint_uses_sidecar,
    write_discovery_checkpoint_payload,
    write_search_results_sidecar,
)


CHECKPOINT_SAVE_INTERVAL = 10
MAX_WORKERS = 128
CRAWL_MAX_WORKERS = 20
CRAWL_LARGE_RESUME_WORKERS = 8
CRAWL_LARGE_RESUME_PENDING = 500
CRAWL_EXECUTOR_OVERSUBSCRIBE = 3


def recommended_workers(requested: int | None = None) -> int:
    """Pick a sensible default for I/O-bound website crawling."""
    cpu = os.cpu_count() or 4
    auto = min(64, max(12, cpu * 4))
    if requested is None:
        return auto
    return max(1, min(requested, MAX_WORKERS))


def recommended_crawl_workers(requested: int | None = None, *, pending_sites: int = 0) -> int:
    workers = min(recommended_workers(requested), CRAWL_MAX_WORKERS)
    if pending_sites >= CRAWL_LARGE_RESUME_PENDING:
        workers = min(workers, CRAWL_LARGE_RESUME_WORKERS)
    return max(1, workers)


class AsyncCheckpointWriter:
    """Serialize checkpoint writes on a background thread so crawlers keep running."""

    def __init__(
        self,
        *,
        initial_crawled_count: int = 0,
        initial_leads_count: int = 0,
    ) -> None:
        self._queue: queue.Queue[
            tuple[Path, Callable[[], tuple[DiscoveryCheckpoint, bool]], threading.Lock] | None
        ] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, name="capper-checkpoint", daemon=True)
        self._last_save_at = 0.0
        self._last_crawled_count = max(0, initial_crawled_count)
        self._last_leads_count = max(0, initial_leads_count)
        self._thread.start()

    def submit(
        self,
        path: Path | None,
        snapshot_builder: Callable[[], tuple[DiscoveryCheckpoint, bool]],
        lock: threading.Lock,
    ) -> None:
        if path is None:
            return
        item = (path, snapshot_builder, lock)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(item)

    def flush(
        self,
        path: Path | None,
        snapshot_builder: Callable[[], tuple[DiscoveryCheckpoint, bool]],
        lock: threading.Lock,
    ) -> None:
        if path is None:
            return
        self._write(path, snapshot_builder, lock)

    def close(
        self,
        path: Path | None,
        snapshot_builder: Callable[[], tuple[DiscoveryCheckpoint, bool]],
        lock: threading.Lock,
    ) -> None:
        if path is not None:
            self.submit(path, snapshot_builder, lock)
        self._queue.put(None)
        self._thread.join(timeout=300)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            path, snapshot_builder, lock = item
            try:
                self._write(path, snapshot_builder, lock)
            except OSError:
                pass

    def _write(
        self,
        path: Path,
        snapshot_builder: Callable[[], tuple[DiscoveryCheckpoint, bool]],
        lock: threading.Lock,
    ) -> None:
        with lock:
            checkpoint, incremental = snapshot_builder()
        new_crawled = checkpoint.crawled_urls[self._last_crawled_count :]
        new_leads = checkpoint.leads[self._last_leads_count :]
        if incremental:
            crawled_sidecar_exists = checkpoint_crawled_path(path).exists()
            if checkpoint_uses_crawled_sidecar(checkpoint) and not crawled_sidecar_exists:
                existing_crawled = checkpoint.crawled_urls[: self._last_crawled_count]
                append_crawled_urls_sidecar(path, existing_crawled)
            append_crawled_urls_sidecar(path, new_crawled)
            leads_sidecar_exists = checkpoint_leads_delta_path(path).exists()
            if len(checkpoint.leads) >= CRAWLED_URLS_EXTERNAL_THRESHOLD and not leads_sidecar_exists:
                existing_leads = checkpoint.leads[: self._last_leads_count]
                append_leads_sidecar(path, existing_leads)
            append_leads_sidecar(path, new_leads)
            self._last_crawled_count = len(checkpoint.crawled_urls)
            self._last_leads_count = len(checkpoint.leads)
        payload = checkpoint_to_payload(checkpoint, path, incremental=incremental)
        search_results = list(checkpoint.search_results) if checkpoint_uses_sidecar(checkpoint) else []
        write_discovery_checkpoint_payload(
            path,
            payload,
            backup_source=path if path.exists() else None,
            create_backup=not incremental,
        )
        if search_results:
            write_search_results_sidecar(path, search_results)
        self._last_save_at = time.monotonic()

    def should_save(self, sites_since_checkpoint: int, save_interval: int) -> bool:
        if sites_since_checkpoint < save_interval:
            return False
        if save_interval >= 50 and time.monotonic() - self._last_save_at < CHECKPOINT_SAVE_MIN_SECONDS:
            return False
        return True
