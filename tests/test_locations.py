from __future__ import annotations

import unittest

from lead_research.locations import (
    cities_for_country,
    cities_for_countries,
    parse_countries,
    parse_population,
    top_cities_for_web_search,
)


class LocationTests(unittest.TestCase):
    def test_parse_population_handles_german_format(self) -> None:
        self.assertEqual(parse_population("3.769.962"), 3769962)
        self.assertEqual(parse_population("9954"), 9954)
        self.assertIsNone(parse_population("unknown"))

    def test_parse_countries_defaults_to_germany(self) -> None:
        self.assertEqual(parse_countries(""), ("DE",))
        self.assertEqual(parse_countries(None), ("DE",))

    def test_parse_countries_accepts_de_and_at(self) -> None:
        self.assertEqual(parse_countries("DE,AT"), ("DE", "AT"))
        self.assertEqual(parse_countries("at;de"), ("AT", "DE"))

    def test_cached_cities_include_small_towns_above_threshold(self) -> None:
        cities = cities_for_country("DE")
        populations = {str(item["name"]): int(item["population"]) for item in cities}

        self.assertGreater(len(cities), 1000)
        self.assertGreater(populations["Berlin"], 1_000_000)
        self.assertGreaterEqual(populations["Meuselwitz"], 5000)
        self.assertLess(populations["Meuselwitz"], 10_000)

    def test_cached_austrian_cities_available(self) -> None:
        cities = cities_for_country("AT")
        names = {str(item["name"]) for item in cities}

        self.assertGreater(len(cities), 50)
        self.assertIn("Wien", names)
        self.assertIn("Graz", names)

    def test_cities_for_countries_deduplicates_names(self) -> None:
        names = cities_for_countries(("DE", "AT"))
        self.assertEqual(len(names), len(set(names)))

    def test_top_cities_for_web_search_limits_per_country(self) -> None:
        pairs = top_cities_for_web_search(("DE", "AT"), per_country=5)
        self.assertEqual(len(pairs), 10)
        self.assertEqual(pairs[0][1], "DE")
        self.assertEqual(pairs[5][1], "AT")

    def test_cities_for_mass_web_search_returns_all_cached_cities(self) -> None:
        from lead_research.locations import cities_for_mass_web_search

        pairs = cities_for_mass_web_search(("DE",))
        self.assertGreater(len(pairs), 1000)
        self.assertEqual(pairs[0][1], "DE")

    def test_cities_for_mass_web_search_can_cap_per_country(self) -> None:
        from lead_research.locations import cities_for_mass_web_search

        pairs = cities_for_mass_web_search(("DE", "AT"), per_country=3)
        self.assertEqual(len(pairs), 6)


if __name__ == "__main__":
    unittest.main()
