from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Mapping

from .concurrency import CRAWL_MAX_WORKERS, recommended_workers
from .directories import (
    DEFAULT_DIRECTORY_DETAIL_PARALLEL,
    DIRECTORY_MAX_DETAIL_PARALLEL,
    build_directory_source_registry,
)
from .directory_profiles import resolve_category_directory_sources
from .directory_registry import directory_sources_by_category
from .locations import DEFAULT_COUNTRIES
from .checkpoint import load_checkpoint_gui_metadata
from .pipeline import DEFAULT_WORKERS, DiscoveryConfig, LeadStats, run_discovery
from .search import (
    DEFAULT_DIRECTORY_PARALLEL_REQUESTS,
    DIRECTORY_MAX_PARALLEL_REQUESTS,
    SearchProviderError,
    combined_provider,
)
from .suppression import SuppressionList


DEFAULT_OUTPUT = "leads.csv"
DEFAULT_CHECKPOINT = "capper-checkpoint.json"
DEFAULT_LIMIT = "5000"
DEFAULT_MAX_PAGES = "5"
DEFAULT_DELAY = "0.3"
DEFAULT_MAX_LEADS = "20000"
DEFAULT_WORKERS_TEXT = str(recommended_workers())
DEFAULT_DIRECTORY_PARALLEL_TEXT = str(DEFAULT_DIRECTORY_PARALLEL_REQUESTS)
DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT = str(DEFAULT_DIRECTORY_DETAIL_PARALLEL)
DEFAULT_PROVIDER = "all"


def build_simple_gui_argv(values: Mapping[str, str | bool]) -> list[str]:
    category = require_text(values, "category", "Kategorie")
    output = str(values.get("output", DEFAULT_OUTPUT)).strip() or DEFAULT_OUTPUT

    argv = [
        "discover",
        "--category",
        category,
        "--provider",
        DEFAULT_PROVIDER,
        "--limit",
        str(values.get("limit", DEFAULT_LIMIT)).strip() or DEFAULT_LIMIT,
        "--max-pages-per-site",
        DEFAULT_MAX_PAGES,
        "--delay",
        DEFAULT_DELAY,
        "--workers",
        str(values.get("workers", DEFAULT_WORKERS_TEXT)).strip() or DEFAULT_WORKERS_TEXT,
        "--max-leads",
        str(values.get("max_leads", DEFAULT_MAX_LEADS)).strip() or DEFAULT_MAX_LEADS,
        "--output",
        output,
    ]
    location = str(values.get("location", "")).strip()
    if location:
        argv.extend(["--location", location])

    suppression_file = str(values.get("suppression_file", "")).strip()
    if suppression_file and Path(suppression_file).exists():
        argv.extend(["--suppression-file", suppression_file])

    checkpoint = str(values.get("checkpoint", DEFAULT_CHECKPOINT)).strip() or DEFAULT_CHECKPOINT
    argv.extend(["--checkpoint", checkpoint])
    if bool(values.get("resume", False)):
        argv.append("--resume")

    return argv


def require_text(values: Mapping[str, str | bool], key: str, label: str) -> str:
    value = values.get(key, "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} ist erforderlich.")
    return value.strip()


