from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .crawl import CrawlConfig, LeadCrawler
from .export import StreamingCsvWriter, write_json
from .extract import normalized_host
from .models import ConsentStatus, Lead, LeadDeduplicator
from .locations import DEFAULT_COUNTRIES
from .search import SearchProvider
from .suppression import SuppressionList


DEFAULT_WORKERS = 12


@dataclass
class LeadStats:
    """Live statistics for a discovery run."""

    websites_total: int = 0
    websites_done: int = 0
    pages_fetched: int = 0
    leads_found: int = 0
    business_leads: int = 0
    personal_leads: int = 0
    duplicates_skipped: int = 0
    suppressed_skipped: int = 0
    unique_domains: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_seconds(self) -> float:
        return max(time.monotonic() - self.started_at, 0.0)

    @property
    def leads_per_minute(self) -> float:
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return round(self.leads_found / elapsed * 60.0, 1)

    def as_dict(self) -> dict:
        return {
            "websites_total": self.websites_total,
            "websites_done": self.websites_done,
            "pages_fetched": self.pages_fetched,
            "leads_found": self.leads_found,
            "business_leads": self.business_leads,
            "personal_leads": self.personal_leads,
            "duplicates_skipped": self.duplicates_skipped,
            "suppressed_skipped": self.suppressed_skipped,
            "unique_domains": self.unique_domains,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "leads_per_minute": self.leads_per_minute,
        }


@dataclass(frozen=True)
class DiscoveryConfig:
    category: str
    location: str = ""
    countries: tuple[str, ...] = DEFAULT_COUNTRIES
    limit: int = 50
    max_pages_per_site: int = 3
    delay: float = 1.0
    include_personal: bool = False
    respect_robots: bool = True
    workers: int = DEFAULT_WORKERS
    max_leads: int = 100000
    dedupe_by: str = "email"


EventCallback = Callable[..., None]


def run_discovery(
    *,
    provider: SearchProvider,
    config: DiscoveryConfig,
    suppression: SuppressionList,
    output: Path,
    on_event: EventCallback | None = None,
) -> LeadStats:
    """Run a concurrent discovery: search, crawl websites in parallel, dedupe,
    suppress, and stream results to disk while reporting live statistics."""

    emit = on_event or (lambda *args, **kwargs: None)
    stats = LeadStats()

    emit("status", "Bereite Suche vor ...")
    try:
        provider.on_status = lambda message: emit("status", message)
    except Exception:  # noqa: BLE001 - status hook is optional
        pass

    scope = f" in {config.location}" if config.location.strip() else ""
    emit("status", f"Suche Quellen fuer '{config.category}'{scope} (max. {config.limit} Websites) ...")
    search_results = provider.search(
        config.category,
        config.location,
        config.limit,
        config.countries,
    )
    stats.websites_total = len(search_results)
    emit("status", f"{stats.websites_total} Websites gefunden. Starte Crawling mit {max(1, config.workers)} Threads ...")
    emit("total", stats.websites_total)

    dedup = LeadDeduplicator(by=config.dedupe_by)
    domains: set[str] = set()
    collected: list[Lead] = []
    is_json = output.suffix.lower() == ".json"
    writer = None if is_json else StreamingCsvWriter(output)

    page_lock = threading.Lock()

    def on_page(url: str) -> None:
        with page_lock:
            stats.pages_fetched += 1
            count = stats.pages_fetched
        emit("page", url, count)

    crawler = LeadCrawler(
        CrawlConfig(
            max_pages_per_site=config.max_pages_per_site,
            delay_seconds=config.delay,
            include_personal=config.include_personal,
            respect_robots=config.respect_robots,
        ),
        on_page=on_page,
    )

    def store_lead(lead: Lead) -> None:
        if suppression.is_suppressed(lead):
            stats.suppressed_skipped += 1
            return
        if not dedup.is_new(lead):
            stats.duplicates_skipped += 1
            return
        stats.leads_found += 1
        if lead.consent_status == ConsentStatus.BUSINESS_PUBLIC:
            stats.business_leads += 1
        else:
            stats.personal_leads += 1
        host = normalized_host(lead.website)
        if host:
            domains.add(host)
            stats.unique_domains = len(domains)
        if writer is not None:
            writer.write(lead)
        else:
            collected.append(lead)
        emit("lead", lead, stats)

    try:
        workers = max(1, config.workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(crawler.crawl_result, result, config.category): result
                for result in search_results
            }
            for future in as_completed(futures):
                result = futures[future]
                try:
                    site_leads = future.result()
                except Exception as exc:  # noqa: BLE001 - keep run alive on a single site failure
                    site_leads = []
                    emit("warning", f"Website-Fehler: {exc}")
                stats.websites_done += 1
                leads_before = stats.leads_found
                for lead in site_leads:
                    store_lead(lead)
                new_leads = stats.leads_found - leads_before
                emit("site_done", result.url, new_leads, stats)
                emit("progress", stats)
                if stats.leads_found >= config.max_leads:
                    for pending in futures:
                        pending.cancel()
                    break
    finally:
        if writer is not None:
            writer.close()
        else:
            write_json(collected, output)

    emit("finished", stats, str(output))
    return stats
