from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .checkpoint import (
    CHECKPOINT_SAVE_MIN_SECONDS,
    DiscoveryCheckpoint,
    checkpoint_to_payload,
    checkpoint_uses_crawled_sidecar,
    checkpoint_uses_sidecar,
    write_discovery_checkpoint_payload,
    write_search_results_sidecar,
)


CHECKPOINT_SAVE_INTERVAL = 10
MAX_WORKERS = 128
CRAWL_MAX_WORKERS = 20
CRAWL_LARGE_RESUME_WORKERS = 4
CRAWL_LARGE_RESUME_PENDING = 500
CRAWL_EXECUTOR_OVERSUBSCRIBE = 2
STALL_RECOVERY_SECONDS = 22.0
HARD_TIMEOUT_GRACE_SECONDS = 3.0


def run_with_hard_timeout(fn: Callable[[], object], timeout_seconds: float) -> object:
    """Run blocking work on a daemon thread so pool workers cannot hang forever."""
    if timeout_seconds <= 0:
        return fn()
    holder: list[object] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            holder.append(fn())
        except BaseException as exc:  # noqa: BLE001 - propagate any failure
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True, name="capper-crawl-hard-timeout")
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(f"hard timeout after {timeout_seconds:.0f}s")
    if errors:
        raise errors[0]
    if not holder:
        raise RuntimeError("crawl worker returned no result")
    return holder[0]


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

    def __init__(self) -> None:
        self._queue: queue.Queue[
            tuple[Path, Callable[[], tuple[DiscoveryCheckpoint, bool]], threading.Lock] | None
        ] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, name="capper-checkpoint", daemon=True)
        self._last_save_at = 0.0
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
        self._thread.join(timeout=60)

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
        payload = checkpoint_to_payload(checkpoint, path, incremental=incremental)
        search_results = list(checkpoint.search_results) if checkpoint_uses_sidecar(checkpoint) else []
        use_subprocess = incremental or checkpoint_uses_crawled_sidecar(checkpoint)
        write_discovery_checkpoint_payload(
            path,
            payload,
            backup_source=path if path.exists() else None,
            create_backup=not incremental,
            use_subprocess=use_subprocess,
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
