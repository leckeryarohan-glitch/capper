from __future__ import annotations

import unittest

from lead_research.directories import build_directory_source_registry, get_directory_scrapers
from lead_research.directory_registry import (
    default_enabled_directory_source_ids,
    planned_directory_sources,
    unavailable_directory_sources,
)


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
        unavailable_ids = {spec.id for spec in unavailable_directory_sources(registry)}
        self.assertIn("firmenverzeichnisse_wlw", unavailable_ids)
        self.assertIn("unternehmensdatenbanken_north_data", unavailable_ids)
        self.assertIn("unternehmensdatenbanken_crunchbase", unavailable_ids)
        self.assertIn("unternehmensdatenbanken_dun_and_bradstreet", unavailable_ids)
        planned_ids = {spec.id for spec in planned_directory_sources(registry)}
        self.assertNotIn("firmenverzeichnisse_wlw", planned_ids)

    def test_firmenverzeichnisse_has_thirteen_implemented_sources(self) -> None:
        registry = build_directory_source_registry()
        implemented = [
            spec for spec in registry if spec.category == "Firmenverzeichnisse" and spec.implemented
        ]
        self.assertEqual(len(implemented), 13)

    def test_pitchbook_and_indeed_are_implemented(self) -> None:
        registry = build_directory_source_registry()
        implemented_ids = {spec.id for spec in registry if spec.implemented}

        self.assertIn("pitchbook", implemented_ids)
        self.assertIn("indeed", implemented_ids)

    def test_logistik_sources_marked_unavailable(self) -> None:
        registry = build_directory_source_registry()
        unavailable_ids = {spec.id for spec in unavailable_directory_sources(registry)}

        self.assertIn("logistik_hapag_lloyd", unavailable_ids)
        self.assertIn("logistik_maersk", unavailable_ids)

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
