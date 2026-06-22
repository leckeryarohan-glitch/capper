from __future__ import annotations

import contextlib
import io
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .cli import main as cli_main


PROVIDERS = ("brave", "bing", "serpapi", "file")


@dataclass(frozen=True)
class GuiField:
    key: str
    label: str
    default: str = ""
    browse: bool = False


DISCOVER_FIELDS = (
    GuiField("category", "Kategorie", "hotel"),
    GuiField("location", "Ort optional", "Berlin"),
    GuiField("seed_file", "Seed-Datei fuer Anbieter 'file'", "examples/seeds.txt", True),
    GuiField("suppression_file", "Opt-out/Suppression-Datei", "examples/suppression.txt", True),
    GuiField("output", "Ausgabe CSV/JSON", "leads.csv", True),
    GuiField("limit", "Suchtreffer Limit", "25"),
    GuiField("max_pages_per_site", "Seiten pro Website", "3"),
    GuiField("delay", "Sekunden Pause pro Website", "1.0"),
)

BATCH_FIELDS = (
    GuiField("categories_file", "Kategorien-Datei", "examples/categories.txt", True),
    GuiField("locations_file", "Orte-Datei optional", "examples/locations.txt", True),
    GuiField("seed_file", "Seed-Datei fuer Anbieter 'file'", "examples/seeds.txt", True),
    GuiField("suppression_file", "Opt-out/Suppression-Datei", "examples/suppression.txt", True),
    GuiField("checkpoint", "Checkpoint-Datei", "capper-checkpoint.json", True),
    GuiField("output", "Ausgabe CSV/JSON", "leads.csv", True),
    GuiField("limit_per_query", "Suchtreffer pro Kategorie/Ort", "50"),
    GuiField("max_leads", "Maximale Leads", "5000"),
    GuiField("max_pages_per_site", "Seiten pro Website", "3"),
    GuiField("delay", "Sekunden Pause pro Website", "1.0"),
    GuiField("query_delay", "Sekunden Pause pro Suche", "2.0"),
)


def build_discover_argv(values: Mapping[str, str | bool]) -> list[str]:
    require_non_empty(values, "category", "Kategorie")
    validate_choice(str(values.get("provider", "file")), PROVIDERS, "Anbieter")
    validate_positive_int(values, "limit", "Suchtreffer Limit")
    validate_positive_int(values, "max_pages_per_site", "Seiten pro Website")
    validate_non_negative_float(values, "delay", "Sekunden Pause pro Website")

    argv = [
        "discover",
        "--category",
        str(values["category"]).strip(),
        "--provider",
        str(values.get("provider", "file")),
        "--limit",
        str(values.get("limit", "25")).strip(),
        "--max-pages-per-site",
        str(values.get("max_pages_per_site", "3")).strip(),
        "--delay",
        str(values.get("delay", "1.0")).strip(),
        "--output",
        str(values.get("output", "leads.csv")).strip() or "leads.csv",
    ]
    append_optional(argv, "--location", values.get("location"))
    append_optional(argv, "--seed-file", values.get("seed_file"))
    append_optional(argv, "--suppression-file", values.get("suppression_file"))
    append_flags(argv, values)
    return argv


def build_batch_argv(values: Mapping[str, str | bool]) -> list[str]:
    require_existing_text_path(values, "categories_file", "Kategorien-Datei")
    validate_choice(str(values.get("provider", "brave")), PROVIDERS, "Anbieter")
    validate_positive_int(values, "limit_per_query", "Suchtreffer pro Kategorie/Ort")
    validate_positive_int(values, "max_leads", "Maximale Leads")
    validate_positive_int(values, "max_pages_per_site", "Seiten pro Website")
    validate_non_negative_float(values, "delay", "Sekunden Pause pro Website")
    validate_non_negative_float(values, "query_delay", "Sekunden Pause pro Suche")

    argv = [
        "batch",
        "--categories-file",
        str(values["categories_file"]).strip(),
        "--provider",
        str(values.get("provider", "brave")),
        "--limit-per-query",
        str(values.get("limit_per_query", "50")).strip(),
        "--max-leads",
        str(values.get("max_leads", "5000")).strip(),
        "--max-pages-per-site",
        str(values.get("max_pages_per_site", "3")).strip(),
        "--delay",
        str(values.get("delay", "1.0")).strip(),
        "--query-delay",
        str(values.get("query_delay", "2.0")).strip(),
        "--checkpoint",
        str(values.get("checkpoint", "capper-checkpoint.json")).strip() or "capper-checkpoint.json",
        "--output",
        str(values.get("output", "leads.csv")).strip() or "leads.csv",
    ]
    append_optional(argv, "--locations-file", values.get("locations_file"))
    append_optional(argv, "--seed-file", values.get("seed_file"))
    append_optional(argv, "--suppression-file", values.get("suppression_file"))
    append_flags(argv, values)
    if values.get("resume"):
        argv.append("--resume")
    return argv


