from __future__ import annotations

import ssl
import unittest
import urllib.error

from lead_research.http import format_request_error, ssl_context


class HttpTests(unittest.TestCase):
    def test_ssl_context_uses_certifi_when_available(self) -> None:
        context = ssl_context()
        self.assertIsInstance(context, ssl.SSLContext)

    def test_format_request_error_explains_auth_failures(self) -> None:
        message = format_request_error(urllib.error.HTTPError("https://example.test/", 401, "Unauthorized", {}, None))
        self.assertIn("API-Key ungueltig", message)

    def test_format_request_error_explains_ssl_failures(self) -> None:
        message = format_request_error(
            urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        )
        self.assertIn("SSL-Zertifikatsfehler", message)
        self.assertIn("certifi", message)


if __name__ == "__main__":
    unittest.main()
