from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

from .checkpoint import (
    DiscoveryCheckpoint,
    checkpoint_to_payload,
    write_discovery_checkpoint_payload,
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
        self._queue: queue.Queue[tuple[Path, dict[str, object]] | None] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, name="capper-checkpoint", daemon=True)
        self._thread.start()

    def submit(self, path: Path | None, checkpoint: DiscoveryCheckpoint) -> None:
        if path is None:
            return
        payload = checkpoint_to_payload(checkpoint)
        try:
            self._queue.put_nowait((path, payload))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait((path, payload))

    def flush(self, path: Path | None, checkpoint: DiscoveryCheckpoint) -> None:
        if path is None:
            return
        write_discovery_checkpoint_payload(path, checkpoint_to_payload(checkpoint))

    def close(self, path: Path | None, checkpoint: DiscoveryCheckpoint) -> None:
        self.flush(path, checkpoint)
        self._queue.put(None)
        self._thread.join(timeout=120)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            path, payload = item
            try:
                write_discovery_checkpoint_payload(path, payload)
            except OSError:
                pass
