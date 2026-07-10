from __future__ import annotations

import unittest

from lead_research.extract import decode_cfemail, extract_emails, extract_phone, parse_page
from lead_research.models import ConsentStatus, classify_email


class ExtractTests(unittest.TestCase):
    def test_extracts_plain_and_obfuscated_emails(self) -> None:
        text = "Kontakt: info@example.de oder sales [at] example [dot] com"

        self.assertEqual(extract_emails(text), ["info@example.de", "sales@example.com"])

    def test_decode_cfemail_roundtrip(self) -> None:
        email = "info@hotel-beispiel.de"
        key = 0x2a
        encoded = format(key, "02x") + "".join(
            format(ord(ch) ^ key, "02x") for ch in email
        )
        self.assertEqual(decode_cfemail(encoded), email)
        self.assertEqual(decode_cfemail("zz"), "")

    def test_extracts_cloudflare_obfuscated_email(self) -> None:
        email = "reservierung@hotel-x.de"
        key = 0x17
        encoded = format(key, "02x") + "".join(
            format(ord(ch) ^ key, "02x") for ch in email
        )
        html_text = (
            '<a class="__cf_email__" data-cfemail="'
            + encoded
            + '">[email&#160;protected]</a>'
        )
        self.assertIn(email, extract_emails(html_text))

    def test_extracts_email_split_by_inline_tags(self) -> None:
        html_text = "Mail: info<span>@</span>hotel-split.de bitte"
        self.assertIn("info@hotel-split.de", extract_emails(html_text))

    def test_tag_strip_does_not_invent_merged_cell_address(self) -> None:
        # Two separate table cells must not merge into info@a.dexyz.
        html_text = "<td>info@a.de</td><td>xyz</td>"
        emails = extract_emails(html_text)
        self.assertIn("info@a.de", emails)
        self.assertNotIn("info@a.dexyz", emails)

    def test_extracts_phone(self) -> None:
        self.assertEqual(extract_phone("Telefon +49 30 1234 5678 heute"), "+49 30 1234 5678")

    def test_parse_page_finds_same_site_contact_links(self) -> None:
        html = """
        <html>
          <head><title>Example GmbH | Start</title></head>
          <body>
            <a href="/kontakt">Kontakt</a>
            <a href="https://other.example/kontakt">Other</a>
          </body>
        </html>
        """

        title, links = parse_page(html, "https://www.example.de")

        self.assertEqual(title, "Example GmbH | Start")
        self.assertEqual(links, ["https://www.example.de/kontakt"])

    def test_classifies_role_and_personal_emails(self) -> None:
        self.assertEqual(classify_email("kontakt@example.de"), ConsentStatus.BUSINESS_PUBLIC)
        self.assertEqual(classify_email("max.mustermann@example.de"), ConsentStatus.PERSONAL_REVIEW_REQUIRED)


if __name__ == "__main__":
    unittest.main()
