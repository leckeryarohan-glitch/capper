from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from .checkpoint import lead_from_dict, lead_to_dict
from .crawl import CrawlConfig, LeadCrawler
from .export import write_csv, write_json
from .models import Lead, dedupe_leads
from .search import SearchProvider
from .suppression import SuppressionList


QueryKey = tuple[str, str]


def read_terms(path: Path) -> list[str]:
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            terms.append(stripped)
    if not terms:
        raise ValueError(f"No entries found in {path}")
    return terms


def run_batch_discovery(
    *,
    provider: SearchProvider,
    categories: list[str],
    locations: list[str],
    crawl_config: CrawlConfig,
    limit_per_query: int,
    max_leads: int,
    output: Path,
    suppression_file: Path | None,
    checkpoint: Path | None,
    resume: bool,
    query_delay: float,
    query_parallel: int = 1,
) -> int:
    if limit_per_query < 1:
        raise ValueError("--limit-per-query must be at least 1")
    if max_leads < 1:
        raise ValueError("--max-leads must be at least 1")

    completed_queries, leads = load_checkpoint(checkpoint) if resume and checkpoint else (set(), [])
    crawler = LeadCrawler(crawl_config)
    query_parallel = max(1, query_parallel)
    state_lock = threading.Lock()
    pending_queries = [
        query_key
        for query_key in query_plan(categories, locations)
        if query_key not in completed_queries
    ]

    def process_query(query_key: QueryKey) -> tuple[QueryKey, list[Lead]]:
        category, location = query_key
        print(f"Searching category={category!r} location={location!r}")
        results = provider.search(category, location, limit_per_query)
        query_leads: list[Lead] = []
        for result in results:
            query_leads.extend(crawler.crawl_result(result, category))
        return query_key, query_leads

    if query_parallel <= 1:
        for query_key in pending_queries:
            with state_lock:
                if len(leads) >= max_leads:
                    break
            _, query_leads = process_query(query_key)
            with state_lock:
                for lead in query_leads:
                    if len(leads) >= max_leads:
                        break
                    leads.append(lead)
                leads = dedupe_leads(leads)
                completed_queries.add(query_key)
                save_checkpoint(checkpoint, completed_queries, leads)
            if query_delay > 0:
                time.sleep(query_delay)
    else:
        with ThreadPoolExecutor(max_workers=query_parallel, thread_name_prefix="capper-batch") as executor:
            futures = {
                executor.submit(process_query, query_key): query_key
                for query_key in pending_queries
            }
            for future in as_completed(futures):
                with state_lock:
                    if len(leads) >= max_leads:
                        break
                query_key, query_leads = future.result()
                with state_lock:
                    for lead in query_leads:
                        if len(leads) >= max_leads:
                            break
                        leads.append(lead)
                    leads = dedupe_leads(leads)
                    completed_queries.add(query_key)
                    save_checkpoint(checkpoint, completed_queries, leads)
                if query_delay > 0:
                    time.sleep(query_delay)

    leads = dedupe_leads(leads)[:max_leads]
    leads = SuppressionList(suppression_file).apply(leads)

    if output.suffix.lower() == ".json":
        write_json(leads, output)
    else:
        write_csv(leads, output)

    save_checkpoint(checkpoint, completed_queries, leads)
    return len(leads)


def query_plan(categories: Iterable[str], locations: Iterable[str]) -> list[QueryKey]:
    cleaned_locations = list(locations) or [""]
    return [(category, location) for category in categories for location in cleaned_locations]


def load_checkpoint(path: Path | None) -> tuple[set[QueryKey], list[Lead]]:
    if path is None or not path.exists():
        return set(), []
    payload = json.loads(path.read_text(encoding="utf-8"))
    completed = {
        (item.get("category", ""), item.get("location", ""))
        for item in payload.get("completed_queries", [])
    }
    leads = [lead_from_dict(item) for item in payload.get("leads", [])]
    return completed, leads


def save_checkpoint(path: Path | None, completed_queries: set[QueryKey], leads: list[Lead]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "completed_queries": [
            {"category": category, "location": location}
            for category, location in sorted(completed_queries)
        ],
        "leads": [lead_to_dict(lead) for lead in leads],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
