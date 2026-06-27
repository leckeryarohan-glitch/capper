from __future__ import annotations

import unittest

from lead_research.directories import build_directory_source_registry, get_directory_scrapers
from lead_research.directory_registry import default_enabled_directory_source_ids


class DirectoryRegistryTests(unittest.TestCase):
    def test_registry_contains_implemented_firmenverzeichnisse(self) -> None:
        registry = build_directory_source_registry()
        implemented_ids = {spec.id for spec in registry if spec.implemented}

        self.assertIn("gelbeseiten", implemented_ids)
        self.assertIn("cylex", implemented_ids)
        self.assertIn("hotfrog", implemented_ids)
        self.assertIn("werkenntdenbesten", implemented_ids)
        self.assertIn("goyellow", implemented_ids)
        self.assertIn("yelp", implemented_ids)
        planned_ids = {spec.id for spec in registry if not spec.implemented}
        self.assertIn("firmenverzeichnisse_wlw", planned_ids)

    def test_get_directory_scrapers_respects_enabled_ids(self) -> None:
        scrapers = get_directory_scrapers({"gelbeseiten", "cylex"})
        labels = [label for label, _ in scrapers]

        self.assertEqual(labels, ["Gelbe Seiten", "Cylex"])

    def test_default_enabled_ids_match_implemented_defaults(self) -> None:
        registry = build_directory_source_registry()
        defaults = default_enabled_directory_source_ids(registry)
        implemented = {spec.id for spec in registry if spec.implemented and spec.default_enabled}

        self.assertEqual(defaults, implemented)


if __name__ == "__main__":
    unittest.main()
