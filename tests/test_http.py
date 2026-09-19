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
        self.previous_data = app.DATA
        app.DATA = Path(self.temp.name)
        app.store = Store(app.DATA / "http.sqlite3")
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        app.store = self.previous_store
        app.DATA = self.previous_data
        self.temp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return response.status, response.read(), response.headers.get_content_type()

    def post(self, path, payload):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())

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

    def test_diagnostics_and_backup_download(self):
        status, created=self.post("/api/backup",{})
        self.assertEqual(status,200)
        status, body, content_type=self.get("/api/backups")
        self.assertEqual(status,200); self.assertEqual(json.loads(body)[0]["filename"],created["filename"])
        status, archive, content_type=self.get("/api/backups/download?name="+created["filename"])
        self.assertEqual(status,200); self.assertEqual(content_type,"application/zip"); self.assertTrue(archive.startswith(b"PK"))
        status, body, _=self.get("/api/diagnostics")
        diagnostics=json.loads(body)
        self.assertEqual(status,200); self.assertEqual(diagnostics["integrity"],"ok"); self.assertTrue(diagnostics["writable"])

    def test_local_backup_can_be_restored_and_deleted(self):
        self.post("/api/profile",{"first_name":"Avant"})
        _, created=self.post("/api/backup",{})
        self.post("/api/profile",{"first_name":"Après"})
        status, result=self.post("/api/backups/restore-local",{"filename":created["filename"]})
        self.assertEqual(status,200); self.assertIn("safety_backup",result)
        _, profile, _=self.get("/api/profile")
        self.assertEqual(json.loads(profile)["first_name"],"Avant")
        status,_=self.post("/api/backups/delete",{"filename":created["filename"]})
        self.assertEqual(status,200)
        _, backups, _=self.get("/api/backups")
        self.assertNotIn(created["filename"],[item["filename"] for item in json.loads(backups)])

    def test_offer_can_be_created_then_updated_over_http(self):
        status, created = self.post("/api/offers", {"title": "Agent", "company": "Test"})
        self.assertEqual(status, 201)
        status, updated = self.post(
            f"/api/offers/{created['id']}/update",
            {"title": "Agent d'accueil", "company": "Test", "contract": "CDI"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["id"], created["id"])
        _, body, _ = self.get("/api/offers")
        self.assertEqual(json.loads(body)[0]["contract"], "CDI")

    def test_profile_update_recalculates_existing_offer_scores(self):
        _, created = self.post("/api/offers", {"title": "Agent accueil", "company": "Test"})
        status, result = self.post("/api/profile", {"title": "Agent accueil"})
        self.assertEqual(status, 200)
        self.assertEqual(result["scores_recalculated"], 1)
        _, body, _ = self.get("/api/offers")
        self.assertEqual(json.loads(body)[0]["id"], created["id"])
        self.assertEqual(json.loads(body)[0]["score"], 50)

    def test_contact_lifecycle_is_available_over_http(self):
        company=app.store.execute("INSERT INTO companies(name) VALUES(?)",("Test",))
        establishment=app.store.execute("INSERT INTO establishments(company_id,name,postcode) VALUES(?,?,?)",(company,"Test Loire","42000"))
        status, created=self.post("/api/contacts",{"establishment_id":establishment,"email":"rh@test.fr","source_url":"https://test.fr/contact"})
        self.assertEqual(status,201)
        status,_=self.post(f"/api/contacts/{created['id']}/update",{"establishment_id":establishment,"name":"RH","email":"emploi@test.fr","source_url":"https://test.fr/emploi"})
        self.assertEqual(status,200)
        status,_=self.post(f"/api/contacts/{created['id']}/active",{"active":False})
        self.assertEqual(status,200)
        _, body, _=self.get("/api/contacts?include_inactive=1")
        self.assertEqual(json.loads(body)[0]["active"],0)
        status,_=self.post(f"/api/contacts/{created['id']}/delete",{})
        self.assertEqual(status,200)


if __name__ == "__main__": unittest.main()
