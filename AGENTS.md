# AGENTS.md

## Cursor Cloud specific instructions

Capper is a single Python package (`lead_research`, project name `capper`) — a
compliant B2B lead-research CLI plus a tkinter desktop GUI. There is no
backend/frontend split and no database; runs read/write local files.

### Environment notes
- Python 3.12 is available; the project requires `>=3.11`. The only declared
  runtime dependency is `certifi` (installed by the startup update script via
  `pip install -e .`).
- The GUI needs the `python3-tk` system package (tkinter). It is preinstalled in
  the VM snapshot, so it is intentionally **not** in the update script (system
  deps, not codebase deps). If `import tkinter` fails, reinstall with
  `sudo apt-get install -y python3-tk`.

### Running things (commands documented in `README.md`)
- Tests (canonical): `python3 -m unittest discover -s tests` — 223 tests, ~5s,
  fully offline (network is stubbed). `pytest` is configured in
  `pyproject.toml` but is not installed by default.
- CLI: `python3 -m lead_research discover ...` (see `README.md` "Quick start").
  The `--provider file` + `--seed-file` path is fully offline-friendly and is
  the best self-contained smoke test.
- GUI: `DISPLAY=:1 python3 -m lead_research gui`. A desktop/X server is
  available on `DISPLAY=:1`.

### Non-obvious gotchas
- No lint tooling is configured (no ruff/flake8/black config). "Lint" is best
  approximated with `python3 -m compileall -q lead_research`.
- The GUI's default search (`provider=all`) requires outbound internet
  (OpenStreetMap/Overpass, Nominatim, DuckDuckGo). It works in this environment,
  but a real run crawls hundreds of live websites and can take several minutes.
  For a quick, deterministic check prefer the CLI `file` provider against a
  local `python3 -m http.server` site.
- Runs write `leads.csv`, `leads.json`, `capper-checkpoint.json` (and
  `capper-checkpoint.json.bak`, `capper-live-status.json`) into the working
  directory. `leads.csv`/`leads.json`/`capper-checkpoint.json` are gitignored;
  the `.bak` and live-status files are not, so clean them up before committing.
