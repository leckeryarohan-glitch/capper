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
    checkpoint_uses_sidecar,
    write_discovery_checkpoint_payload,
    write_search_results_sidecar,
)


CHECKPOINT_SAVE_INTERVAL = 10
MAX_WORKERS = 128


def recommended_workers(requested: int | None = None) -> int:
    """Pick a sensible default for I/O-bound website crawling."""
    cpu = os.cpu_count() or 4
    auto = min(64, max(12, cpu * 4))
    if requested is None:
        return auto
    return max(1, min(requested, MAX_WORKERS))


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
        self.flush(path, snapshot_builder, lock)
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
            payload = checkpoint_to_payload(checkpoint, path, incremental=incremental)
            search_results = list(checkpoint.search_results)
            write_sidecar = checkpoint_uses_sidecar(checkpoint)
        write_discovery_checkpoint_payload(
            path,
            payload,
            backup_source=path if path.exists() else None,
            create_backup=not incremental,
        )
        if write_sidecar and search_results:
            write_search_results_sidecar(path, search_results)
        self._last_save_at = time.monotonic()

    def should_save(self, sites_since_checkpoint: int, save_interval: int) -> bool:
        if sites_since_checkpoint < save_interval:
            return False
        if save_interval >= 50 and time.monotonic() - self._last_save_at < CHECKPOINT_SAVE_MIN_SECONDS:
            return False
        return True
