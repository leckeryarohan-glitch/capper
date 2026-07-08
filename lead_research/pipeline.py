from __future__ import annotations

import os
import socket
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .checkpoint import (
    DiscoveryCheckpoint,
    append_lead,
    checkpoint_save_interval,
    checkpoint_uses_sidecar,
    clear_directory_search_progress,
    config_fingerprint,
    load_discovery_checkpoint,
    mark_result_crawled,
    new_discovery_checkpoint,
    save_discovery_checkpoint,
    search_result_crawl_key_from_dict,
    search_result_from_dict,
    update_directory_search_progress,
    update_search_results,
    validate_checkpoint_config,
)
from .concurrency import (
    AsyncCheckpointWriter,
    CRAWL_EXECUTOR_OVERSUBSCRIBE,
    recommended_crawl_workers,
    recommended_workers,
)
from .crawl import (
    CrawlConfig,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_SITE_TIMEOUT_SECONDS,
    LeadCrawler,
    RESUME_READ_TIMEOUT_SECONDS,
    RESUME_REQUEST_TIMEOUT_SECONDS,
    RESUME_SITE_TIMEOUT_SECONDS,
)
from .export import StreamingCsvWriter, write_json
from .extract import normalized_host
from .models import ConsentStatus, Lead, LeadDeduplicator, SearchResult, search_result_crawl_key, search_result_display_label
from .locations import DEFAULT_COUNTRIES
from .search import SearchProvider, ZenRowsResumeState, find_zenrows_provider, is_zenrows_only_provider
from .search import DirectoryResumeState, MultiSourceProvider, DirectorySearchProvider, find_directory_provider
from .suppression import SuppressionList


DEFAULT_WORKERS = recommended_workers()
_crawl_local = threading.local()
FUTURE_RESULT_GRACE_SECONDS = 8
CRAWL_ACTIVE_BATCH_MULTIPLIER = 1
CRAWL_WAIT_HEARTBEAT_SECONDS = 15
PAGE_EVENT_INTERVAL = 25


def build_crawl_config(*, config: DiscoveryConfig, resume: bool, pending_sites: int) -> CrawlConfig:
    fast_resume = resume and pending_sites >= 100
    return CrawlConfig(
        max_pages_per_site=config.max_pages_per_site,
        delay_seconds=0.0 if fast_resume else config.delay,
        include_personal=config.include_personal,
        respect_robots=not fast_resume and config.respect_robots,
        request_timeout_seconds=RESUME_REQUEST_TIMEOUT_SECONDS if fast_resume else DEFAULT_REQUEST_TIMEOUT_SECONDS,
        site_timeout_seconds=RESUME_SITE_TIMEOUT_SECONDS if fast_resume else DEFAULT_SITE_TIMEOUT_SECONDS,
        read_timeout_seconds=RESUME_READ_TIMEOUT_SECONDS if fast_resume else DEFAULT_READ_TIMEOUT_SECONDS,
    )


