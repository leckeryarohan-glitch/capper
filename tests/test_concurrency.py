from __future__ import annotations

import time
import unittest

from lead_research.concurrency import recommended_crawl_workers, recommended_workers, run_with_hard_timeout
from lead_research.search import zenrows_parallel_workers


class ConcurrencyTests(unittest.TestCase):
    def test_recommended_workers_scales_with_request(self) -> None:
        self.assertGreater(recommended_workers(), 8)
        self.assertEqual(recommended_workers(4), 4)
        self.assertEqual(recommended_workers(500), 128)

    def test_zenrows_parallel_workers_only_for_mass_runs(self) -> None:
        self.assertEqual(zenrows_parallel_workers(12, False, 100), 1)
        self.assertEqual(zenrows_parallel_workers(12, True, 10), 1)
        self.assertEqual(zenrows_parallel_workers(12, True, 200), 6)
        self.assertEqual(zenrows_parallel_workers(2, True, 200), 2)

    def test_recommended_crawl_workers_caps_large_resume(self) -> None:
        self.assertEqual(recommended_crawl_workers(128, pending_sites=10), 20)
        self.assertEqual(recommended_crawl_workers(128, pending_sites=1000), 4)
        self.assertEqual(recommended_crawl_workers(4, pending_sites=1000), 4)

    def test_run_with_hard_timeout_raises_when_work_blocks(self) -> None:
        def slow() -> str:
            time.sleep(0.2)
            return "ok"

        with self.assertRaises(TimeoutError):
            run_with_hard_timeout(slow, 0.05)

    def test_run_with_hard_timeout_returns_result(self) -> None:
        self.assertEqual(run_with_hard_timeout(lambda: 42, 1.0), 42)


if __name__ == "__main__":
    unittest.main()
