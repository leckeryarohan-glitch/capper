from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lead_research.models import Lead
from lead_research.suppression import SuppressionList


class SuppressionTests(unittest.TestCase):
    def test_filters_email_and_domain_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suppression_file = Path(tmp) / "suppression.txt"
            suppression_file.write_text("blocked@example.de\nexample.org\n", encoding="utf-8")
            leads = [
                Lead(category="hotel", source_url="https://a", website="https://a", email="blocked@example.de"),
                Lead(category="hotel", source_url="https://b", website="https://b", email="info@example.org"),
                Lead(category="hotel", source_url="https://c", website="https://c", email="info@example.com"),
            ]

            filtered = SuppressionList(suppression_file).apply(leads)

        self.assertEqual([lead.email for lead in filtered], ["info@example.com"])


if __name__ == "__main__":
    unittest.main()
