from __future__ import annotations

import unittest

from lead_research.gui import build_simple_gui_argv


class GuiArgumentTests(unittest.TestCase):
    def test_builds_simple_common_source_discover_command(self) -> None:
        argv = build_simple_gui_argv(
            {
                "category": "hotel",
                "location": "Berlin",
                "output": "leads.csv",
                "suppression_file": "examples/suppression.txt",
            }
        )

        self.assertEqual(argv[0], "discover")
        self.assertIn("--category", argv)
        self.assertIn("hotel", argv)
        self.assertIn("--provider", argv)
        self.assertIn("auto", argv)
        self.assertIn("--source-profile", argv)
        self.assertIn("common", argv)
        self.assertIn("--location", argv)
        self.assertIn("Berlin", argv)
        self.assertIn("--suppression-file", argv)

    def test_allows_only_category_as_required_input(self) -> None:
        argv = build_simple_gui_argv({"category": "restaurant"})

        self.assertIn("restaurant", argv)
        self.assertIn("leads.csv", argv)
        self.assertNotIn("--location", argv)

    def test_rejects_missing_category(self) -> None:
        with self.assertRaises(ValueError):
            build_simple_gui_argv({"category": "  "})


if __name__ == "__main__":
    unittest.main()
