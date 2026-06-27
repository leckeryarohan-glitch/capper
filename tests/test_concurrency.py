from __future__ import annotations

import unittest

from lead_research.concurrency import recommended_workers
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


if __name__ == "__main__":
    unittest.main()
