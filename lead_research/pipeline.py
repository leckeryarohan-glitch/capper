from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .checkpoint import (
    DiscoveryCheckpoint,
    append_lead,
    config_fingerprint,
    load_discovery_checkpoint,
    mark_result_crawled,
    new_discovery_checkpoint,
    save_discovery_checkpoint,
    update_search_results,
    validate_checkpoint_config,
)
from .concurrency import CHECKPOINT_SAVE_INTERVAL, AsyncCheckpointWriter, recommended_workers
from .crawl import CrawlConfig, DEFAULT_SITE_TIMEOUT_SECONDS, LeadCrawler
from .export import StreamingCsvWriter, write_json
from .extract import normalized_host
from .models import ConsentStatus, Lead, LeadDeduplicator, SearchResult, search_result_crawl_key, search_result_display_label
from .locations import DEFAULT_COUNTRIES
from .search import SearchProvider, ZenRowsResumeState, find_zenrows_provider, is_zenrows_only_provider
from .suppression import SuppressionList


DEFAULT_WORKERS = recommended_workers()
_crawl_local = threading.local()
FUTURE_RESULT_GRACE_SECONDS = 8


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
    checkpoint: Path | None = None,
    resume: bool = False,
) -> LeadStats:
    """Run a concurrent discovery: search, crawl websites in parallel, dedupe,
    suppress, and stream results to disk while reporting live statistics."""

    emit = on_event or (lambda *args, **kwargs: None)
    stats = LeadStats()
    expected_config = config_fingerprint(
        category=config.category,
        location=config.location,
        countries=config.countries,
        limit=config.limit,
        max_leads=config.max_leads,
        dedupe_by=config.dedupe_by,
    )

    checkpoint_state: DiscoveryCheckpoint | None = None
    if resume and checkpoint:
        loaded = load_discovery_checkpoint(checkpoint)
        if loaded is not None:
            validate_checkpoint_config(loaded, expected_config)
            checkpoint_state = loaded
            emit(
                "status",
                f"Checkpoint geladen: {len(loaded.search_results)} Websites, "
                f"{len(loaded.crawled_urls)} gecrawlt, {len(loaded.leads)} Leads.",
            )

    if checkpoint_state is None:
        checkpoint_state = new_discovery_checkpoint(
            category=config.category,
            location=config.location,
            countries=config.countries,
            limit=config.limit,
            max_leads=config.max_leads,
            dedupe_by=config.dedupe_by,
        )

    emit("status", "Bereite Suche vor ...")
    try:
        provider.on_status = lambda message: emit("status", message)
    except Exception:  # noqa: BLE001 - status hook is optional
        pass

    search_results = _discover_websites(
        provider=provider,
        config=config,
        checkpoint_state=checkpoint_state,
        checkpoint_path=checkpoint,
        emit=emit,
    )
    stats.websites_total = len(search_results)
    worker_count = recommended_workers(config.workers)
    emit(
        "status",
        f"{stats.websites_total} Websites gefunden. Starte Crawling mit {worker_count} parallelen Threads ...",
    )
    emit("total", stats.websites_total)

    dedup = LeadDeduplicator(by=config.dedupe_by)
    domains: set[str] = set()
    collected: list[Lead] = []
    crawled_urls = checkpoint_state.crawled_url_set
    for lead in checkpoint_state.lead_objects():
        dedup.is_new(lead)
        host = normalized_host(lead.website)
        if host:
            domains.add(host)
        stats.leads_found += 1
        if lead.consent_status == ConsentStatus.BUSINESS_PUBLIC:
            stats.business_leads += 1
        else:
            stats.personal_leads += 1
    stats.unique_domains = len(domains)
    stats.websites_done = len(crawled_urls)

    pending_results = [
        result
        for result in search_results
        if search_result_crawl_key(result) not in crawled_urls
    ]
    if stats.websites_done:
        emit(
            "status",
            f"Crawling wird fortgesetzt: {len(pending_results)} von {stats.websites_total} Websites offen.",
        )

    is_json = output.suffix.lower() == ".json"
    writer = None if is_json else StreamingCsvWriter(output, append=resume and output.exists())
    if is_json and resume and checkpoint_state.leads:
        collected.extend(checkpoint_state.lead_objects())

    page_lock = threading.Lock()
    state_lock = threading.Lock()
    sites_since_checkpoint = 0
    checkpoint_writer = AsyncCheckpointWriter()

    def on_page(url: str) -> None:
        with page_lock:
            stats.pages_fetched += 1
            count = stats.pages_fetched
        emit("page", url, count)

    crawl_config = CrawlConfig(
        max_pages_per_site=config.max_pages_per_site,
        delay_seconds=config.delay,
        include_personal=config.include_personal,
        respect_robots=config.respect_robots,
        site_timeout_seconds=DEFAULT_SITE_TIMEOUT_SECONDS,
    )

    def thread_crawler() -> LeadCrawler:
        crawler = getattr(_crawl_local, "crawler", None)
        if crawler is None:
            crawler = LeadCrawler(crawl_config, on_page=on_page)
            _crawl_local.crawler = crawler
        return crawler

    def crawl_site(result: SearchResult) -> tuple[SearchResult, list[Lead]]:
        return result, thread_crawler().crawl_result(result, config.category)

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
        append_lead(checkpoint_state, lead)
        emit("lead", lead, stats)

    def maybe_save_checkpoint(force: bool = False) -> None:
        nonlocal sites_since_checkpoint
        if not force and sites_since_checkpoint < CHECKPOINT_SAVE_INTERVAL:
            return
        sites_since_checkpoint = 0
        checkpoint_writer.submit(checkpoint, checkpoint_state)

    try:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="capper-crawl") as executor:
            futures = [executor.submit(crawl_site, result) for result in pending_results]
            future_map = {future: result for future, result in zip(futures, pending_results, strict=False)}
            for future in as_completed(future_map):
                result = future_map[future]
                site_timeout = crawl_config.site_timeout_seconds + FUTURE_RESULT_GRACE_SECONDS
                try:
                    _, site_leads = future.result(timeout=site_timeout)
                except TimeoutError:
                    site_leads = []
                    emit("warning", f"Website-Timeout (uebersprungen): {search_result_display_label(result)}")
                except Exception as exc:  # noqa: BLE001 - keep run alive on a single site failure
                    site_leads = []
                    emit("warning", f"Website-Fehler: {exc}")
                with state_lock:
                    stats.websites_done += 1
                    mark_result_crawled(checkpoint_state, result)
                    leads_before = stats.leads_found
                    for lead in site_leads:
                        store_lead(lead)
                    new_leads = stats.leads_found - leads_before
                    sites_since_checkpoint += 1
                emit("site_done", search_result_display_label(result), new_leads, stats)
                emit("progress", stats)
                maybe_save_checkpoint()
                if stats.leads_found >= config.max_leads:
                    for pending in futures:
                        pending.cancel()
                    break
    finally:
        checkpoint_writer.close(checkpoint, checkpoint_state)
        if writer is not None:
            writer.close()
        else:
            write_json(collected, output)
        save_discovery_checkpoint(checkpoint, checkpoint_state)

    emit("finished", stats, str(output))
    if checkpoint:
        emit("status", f"Checkpoint gespeichert: {checkpoint}")
    return stats


