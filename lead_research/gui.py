from __future__ import annotations

import contextlib
import io
import queue
import threading
from pathlib import Path
from typing import Mapping

from .cli import main as cli_main
from .search import COMMON_SOURCE_DOMAINS


DEFAULT_OUTPUT = "leads.csv"
DEFAULT_LIMIT = "50"
DEFAULT_MAX_PAGES = "3"
DEFAULT_DELAY = "1.0"


def build_simple_gui_argv(values: Mapping[str, str | bool]) -> list[str]:
    category = require_text(values, "category", "Kategorie")
    output = str(values.get("output", DEFAULT_OUTPUT)).strip() or DEFAULT_OUTPUT

    argv = [
        "discover",
        "--category",
        category,
        "--provider",
        "auto",
        "--source-profile",
        "common",
        "--limit",
        DEFAULT_LIMIT,
        "--max-pages-per-site",
        DEFAULT_MAX_PAGES,
        "--delay",
        DEFAULT_DELAY,
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

    class SimpleCapperGui:
        def __init__(self, root: "tk.Tk"):
            self.root = root
            self.root.title("Capper Lead Finder")
            self.messages: "queue.Queue[str]" = queue.Queue()
            self.worker: threading.Thread | None = None

            self.category = tk.StringVar(value="hotel")
            self.location = tk.StringVar(value="")
            self.output = tk.StringVar(value=DEFAULT_OUTPUT)
            self.suppression_file = tk.StringVar(value="examples/suppression.txt")

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

            ttk.Label(outer, text="CSV-Ausgabe").grid(row=3, column=0, sticky="w", pady=4)
            ttk.Entry(outer, textvariable=self.output).grid(row=3, column=1, sticky="ew", pady=4)
            ttk.Button(outer, text="Auswaehlen", command=self._choose_output).grid(row=3, column=2, padx=(8, 0), pady=4)

            ttk.Label(outer, text="Opt-out Liste").grid(row=4, column=0, sticky="w", pady=4)
            ttk.Entry(outer, textvariable=self.suppression_file).grid(row=4, column=1, sticky="ew", pady=4)
            ttk.Button(outer, text="Auswaehlen", command=self._choose_suppression).grid(row=4, column=2, padx=(8, 0), pady=4)

            sources = ", ".join(COMMON_SOURCE_DOMAINS[:5]) + " ..."
            source_text = (
                "Es wird automatisch in gaengigen Branchen- und Firmenquellen gesucht "
                f"({sources}). Dafuer muss mindestens ein API-Key gesetzt sein."
            )
            ttk.Label(outer, text=source_text, wraplength=620).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 8))

            self.start_button = ttk.Button(outer, text="Leads suchen", command=self._start)
            self.start_button.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 10))

            self.log = scrolledtext.ScrolledText(outer, height=14, state="disabled")
            self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
            outer.rowconfigure(7, weight=1)

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

            try:
                argv = build_simple_gui_argv(
                    {
                        "category": self.category.get(),
                        "location": self.location.get(),
                        "output": self.output.get(),
                        "suppression_file": self.suppression_file.get(),
                    }
                )
            except ValueError as exc:
                messagebox.showerror("Eingabe pruefen", str(exc))
                return

            self._append_log("\nStarte Suche fuer Kategorie: " + self.category.get().strip() + "\n")
            self._append_log("Befehl: capper " + " ".join(argv) + "\n")
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
    SimpleCapperGui(root)
    root.mainloop()
    return 0
