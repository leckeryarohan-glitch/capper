from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Mapping

from .pipeline import DEFAULT_WORKERS, DiscoveryConfig, LeadStats, run_discovery
from .search import provider_from_name
from .suppression import SuppressionList


DEFAULT_OUTPUT = "leads.csv"
DEFAULT_LIMIT = "500"
DEFAULT_MAX_PAGES = "3"
DEFAULT_DELAY = "0.3"
DEFAULT_MAX_LEADS = "2000"
DEFAULT_WORKERS_TEXT = str(DEFAULT_WORKERS)
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

    provider = provider_from_name(DEFAULT_PROVIDER)
    config = DiscoveryConfig(
        category=category,
        location=location,
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

            self.category = tk.StringVar(value="hotel")
            self.location = tk.StringVar(value="")
            self.output = tk.StringVar(value=DEFAULT_OUTPUT)
            self.suppression_file = tk.StringVar(value="examples/suppression.txt")
            self.max_leads = tk.StringVar(value=DEFAULT_MAX_LEADS)
            self.limit = tk.StringVar(value=DEFAULT_LIMIT)
            self.workers = tk.StringVar(value=DEFAULT_WORKERS_TEXT)
            self.status_text = tk.StringVar(value="Bereit.")
            self.lead_count_text = tk.StringVar(value="Gefundene Leads: 0")
            self.stats_text = tk.StringVar(value="Statistik: noch keine Suche gestartet.")
            self.progress_value = tk.DoubleVar(value=0)

            self._build()
            self._poll_messages()

        def _build(self) -> None:
            outer = ttk.Frame(self.root, padding=16)
            outer.grid(row=0, column=0, sticky="nsew")
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            outer.columnconfigure(1, weight=1)

            title = ttk.Label(outer, text="Welche Branche soll gesucht werden?", font=("", 14, "bold"))
            title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

            ttk.Label(outer, text="Kategorie").grid(row=1, column=0, sticky="w", pady=4)
            ttk.Entry(outer, textvariable=self.category).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)

            ttk.Label(outer, text="Ort optional").grid(row=2, column=0, sticky="w", pady=4)
            ttk.Entry(outer, textvariable=self.location).grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)

            limits_frame = ttk.Frame(outer)
            limits_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
            ttk.Label(limits_frame, text="Max. Leads").grid(row=0, column=0, sticky="w")
            ttk.Entry(limits_frame, textvariable=self.max_leads, width=10).grid(row=0, column=1, padx=(4, 16))
            ttk.Label(limits_frame, text="Websites (max)").grid(row=0, column=2, sticky="w")
            ttk.Entry(limits_frame, textvariable=self.limit, width=10).grid(row=0, column=3, padx=(4, 16))
            ttk.Label(limits_frame, text="Threads").grid(row=0, column=4, sticky="w")
            ttk.Entry(limits_frame, textvariable=self.workers, width=6).grid(row=0, column=5, padx=(4, 0))

            ttk.Label(outer, text="CSV-Ausgabe").grid(row=4, column=0, sticky="w", pady=4)
            ttk.Entry(outer, textvariable=self.output).grid(row=4, column=1, sticky="ew", pady=4)
            ttk.Button(outer, text="Auswaehlen", command=self._choose_output).grid(row=4, column=2, padx=(8, 0), pady=4)

            ttk.Label(outer, text="Opt-out Liste").grid(row=5, column=0, sticky="w", pady=4)
            ttk.Entry(outer, textvariable=self.suppression_file).grid(row=5, column=1, sticky="ew", pady=4)
            ttk.Button(outer, text="Auswaehlen", command=self._choose_suppression).grid(row=5, column=2, padx=(8, 0), pady=4)

            source_text = (
                "Vollautomatisch ohne API-Key: Capper kombiniert OpenStreetMap/Overpass, Nominatim "
                "und DuckDuckGo, findet reale Unternehmen mit Website und durchsucht diese parallel "
                "nach oeffentlichen B2B-Kontakten. Doppelte E-Mails werden automatisch entfernt."
            )
            ttk.Label(outer, text=source_text, wraplength=760).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 8))

            self.start_button = ttk.Button(outer, text="Leads suchen", command=self._start)
            self.start_button.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 10))

            self.progress = ttk.Progressbar(outer, variable=self.progress_value, maximum=1)
            self.progress.grid(row=8, column=0, columnspan=3, sticky="ew")
            ttk.Label(outer, textvariable=self.status_text).grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))
            ttk.Label(outer, textvariable=self.lead_count_text).grid(row=9, column=2, sticky="e", pady=(4, 0))
            ttk.Label(outer, textvariable=self.stats_text).grid(row=10, column=0, columnspan=3, sticky="w", pady=(2, 6))

            columns = ("company", "email", "website", "status")
            self.lead_table = ttk.Treeview(outer, columns=columns, show="headings", height=8)
            self.lead_table.heading("company", text="Firma")
            self.lead_table.heading("email", text="E-Mail")
            self.lead_table.heading("website", text="Website")
            self.lead_table.heading("status", text="Status")
            self.lead_table.column("company", width=180)
            self.lead_table.column("email", width=180)
            self.lead_table.column("website", width=260)
            self.lead_table.column("status", width=110)
            self.lead_table.grid(row=11, column=0, columnspan=3, sticky="nsew", pady=(8, 8))

            self.log = scrolledtext.ScrolledText(outer, height=8, state="disabled")
            self.log.grid(row=12, column=0, columnspan=3, sticky="nsew")
            outer.rowconfigure(11, weight=1)
            outer.rowconfigure(12, weight=1)

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

        def _start(self) -> None:
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("Capper", "Die Suche laeuft bereits.")
                return

            values = {
                "category": self.category.get(),
                "location": self.location.get(),
                "output": self.output.get(),
                "suppression_file": self.suppression_file.get(),
                "max_leads": self.max_leads.get(),
                "limit": self.limit.get(),
                "workers": self.workers.get(),
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
            for item in self.lead_table.get_children():
                self.lead_table.delete(item)

        def _run_discovery(self, values: Mapping[str, str | bool]) -> None:
            try:
                exit_code = run_gui_discovery(values, self.messages)
            except Exception as exc:
                self.messages.put(("error", str(exc)))
                exit_code = 2
            self.messages.put(("done", exit_code))

        def _poll_messages(self) -> None:
            while True:
                try:
                    message = self.messages.get_nowait()
                except queue.Empty:
                    break
                self._handle_message(message)
            self.root.after(100, self._poll_messages)

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
            if kind == "status":
                self.status_text.set(message[1])
                self._append_log(message[1] + "\n")
            elif kind == "total":
                total = max(int(message[1]), 1)
                self.progress.configure(maximum=total)
                self.progress_value.set(0)
                self.status_text.set(f"{message[1]} Websites gefunden. Starte paralleles Crawling ...")
            elif kind == "progress":
                stats = message[1]
                self.progress.configure(maximum=max(stats.websites_total, 1))
                self.progress_value.set(stats.websites_done)
                self.status_text.set(
                    f"Website {stats.websites_done}/{stats.websites_total} · {stats.leads_per_minute} Leads/min"
                )
                self._update_stats(stats)
            elif kind == "warning":
                self._append_log("Hinweis: " + message[1] + "\n")
            elif kind == "lead":
                lead, stats = message[1], message[2]
                self._update_stats(stats)
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
                self._append_log(f"Lead gefunden: {lead.email} ({lead.company_name})\n")
            elif kind == "finished":
                stats, output = message[1], message[2]
                self._update_stats(stats)
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
                self.start_button.configure(state="normal")
                self._append_log(f"Fertig mit Exit-Code {message[1]}.\n")

        def _append_log(self, text: str) -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text)
            self.log.see("end")
            self.log.configure(state="disabled")

    root = tk.Tk()
    SimpleCapperGui(root)
    root.mainloop()
    return 0
