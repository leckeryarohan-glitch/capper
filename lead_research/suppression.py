from __future__ import annotations

from pathlib import Path

from .models import ConsentStatus, Lead


class SuppressionList:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._entries = load_entries(path) if path else set()

    def is_suppressed(self, lead: Lead) -> bool:
        email = lead.email.lower()
        domain = f"@{lead.domain}"
        return email in self._entries or domain in self._entries or lead.domain in self._entries

    def apply(self, leads: list[Lead]) -> list[Lead]:
        filtered: list[Lead] = []
        for lead in leads:
            if self.is_suppressed(lead):
                lead.consent_status = ConsentStatus.SUPPRESSED
                lead.notes.append("Suppressed by opt-out list")
                continue
            filtered.append(lead)
        return filtered


def load_entries(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lower()
        if stripped and not stripped.startswith("#"):
            entries.add(stripped)
    return entries
