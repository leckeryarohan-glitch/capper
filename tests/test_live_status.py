from __future__ import annotations

import tempfile
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


if __name__ == "__main__":
    unittest.main()
