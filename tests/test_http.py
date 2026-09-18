import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import app
from core import Store


class HttpSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_store = app.store
        app.store = Store(Path(self.temp.name) / "http.sqlite3")
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        app.store = self.previous_store
        self.temp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return response.status, response.read(), response.headers.get_content_type()

    def test_main_page_and_empty_api_routes_are_really_successful(self):
        status, body, content_type = self.get("/")
        self.assertEqual(status, 200); self.assertEqual(content_type, "text/html")
        self.assertIn(b"Cap Emploi", body)
        for path in ("/api/establishments", "/api/contacts", "/api/trash", "/api/offers"):
            status, body, content_type = self.get(path)
            self.assertEqual(status, 200, path); self.assertEqual(content_type, "application/json")
            self.assertEqual(json.loads(body), [])

    def test_statistics_route_returns_successful_json(self):
        status, body, _ = self.get("/api/statistics?period=month")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 0)


if __name__ == "__main__": unittest.main()
