from __future__ import annotations

from pathlib import Path

from .batch import run_batch_discovery
from .crawl import CrawlConfig
from .directory_profiles import resolve_mass_directory_sources
from .locations import cities_for_mass_web_search
from .search import DirectorySearchProvider, SearchProvider


def mass_city_locations(countries: tuple[str, ...]) -> list[str]:
    return [city for city, _country in cities_for_mass_web_search(countries)]


def run_mass_discovery(
    *,
    category: str,
    countries: tuple[str, ...],
    crawl_config: CrawlConfig,
    limit_per_query: int,
    target_leads: int,
    output: Path,
    suppression_file: Path | None,
    checkpoint: Path | None,
    resume: bool,
    query_delay: float,
    query_parallel: int,
    directory_parallel: int = 40,
    directory_detail_parallel: int = 12,
    enabled_directory_sources: set[str] | None = None,
    provider: SearchProvider | None = None,
) -> int:
    locations = mass_city_locations(countries)
    if not locations:
        raise ValueError(f"No cities found for countries: {', '.join(countries)}")

    if provider is None:
        source_ids = enabled_directory_sources or resolve_mass_directory_sources(category)
        if not source_ids:
            raise ValueError(
                f"No directory sources resolved for category {category!r}. "
                "Check directory_profiles or pass explicit source IDs."
            )
        provider = DirectorySearchProvider(
            enabled_directory_sources=source_ids,
            parallel_requests=directory_parallel,
            detail_parallel_requests=directory_detail_parallel,
            mass_mode=True,
        )

    return run_batch_discovery(
        provider=provider,
        categories=[category],
        locations=locations,
        crawl_config=crawl_config,
        limit_per_query=limit_per_query,
        max_leads=target_leads,
        output=output,
        suppression_file=suppression_file,
        checkpoint=checkpoint,
        resume=resume,
        query_delay=query_delay,
        query_parallel=query_parallel,
    )
