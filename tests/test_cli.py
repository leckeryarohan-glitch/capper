from __future__ import annotations

import csv
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lead_research.cli import main


class LeadTestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/":
            body = """
            <html>
              <head><title>Hotel Beispiel GmbH</title></head>
              <body><a href="/kontakt">Kontakt</a></body>
            </html>
            """
        elif self.path == "/kontakt":
            body = """
            <html>
              <head><title>Kontakt | Hotel Beispiel GmbH</title></head>
              <body>Schreiben Sie an kontakt@hotel-beispiel.test oder +49 30 123456.</body>
            </html>
            """
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


class CliTests(unittest.TestCase):
    def test_discovers_leads_from_seed_file(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), LeadTestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                seed_file = tmp_path / "seeds.txt"
                output_file = tmp_path / "leads.csv"
                seed_file.write_text(f"http://127.0.0.1:{server.server_port}/\n", encoding="utf-8")

                exit_code = main(
                    [
                        "discover",
                        "--category",
                        "hotel",
                        "--provider",
                        "file",
                        "--seed-file",
                        str(seed_file),
                        "--output",
                        str(output_file),
                        "--delay",
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
        self.assertEqual(rows[0]["category"], "hotel")
        self.assertEqual(rows[0]["email"], "kontakt@hotel-beispiel.test")
        self.assertEqual(rows[0]["consent_status"], "business_public")


if __name__ == "__main__":
    unittest.main()
