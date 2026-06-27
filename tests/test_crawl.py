from __future__ import annotations

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from lead_research.crawl import (
    CrawlConfig,
    LeadCrawler,
    DEFAULT_SITE_TIMEOUT_SECONDS,
    fetch_url,
    guessed_contact_urls,
)
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

    def test_fetch_url_returns_none_for_control_character_url(self) -> None:
        self.assertIsNone(fetch_url("https://example.test/path with space"))

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

    def test_crawl_respects_site_timeout(self) -> None:
        def slow_fetch(*args, **kwargs):
            time.sleep(5)
            return None

        crawler = LeadCrawler(
            CrawlConfig(
                max_pages_per_site=3,
                delay_seconds=0.0,
                respect_robots=False,
                site_timeout_seconds=0.3,
                request_timeout_seconds=0.2,
            )
        )
        started = time.monotonic()
        with patch("lead_research.crawl.fetch_url", side_effect=slow_fetch), patch(
            "lead_research.crawl.time.sleep"
        ):
            leads = crawler.crawl_result(SearchResult(title="Slow", url="https://slow.example/"), "hotel")
        elapsed = time.monotonic() - started

        self.assertEqual(leads, [])
        self.assertLess(elapsed, 2.0)

    def test_default_site_timeout_is_reasonable(self) -> None:
        self.assertGreaterEqual(DEFAULT_SITE_TIMEOUT_SECONDS, 20.0)
        self.assertLessEqual(DEFAULT_SITE_TIMEOUT_SECONDS, 60.0)

    def test_crawler_collects_multiple_emails_across_pages(self) -> None:
        def fake_fetch(url: str, **kwargs):
            if "/kontakt" in url:
                return "<html><body>kontakt@multi.example</body></html>", url
            if "multi.example" in url:
                return "<html><body>sales@multi.example</body></html>", url
            return None

        crawler = LeadCrawler(CrawlConfig(max_pages_per_site=5, delay_seconds=0.0, respect_robots=False))
        with patch("lead_research.crawl.fetch_url", side_effect=fake_fetch), patch(
            "lead_research.crawl.time.sleep"
        ):
            leads = crawler.crawl_result(SearchResult(title="Multi", url="https://multi.example/"), "hotel")

        emails = sorted(lead.email for lead in leads)
        self.assertEqual(emails, ["kontakt@multi.example", "sales@multi.example"])

    def test_crawler_uses_directory_email_without_fetching(self) -> None:
        crawler = LeadCrawler(CrawlConfig(max_pages_per_site=1, delay_seconds=0.0, respect_robots=False))
        result = SearchResult(
            title="Spedition Demo",
            url="",
            snippet="Gelbe Seiten",
            directory_email="info@spedition-demo.example",
            directory_source_url="https://www.gelbeseiten.de/gsbiz/demo",
        )
        with patch("lead_research.crawl.fetch_url") as fetch_mock:
            leads = crawler.crawl_result(result, "logistik")

        fetch_mock.assert_not_called()
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].email, "info@spedition-demo.example")
        self.assertEqual(leads[0].source_url, "https://www.gelbeseiten.de/gsbiz/demo")


if __name__ == "__main__":
    unittest.main()
