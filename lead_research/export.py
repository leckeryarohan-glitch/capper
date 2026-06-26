from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .models import Lead


CSV_FIELDS = [
    "category",
    "company_name",
    "email",
    "phone",
    "website",
    "source_url",
    "page_title",
    "consent_status",
    "notes",
    "discovered_at",
]


def lead_to_row(lead: Lead) -> dict:
    row = asdict(lead)
    row["consent_status"] = lead.consent_status.value
    row["notes"] = "; ".join(lead.notes)
    return {field: row.get(field, "") for field in CSV_FIELDS}


def write_csv(leads: list[Lead], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for lead in leads:
            writer.writerow(lead_to_row(lead))


class StreamingCsvWriter:
    """Writes leads to CSV incrementally so large runs persist as they progress."""

    def __init__(self, path: Path, *, append: bool = False, flush_every: int = 8):
        self.path = path
        self.flush_every = max(1, flush_every)
        self._pending_flush = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.path.exists() and self.path.stat().st_size > 0
        mode = "a" if append and file_exists else "w"
        self._file = self.path.open(mode, encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELDS)
        if mode == "w":
            self._writer.writeheader()
            self._file.flush()

    def write(self, lead: Lead) -> None:
        self._writer.writerow(lead_to_row(lead))
        self._pending_flush += 1
        if self._pending_flush >= self.flush_every:
            self._file.flush()
            self._pending_flush = 0

    def close(self) -> None:
        if not self._file.closed:
            if self._pending_flush:
                self._file.flush()
            self._file.close()

    def __enter__(self) -> "StreamingCsvWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def write_json(leads: list[Lead], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for lead in leads:
        item = asdict(lead)
        item["consent_status"] = lead.consent_status.value
        payload.append(item)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
