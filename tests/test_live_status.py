from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from lead_research.live_status import read_live_status, write_live_status
from lead_research.pipeline import LeadStats


class LiveStatusTests(unittest.TestCase):
    def test_write_and_read_live_status(self) -> None:
        stats = LeadStats(websites_total=100, websites_done=12, leads_found=3, pages_fetched=40)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capper-live-status.json"
            write_live_status(path, stats, phase="crawl", status="Crawling", min_interval_seconds=0.0)
            loaded = read_live_status(path)

        assert loaded is not None
        self.assertEqual(loaded.websites_done, 12)
        self.assertEqual(loaded.leads_found, 3)
        self.assertEqual(loaded.phase, "crawl")
        self.assertEqual(loaded.status, "Crawling")

    def test_live_status_round_trips_activity_events(self) -> None:
        stats = LeadStats(websites_total=100, websites_done=12, leads_found=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capper-live-status.json"
            write_live_status(
                path,
                stats,
                phase="crawl",
                status="Crawling",
                min_interval_seconds=0.0,
                active_sites=8,
                current_site="https://example.com",
                recent_events=[(1, "[+] example.com: +1 Leads"), (2, "[.] test.de: keine")],
            )
            loaded = read_live_status(path)

        assert loaded is not None
        self.assertEqual(loaded.active_sites, 8)
        self.assertEqual(loaded.current_site, "https://example.com")
        self.assertEqual(
            loaded.recent_events,
            ((1, "[+] example.com: +1 Leads"), (2, "[.] test.de: keine")),
        )

    def test_live_status_to_lead_stats_uses_rates_from_file(self) -> None:
        from lead_research.live_status import LiveRunStatus, live_status_to_lead_stats

        status = LiveRunStatus(
            websites_done=500,
            websites_total=45000,
            leads_found=1200,
            pages_fetched=800,
            unique_domains=400,
            duplicates_skipped=10,
            suppressed_skipped=2,
            leads_per_minute=4.2,
            phase="crawl",
            status="Crawling",
            updated_at=time.time(),
            websites_per_minute=18.5,
        )
        stats = live_status_to_lead_stats(status)
        self.assertEqual(stats.leads_per_minute, 4.2)
        self.assertEqual(stats.websites_per_minute, 18.5)

    def test_leads_per_minute_uses_session_baseline_on_resume(self) -> None:
        stats = LeadStats(
            websites_done=500,
            websites_total=45000,
            leads_found=1205,
            leads_baseline=1200,
            websites_baseline=500,
        )
        stats.session_started_at = time.monotonic() - 60.0
        self.assertEqual(stats.leads_per_minute, 5.0)
        self.assertEqual(stats.websites_per_minute, 0.0)


if __name__ == "__main__":
    unittest.main()
