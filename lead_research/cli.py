from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import read_terms, run_batch_discovery
from .crawl import CrawlConfig
from .pipeline import DEFAULT_WORKERS, DiscoveryConfig, run_discovery
from .search import SearchProviderError, provider_from_name
from .suppression import SuppressionList


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capper",
        description="Compliant B2B lead research from public business websites.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover",
        help="Find public business contact leads for a category.",
    )
    discover.add_argument("--category", required=True, help="Business category, e.g. hotel.")
    discover.add_argument("--location", default="", help="Optional location, e.g. Berlin.")
    discover.add_argument(
        "--provider",
        choices=["all", "auto", "osm", "duckduckgo", "google", "brave", "bing", "serpapi", "file"],
        default="file",
        help="Search provider. API providers require their matching environment variable.",
    )
    discover.add_argument(
        "--source-profile",
        choices=["web", "common"],
        default="web",
        help="Use normal web search or common business/directory sources.",
    )
    discover.add_argument(
        "--seed-file",
        type=Path,
        help="Plain text file with one URL per line, required for --provider=file.",
    )
    discover.add_argument("--limit", type=int, default=25, help="Maximum search results to inspect.")
    discover.add_argument(
        "--max-pages-per-site",
        type=int,
        default=3,
        help="Maximum pages to crawl per search result.",
    )
    discover.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between page requests on a site.",
    )
    discover.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Number of websites to crawl in parallel.",
    )
    discover.add_argument(
        "--max-leads",
        type=int,
        default=100000,
        help="Stop after this many deduplicated reviewable leads.",
    )
    discover.add_argument(
        "--dedupe",
        choices=["email", "email_website"],
        default="email",
        help="Deduplicate by email only (default) or by email+website.",
    )
    discover.add_argument(
        "--include-personal-review",
        action="store_true",
        help="Include non-role emails marked as personal_review_required.",
    )
    discover.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Do not check robots.txt before crawling websites.",
    )
    discover.add_argument(
        "--suppression-file",
        type=Path,
        help="Opt-out list with emails or domains to exclude.",
    )
    discover.add_argument(
        "--output",
        type=Path,
        default=Path("leads.csv"),
        help="Output file path (.csv or .json).",
    )

    batch = subparsers.add_parser(
        "batch",
        help="Run many compliant lead discovery queries from category and location files.",
    )
    batch.add_argument(
        "--categories-file",
        type=Path,
        required=True,
        help="Text file with one business category per line.",
    )
    batch.add_argument(
        "--locations-file",
        type=Path,
        help="Optional text file with one location per line. Omit for category-only queries.",
    )
    batch.add_argument(
        "--provider",
        choices=["all", "auto", "osm", "duckduckgo", "google", "brave", "bing", "serpapi", "file"],
        default="brave",
        help="Search provider. Use official APIs for high-volume runs.",
    )
    batch.add_argument(
        "--seed-file",
        type=Path,
        help="Plain text file with one URL per line, required for --provider=file.",
    )
    batch.add_argument(
        "--limit-per-query",
        type=int,
        default=25,
        help="Maximum search results to inspect for each category/location query.",
    )
    batch.add_argument(
        "--max-leads",
        type=int,
        default=5000,
        help="Stop after this many deduplicated reviewable leads.",
    )
    batch.add_argument(
        "--max-pages-per-site",
        type=int,
        default=3,
        help="Maximum pages to crawl per search result.",
    )
    batch.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between page requests on a site.",
    )
    batch.add_argument(
        "--query-delay",
        type=float,
        default=2.0,
        help="Delay in seconds between search provider queries.",
    )
    batch.add_argument(
        "--include-personal-review",
        action="store_true",
        help="Include non-role emails marked as personal_review_required.",
    )
    batch.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Do not check robots.txt before crawling websites.",
    )
    batch.add_argument(
        "--suppression-file",
        type=Path,
        help="Opt-out list with emails or domains to exclude.",
    )
    batch.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("capper-checkpoint.json"),
        help="Checkpoint file used to resume long-running batches.",
    )
    batch.add_argument(
        "--resume",
        action="store_true",
        help="Resume from --checkpoint instead of starting a new batch.",
    )
    batch.add_argument(
        "--output",
        type=Path,
        default=Path("leads.csv"),
        help="Output file path (.csv or .json).",
    )

    subparsers.add_parser(
        "gui",
        help="Open a desktop form for guided lead discovery.",
    )
    return parser


def run_discover(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    if args.max_pages_per_site < 1:
        raise ValueError("--max-pages-per-site must be at least 1")
    if args.max_leads < 1:
        raise ValueError("--max-leads must be at least 1")
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    provider = provider_from_name(args.provider, args.seed_file, args.source_profile)
    config = DiscoveryConfig(
        category=args.category,
        location=args.location,
        limit=args.limit,
        max_pages_per_site=args.max_pages_per_site,
        delay=args.delay,
        include_personal=args.include_personal_review,
        respect_robots=not args.ignore_robots,
        workers=args.workers,
        max_leads=args.max_leads,
        dedupe_by=args.dedupe,
    )

    def report(kind: str, *payload: object) -> None:
        if kind == "status":
            print(payload[0])
        elif kind == "site_done":
            url, new_leads, run_stats = payload
            print(
                f"[{run_stats.websites_done}/{run_stats.websites_total}] {url} "
                f"(+{new_leads}) | leads={run_stats.leads_found} dups={run_stats.duplicates_skipped} "
                f"pages={run_stats.pages_fetched}"
            )

    stats = run_discovery(
        provider=provider,
        config=config,
        suppression=SuppressionList(args.suppression_file),
        output=args.output,
        on_event=report,
    )

    print(f"Discovered {stats.leads_found} reviewable lead(s). Wrote {args.output}.")
    print(
        f"Statistics: {stats.websites_done}/{stats.websites_total} websites, "
        f"{stats.pages_fetched} pages, {stats.unique_domains} domains, "
        f"{stats.duplicates_skipped} duplicates skipped, "
        f"{stats.suppressed_skipped} suppressed, "
        f"{stats.leads_per_minute} leads/min."
    )
    if not args.include_personal_review:
        print("Personal-looking emails were excluded. Use --include-personal-review to export them for manual review.")
    return 0


def run_batch(args: argparse.Namespace) -> int:
    if args.max_pages_per_site < 1:
        raise ValueError("--max-pages-per-site must be at least 1")

    categories = read_terms(args.categories_file)
    locations = read_terms(args.locations_file) if args.locations_file else [""]
    provider = provider_from_name(args.provider, args.seed_file)
    count = run_batch_discovery(
        provider=provider,
        categories=categories,
        locations=locations,
        crawl_config=CrawlConfig(
            max_pages_per_site=args.max_pages_per_site,
            delay_seconds=args.delay,
            include_personal=args.include_personal_review,
            respect_robots=not args.ignore_robots,
        ),
        limit_per_query=args.limit_per_query,
        max_leads=args.max_leads,
        output=args.output,
        suppression_file=args.suppression_file,
        checkpoint=args.checkpoint,
        resume=args.resume,
        query_delay=args.query_delay,
    )

    print(f"Discovered {count} reviewable lead(s). Wrote {args.output}.")
    print(f"Checkpoint: {args.checkpoint}")
    if not args.include_personal_review:
        print("Personal-looking emails were excluded. Use --include-personal-review to export them for manual review.")
    return 0


def run_gui() -> int:
    from .gui import run_gui as launch_gui

    return launch_gui()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            return run_discover(args)
        if args.command == "batch":
            return run_batch(args)
        if args.command == "gui":
            return run_gui()
    except (OSError, SearchProviderError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