def append_optional(argv: list[str], flag: str, value: str | bool | None) -> None:
    if isinstance(value, str) and value.strip():
        argv.extend([flag, value.strip()])


def append_flags(argv: list[str], values: Mapping[str, str | bool]) -> None:
    if values.get("include_personal_review"):
        argv.append("--include-personal-review")
    if not values.get("respect_robots", True):
        argv.append("--ignore-robots")


def require_non_empty(values: Mapping[str, str | bool], key: str, label: str) -> None:
    value = values.get(key, "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} ist erforderlich.")


def require_existing_text_path(values: Mapping[str, str | bool], key: str, label: str) -> None:
    require_non_empty(values, key, label)
    path = Path(str(values[key]).strip())
    if not path.exists():
        raise ValueError(f"{label} existiert nicht: {path}")


def validate_choice(value: str, choices: tuple[str, ...], label: str) -> None:
    if value not in choices:
        raise ValueError(f"{label} muss einer dieser Werte sein: {', '.join(choices)}")


def validate_positive_int(values: Mapping[str, str | bool], key: str, label: str) -> None:
    raw_value = str(values.get(key, "")).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{label} muss eine ganze Zahl sein.") from exc
    if value < 1:
        raise ValueError(f"{label} muss mindestens 1 sein.")


def validate_non_negative_float(values: Mapping[str, str | bool], key: str, label: str) -> None:
    raw_value = str(values.get(key, "")).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{label} muss eine Zahl sein.") from exc
    if value < 0:
        raise ValueError(f"{label} darf nicht negativ sein.")


