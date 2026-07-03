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
        self.assertNotIn("firmenverzeichnisse_wlw", unavailable_ids)
        self.assertIn("lokale_portale_kennstdueinen", unavailable_ids)
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
        self.assertEqual(len(implemented), 15)

    def test_pitchbook_and_indeed_are_implemented(self) -> None:
        registry = build_directory_source_registry()
        implemented_ids = {spec.id for spec in registry if spec.implemented}

        self.assertIn("pitchbook", implemented_ids)
        self.assertIn("indeed", implemented_ids)
        self.assertIn("stepstone", implemented_ids)

    def test_jameda_sanego_and_restaurantguru_are_implemented(self) -> None:
        registry = build_directory_source_registry()
        implemented_ids = {spec.id for spec in registry if spec.implemented}

        self.assertIn("jameda", implemented_ids)
        self.assertIn("sanego", implemented_ids)
        self.assertIn("restaurantguru", implemented_ids)
        self.assertIn("docfinder", implemented_ids)
        self.assertIn("anwaltauskunft", implemented_ids)
        self.assertIn("steuerberater", implemented_ids)
        self.assertIn("herold", implemented_ids)
        self.assertIn("wko", implemented_ids)
        self.assertIn("golocal", implemented_ids)
        self.assertIn("wlw", implemented_ids)
        self.assertIn("treatwell", implemented_ids)

    def test_lieferanten_europages_is_implemented(self) -> None:
        registry = build_directory_source_registry()
        implemented_ids = {spec.id for spec in registry if spec.implemented}

        self.assertIn("lieferanten_europages", implemented_ids)
        self.assertIn("europages", implemented_ids)

    def test_stepstone_not_marked_unavailable(self) -> None:
        registry = build_directory_source_registry()
        unavailable_ids = {spec.id for spec in unavailable_directory_sources(registry)}

        self.assertNotIn("jobboersen_stepstone", unavailable_ids)

    def test_treatwell_not_marked_unavailable(self) -> None:
        registry = build_directory_source_registry()
        unavailable_ids = {spec.id for spec in unavailable_directory_sources(registry)}

        self.assertNotIn("branchen_treatwell", unavailable_ids)
        self.assertIn("jobboersen_monster", unavailable_ids)
        self.assertIn("immobilien_idealista", unavailable_ids)
        self.assertIn("lieferanten_thomasnet", unavailable_ids)
        self.assertIn("branchen_physiotherapeuten", unavailable_ids)

    def test_blocked_gastronomie_and_aerzte_sources_marked_unavailable(self) -> None:
        registry = build_directory_source_registry()
        unavailable_ids = {spec.id for spec in unavailable_directory_sources(registry)}

        self.assertIn("gastronomie_opentable", unavailable_ids)
        self.assertIn("gastronomie_tripadvisor", unavailable_ids)
        self.assertIn("aerzte_doctolib", unavailable_ids)
        self.assertIn("bewertungen_trustpilot", unavailable_ids)
        self.assertIn("handwerker_trustatrader", unavailable_ids)
        self.assertIn("ihk___hwk_mitgliederverzeichnisse", unavailable_ids)
        self.assertIn("ihk___hwk_amtliches_verzeichnis", unavailable_ids)
        self.assertNotIn("lokale_portale_golocal", unavailable_ids)
        self.assertIn("branchen_notare", unavailable_ids)
        self.assertNotIn("aerzte_docfinder", unavailable_ids)
        self.assertNotIn("branchen_anwaltauskunft", unavailable_ids)
        self.assertNotIn("branchen_steuerberater", unavailable_ids)
        self.assertNotIn("gastronomie_restaurant_guru", unavailable_ids)

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
