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


def write_csv(leads: list[Lead], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for lead in leads:
            row = asdict(lead)
            row["consent_status"] = lead.consent_status.value
            row["notes"] = "; ".join(lead.notes)
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def write_json(leads: list[Lead], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for lead in leads:
        item = asdict(lead)
        item["consent_status"] = lead.consent_status.value
        payload.append(item)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
