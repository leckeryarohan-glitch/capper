from __future__ import annotations

import threading
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
    directory_email: str = ""
    directory_phone: str = ""
    directory_source_url: str = ""


def search_result_crawl_key(result: SearchResult) -> str:
    url = result.url.strip()
    if url:
        return url.lower().rstrip("/")
    email = result.directory_email.strip().lower()
    if email:
        return f"directory-email:{email}"
    source = result.directory_source_url.strip().lower().rstrip("/")
    if source:
        return f"directory-source:{source}"
    title = result.title.strip().lower()
    return f"directory-title:{title}" if title else "directory-empty"


def search_result_display_label(result: SearchResult) -> str:
    if result.url.strip():
        return result.url
    if result.directory_email.strip():
        return result.directory_email
    return result.title or result.directory_source_url or "Branchenverzeichnis"


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

    @property
    def email_key(self) -> str:
        return self.email.strip().lower()

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


def dedupe_leads(leads: Iterable[Lead], by: str = "email") -> list[Lead]:
    """Remove duplicate leads.

    by="email" keeps only the first lead per email address (global dedupe),
    by="email_website" keeps the first lead per (email, website) pair.
    """
    seen: set = set()
    unique: list[Lead] = []
    for lead in leads:
        key = lead.email_key if by == "email" else lead.key()
        if not key or (by == "email" and not lead.email_key):
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(lead)
    return unique


class LeadDeduplicator:
    """Thread-safe, incremental duplicate checker for streaming pipelines."""

    def __init__(self, by: str = "email"):
        self.by = by
        self._seen: set = set()
        self._lock = threading.Lock()

    def _key(self, lead: Lead):
        return lead.email_key if self.by == "email" else lead.key()

    def is_new(self, lead: Lead) -> bool:
        key = self._key(lead)
        if not key:
            return False
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def add_existing(self, leads: Iterable[Lead]) -> None:
        with self._lock:
            for lead in leads:
                key = self._key(lead)
                if key:
                    self._seen.add(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)