class QueueWriter(io.TextIOBase):
    def __init__(self, messages: "queue.Queue[str]"):
        self.messages = messages

    def write(self, text: str) -> int:
        if text:
            self.messages.put(text)
        return len(text)

    def flush(self) -> None:
        return


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
    except ImportError as exc:
        raise RuntimeError("Tkinter ist nicht installiert. Bitte Python mit Tk-Unterstuetzung verwenden.") from exc

    class CapperGui:
        def __init__(self, root: "tk.Tk"):
            self.root = root
            self.root.title("Capper Lead Research")
            self.messages: "queue.Queue[str]" = queue.Queue()
            self.worker: threading.Thread | None = None
            self.mode = tk.StringVar(value="discover")
            self.provider = tk.StringVar(value="file")
            self.include_personal_review = tk.BooleanVar(value=False)
            self.respect_robots = tk.BooleanVar(value=True)
            self.resume = tk.BooleanVar(value=False)
            self.entries: dict[str, "tk.Entry"] = {}

            self._build()
            self._poll_messages()
            self._refresh_mode()

        def _build(self) -> None:
            outer = ttk.Frame(self.root, padding=12)
            outer.grid(row=0, column=0, sticky="nsew")
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            outer.columnconfigure(0, weight=1)

            top = ttk.LabelFrame(outer, text="Modus")
            top.grid(row=0, column=0, sticky="ew")
            ttk.Radiobutton(top, text="Einzelne Kategorie", variable=self.mode, value="discover", command=self._refresh_mode).grid(
                row=0, column=0, padx=6, pady=6, sticky="w"
            )
            ttk.Radiobutton(top, text="Batch fuer viele Leads", variable=self.mode, value="batch", command=self._refresh_mode).grid(
                row=0, column=1, padx=6, pady=6, sticky="w"
            )
            ttk.Label(top, text="Anbieter").grid(row=0, column=2, padx=6, sticky="e")
            ttk.OptionMenu(top, self.provider, self.provider.get(), *PROVIDERS).grid(row=0, column=3, padx=6, sticky="w")

            self.form = ttk.Frame(outer)
            self.form.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
            self.form.columnconfigure(1, weight=1)

            options = ttk.LabelFrame(outer, text="Schutz & Optionen")
            options.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            ttk.Checkbutton(options, text="robots.txt respektieren", variable=self.respect_robots).grid(
                row=0, column=0, padx=6, pady=6, sticky="w"
            )
            ttk.Checkbutton(
                options,
                text="Personenbezogene E-Mails nur fuer manuelle Review exportieren",
                variable=self.include_personal_review,
            ).grid(row=0, column=1, padx=6, pady=6, sticky="w")
            ttk.Checkbutton(options, text="Batch fortsetzen", variable=self.resume).grid(row=0, column=2, padx=6, pady=6, sticky="w")

            actions = ttk.Frame(outer)
            actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
            self.start_button = ttk.Button(actions, text="Lead-Recherche starten", command=self._start)
            self.start_button.grid(row=0, column=0, sticky="w")

            self.log = scrolledtext.ScrolledText(outer, height=14, state="disabled")
            self.log.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
            outer.rowconfigure(4, weight=1)

        def _refresh_mode(self) -> None:
            for child in self.form.winfo_children():
                child.destroy()
            self.entries.clear()
            fields = DISCOVER_FIELDS if self.mode.get() == "discover" else BATCH_FIELDS
            for row, field in enumerate(fields):
                ttk.Label(self.form, text=field.label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
                entry = ttk.Entry(self.form)
                entry.insert(0, field.default)
                entry.grid(row=row, column=1, sticky="ew", pady=3)
                self.entries[field.key] = entry
                if field.browse:
                    ttk.Button(self.form, text="Auswaehlen", command=lambda key=field.key: self._browse(key)).grid(
                        row=row, column=2, padx=(8, 0), pady=3
                    )

        def _browse(self, key: str) -> None:
            if key in {"output", "checkpoint"}:
                selected = filedialog.asksaveasfilename()
            else:
                selected = filedialog.askopenfilename()
            if selected:
                entry = self.entries[key]
                entry.delete(0, "end")
                entry.insert(0, selected)

        def _values(self) -> dict[str, str | bool]:
            values: dict[str, str | bool] = {key: entry.get() for key, entry in self.entries.items()}
            values["provider"] = self.provider.get()
            values["include_personal_review"] = self.include_personal_review.get()
            values["respect_robots"] = self.respect_robots.get()
            values["resume"] = self.resume.get()
            return values

        def _start(self) -> None:
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("Capper", "Ein Lauf ist bereits aktiv.")
                return
            try:
                values = self._values()
                argv = build_discover_argv(values) if self.mode.get() == "discover" else build_batch_argv(values)
            except ValueError as exc:
                messagebox.showerror("Eingaben pruefen", str(exc))
                return

            self._append_log("\nStarte: capper " + " ".join(argv) + "\n")
            self.start_button.configure(state="disabled")
            self.worker = threading.Thread(target=self._run_cli, args=(argv,), daemon=True)
            self.worker.start()

        def _run_cli(self, argv: list[str]) -> None:
            writer = QueueWriter(self.messages)
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                exit_code = cli_main(argv)
            self.messages.put(f"\nFertig mit Exit-Code {exit_code}.\n")
            self.messages.put("__GUI_DONE__")

        def _poll_messages(self) -> None:
            while True:
                try:
                    message = self.messages.get_nowait()
                except queue.Empty:
                    break
                if message == "__GUI_DONE__":
                    self.start_button.configure(state="normal")
                else:
                    self._append_log(message)
            self.root.after(100, self._poll_messages)

        def _append_log(self, text: str) -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text)
            self.log.see("end")
            self.log.configure(state="disabled")

    root = tk.Tk()
    app = CapperGui(root)
    root.mainloop()
    return 0