def _discover_websites(
    *,
    provider: SearchProvider,
    config: DiscoveryConfig,
    checkpoint_state: DiscoveryCheckpoint,
    checkpoint_path: Path | None,
    emit: EventCallback,
) -> list[SearchResult]:
    if checkpoint_state.search_complete:
        return checkpoint_state.search_result_objects()

    scope = f" in {config.location}" if config.location.strip() else ""
    emit("status", f"Suche Quellen fuer '{config.category}'{scope} (max. {config.limit} Websites) ...")

    zenrows = find_zenrows_provider(provider)
    use_resumable_zenrows = zenrows is not None and (
        is_zenrows_only_provider(provider) or bool(checkpoint_state.zenrows_completed_plans)
    )
    if use_resumable_zenrows and zenrows is not None:
        resume_state = None
        if checkpoint_state.search_results or checkpoint_state.zenrows_completed_plans:
            resume_state = ZenRowsResumeState(
                results=checkpoint_state.search_result_objects(),
                seen_urls={result.url.lower().rstrip("/") for result in checkpoint_state.search_result_objects()},
                completed_plans=set(checkpoint_state.zenrows_completed_plans),
            )

        def persist_zenrows_progress(state: ZenRowsResumeState) -> None:
            update_search_results(checkpoint_state, state.results)
            checkpoint_state.zenrows_completed_plans = sorted(state.completed_plans)
            save_discovery_checkpoint(checkpoint_path, checkpoint_state)

        search_results = zenrows.search(
            config.category,
            config.location,
            config.limit,
            config.countries,
            resume_state=resume_state,
            on_plan_complete=persist_zenrows_progress,
            parallel_workers=max(1, recommended_workers(config.workers) // 4),
        )
    else:
        if checkpoint_state.search_results and checkpoint_state.zenrows_completed_plans:
            emit(
                "status",
                "Hinweis: ZenRows-Checkpoint erkannt, aber kein ZenRows-Provider aktiv. Starte Suche neu.",
            )
        search_results = provider.search(
            config.category,
            config.location,
            config.limit,
            config.countries,
        )

    checkpoint_state.search_complete = True
    update_search_results(checkpoint_state, search_results)
    save_discovery_checkpoint(checkpoint_path, checkpoint_state)
    return search_results
