from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .crawl import CrawlConfig, LeadCrawler
from .export import write_csv, write_json
from .models import ConsentStatus, Lead, dedupe_leads
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
) -> int:
    if limit_per_query < 1:
        raise ValueError("--limit-per-query must be at least 1")
    if max_leads < 1:
        raise ValueError("--max-leads must be at least 1")

    completed_queries, leads = load_checkpoint(checkpoint) if resume and checkpoint else (set(), [])
    crawler = LeadCrawler(crawl_config)

    for category, location in query_plan(categories, locations):
        query_key = (category, location)
        if query_key in completed_queries:
            continue
        if len(leads) >= max_leads:
            break

        print(f"Searching category={category!r} location={location!r}")
        results = provider.search(category, location, limit_per_query)
        for result in results:
            if len(leads) >= max_leads:
                break
            leads.extend(crawler.crawl_result(result, category))
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


def lead_to_dict(lead: Lead) -> dict:
    item = asdict(lead)
    item["consent_status"] = lead.consent_status.value
    return item


def lead_from_dict(item: dict) -> Lead:
    restored = dict(item)
    restored["consent_status"] = ConsentStatus(restored.get("consent_status", ConsentStatus.BUSINESS_PUBLIC))
    restored["notes"] = list(restored.get("notes", []))
    return Lead(**restored)
