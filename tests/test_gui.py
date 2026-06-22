from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lead_research.gui import build_batch_argv, build_discover_argv


class GuiArgumentTests(unittest.TestCase):
    def test_build_discover_argv_from_form_values(self) -> None:
        argv = build_discover_argv(
            {
                "category": "hotel",
                "location": "Berlin",
                "provider": "file",
                "seed_file": "examples/seeds.txt",
                "suppression_file": "examples/suppression.txt",
                "output": "leads.csv",
                "limit": "25",
                "max_pages_per_site": "3",
                "delay": "0",
                "include_personal_review": False,
                "respect_robots": True,
            }
        )

        self.assertIn("discover", argv)
        self.assertIn("--category", argv)
        self.assertIn("hotel", argv)
        self.assertIn("--location", argv)
        self.assertNotIn("--ignore-robots", argv)

    def test_build_batch_argv_from_form_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            categories_file = Path(tmp) / "categories.txt"
            categories_file.write_text("hotel\n", encoding="utf-8")

            argv = build_batch_argv(
                {
                    "categories_file": str(categories_file),
                    "locations_file": "",
                    "provider": "brave",
                    "suppression_file": "",
                    "checkpoint": "capper-checkpoint.json",
                    "output": "leads.csv",
                    "limit_per_query": "50",
                    "max_leads": "5000",
                    "max_pages_per_site": "3",
                    "delay": "1",
                    "query_delay": "2",
                    "include_personal_review": True,
                    "respect_robots": False,
                    "resume": True,
                }
            )

        self.assertEqual(argv[0], "batch")
        self.assertIn("--categories-file", argv)
        self.assertIn("--include-personal-review", argv)
        self.assertIn("--ignore-robots", argv)
        self.assertIn("--resume", argv)

    def test_rejects_invalid_numeric_values(self) -> None:
        with self.assertRaises(ValueError):
            build_discover_argv(
                {
                    "category": "hotel",
                    "provider": "file",
                    "limit": "0",
                    "max_pages_per_site": "3",
                    "delay": "1",
                }
            )


if __name__ == "__main__":
    unittest.main()
