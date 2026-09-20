import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import ANY, Mock, patch
from pathlib import Path

import app
from core import Store


class HttpSmokeTests(unittest.TestCase):
    def test_browser_open_delay_is_ten_seconds(self):
        server=Mock(); server.serve_forever.side_effect=KeyboardInterrupt
        timer=Mock()
        with patch.object(app,"ThreadingHTTPServer",return_value=server), patch.object(app.threading,"Timer",return_value=timer) as make_timer:
            app.run_server(port=8765,open_browser=True)
        make_timer.assert_called_once_with(10,ANY); timer.start.assert_called_once_with(); server.server_close.assert_called_once_with()

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

    def post_error(self, path, payload):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(path, payload)
        error = caught.exception
        try: return error.code, json.loads(error.read())
        finally: error.close()

    def test_main_page_and_empty_api_routes_are_really_successful(self):
        status, body, content_type = self.get("/")
        self.assertEqual(status, 200); self.assertEqual(content_type, "text/html")
        self.assertIn(b"Carnet Emploi", body)
        self.assertIn(b'class="skip-link"',body); self.assertIn(b'aria-label="Navigation principale"',body)
        self.assertIn(b'/common.js',body); self.assertNotIn(b'data-page="mockups"',body)
        for path in ("/api/establishments", "/api/contacts", "/api/trash", "/api/offers"):
            status, body, content_type = self.get(path)
            self.assertEqual(status, 200, path); self.assertEqual(content_type, "application/json")
            self.assertEqual(json.loads(body), [])

    def test_statistics_route_returns_successful_json(self):
        status, body, _ = self.get("/api/statistics?period=month")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 0); self.assertEqual(json.loads(body)["drafts"],0)

    def test_diagnostics_and_backup_download(self):
        status, created=self.post("/api/backup",{})
        self.assertEqual(status,200)
        status, body, content_type=self.get("/api/backups")
        self.assertEqual(status,200); self.assertEqual(json.loads(body)[0]["filename"],created["filename"])
        status, archive, content_type=self.get("/api/backups/download?name="+created["filename"])
        self.assertEqual(status,200); self.assertEqual(content_type,"application/zip"); self.assertTrue(archive.startswith(b"PK"))
        status, body, _=self.get("/api/diagnostics")
        diagnostics=json.loads(body)
        self.assertEqual(status,200); self.assertEqual(diagnostics["status"],"ok"); self.assertEqual(diagnostics["foreign_key_errors"],0); self.assertTrue(diagnostics["writable"])
        status, body, _=self.get("/api/health")
        self.assertEqual(status,200); self.assertEqual(json.loads(body)["application"],"Carnet Emploi 42")

    def test_configuration_status_exposes_flags_but_never_secrets(self):
        values={"FRANCE_TRAVAIL_CLIENT_ID":"client","FRANCE_TRAVAIL_CLIENT_SECRET":"tres-secret","INSEE_API_TOKEN":"jeton-secret","CARNET_EMPLOI_BACKUP_DIR":"/media/usb"}
        with patch.dict("os.environ",values): status,body,_=self.get("/api/configuration/status")
        payload=json.loads(body); self.assertEqual(status,200); self.assertTrue(payload["france_travail"]); self.assertTrue(payload["insee"]); self.assertTrue(payload["external_backup"])
        self.assertNotIn("tres-secret",body.decode()); self.assertNotIn("jeton-secret",body.decode())

    def test_configuration_can_be_saved_once_and_cleared_without_exposing_secrets(self):
        values={"france_travail_client_id":"client","france_travail_client_secret":"tres-secret","insee_api_token":"jeton-secret","external_backup_directory":str(Path(self.temp.name)/"usb")}
        with patch.dict("os.environ",{"FRANCE_TRAVAIL_CLIENT_ID":"","FRANCE_TRAVAIL_CLIENT_SECRET":"","INSEE_API_TOKEN":"","CARNET_EMPLOI_BACKUP_DIR":""}):
            status,result=self.post("/api/configuration",{"values":values})
            self.assertEqual(status,200); self.assertTrue(result["france_travail"]); self.assertTrue(result["insee"])
            self.assertNotIn("tres-secret",json.dumps(result)); self.assertNotIn("jeton-secret",json.dumps(result))
            self.assertTrue((app.DATA/"configuration.json").is_file())
            status,result=self.post("/api/configuration/clear",{"keys":list(values)})
        self.assertEqual(status,200); self.assertFalse(result["france_travail"]); self.assertFalse(result["insee"])

    def test_csv_export_can_be_downloaded(self):
        app.store.execute("INSERT INTO applications(position,created_at) VALUES(?,?)",("Agent","2026-09-19"))
        status,created=self.post("/api/export/csv",{})
        self.assertEqual(status,200)
        status,body,content_type=self.get("/api/exports/download?name="+created["filename"])
        self.assertEqual(status,200); self.assertEqual(content_type,"text/csv"); self.assertIn(b"position",body)

    def test_external_backup_requires_configuration_then_copies_archive(self):
        status,error=self.post_error("/api/backup/external",{})
        self.assertEqual(status,400); self.assertIn("CARNET_EMPLOI_BACKUP_DIR",error["error"])
        external=Path(self.temp.name)/"external"
        with patch.dict("os.environ",{"CARNET_EMPLOI_BACKUP_DIR":str(external)}):
            status,result=self.post("/api/backup/external",{})
        self.assertEqual(status,200); self.assertTrue((external/result["filename"]).is_file())

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

    def test_france_travail_search_is_explicitly_disabled_without_credentials(self):
        with patch.dict("os.environ",{"FRANCE_TRAVAIL_CLIENT_ID":"","FRANCE_TRAVAIL_CLIENT_SECRET":""}):
            status,error=self.post_error("/api/france-travail/search",{"keyword":"agent"})
        self.assertEqual(status,400); self.assertIn("France Travail",error["error"])

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

    def test_manual_establishment_makes_offline_spontaneous_flow_available(self):
        status,created=self.post("/api/establishments/manual",{"company":"Association Test","name":"Siège","postcode":"42000","city":"Saint-Étienne"})
        self.assertEqual(status,201)
        status,result=self.post("/api/spontaneous",{"position":"Agent d'accueil","establishment_ids":[created["id"]],"contact_ids":{}})
        self.assertEqual(status,201); self.assertEqual(len(result["applications"]),1)

    def test_unused_establishment_can_be_edited_disabled_and_deleted(self):
        _,created=self.post("/api/establishments/manual",{"company":"Test","name":"Agence","postcode":"42000","city":"Saint-Étienne"})
        status,_=self.post(f"/api/establishments/{created['id']}/update",{"company":"Test","name":"Agence Loire","postcode":"42300","city":"Roanne"})
        self.assertEqual(status,200)
        status,_=self.post(f"/api/establishments/{created['id']}/active",{"active":False})
        self.assertEqual(status,200)
        _,body,_=self.get("/api/establishments?include_inactive=1")
        self.assertEqual(json.loads(body)[0]["active"],0); self.assertEqual(json.loads(body)[0]["city"],"Roanne")
        status,_=self.post(f"/api/establishments/{created['id']}/delete",{})
        self.assertEqual(status,200)

    def test_spontaneous_duplicate_requires_confirmation_and_keeps_resume(self):
        company=app.store.execute("INSERT INTO companies(name) VALUES(?)",("Test",))
        establishment=app.store.execute("INSERT INTO establishments(company_id,name,postcode) VALUES(?,?,?)",(company,"Test Loire","42000"))
        resume=app.store.execute("INSERT INTO resumes(filename,path,created_at,preferred) VALUES(?,?,?,?)",("cv.docx","cv.docx","2026-09-18",1))
        app.store.execute("INSERT INTO applications(establishment_id,position,created_at) VALUES(?,?,?)",(establishment,"Agent","2026-09-18"))
        payload={"position":"Agent","establishment_ids":[establishment],"contact_ids":{},"resume_id":resume}
        status, warning=self.post("/api/spontaneous",payload)
        self.assertEqual(status,200); self.assertTrue(warning["confirmation_required"])
        self.assertEqual(len(app.store.rows("SELECT id FROM applications")),1)
        status, created=self.post("/api/spontaneous",{**payload,"confirm_duplicates":True})
        self.assertEqual(status,201); self.assertEqual(len(created["applications"]),1)
        row=app.store.rows("SELECT resume_id FROM applications ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual(row["resume_id"],resume)

    def test_unsent_application_draft_can_be_deleted_over_http(self):
        offer=app.store.create_offer({"title":"Agent","company":"Test"})
        draft=app.store.apply_to_offer(offer["id"])
        status,result=self.post(f"/api/applications/{draft}/delete-draft",{})
        self.assertEqual(status,200); self.assertTrue(result["ok"])
        self.assertEqual(app.store.applications(),[])

    def test_sent_status_cannot_bypass_human_confirmation(self):
        application=app.store.execute("INSERT INTO applications(position,created_at) VALUES(?,?)",("Agent","2026-09-19"))
        status,error=self.post_error(f"/api/applications/{application}",{"status":"Candidature envoyée"})
        self.assertEqual(status,400); self.assertIn("Confirmez d'abord",error["error"])


if __name__ == "__main__": unittest.main()