def parse_positive_int(value: str | bool | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def selected_countries(values: Mapping[str, str | bool]) -> tuple[str, ...]:
    codes: list[str] = []
    if bool(values.get("country_de", True)):
        codes.append("DE")
    if bool(values.get("country_at", False)):
        codes.append("AT")
    return tuple(codes) if codes else DEFAULT_COUNTRIES


def selected_directory_source_ids(values: Mapping[str, str | bool]) -> set[str]:
    registry = build_directory_source_registry()
    enabled: set[str] = set()
    for spec in registry:
        if not spec.implemented:
            continue
        key = f"dir_source_{spec.id}"
        if bool(values.get(key, spec.default_enabled)):
            enabled.add(spec.id)
    return enabled


def collect_gui_settings(values: Mapping[str, str | bool]) -> dict[str, object]:
    countries: list[str] = []
    if bool(values.get("country_de", True)):
        countries.append("DE")
    if bool(values.get("country_at", False)):
        countries.append("AT")
    if not countries:
        countries = list(DEFAULT_COUNTRIES)
    return {
        "category": str(values.get("category", "")).strip(),
        "location": str(values.get("location", "")).strip(),
        "countries": countries,
        "limit": parse_positive_int(values.get("limit"), int(DEFAULT_LIMIT)),
        "max_leads": parse_positive_int(values.get("max_leads"), int(DEFAULT_MAX_LEADS)),
        "workers": str(values.get("workers", DEFAULT_WORKERS_TEXT)).strip() or DEFAULT_WORKERS_TEXT,
        "directory_parallel": str(values.get("directory_parallel", DEFAULT_DIRECTORY_PARALLEL_TEXT)).strip()
        or DEFAULT_DIRECTORY_PARALLEL_TEXT,
        "directory_detail_parallel": str(
            values.get("directory_detail_parallel", DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT)
        ).strip()
        or DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT,
        "use_osm": bool(values.get("use_osm", True)),
        "use_duckduckgo": bool(values.get("use_duckduckgo", True)),
        "use_directories": bool(values.get("use_directories", True)),
        "use_zenrows_google": bool(values.get("use_zenrows_google", True)),
        "use_google_maps": bool(values.get("use_google_maps", True)),
        "use_serpapi": bool(values.get("use_serpapi", True)),
        "directory_sources": sorted(selected_directory_source_ids(values)),
    }


DEFAULT_GUI_LEAD_ROWS = 500
GUI_POLL_INTERVAL_MS = 50
GUI_POLL_TIME_BUDGET_MS = 20
GUI_MESSAGES_PER_POLL = 80
GUI_MAX_DRAIN_PER_CYCLE = 250
GUI_MAX_PENDING_MESSAGES = 300
GUI_LEADS_PER_POLL = 2
GUI_LOG_EVERY_N_PAGES = 25
GUI_MAX_LOG_LINES = 1500
GUI_UI_UPDATE_INTERVAL_S = 0.25
GUI_QUIET_UI_INTERVAL_MS = 1000
GUI_SITE_LOG_EVERY = 50
GUI_LOG_SCROLL_EVERY = 8
CHECKPOINT_PATH_DEBOUNCE_MS = 500


def coalesce_gui_messages(messages: list[tuple]) -> list[tuple]:
    """Collapse bursty crawl updates so the Tk main thread stays responsive."""
    if len(messages) <= 1:
        return messages

    coalesced: list[tuple] = []
    latest_progress: tuple | None = None
    latest_site_done: tuple | None = None
    latest_page: tuple | None = None
    warning_count = 0
    warning_sample = ""

    def flush_crawl_updates() -> None:
        nonlocal latest_progress, latest_site_done, latest_page, warning_count, warning_sample
        if warning_count:
            text = warning_sample
            if warning_count > 1:
                text = f"{warning_count}x Hinweise (zuletzt: {warning_sample})"
            coalesced.append(("warning", text))
            warning_count = 0
            warning_sample = ""
        if latest_site_done is not None:
            coalesced.append(latest_site_done)
            latest_site_done = None
            latest_progress = None
        elif latest_progress is not None:
            coalesced.append(latest_progress)
            latest_progress = None
        if latest_page is not None:
            coalesced.append(latest_page)
            latest_page = None

    for message in messages:
        kind = message[0]
        if kind == "progress":
            latest_progress = message
        elif kind == "site_done":
            latest_site_done = message
            latest_progress = None
        elif kind == "page":
            latest_page = message
        elif kind == "warning":
            warning_count += 1
            warning_sample = str(message[1])
        elif kind == "lead":
            coalesced.append(message)
        else:
            flush_crawl_updates()
            coalesced.append(message)
    flush_crawl_updates()
    return coalesced


def checkpoint_settings_for_gui(path: Path) -> dict[str, object] | None:
    metadata = load_checkpoint_gui_metadata(path)
    if metadata is None:
        return None

    countries = list(metadata.get("countries", list(DEFAULT_COUNTRIES)))
    if not countries:
        countries = list(DEFAULT_COUNTRIES)
    return {
        "category": str(metadata.get("category", "")),
        "location": str(metadata.get("location", "")),
        "countries": countries,
        "limit": int(metadata.get("limit") or DEFAULT_LIMIT),
        "max_leads": int(metadata.get("max_leads") or DEFAULT_MAX_LEADS),
        "workers": str(metadata.get("workers") or DEFAULT_WORKERS_TEXT),
        "directory_parallel": str(metadata.get("directory_parallel") or DEFAULT_DIRECTORY_PARALLEL_TEXT),
        "directory_detail_parallel": str(
            metadata.get("directory_detail_parallel") or DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT
        ),
        "use_osm": bool(metadata.get("use_osm", True)),
        "use_duckduckgo": bool(metadata.get("use_duckduckgo", True)),
        "use_directories": bool(metadata.get("use_directories", True)),
        "use_zenrows_google": bool(metadata.get("use_zenrows_google", True)),
        "use_google_maps": bool(metadata.get("use_google_maps", True)),
        "use_serpapi": bool(metadata.get("use_serpapi", False)),
        "directory_sources": list(metadata.get("directory_sources", [])),
        "progress_summary": str(metadata.get("progress_summary", "")),
    }


def apply_gui_settings(values: dict[str, object], settings: Mapping[str, object]) -> None:
    values["category"] = str(settings.get("category", ""))
    values["location"] = str(settings.get("location", ""))
    countries = {str(code).upper() for code in settings.get("countries", list(DEFAULT_COUNTRIES))}
    values["country_de"] = "DE" in countries
    values["country_at"] = "AT" in countries
    values["limit"] = str(settings.get("limit", DEFAULT_LIMIT))
    values["max_leads"] = str(settings.get("max_leads", DEFAULT_MAX_LEADS))
    values["workers"] = str(settings.get("workers", DEFAULT_WORKERS_TEXT))
    values["directory_parallel"] = str(settings.get("directory_parallel", DEFAULT_DIRECTORY_PARALLEL_TEXT))
    values["directory_detail_parallel"] = str(
        settings.get("directory_detail_parallel", DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT)
    )
    values["use_osm"] = bool(settings.get("use_osm", True))
    values["use_duckduckgo"] = bool(settings.get("use_duckduckgo", True))
    values["use_directories"] = bool(settings.get("use_directories", True))
    values["use_zenrows_google"] = bool(settings.get("use_zenrows_google", True))
    values["use_google_maps"] = bool(settings.get("use_google_maps", True))
    values["use_serpapi"] = bool(settings.get("use_serpapi", False))

    directory_sources = settings.get("directory_sources")
    if isinstance(directory_sources, list):
        enabled_ids = {str(source_id) for source_id in directory_sources}
        for spec in build_directory_source_registry():
            if spec.implemented:
                values[f"dir_source_{spec.id}"] = spec.id in enabled_ids


def run_gui_discovery(
    values: Mapping[str, str | bool],
    events: "queue.Queue[tuple]",
) -> int:
    category = require_text(values, "category", "Kategorie")
    location = str(values.get("location", "")).strip()
    output = Path(str(values.get("output", DEFAULT_OUTPUT)).strip() or DEFAULT_OUTPUT)
    suppression_path = optional_existing_path(values.get("suppression_file"))
    max_leads = parse_positive_int(values.get("max_leads"), int(DEFAULT_MAX_LEADS))
    limit = parse_positive_int(values.get("limit"), int(DEFAULT_LIMIT))
    workers = parse_positive_int(values.get("workers"), DEFAULT_WORKERS)
    directory_parallel = parse_positive_int(
        values.get("directory_parallel"),
        DEFAULT_DIRECTORY_PARALLEL_REQUESTS,
    )
    directory_parallel = max(1, min(directory_parallel, DIRECTORY_MAX_PARALLEL_REQUESTS))
    directory_detail_parallel = parse_positive_int(
        values.get("directory_detail_parallel"),
        DEFAULT_DIRECTORY_DETAIL_PARALLEL,
    )
    directory_detail_parallel = max(1, min(directory_detail_parallel, DIRECTORY_MAX_DETAIL_PARALLEL))
    checkpoint = Path(str(values.get("checkpoint", DEFAULT_CHECKPOINT)).strip() or DEFAULT_CHECKPOINT)
    resume = bool(values.get("resume", False))

    serpapi_key = str(values.get("serpapi_key", "")).strip() or os.getenv("SERPAPI_API_KEY", "").strip()
    zenrows_key = str(values.get("zenrows_key", "")).strip() or os.getenv("ZENROWS_API_KEY", "").strip()

    use_osm = bool(values.get("use_osm", True))
    use_duckduckgo = bool(values.get("use_duckduckgo", True))
    use_directories = bool(values.get("use_directories", True))
    use_zenrows_google = bool(values.get("use_zenrows_google", True))
    use_google_maps = bool(values.get("use_google_maps", True))
    use_serpapi = bool(values.get("use_serpapi", True))

    needs_zenrows = use_directories or use_zenrows_google or use_google_maps
    if needs_zenrows and not zenrows_key and not (
        use_directories and os.getenv("DIRECTORY_ALLOW_DIRECT_FETCH") == "1"
    ):
        raise SearchProviderError(
            "ZenRows-Quellen (Branchenverzeichnisse, Google, Google Maps) benoetigen einen ZenRows-Key."
        )
    if use_directories:
        selected_sources = selected_directory_source_ids(values)
        enabled_directory_sources = resolve_category_directory_sources(category, selected_sources)
        if not enabled_directory_sources:
            raise SearchProviderError("Aktiviere mindestens ein Branchenverzeichnis unter 'Branchenquellen'.")
    else:
        enabled_directory_sources = set()
    if use_zenrows_google and not zenrows_key:
        raise SearchProviderError("Google-Suche via ZenRows benoetigt einen ZenRows-Key.")
    if use_google_maps and not zenrows_key:
        raise SearchProviderError("Google Maps via ZenRows benoetigt einen ZenRows-Key.")
    if use_serpapi and not serpapi_key:
        raise SearchProviderError("Google-Suche via SerpAPI benoetigt einen SerpAPI-Key.")

    provider = combined_provider(
        use_osm=use_osm,
        use_duckduckgo=use_duckduckgo,
        use_directories=use_directories,
        use_zenrows_google=use_zenrows_google,
        use_google_maps=use_google_maps,
        use_serpapi=use_serpapi,
        serpapi_key=serpapi_key,
        zenrows_key=zenrows_key,
        enabled_directory_sources=enabled_directory_sources if use_directories else None,
        directory_parallel_requests=directory_parallel if use_directories else None,
        directory_detail_parallel_requests=directory_detail_parallel if use_directories else None,
        directory_mass_mode=limit >= 500 if use_directories else False,
    )
    if not getattr(provider, "providers", None):
        raise SearchProviderError(
            "Keine Suchquelle aktiv. Aktiviere mindestens eine Quelle unter 'Suchquellen'."
        )
    config = DiscoveryConfig(
        category=category,
        location=location,
        countries=selected_countries(values),
        limit=limit,
        max_pages_per_site=int(DEFAULT_MAX_PAGES),
        delay=float(DEFAULT_DELAY),
        include_personal=False,
        respect_robots=True,
        workers=workers,
        max_leads=max_leads,
        dedupe_by="email",
    )

    run_discovery(
        provider=provider,
        config=config,
        suppression=SuppressionList(suppression_path),
        output=output,
        on_event=lambda *event: events.put(event),
        checkpoint=checkpoint,
        resume=resume,
        gui_settings=collect_gui_settings(values),
    )
    return 0


def optional_existing_path(value: str | bool | None) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip())
    return path if path.exists() else None


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
    except ImportError as exc:
        raise RuntimeError("Tkinter ist nicht installiert. Bitte Python mit Tk-Unterstuetzung verwenden.") from exc

    class SimpleCapperGui:
        def __init__(self, root: "tk.Tk"):
            self.root = root
            self.root.title("Capper Lead Finder")
            self.messages: "queue.Queue[tuple]" = queue.Queue()
            self.worker: threading.Thread | None = None
            self._checkpoint_debounce_id: str | None = None
            self._gui_leads_shown = 0
            self._last_stats_refresh = 0.0
            self._pending_stats: LeadStats | None = None
            self._sites_since_log = 0
            self._log_lines_since_scroll = 0
            self._quiet_mode = False
            self._leads_this_poll = 0
            self._pending_messages: deque[tuple] = deque()
            self._progress_total = 0
            self._quiet_ui_tick_scheduled = False

            self.category = tk.StringVar(value="hotel")
            self.location = tk.StringVar(value="")
            self.output = tk.StringVar(value=DEFAULT_OUTPUT)
            self.suppression_file = tk.StringVar(value="examples/suppression.txt")
            self.max_leads = tk.StringVar(value=DEFAULT_MAX_LEADS)
            self.limit = tk.StringVar(value=DEFAULT_LIMIT)
            self.workers = tk.StringVar(value=DEFAULT_WORKERS_TEXT)
            self.directory_parallel = tk.StringVar(value=DEFAULT_DIRECTORY_PARALLEL_TEXT)
            self.directory_detail_parallel = tk.StringVar(value=DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT)
            self.serpapi_key = tk.StringVar(value=os.environ.get("SERPAPI_API_KEY", ""))
            self.zenrows_key = tk.StringVar(value=os.environ.get("ZENROWS_API_KEY", ""))
            self.use_osm = tk.BooleanVar(value=True)
            self.use_duckduckgo = tk.BooleanVar(value=True)
            self.use_directories = tk.BooleanVar(value=True)
            self.use_zenrows_google = tk.BooleanVar(value=True)
            self.use_google_maps = tk.BooleanVar(value=True)
            self.use_serpapi = tk.BooleanVar(value=True)
            self.directory_source_vars: dict[str, tk.BooleanVar] = {}
            for spec in build_directory_source_registry():
                if spec.implemented:
                    self.directory_source_vars[spec.id] = tk.BooleanVar(value=spec.default_enabled)
            self.country_de = tk.BooleanVar(value=True)
            self.country_at = tk.BooleanVar(value=False)
            self.checkpoint = tk.StringVar(value=DEFAULT_CHECKPOINT)
            self.resume = tk.BooleanVar(value=False)
            self.status_text = tk.StringVar(value="Bereit.")
            self.lead_count_text = tk.StringVar(value="Gefundene Leads: 0")
            self.stats_text = tk.StringVar(value="Statistik: noch keine Suche gestartet.")
            self.current_page_text = tk.StringVar(value="Aktuelle Seite: -")
            self.progress_value = tk.DoubleVar(value=0)

            self._build()
            self._poll_messages()

        def _build(self) -> None:
            self.root.minsize(480, 420)
            self.root.geometry("820x680")

            outer = ttk.Frame(self.root, padding=16)
            outer.grid(row=0, column=0, sticky="nsew")
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            outer.columnconfigure(0, weight=1)
            outer.rowconfigure(2, weight=1)

            self.start_button = ttk.Button(outer, text="Leads suchen", command=self._start)
            self.start_button.grid(row=0, column=0, sticky="ew", pady=(0, 8))

            title = ttk.Label(outer, text="Welche Branche soll gesucht werden?", font=("", 14, "bold"))
            title.grid(row=1, column=0, sticky="w", pady=(0, 8))

            scroll_container = ttk.Frame(outer)
            scroll_container.grid(row=2, column=0, sticky="nsew")
            scroll_container.columnconfigure(0, weight=1)
            scroll_container.rowconfigure(0, weight=1)

            main_canvas = tk.Canvas(scroll_container, highlightthickness=0)
            main_scroll = ttk.Scrollbar(scroll_container, orient="vertical", command=main_canvas.yview)
            content = ttk.Frame(main_canvas)
            content.bind(
                "<Configure>",
                lambda _event: main_canvas.configure(scrollregion=main_canvas.bbox("all")),
            )
            content_window = main_canvas.create_window((0, 0), window=content, anchor="nw")
            main_canvas.configure(yscrollcommand=main_scroll.set)
            main_canvas.grid(row=0, column=0, sticky="nsew")
            main_scroll.grid(row=0, column=1, sticky="ns")

            def _resize_content(event: "tk.Event") -> None:
                main_canvas.itemconfigure(content_window, width=event.width)

            main_canvas.bind("<Configure>", _resize_content)
            self._bind_canvas_scroll(main_canvas, scroll_container)

            content.columnconfigure(1, weight=1)

            ttk.Label(content, text="Kategorie").grid(row=0, column=0, sticky="w", pady=4)
            ttk.Entry(content, textvariable=self.category).grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

            ttk.Label(content, text="Ort optional").grid(row=1, column=0, sticky="w", pady=4)
            ttk.Entry(content, textvariable=self.location).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)

            countries_frame = ttk.Frame(content)
            countries_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
            ttk.Label(countries_frame, text="Laender").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Checkbutton(countries_frame, text="Deutschland", variable=self.country_de).grid(row=0, column=1, padx=(0, 12))
            ttk.Checkbutton(countries_frame, text="Oesterreich", variable=self.country_at).grid(row=0, column=2)

            limits_frame = ttk.Frame(content)
            limits_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
            ttk.Label(limits_frame, text="Max. Leads").grid(row=0, column=0, sticky="w")
            ttk.Entry(limits_frame, textvariable=self.max_leads, width=10).grid(row=0, column=1, padx=(4, 16))
            ttk.Label(limits_frame, text="Websites (max)").grid(row=0, column=2, sticky="w")
            ttk.Entry(limits_frame, textvariable=self.limit, width=10).grid(row=0, column=3, padx=(4, 16))
            ttk.Label(limits_frame, text="Threads").grid(row=0, column=4, sticky="w")
            ttk.Entry(limits_frame, textvariable=self.workers, width=6).grid(row=0, column=5, padx=(4, 16))
            ttk.Label(limits_frame, text="ZenRows parallel").grid(row=1, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(limits_frame, textvariable=self.directory_parallel, width=6).grid(row=1, column=1, padx=(4, 16), pady=(4, 0))
            ttk.Label(limits_frame, text="Detail parallel").grid(row=1, column=2, sticky="w", pady=(4, 0))
            ttk.Entry(limits_frame, textvariable=self.directory_detail_parallel, width=6).grid(
                row=1, column=3, padx=(4, 16), pady=(4, 0)
            )
            ttk.Label(
                limits_frame,
                text=f"(Quellen / Detailseiten; max {DIRECTORY_MAX_PARALLEL_REQUESTS}/{DIRECTORY_MAX_DETAIL_PARALLEL})",
                foreground="#555",
            ).grid(row=1, column=4, columnspan=2, sticky="w", pady=(4, 0))
            ttk.Label(
                limits_frame,
                text=f"(Crawling; max {CRAWL_MAX_WORKERS} parallel fuer Stabilitaet)",
                foreground="#555",
            ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(2, 0))

            ttk.Label(content, text="CSV-Ausgabe").grid(row=4, column=0, sticky="w", pady=4)
            ttk.Entry(content, textvariable=self.output).grid(row=4, column=1, sticky="ew", pady=4)
            ttk.Button(content, text="Auswaehlen", command=self._choose_output).grid(row=4, column=2, padx=(8, 0), pady=4)

            checkpoint_frame = ttk.Frame(content)
            checkpoint_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=4)
            checkpoint_frame.columnconfigure(1, weight=1)
            ttk.Label(checkpoint_frame, text="Checkpoint").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Entry(checkpoint_frame, textvariable=self.checkpoint).grid(row=0, column=1, sticky="ew")
            ttk.Checkbutton(
                checkpoint_frame,
                text="Fortsetzen",
                variable=self.resume,
                command=self._on_resume_toggled,
            ).grid(row=0, column=2, padx=(12, 0))

            self.checkpoint.trace_add("write", self._on_checkpoint_path_changed)

            ttk.Label(content, text="Opt-out Liste").grid(row=6, column=0, sticky="w", pady=4)
            ttk.Entry(content, textvariable=self.suppression_file).grid(row=6, column=1, sticky="ew", pady=4)
            ttk.Button(content, text="Auswaehlen", command=self._choose_suppression).grid(row=6, column=2, padx=(8, 0), pady=4)

            keys_frame = ttk.Frame(content)
            keys_frame.grid(row=7, column=0, columnspan=3, sticky="ew", pady=4)
            keys_frame.columnconfigure(1, weight=1)
            keys_frame.columnconfigure(3, weight=1)
            ttk.Label(keys_frame, text="SerpAPI Key").grid(row=0, column=0, sticky="w", padx=(0, 4))
            ttk.Entry(keys_frame, textvariable=self.serpapi_key, show="*").grid(row=0, column=1, sticky="ew", padx=(0, 12))
            ttk.Label(keys_frame, text="ZenRows Key").grid(row=0, column=2, sticky="w", padx=(0, 4))
            ttk.Entry(keys_frame, textvariable=self.zenrows_key, show="*").grid(row=0, column=3, sticky="ew")

            sources_frame = ttk.LabelFrame(content, text="Suchquellen", padding=8)
            sources_frame.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 4))
            sources_frame.columnconfigure(1, weight=1)

            free_sources = ttk.Frame(sources_frame)
            free_sources.grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(free_sources, text="Ohne API-Key:").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Checkbutton(free_sources, text="OpenStreetMap", variable=self.use_osm).grid(row=0, column=1, padx=(0, 12))
            ttk.Checkbutton(free_sources, text="DuckDuckGo", variable=self.use_duckduckgo).grid(row=0, column=2)

            api_sources = ttk.Frame(sources_frame)
            api_sources.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Label(api_sources, text="Mit API-Key:").grid(row=0, column=0, sticky="nw", padx=(0, 8))
            api_checks = ttk.Frame(api_sources)
            api_checks.grid(row=0, column=1, sticky="w")
            ttk.Checkbutton(
                api_checks,
                text="Branchenverzeichnisse via ZenRows aktivieren",
                variable=self.use_directories,
            ).grid(row=0, column=0, sticky="w")
            ttk.Checkbutton(
                api_checks,
                text="Google-Suche via ZenRows",
                variable=self.use_zenrows_google,
            ).grid(row=1, column=0, sticky="w", pady=(4, 0))
            ttk.Checkbutton(
                api_checks,
                text="Google Maps via ZenRows (experimentell)",
                variable=self.use_google_maps,
            ).grid(row=2, column=0, sticky="w", pady=(4, 0))
            ttk.Checkbutton(
                api_checks,
                text="Google-Suche via SerpAPI",
                variable=self.use_serpapi,
            ).grid(row=3, column=0, sticky="w", pady=(4, 0))

            directory_frame = ttk.LabelFrame(content, text="Branchenquellen (ZenRows Universal API)", padding=8)
            directory_frame.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 4))
            directory_frame.columnconfigure(0, weight=1)
            directory_frame.rowconfigure(0, weight=1)

            directory_canvas = tk.Canvas(directory_frame, height=120, highlightthickness=0)
            directory_scroll = ttk.Scrollbar(directory_frame, orient="vertical", command=directory_canvas.yview)
            directory_inner = ttk.Frame(directory_canvas)
            directory_inner.bind(
                "<Configure>",
                lambda _event: directory_canvas.configure(scrollregion=directory_canvas.bbox("all")),
            )
            directory_window = directory_canvas.create_window((0, 0), window=directory_inner, anchor="nw")
            directory_canvas.configure(yscrollcommand=directory_scroll.set)
            directory_canvas.grid(row=0, column=0, sticky="nsew")
            directory_scroll.grid(row=0, column=1, sticky="ns")

            def _resize_directory(event: "tk.Event") -> None:
                directory_canvas.itemconfigure(directory_window, width=event.width)

            directory_canvas.bind("<Configure>", _resize_directory)
            self._bind_canvas_scroll(directory_canvas, directory_frame)

            row_idx = 0
            for category, specs in directory_sources_by_category(build_directory_source_registry()).items():
                if not specs:
                    continue
                implemented_specs = [spec for spec in specs if spec.implemented]
                unavailable_specs = [spec for spec in specs if spec.unavailable]
                planned_specs = [spec for spec in specs if not spec.implemented and not spec.unavailable]
                if not implemented_specs and not planned_specs and not unavailable_specs:
                    continue
                ttk.Label(directory_inner, text=category, font=("", 10, "bold")).grid(
                    row=row_idx, column=0, sticky="w", pady=(8 if row_idx else 0, 4)
                )
                row_idx += 1
                for spec in implemented_specs:
                    ttk.Checkbutton(
                        directory_inner,
                        text=spec.label,
                        variable=self.directory_source_vars[spec.id],
                    ).grid(row=row_idx, column=0, sticky="w", padx=(12, 0))
                    row_idx += 1
                if unavailable_specs:
                    unavailable_text = ", ".join(spec.label for spec in unavailable_specs[:8])
                    if len(unavailable_specs) > 8:
                        unavailable_text += f" (+{len(unavailable_specs) - 8} weitere)"
                    ttk.Label(
                        directory_inner,
                        text=f"Nicht verfuegbar (ZenRows/JS): {unavailable_text}",
                        foreground="#888",
                        wraplength=560,
                    ).grid(row=row_idx, column=0, sticky="w", padx=(12, 0), pady=(2, 0))
                    row_idx += 1
                if planned_specs:
                    planned_text = ", ".join(spec.label for spec in planned_specs[:8])
                    if len(planned_specs) > 8:
                        planned_text += f" (+{len(planned_specs) - 8} weitere geplant)"
                    ttk.Label(
                        directory_inner,
                        text=f"Geplant: {planned_text}",
                        foreground="#666",
                        wraplength=560,
                    ).grid(row=row_idx, column=0, sticky="w", padx=(12, 0), pady=(2, 0))
                    row_idx += 1

            source_text = (
                "Aktiviere einzelne Branchenquellen. Implementierte Quellen laufen ueber ZenRows "
                "(Adaptive Stealth). Quellen ohne brauchbare Ergebnisse sind als 'Nicht verfuegbar' "
                "markiert; weitere Kategorien werden schrittweise ergaenzt."
            )
            ttk.Label(content, text=source_text, wraplength=560).grid(row=10, column=0, columnspan=3, sticky="ew", pady=(10, 8))

            columns = ("company", "email", "website", "status")
            self.lead_table = ttk.Treeview(content, columns=columns, show="headings", height=6)
            self.lead_table.heading("company", text="Firma")
            self.lead_table.heading("email", text="E-Mail")
            self.lead_table.heading("website", text="Website")
            self.lead_table.heading("status", text="Status")
            self.lead_table.column("company", width=160, minwidth=80)
            self.lead_table.column("email", width=160, minwidth=80)
            self.lead_table.column("website", width=200, minwidth=100)
            self.lead_table.column("status", width=90, minwidth=70)
            self.lead_table.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(8, 8))

            self.log = scrolledtext.ScrolledText(content, height=6, state="disabled")
            self.log.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(0, 4))

            footer = ttk.Frame(outer)
            footer.grid(row=3, column=0, sticky="ew", pady=(8, 0))
            footer.columnconfigure(0, weight=1)

            self.progress = ttk.Progressbar(footer, variable=self.progress_value, maximum=1)
            self.progress.grid(row=0, column=0, sticky="ew")

            status_row = ttk.Frame(footer)
            status_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
            status_row.columnconfigure(0, weight=1)
            ttk.Label(status_row, textvariable=self.status_text).grid(row=0, column=0, sticky="w")
            ttk.Label(status_row, textvariable=self.lead_count_text).grid(row=0, column=1, sticky="e")

            ttk.Label(footer, textvariable=self.stats_text).grid(row=2, column=0, sticky="w", pady=(2, 0))
            ttk.Label(footer, textvariable=self.current_page_text, foreground="#555").grid(
                row=3, column=0, sticky="w", pady=(0, 2)
            )

        def _bind_canvas_scroll(self, canvas: "tk.Canvas", widget: "tk.Widget") -> None:
            def _on_mousewheel(event: "tk.Event") -> None:
                if event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")
                elif getattr(event, "delta", 0):
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

            def _bind(_event: "tk.Event | None" = None) -> None:
                canvas.bind_all("<MouseWheel>", _on_mousewheel)
                canvas.bind_all("<Button-4>", _on_mousewheel)
                canvas.bind_all("<Button-5>", _on_mousewheel)

            def _unbind(_event: "tk.Event | None" = None) -> None:
                canvas.unbind_all("<MouseWheel>")
                canvas.unbind_all("<Button-4>")
                canvas.unbind_all("<Button-5>")

            widget.bind("<Enter>", _bind)
            widget.bind("<Leave>", _unbind)

        def _choose_output(self) -> None:
            selected = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("JSON", "*.json"), ("Alle Dateien", "*.*")],
            )
            if selected:
                self.output.set(selected)

        def _choose_suppression(self) -> None:
            selected = filedialog.askopenfilename(filetypes=[("Textdateien", "*.txt"), ("Alle Dateien", "*.*")])
            if selected:
                self.suppression_file.set(selected)

        def _gui_values_dict(self) -> dict[str, object]:
            values: dict[str, object] = {
                "category": self.category.get(),
                "location": self.location.get(),
                "max_leads": self.max_leads.get(),
                "limit": self.limit.get(),
                "workers": self.workers.get(),
                "directory_parallel": self.directory_parallel.get(),
                "directory_detail_parallel": self.directory_detail_parallel.get(),
                "use_osm": self.use_osm.get(),
                "use_duckduckgo": self.use_duckduckgo.get(),
                "use_directories": self.use_directories.get(),
                "use_zenrows_google": self.use_zenrows_google.get(),
                "use_google_maps": self.use_google_maps.get(),
                "use_serpapi": self.use_serpapi.get(),
                "country_de": self.country_de.get(),
                "country_at": self.country_at.get(),
            }
            for source_id, var in self.directory_source_vars.items():
                values[f"dir_source_{source_id}"] = var.get()
            return values

        def _apply_checkpoint_settings(self, settings: Mapping[str, object]) -> None:
            values = self._gui_values_dict()
            apply_gui_settings(values, settings)
            self.category.set(str(values["category"]))
            self.location.set(str(values["location"]))
            self.max_leads.set(str(values["max_leads"]))
            self.limit.set(str(values["limit"]))
            self.workers.set(str(values["workers"]))
            self.directory_parallel.set(str(values["directory_parallel"]))
            self.directory_detail_parallel.set(
                str(values.get("directory_detail_parallel", DEFAULT_DIRECTORY_DETAIL_PARALLEL_TEXT))
            )
            self.use_osm.set(bool(values["use_osm"]))
            self.use_duckduckgo.set(bool(values["use_duckduckgo"]))
            self.use_directories.set(bool(values["use_directories"]))
            self.use_zenrows_google.set(bool(values["use_zenrows_google"]))
            self.use_google_maps.set(bool(values.get("use_google_maps", True)))
            self.use_serpapi.set(bool(values["use_serpapi"]))
            self.country_de.set(bool(values["country_de"]))
            self.country_at.set(bool(values["country_at"]))
            for source_id, var in self.directory_source_vars.items():
                var.set(bool(values.get(f"dir_source_{source_id}", var.get())))

        def _load_checkpoint_into_form(self, *, show_errors: bool = True, sync: bool = False) -> bool:
            path = Path(self.checkpoint.get().strip() or DEFAULT_CHECKPOINT)
            if sync:
                try:
                    settings = checkpoint_settings_for_gui(path)
                except Exception as exc:  # noqa: BLE001 - surface load errors in the GUI
                    if show_errors:
                        messagebox.showerror("Checkpoint", f"Checkpoint konnte nicht gelesen werden: {exc}")
                        self.resume.set(False)
                    return False
                if settings is None:
                    if show_errors:
                        messagebox.showwarning("Checkpoint", f"Datei nicht gefunden: {path}")
                        self.resume.set(False)
                    return False
                self._apply_checkpoint_settings(settings)
                summary = str(settings.get("progress_summary", ""))
                self.status_text.set(f"Einstellungen aus Checkpoint geladen ({summary}).")
                self._append_log(f"Checkpoint-Einstellungen geladen: {path} — {summary}\n")
                return True

            self.status_text.set("Lade Checkpoint-Metadaten ...")

            def worker() -> None:
                try:
                    settings = checkpoint_settings_for_gui(path)
                    self.messages.put(("checkpoint_settings", settings, str(path), show_errors))
                except Exception as exc:  # noqa: BLE001 - surface load errors in the GUI
                    self.messages.put(("checkpoint_error", str(exc), show_errors))

            threading.Thread(target=worker, name="capper-checkpoint-gui", daemon=True).start()
            return True

        def _apply_checkpoint_settings_message(
            self,
            settings: dict[str, object] | None,
            path: str,
            *,
            show_errors: bool,
        ) -> None:
            if settings is None:
                if show_errors:
                    messagebox.showwarning("Checkpoint", f"Datei nicht gefunden: {path}")
                    self.resume.set(False)
                return
            self._apply_checkpoint_settings(settings)
            summary = str(settings.get("progress_summary", ""))
            self.status_text.set(f"Einstellungen aus Checkpoint geladen ({summary}).")
            self._append_log(f"Checkpoint-Einstellungen geladen: {path} — {summary}\n")

        def _on_resume_toggled(self) -> None:
            if self.resume.get():
                self._load_checkpoint_into_form()

        def _on_checkpoint_path_changed(self, *_args: object) -> None:
            if not self.resume.get():
                return
            if self._checkpoint_debounce_id is not None:
                self.root.after_cancel(self._checkpoint_debounce_id)
            self._checkpoint_debounce_id = self.root.after(
                CHECKPOINT_PATH_DEBOUNCE_MS,
                self._debounced_checkpoint_load,
            )

        def _debounced_checkpoint_load(self) -> None:
            self._checkpoint_debounce_id = None
            self._load_checkpoint_into_form(show_errors=False)

        def _start(self) -> None:
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("Capper", "Die Suche laeuft bereits.")
                return

            if self.resume.get():
                path = Path(self.checkpoint.get().strip() or DEFAULT_CHECKPOINT)
                if not path.exists():
                    messagebox.showwarning("Checkpoint", f"Datei nicht gefunden: {path}")
                    return

            values = {
                "category": self.category.get(),
                "location": self.location.get(),
                "output": self.output.get(),
                "suppression_file": self.suppression_file.get(),
                "max_leads": self.max_leads.get(),
                "limit": self.limit.get(),
                "workers": self.workers.get(),
                "directory_parallel": self.directory_parallel.get(),
                "directory_detail_parallel": self.directory_detail_parallel.get(),
                "serpapi_key": self.serpapi_key.get(),
                "zenrows_key": self.zenrows_key.get(),
                "use_osm": self.use_osm.get(),
                "use_duckduckgo": self.use_duckduckgo.get(),
                "use_directories": self.use_directories.get(),
                "use_zenrows_google": self.use_zenrows_google.get(),
                "use_google_maps": self.use_google_maps.get(),
                "use_serpapi": self.use_serpapi.get(),
                **{f"dir_source_{source_id}": var.get() for source_id, var in self.directory_source_vars.items()},
                "country_de": self.country_de.get(),
                "country_at": self.country_at.get(),
                "checkpoint": self.checkpoint.get(),
                "resume": self.resume.get(),
            }
            try:
                require_text(values, "category", "Kategorie")
            except ValueError as exc:
                messagebox.showerror("Eingabe pruefen", str(exc))
                return

            self._reset_run()
            self._append_log("\nStarte vollautomatische Suche fuer Kategorie: " + self.category.get().strip() + "\n")
            self.start_button.configure(state="disabled")
            self.worker = threading.Thread(target=self._run_discovery, args=(values,), daemon=True)
            self.worker.start()

        def _reset_run(self) -> None:
            self.progress.configure(maximum=1)
            self.progress_value.set(0)
            self.status_text.set("Starte Suche ...")
            self.lead_count_text.set("Gefundene Leads: 0")
            self.stats_text.set("Statistik: Suche wird vorbereitet ...")
            self.current_page_text.set("Aktuelle Seite: -")
            self._gui_leads_shown = 0
            self._sites_since_log = 0
            self._pending_stats = None
            self._last_stats_refresh = 0.0
            self._log_lines_since_scroll = 0
            self._quiet_mode = False
            self._leads_this_poll = 0
            self._pending_messages.clear()
            self._progress_total = 0
            self._quiet_ui_tick_scheduled = False
            for item in self.lead_table.get_children():
                self.lead_table.delete(item)
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")

        def _run_discovery(self, values: Mapping[str, str | bool]) -> None:
            try:
                exit_code = run_gui_discovery(values, self.messages)
            except Exception as exc:
                self.messages.put(("error", str(exc)))
                exit_code = 2
            self.messages.put(("done", exit_code))

        def _poll_messages(self) -> None:
            started = time.monotonic()
            batch: list[tuple] = []
            while self._pending_messages and len(batch) < GUI_MAX_DRAIN_PER_CYCLE:
                batch.append(self._pending_messages.popleft())
            batch.extend(self._drain_messages())
            if len(batch) > 1:
                batch = coalesce_gui_messages(batch)
            processed = 0
            self._leads_this_poll = 0
            leftover: list[tuple] = []
            for message in batch:
                if processed >= GUI_MESSAGES_PER_POLL:
                    leftover.append(message)
                    continue
                if (time.monotonic() - started) * 1000 >= GUI_POLL_TIME_BUDGET_MS:
                    leftover.append(message)
                    continue
                self._handle_message(message)
                processed += 1
            if leftover:
                if len(leftover) > GUI_MAX_PENDING_MESSAGES:
                    leftover = coalesce_gui_messages(leftover)[-GUI_MAX_PENDING_MESSAGES:]
                self._pending_messages.extend(leftover)
            self._flush_stats_if_due()
            self.root.after(GUI_POLL_INTERVAL_MS, self._poll_messages)

        def _drain_messages(self) -> list[tuple]:
            batch: list[tuple] = []
            while len(batch) < GUI_MAX_DRAIN_PER_CYCLE:
                try:
                    batch.append(self.messages.get_nowait())
                except queue.Empty:
                    break
            return batch

        def _note_stats(self, stats: LeadStats) -> None:
            self._pending_stats = stats

        def _flush_stats_if_due(self, *, force: bool = False) -> None:
            if self._pending_stats is None:
                return
            now = time.monotonic()
            if not force and now - self._last_stats_refresh < GUI_UI_UPDATE_INTERVAL_S:
                return
            stats = self._pending_stats
            self._pending_stats = None
            self._last_stats_refresh = now
            self._apply_stats(stats)

        def _apply_stats(self, stats: LeadStats) -> None:
            total = max(stats.websites_total, 1)
            if self._progress_total != total:
                self.progress.configure(maximum=total)
                self._progress_total = total
            self.progress_value.set(stats.websites_done)
            self.status_text.set(
                f"Website {stats.websites_done}/{stats.websites_total} · {stats.leads_per_minute} Leads/min"
            )
            self._update_stats(stats)

        def _schedule_quiet_ui_tick(self) -> None:
            if self._quiet_ui_tick_scheduled:
                return
            self._quiet_ui_tick_scheduled = True
            self.root.after(GUI_QUIET_UI_INTERVAL_MS, self._quiet_ui_tick)

        def _quiet_ui_tick(self) -> None:
            self._quiet_ui_tick_scheduled = False
            if not self._quiet_mode:
                return
            self._flush_stats_if_due(force=True)
            if self.worker and self.worker.is_alive():
                self._schedule_quiet_ui_tick()

        def _update_stats(self, stats: LeadStats) -> None:
            self.lead_count_text.set(f"Gefundene Leads: {stats.leads_found}")
            self.stats_text.set(
                f"Websites {stats.websites_done}/{stats.websites_total} · "
                f"Seiten {stats.pages_fetched} · "
                f"Firmen-Domains {stats.unique_domains} · "
                f"Duplikate uebersprungen {stats.duplicates_skipped} · "
                f"Gesperrt {stats.suppressed_skipped} · "
                f"{stats.leads_per_minute}/min"
            )

        def _handle_message(self, message: tuple) -> None:
            kind = message[0]
            if kind == "checkpoint_settings":
                settings, path, show_errors = message[1], message[2], message[3]
                self._apply_checkpoint_settings_message(settings, path, show_errors=show_errors)
            elif kind == "checkpoint_error":
                error_text, show_errors = message[1], message[2]
                if show_errors:
                    messagebox.showerror("Checkpoint", f"Checkpoint konnte nicht gelesen werden: {error_text}")
                    self.resume.set(False)
                self.status_text.set("Checkpoint konnte nicht geladen werden.")
            elif kind == "status":
                text = str(message[1])
                if self._quiet_mode:
                    if any(marker in text for marker in ("Fehler", "Fertig", "Optimiere", "Checkpoint geladen")):
                        self.status_text.set(text)
                        self._append_log(text + "\n")
                    return
                self.status_text.set(text)
                if "fortgesetzt" in text or "Crawling aktiv" in text:
                    self.stats_text.set("Statistik: Crawling läuft, erste Website wird geprüft ...")
                elif "Checkpoint geladen" in text:
                    self.stats_text.set("Statistik: Checkpoint geladen, bereite Crawling vor ...")
                if any(
                    marker in text
                    for marker in (
                        "Checkpoint",
                        "Fertig",
                        "Fehler",
                        "fortgesetzt",
                        "Starte Crawling",
                        "Websites gefunden",
                        "Optimiere",
                    )
                ):
                    self._append_log(text + "\n")
            elif kind == "total":
                total = max(int(message[1]), 1)
                self.progress.configure(maximum=total)
                self.progress_value.set(0)
                self.status_text.set(f"{message[1]} Websites gefunden. Starte paralleles Crawling ...")
            elif kind == "quiet":
                self._quiet_mode = bool(message[1])
                if self._quiet_mode:
                    self._schedule_quiet_ui_tick()
            elif kind == "progress":
                stats = message[1]
                self._note_stats(stats)
                if self._quiet_mode:
                    return
                self._flush_stats_if_due()
                if stats.websites_done == 0 and stats.websites_total > 0:
                    self.stats_text.set(
                        f"Statistik: Crawling startet · {stats.leads_found} Leads bisher · "
                        f"0/{stats.websites_total} Websites in diesem Lauf"
                    )
            elif kind == "page":
                url, count = message[1], message[2]
                self.current_page_text.set(f"Aktuelle Seite ({count}): {url}")
                if count == 1 or count % GUI_LOG_EVERY_N_PAGES == 0:
                    self._append_log(f"  geprueft: {url} ({count} Seiten)\n")
            elif kind == "site_done":
                _url, _new_leads, stats = message[1], message[2], message[3]
                self._note_stats(stats)
                if self._quiet_mode:
                    return
                self._flush_stats_if_due()
                self._sites_since_log += 1
                if self._sites_since_log >= GUI_SITE_LOG_EVERY:
                    self._sites_since_log = 0
                    self._append_log(
                        f"Fortschritt: {stats.websites_done}/{stats.websites_total} Websites, "
                        f"{stats.leads_found} Leads\n"
                    )
            elif kind == "warning":
                self._append_log("Hinweis: " + message[1] + "\n")
            elif kind == "lead":
                if self._quiet_mode:
                    if len(message) > 2:
                        self._note_stats(message[2])
                    return
                if self._leads_this_poll >= GUI_LEADS_PER_POLL:
                    return
                lead = message[1]
                if len(message) > 2:
                    self._note_stats(message[2])
                if self._gui_leads_shown < DEFAULT_GUI_LEAD_ROWS:
                    self.lead_table.insert(
                        "",
                        "end",
                        values=(
                            lead.company_name,
                            lead.email,
                            lead.website,
                            lead.consent_status.value,
                        ),
                    )
                    self._gui_leads_shown += 1
                    self._leads_this_poll += 1
                elif self._gui_leads_shown == DEFAULT_GUI_LEAD_ROWS:
                    self._gui_leads_shown += 1
                    self._append_log(
                        f"Hinweis: Weitere Leads werden nur in der CSV gespeichert "
                        f"(GUI-Anzeige auf {DEFAULT_GUI_LEAD_ROWS} begrenzt).\n",
                        scroll=True,
                    )
            elif kind == "finished":
                stats, output = message[1], message[2]
                self._note_stats(stats)
                self._flush_stats_if_due(force=True)
                self.status_text.set(f"Fertig. {stats.leads_found} Leads geschrieben nach {output}.")
                self._append_log(
                    f"Fertig. {stats.leads_found} Leads, {stats.duplicates_skipped} Duplikate uebersprungen, "
                    f"geschrieben nach {output}.\n"
                )
            elif kind == "error":
                self.status_text.set("Fehler: " + message[1])
                self._append_log("Fehler: " + message[1] + "\n")
                messagebox.showerror("Capper", message[1])
            elif kind == "done":
                self._flush_stats_if_due(force=True)
                self.start_button.configure(state="normal")
                self._append_log(f"Fertig mit Exit-Code {message[1]}.\n", scroll=True)

        def _append_log(self, text: str, *, scroll: bool = False) -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text)
            try:
                line_count = int(self.log.index("end-1c").split(".")[0])
            except (tk.TclError, ValueError):
                line_count = 0
            if line_count > GUI_MAX_LOG_LINES:
                self.log.delete("1.0", f"{line_count - GUI_MAX_LOG_LINES}.0")
            self._log_lines_since_scroll += text.count("\n")
            if scroll or self._log_lines_since_scroll >= GUI_LOG_SCROLL_EVERY:
                self.log.see("end")
                self._log_lines_since_scroll = 0
            self.log.configure(state="disabled")

    root = tk.Tk()
    SimpleCapperGui(root)
    root.mainloop()
    return 0
