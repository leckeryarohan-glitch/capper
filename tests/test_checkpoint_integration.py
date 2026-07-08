"""Optional live integration test using a real checkpoint file.

Set environment variables before running:
  CHECKPOINT_PATH=/path/to/capper-checkpoint.json
  ZENROWS_API_KEY=your-key

Example:
  CHECKPOINT_PATH=/tmp/capper-integration-test/capper-checkpoint.json \\
  ZENROWS_API_KEY=... \\
  python3 -m unittest tests.test_checkpoint_integration -v
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from shutil import copy2

from lead_research.checkpoint import load_checkpoint_gui_metadata, load_discovery_checkpoint
from lead_research.gui import run_gui_discovery
from lead_research.live_status import live_status_path_for_checkpoint, read_live_status


CHECKPOINT_PATH = os.getenv("CHECKPOINT_PATH", "").strip()
ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY", "").strip()
MIN_NEW_SITES = int(os.getenv("CHECKPOINT_TEST_MIN_NEW_SITES", "8"))
TARGET_TOTAL_LEADS = int(os.getenv("CHECKPOINT_TEST_TARGET_TOTAL_LEADS", "0"))
MAX_SECONDS = float(os.getenv("CHECKPOINT_TEST_MAX_SECONDS", "180"))


@unittest.skipUnless(
    CHECKPOINT_PATH and ZENROWS_API_KEY and Path(CHECKPOINT_PATH).exists(),
    "Set CHECKPOINT_PATH and ZENROWS_API_KEY to run live checkpoint integration test",
)
class CheckpointIntegrationTests(unittest.TestCase):
    def test_resume_crawl_makes_progress_without_absurd_leads_per_minute(self) -> None:
        source = Path(CHECKPOINT_PATH)
        metadata = load_checkpoint_gui_metadata(source)
        self.assertIsNotNone(metadata)
        assert metadata is not None

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            checkpoint = work / "capper-checkpoint.json"
            output = work / "leads.csv"
            copy2(source, checkpoint)

            loaded = load_discovery_checkpoint(checkpoint)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            baseline_done = len(loaded.crawled_urls)
            baseline_leads = len(loaded.leads)

            values = {
                "category": str(metadata.get("category", "hotel")),
                "location": str(metadata.get("location", "")),
                "country_de": "DE" in metadata.get("countries", ["DE"]),
                "country_at": "AT" in metadata.get("countries", []),
                "limit": int(metadata.get("limit", 500000)),
                "max_leads": int(metadata.get("max_leads", 200000)),
                "workers": 4,
                "directory_parallel": 6,
                "directory_detail_parallel": 4,
                "output": str(output),
                "checkpoint": str(checkpoint),
                "resume": True,
                "zenrows_key": ZENROWS_API_KEY,
                "use_osm": bool(metadata.get("use_osm", True)),
                "use_duckduckgo": bool(metadata.get("use_duckduckgo", True)),
                "use_directories": bool(metadata.get("use_directories", True)),
                "use_zenrows_google": bool(metadata.get("use_zenrows_google", True)),
                "use_google_maps": bool(metadata.get("use_google_maps", False)),
                "use_serpapi": bool(metadata.get("use_serpapi", False)),
            }
            for source_id in metadata.get("directory_sources", []):
                values[f"dir_source_{source_id}"] = True

            events: queue.Queue[tuple] = queue.Queue()
            done = threading.Event()
            result: dict[str, object] = {}

            def worker() -> None:
                try:
                    result["exit_code"] = run_gui_discovery(values, events)
                except Exception as exc:  # noqa: BLE001
                    result["error"] = exc
                finally:
                    done.set()

            thread = threading.Thread(target=worker, name="checkpoint-integration", daemon=True)
            thread.start()

            live_path = live_status_path_for_checkpoint(checkpoint)
            started = time.monotonic()
            last_done = baseline_done
            last_leads = baseline_leads
            last_progress_at = started
            peak_leads_per_min = 0.0
            new_sites = 0
            target_total_leads = (
                TARGET_TOTAL_LEADS if TARGET_TOTAL_LEADS > 0 else baseline_leads
            )

            while not done.is_set():
                now = time.monotonic()
                if now - started >= MAX_SECONDS:
                    break
                status = read_live_status(live_path)
                if status is not None:
                    peak_leads_per_min = max(peak_leads_per_min, status.leads_per_minute)
                    if status.websites_done > last_done or status.leads_found > last_leads:
                        new_sites = status.websites_done - baseline_done
                        last_done = status.websites_done
                        last_leads = status.leads_found
                        last_progress_at = now
                        print(
                            f"progress: sites={status.websites_done} "
                            f"(+{new_sites}) leads={status.leads_found} "
                            f"(+{status.leads_found - baseline_leads}) "
                            f"{status.leads_per_minute}/min",
                            flush=True,
                        )
                    elif now - last_progress_at >= 45.0:
                        self.fail(
                            f"No crawl progress for 45s (done={status.websites_done}, "
                            f"leads={status.leads_found}, baseline={baseline_done}, "
                            f"phase={status.phase!r})"
                        )
                    if TARGET_TOTAL_LEADS > 0:
                        if status.leads_found >= target_total_leads:
                            break
                    elif new_sites >= MIN_NEW_SITES:
                        break
                time.sleep(2.0)

            if "error" in result:
                self.fail(f"run_gui_discovery failed: {result['error']!r}")

            final = read_live_status(live_path)
            final_leads = final.leads_found if final is not None else last_leads
            final_done = final.websites_done if final is not None else last_done

            if TARGET_TOTAL_LEADS > 0:
                self.assertGreaterEqual(
                    final_leads,
                    target_total_leads,
                    f"Expected at least {target_total_leads} leads in {MAX_SECONDS:.0f}s, got {final_leads}",
                )
            else:
                self.assertGreaterEqual(
                    new_sites,
                    MIN_NEW_SITES,
                    f"Expected at least {MIN_NEW_SITES} new sites in {MAX_SECONDS:.0f}s, got {new_sites}",
                )
                if final is not None:
                    self.assertGreaterEqual(final.websites_done, baseline_done + MIN_NEW_SITES)
                    self.assertGreaterEqual(final.leads_found, baseline_leads)

            self.assertLess(
                peak_leads_per_min,
                500.0,
                f"Leads/min looked inflated (peak={peak_leads_per_min})",
            )
            print(
                f"done: sites={final_done} leads={final_leads} "
                f"peak_lpm={peak_leads_per_min}",
                flush=True,
            )


if __name__ == "__main__":
    unittest.main()
