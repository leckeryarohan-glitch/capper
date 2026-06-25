from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lead_research.crawl import CrawlConfig, LeadCrawler, guessed_contact_urls
from lead_research.models import SearchResult


class ImpressumOnlyHandler(BaseHTTPRequestHandler):
    """Start page has no contact link; only /impressum carries the email."""

    def do_GET(self) -> None:
        if self.path == "/":
            body = "<html><head><title>Spedition Beispiel</title></head><body>Willkommen</body></html>"
        elif self.path.rstrip("/") == "/impressum":
            body = "<html><head><title>Impressum</title></head><body>info@spedition-beispiel.test</body></html>"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


class CrawlTests(unittest.TestCase):
    def test_guessed_contact_urls_builds_common_paths(self) -> None:
        urls = guessed_contact_urls("https://example.test/some/page")

        self.assertIn("https://example.test/impressum", urls)
        self.assertIn("https://example.test/kontakt", urls)

    def test_guessed_contact_urls_handles_invalid_input(self) -> None:
        self.assertEqual(guessed_contact_urls("not-a-url"), [])

    def test_crawler_finds_unlinked_impressum_email(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ImpressumOnlyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/"
            crawler = LeadCrawler(CrawlConfig(max_pages_per_site=3, delay_seconds=0.0, respect_robots=False))
            leads = crawler.crawl_result(SearchResult(title="Spedition", url=base), "logistik")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual([lead.email for lead in leads], ["info@spedition-beispiel.test"])


if __name__ == "__main__":
    unittest.main()
