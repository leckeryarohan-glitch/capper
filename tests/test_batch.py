from __future__ import annotations

import csv
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lead_research.batch import load_checkpoint, query_plan, save_checkpoint
from lead_research.cli import main
from lead_research.models import Lead


class BatchLeadHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = """
        <html>
          <head><title>Logistik Beispiel GmbH</title></head>
          <body>Kontakt: info@logistik-beispiel.test</body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


class BatchTests(unittest.TestCase):
    def test_query_plan_builds_category_location_cartesian_product(self) -> None:
        plan = query_plan(["hotel", "logistik"], ["Berlin", "Hamburg"])

        self.assertEqual(
            plan,
            [
                ("hotel", "Berlin"),
                ("hotel", "Hamburg"),
                ("logistik", "Berlin"),
                ("logistik", "Hamburg"),
            ],
        )

    def test_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint.json"
            lead = Lead(
                category="logistik",
                source_url="https://example.test",
                website="https://example.test",
                email="info@example.test",
            )

            save_checkpoint(checkpoint, {("logistik", "Berlin")}, [lead])
            completed, leads = load_checkpoint(checkpoint)

        self.assertEqual(completed, {("logistik", "Berlin")})
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].email, "info@example.test")

    def test_batch_cli_discovers_leads_from_seed_file(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), BatchLeadHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                categories_file = tmp_path / "categories.txt"
                seed_file = tmp_path / "seeds.txt"
                output_file = tmp_path / "leads.csv"
                checkpoint = tmp_path / "checkpoint.json"
                categories_file.write_text("lager logistik\n", encoding="utf-8")
                seed_file.write_text(f"http://127.0.0.1:{server.server_port}/\n", encoding="utf-8")

                exit_code = main(
                    [
                        "batch",
                        "--categories-file",
                        str(categories_file),
                        "--provider",
                        "file",
                        "--seed-file",
                        str(seed_file),
                        "--limit-per-query",
                        "1",
                        "--max-leads",
                        "10",
                        "--checkpoint",
                        str(checkpoint),
                        "--output",
                        str(output_file),
                        "--delay",
                        "0",
                        "--query-delay",
                        "0",
                        "--ignore-robots",
                    ]
                )

                self.assertEqual(exit_code, 0)
                with output_file.open(encoding="utf-8", newline="") as file:
                    rows = list(csv.DictReader(file))
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "lager logistik")
        self.assertEqual(rows[0]["email"], "info@logistik-beispiel.test")


if __name__ == "__main__":
    unittest.main()
