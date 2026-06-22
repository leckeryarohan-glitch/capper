from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class ConsentStatus(str, Enum):
    """How safe the contact is for outreach review."""

    BUSINESS_PUBLIC = "business_public"
    PERSONAL_REVIEW_REQUIRED = "personal_review_required"
    SUPPRESSED = "suppressed"


ROLE_PREFIXES = {
    "admin",
    "booking",
    "contact",
    "customerservice",
    "hello",
    "hilfe",
    "info",
    "kontakt",
    "office",
    "reservierung",
    "sales",
    "service",
    "support",
    "team",
    "vertrieb",
}


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class Lead:
    category: str
    source_url: str
    website: str
    email: str
    company_name: str = ""
    phone: str = ""
    page_title: str = ""
    consent_status: ConsentStatus = ConsentStatus.BUSINESS_PUBLIC
    notes: list[str] = field(default_factory=list)
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def domain(self) -> str:
        return self.email.split("@", 1)[-1].lower()

    def key(self) -> tuple[str, str]:
        return (self.email.lower(), self.website.lower())


def classify_email(email: str) -> ConsentStatus:
    local_part = email.split("@", 1)[0].lower()
    normalized = "".join(ch for ch in local_part if ch.isalpha())
    if normalized in ROLE_PREFIXES:
        return ConsentStatus.BUSINESS_PUBLIC
    if any(normalized.startswith(prefix) for prefix in ROLE_PREFIXES):
        return ConsentStatus.BUSINESS_PUBLIC
    return ConsentStatus.PERSONAL_REVIEW_REQUIRED


def dedupe_leads(leads: Iterable[Lead]) -> list[Lead]:
    seen: set[tuple[str, str]] = set()
    unique: list[Lead] = []
    for lead in leads:
        key = lead.key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(lead)
    return unique
