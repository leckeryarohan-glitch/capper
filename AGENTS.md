# AGENTS.md

## Cursor Cloud specific instructions

Capper is a single-product, pure-Python project (no third-party runtime
dependencies). Code lives in `lead_research/`; tests in `tests/`; sample inputs
in `examples/`. Standard commands are documented in `README.md`.

- Python 3.11+ is required (`pyproject.toml`); the VM has 3.12.
- The package is installed editable by the startup update script
  (`python3 -m pip install -e .`), which only registers the `capper` console
  script — there are no runtime dependencies to fetch.
- Tests: `python3 -m unittest discover -s tests` (also works via `python3 -m pytest`).
  The suite is fully offline; no network or services required.
- There is no linter, formatter, or build/lint config in this repo.

### Running the app

- CLI offline (deterministic, no network): use `--provider file` with a seed file
  of URLs, e.g.
  `python3 -m lead_research discover --category "lager logistik" --provider file --seed-file examples/seeds.txt --output /tmp/leads.csv`.
  Note `examples/seeds.txt` ships pointing at `https://example.com`, which yields
  0 leads (no contacts on that page); point the seed file at a real/served site
  to extract leads.
- CLI live (needs outbound internet, no API key): `--provider osm` queries
  OpenStreetMap/Overpass, e.g.
  `python3 -m lead_research discover --category "hotel" --location "Berlin" --provider osm --output /tmp/leads.csv`.
- Other providers (`google`, `brave`, `bing`, `serpapi`) require API keys via env
  vars (see `README.md`); none are needed for testing.

### GUI (Tkinter)

- `python3 -m lead_research gui` launches a Tkinter desktop app. It requires the
  system package `python3-tk` (installed in the VM snapshot, NOT via the update
  script) and an X display. On the cloud VM the desktop display is `:1`, so run
  `DISPLAY=:1 python3 -m lead_research gui`.
- The GUI is hard-wired to the no-key `osm` provider, so a GUI search needs
  outbound internet (Overpass + crawling real sites) and may take ~30-60s.