def _checkpoint_payload_has_external_search(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return False
    return '"search_results_external":true' in head.replace(" ", "")


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
    gui_settings: dict[str, object] | None = None,
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
        emit("status", f"Lade Checkpoint {checkpoint} ...")
        loaded = load_discovery_checkpoint(checkpoint, on_status=lambda msg: emit("status", msg))
        if loaded is not None:
            validate_checkpoint_config(loaded, expected_config)
            checkpoint_state = loaded
            emit(
                "status",
                f"Checkpoint geladen: {len(loaded.search_results)} Websites, "
                f"{len(loaded.crawled_urls)} gecrawlt, {len(loaded.leads)} Leads."
                + (
                    f" Branchenverzeichnisse: {len(loaded.directory_completed_locations)} Orte fertig."
                    if loaded.directory_completed_locations and not loaded.search_complete
                    else ""
                ),
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

    if gui_settings is not None:
        checkpoint_state.config["gui_settings"] = gui_settings

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
    stats.websites_total = (
        len(checkpoint_state.search_results)
        if checkpoint_state.search_complete
        else len(search_results)
    )

    dedup = LeadDeduplicator(by=config.dedupe_by)
    domains: set[str] = set()
    collected: list[Lead] = []
    crawled_urls = checkpoint_state.crawled_url_set
    dedup.add_existing_dicts(checkpoint_state.leads)
    for lead_dict in checkpoint_state.leads:
        host = normalized_host(str(lead_dict.get("website", "")))
        if host:
            domains.add(host)
        stats.leads_found += 1
        consent = str(lead_dict.get("consent_status", ConsentStatus.BUSINESS_PUBLIC.value))
        if consent == ConsentStatus.BUSINESS_PUBLIC.value:
            stats.business_leads += 1
        else:
            stats.personal_leads += 1
    stats.unique_domains = len(domains)
    stats.websites_done = len(crawled_urls)

    if checkpoint_state.search_complete:
        pending_count = max(0, len(checkpoint_state.search_results) - len(crawled_urls))

        def iter_pending_results():
            for item in checkpoint_state.search_results:
                if search_result_crawl_key_from_dict(item) not in crawled_urls:
                    yield search_result_from_dict(item)

        pending_iter = iter_pending_results()
    else:
        pending_count = sum(
            1 for result in search_results if search_result_crawl_key(result) not in crawled_urls
        )
        pending_iter = (
            result
            for result in search_results
            if search_result_crawl_key(result) not in crawled_urls
        )
    worker_count = recommended_crawl_workers(config.workers, pending_sites=pending_count)
    if worker_count < recommended_workers(config.workers):
        emit(
            "status",
            f"Crawling mit {worker_count} parallelen Websites "
            f"(Limit fuer stabile Resume-Laeufe, eingestellt: {recommended_workers(config.workers)}).",
        )
    emit(
        "status",
        f"{stats.websites_total} Websites gefunden. Starte Crawling mit {worker_count} parallelen Threads ...",
    )
    emit("total", stats.websites_total)
    if stats.websites_done:
        emit(
            "status",
            f"Crawling wird fortgesetzt: {pending_count} von {stats.websites_total} Websites offen.",
        )
    emit("progress", stats)

    is_json = output.suffix.lower() == ".json"
    writer = None if is_json else StreamingCsvWriter(output, append=resume and output.exists())
    if is_json and resume and checkpoint_state.leads:
        collected.extend(checkpoint_state.lead_objects())

    page_lock = threading.Lock()
    state_lock = threading.Lock()
    sites_since_checkpoint = 0
    checkpoint_writer = AsyncCheckpointWriter()
    checkpoint_save_every = checkpoint_save_interval(checkpoint_state)
    pages_since_emit = 0

    def checkpoint_snapshot() -> tuple[DiscoveryCheckpoint, bool]:
        incremental = checkpoint_uses_sidecar(checkpoint_state)
        return checkpoint_state, incremental

    def on_page(url: str) -> None:
        nonlocal pages_since_emit
        with page_lock:
            stats.pages_fetched += 1
            count = stats.pages_fetched
            pages_since_emit += 1
        if pages_since_emit == 1 or pages_since_emit >= PAGE_EVENT_INTERVAL:
            pages_since_emit = 0
            emit("page", url, count)

    crawl_config = build_crawl_config(config=config, resume=resume, pending_sites=pending_count)
    if resume and pending_count >= 100:
        emit(
            "status",
            f"Schneller Resume-Modus: kuerzere Timeouts ({crawl_config.site_timeout_seconds:.0f}s/Site), "
            f"ohne Robots.txt und ohne Delay.",
        )

    def thread_crawler() -> LeadCrawler:
        crawler = getattr(_crawl_local, "crawler", None)
        if crawler is None:
            crawler = LeadCrawler(crawl_config, on_page=on_page)
            _crawl_local.crawler = crawler
        return crawler

    def crawl_site(result: SearchResult) -> tuple[SearchResult, list[Lead]]:
        previous_socket_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(crawl_config.request_timeout_seconds)
        try:
            return result, thread_crawler().crawl_result(result, config.category)
        finally:
            socket.setdefaulttimeout(previous_socket_timeout)

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
        if not force and not checkpoint_writer.should_save(sites_since_checkpoint, checkpoint_save_every):
            return
        sites_since_checkpoint = 0
        if (
            checkpoint
            and checkpoint_uses_sidecar(checkpoint_state)
            and not _checkpoint_payload_has_external_search(checkpoint)
        ):
            emit("status", "Optimiere Checkpoint-Format im Hintergrund ...")
        checkpoint_writer.submit(checkpoint, checkpoint_snapshot, state_lock)

    try:
        executor_workers = min(worker_count * CRAWL_EXECUTOR_OVERSUBSCRIBE, 48)
        stale_limit = crawl_config.site_timeout_seconds + FUTURE_RESULT_GRACE_SECONDS
        with ThreadPoolExecutor(
            max_workers=executor_workers,
            thread_name_prefix="capper-crawl",
        ) as executor:
            active_futures: dict = {}
            active_started: dict = {}
            stop_submitting = False
            batch_limit = max(worker_count * CRAWL_ACTIVE_BATCH_MULTIPLIER, worker_count + 1)

            def submit_more() -> None:
                if stop_submitting:
                    return
                while len(active_futures) < batch_limit:
                    try:
                        result = next(pending_iter)
                    except StopIteration:
                        break
                    future = executor.submit(crawl_site, result)
                    active_futures[future] = result
                    active_started[future] = time.monotonic()

            def finish_site(result: SearchResult, site_leads: list[Lead], warning: str | None = None) -> bool:
                nonlocal sites_since_checkpoint, stop_submitting
                if warning:
                    emit("warning", warning)
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
                    stop_submitting = True
                    for pending_future in list(active_futures):
                        pending_future.cancel()
                    active_futures.clear()
                    active_started.clear()
                    return True
                return False

            def process_completed(future) -> bool:
                result = active_futures.pop(future)
                active_started.pop(future, None)
                try:
                    _, site_leads = future.result(timeout=FUTURE_RESULT_GRACE_SECONDS)
                except TimeoutError:
                    site_leads = []
                    warning = f"Website-Timeout (uebersprungen): {search_result_display_label(result)}"
                    return finish_site(result, site_leads, warning)
                except Exception as exc:  # noqa: BLE001 - keep run alive on a single site failure
                    site_leads = []
                    warning = f"Website-Fehler: {exc}"
                    return finish_site(result, site_leads, warning)
                return finish_site(result, site_leads)

            def evict_stale_futures() -> int:
                now = time.monotonic()
                stale_futures = [
                    future
                    for future, started_at in active_started.items()
                    if now - started_at >= stale_limit
                ]
                for future in stale_futures:
                    result = active_futures.pop(future, None)
                    active_started.pop(future, None)
                    if result is None:
                        continue
                    warning = (
                        f"Website haengt (uebersprungen nach {crawl_config.site_timeout_seconds:.0f}s): "
                        f"{search_result_display_label(result)}"
                    )
                    if finish_site(result, [], warning):
                        return len(stale_futures)
                return len(stale_futures)

            submit_more()
            if active_futures:
                emit(
                    "status",
                    f"Crawling aktiv ({len(active_futures)} Websites parallel, erste Ergebnisse in 1-2 Min.) ...",
                )
            elif pending_count == 0:
                emit("status", "Alle Websites aus dem Checkpoint sind bereits gecrawlt.")
            last_wait_notice = time.monotonic()
            while active_futures:
                done, _ = wait(
                    active_futures.keys(),
                    timeout=CRAWL_WAIT_HEARTBEAT_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    evicted = evict_stale_futures()
                    if evicted:
                        submit_more()
                        if stop_submitting:
                            break
                    now = time.monotonic()
                    if now - last_wait_notice >= CRAWL_WAIT_HEARTBEAT_SECONDS:
                        emit(
                            "status",
                            f"Crawling laeuft: {len(active_futures)} aktiv, "
                            f"{stats.websites_done}/{stats.websites_total} fertig, "
                            f"{stats.pages_fetched} Seiten ...",
                        )
                        emit("progress", stats)
                        last_wait_notice = now
                    continue
                for future in done:
                    if process_completed(future):
                        break
                if stop_submitting:
                    break
                submit_more()
    finally:
        checkpoint_writer.close(checkpoint, checkpoint_snapshot, state_lock)
        if writer is not None:
            writer.close()
        else:
            write_json(collected, output)

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
        emit("status", f"Verwende {len(checkpoint_state.search_results)} Websites aus Checkpoint ...")
        return []

    scope = f" in {config.location}" if config.location.strip() else ""
    emit("status", f"Suche Quellen fuer '{config.category}'{scope} (max. {config.limit} Websites) ...")

    zenrows = find_zenrows_provider(provider)
    use_resumable_zenrows = zenrows is not None and (
        is_zenrows_only_provider(provider) or bool(checkpoint_state.zenrows_completed_plans)
    )
    if use_resumable_zenrows and zenrows is not None:
        resume_state = None
        if checkpoint_state.search_results or checkpoint_state.zenrows_completed_plans:
            restored_results = checkpoint_state.search_result_objects()
            resume_state = ZenRowsResumeState(
                results=restored_results,
                seen_urls={
                    result.url.lower().rstrip("/")
                    for result in restored_results
                    if result.url.strip()
                },
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

        directory_resume = None
        if checkpoint_state.directory_completed_locations or checkpoint_state.directory_partial_results:
            directory_resume = DirectoryResumeState(
                results=checkpoint_state.directory_search_result_objects(),
                seen=set(checkpoint_state.directory_seen_keys),
                completed_locations=set(checkpoint_state.directory_completed_locations),
            )

        def persist_directory_progress(state: DirectoryResumeState) -> None:
            update_directory_search_progress(
                checkpoint_state,
                results=state.results,
                seen=state.seen,
                completed_locations=state.completed_locations,
            )
            save_discovery_checkpoint(checkpoint_path, checkpoint_state)

        directory_progress_callback = (
            persist_directory_progress if find_directory_provider(provider) is not None else None
        )
        if isinstance(provider, MultiSourceProvider):
            search_results = provider.search(
                config.category,
                config.location,
                config.limit,
                config.countries,
                directory_resume_state=directory_resume,
                on_directory_location_complete=directory_progress_callback,
            )
        elif isinstance(provider, DirectorySearchProvider):
            search_results = provider.search(
                config.category,
                config.location,
                config.limit,
                config.countries,
                resume_state=directory_resume,
                on_location_complete=directory_progress_callback,
            )
        else:
            search_results = provider.search(
                config.category,
                config.location,
                config.limit,
                config.countries,
            )

    checkpoint_state.search_complete = True
    update_search_results(checkpoint_state, search_results)
    clear_directory_search_progress(checkpoint_state)
    save_discovery_checkpoint(checkpoint_path, checkpoint_state)
    return search_results
