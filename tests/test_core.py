import tempfile, unittest
from datetime import date
from pathlib import Path
from core import Store, build_email, duplicate_candidates, hidden_offer, score_offer, validate_public_contact
from connectors import SireneConnector

class DomainTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.tmp.name)/"test.db")
    def tearDown(self): self.tmp.cleanup()
    def test_schema_and_profile(self):
        self.store.upsert_profile({"first_name":"Anne","department":"42"})
        self.assertEqual(self.store.rows("SELECT first_name FROM profile")[0]["first_name"],"Anne")
    def test_fixed_explainable_score(self):
        result=score_offer({"title":"Agent accueil","sector":"culture","description":"relation public équipe service"},{"positions":"agent accueil","sector":"culture"},"relation public équipe service accueil",{"outbound_minutes":35,"return_minutes":40})
        self.assertEqual(result["total"],100); self.assertEqual(sum(x["maximum"] for x in result["details"]),100)
    def test_unknown_is_not_negative(self):
        result=score_offer({"title":"Inconnu"},{"positions":"agent"})
        self.assertEqual(result["total"],0); self.assertIn("non vérifiée",result["details"][0]["reason"])
    def test_hidden_rules(self):
        self.assertIn("Score inférieur à 70",hidden_offer({"title":"Agent"},40))
        self.assertTrue(any("stage" in x for x in hidden_offer({"title":"Stage communication"},90)))
    def test_contacts_require_public_professional_source(self):
        with self.assertRaises(ValueError): validate_public_contact({"email":"personne@gmail.com","source_url":"https://example.org"})
        self.assertTrue(validate_public_contact({"email":"rh@entreprise.fr","source_url":"https://entreprise.fr/contact"}))
    def test_one_email_six_lines(self):
        mail=build_email("Accueil","Anne Dupont","Médiathèque","Votre mission m'intéresse.")
        self.assertLessEqual(len(mail["body"].splitlines()),6); self.assertIn("Accueil",mail["subject"])
    def test_duplicates(self):
        cid=self.store.execute("INSERT INTO companies(name) VALUES(?)",("Test",)); eid=self.store.execute("INSERT INTO establishments(company_id,name,postcode,city) VALUES(?,?,?,?)",(cid,"Test Loire","42000","Saint-Étienne")); self.store.execute("INSERT INTO applications(establishment_id,position,email_to,created_at) VALUES(?,?,?,?)",(eid,"Agent","rh@test.fr","2026-01-01"))
        self.assertEqual(len(duplicate_candidates(self.store,eid,"Agent","autre@test.fr")),1)
    def test_sirene_normalization_active_establishment(self):
        x=SireneConnector.normalize({"siren":"1","siret":"12","etatAdministratifEtablissement":"A","uniteLegale":{"denominationUniteLegale":"ACME"},"adresseEtablissement":{"codePostalEtablissement":"42000","libelleCommuneEtablissement":"SAINT-ETIENNE"}})
        self.assertTrue(x["active"]); self.assertEqual(x["postcode"],"42000")
    def test_connector_requires_token(self):
        with self.assertRaises(RuntimeError): SireneConnector("").search_loire()
    def test_maintenance_creates_reminder_and_marks_no_reply(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,created_at) VALUES(?,?,?,?)",("Agent","Candidature envoyée","2026-01-01","2026-01-01"))
        self.assertEqual(self.store.maintain(date(2026,3,5)),1)
        self.assertEqual(self.store.rows("SELECT status FROM applications")[0]["status"],"Sans réponse")
        self.assertEqual(len(self.store.rows("SELECT * FROM notifications")),1)
        self.assertEqual(self.store.maintain(date(2026,3,5)),0)
    def test_tracking_update_validates_status(self):
        app=self.store.execute("INSERT INTO applications(position,created_at) VALUES(?,?)",("Agent","2026-09-01"))
        self.store.update_application(app,{"status":"Candidature envoyée","sent_at":"2026-09-02","next_action":"Relancer"})
        row=self.store.applications()[0]
        self.assertEqual(row["status"],"Candidature envoyée"); self.assertEqual(row["next_action"],"Relancer")
        with self.assertRaisesRegex(ValueError,"Statut inconnu"): self.store.update_application(app,{"status":"Peut-être"})
    def test_statistics_week_and_month(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,response_at,created_at) VALUES(?,?,?,?,?)",("Agent","Refusée","2026-09-10","2026-09-12","2026-09-10"))
        self.store.execute("INSERT INTO applications(position,status,created_at) VALUES(?,?,?)",("Accueil","Candidature préparée","2026-08-20"))
        stats=self.store.statistics("month",date(2026,9,13))
        self.assertEqual(stats["total"],2); self.assertEqual(stats["responses"],1); self.assertEqual(stats["average_delay"],2)
        self.assertEqual(self.store.statistics("week",date(2026,9,13))["total"],1)
    def test_announced_reply_date_drives_reminder(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,expected_reply,created_at) VALUES(?,?,?,?,?)",("Agent","Candidature envoyée","2026-09-01","2026-09-20","2026-09-01"))
        self.assertEqual(self.store.maintain(date(2026,9,19)),0)
        self.assertEqual(self.store.maintain(date(2026,9,20)),1)

    def test_manual_offer_is_scored_and_can_create_one_draft(self):
        self.store.upsert_profile({"first_name":"Anne","last_name":"Dupont","title":"Agent accueil","summary":"culture"})
        created=self.store.create_offer({"title":"Agent accueil","company":"Médiathèque Loire","city":"Saint-Étienne","sector":"culture","description":"Accueil du public","source_url":"https://example.org/offre","outbound_minutes":"25","return_minutes":"30"})
        self.assertGreaterEqual(created["score"]["total"],90)
        displayed=self.store.dashboard()["offers"][0]
        self.assertEqual(len(displayed["score_details"]),4); self.assertEqual(displayed["source_url"],"https://example.org/offre")
        application_id=self.store.apply_to_offer(created["id"])
        application=self.store.applications()[0]
        self.assertEqual(application["id"],application_id); self.assertEqual(application["company"],"Médiathèque Loire")
        with self.assertRaisesRegex(ValueError,"existe déjà"): self.store.apply_to_offer(created["id"])

    def test_manual_offer_validates_required_and_travel_fields(self):
        with self.assertRaisesRegex(ValueError,"obligatoires"): self.store.create_offer({"title":"Agent"})
        with self.assertRaisesRegex(ValueError,"deux durées"): self.store.create_offer({"title":"Agent","company":"Test","outbound_minutes":"20"})

    def test_offer_can_be_trashed_and_restored(self):
        offer=self.store.create_offer({"title":"Agent","company":"Test"})
        self.store.trash_offer(offer["id"]); self.assertEqual(self.store.dashboard()["offers"],[])
        self.store.restore_offer(offer["id"]); self.assertEqual(len(self.store.dashboard()["offers"]),1)
        with self.assertRaisesRegex(ValueError,"absente"): self.store.restore_offer(offer["id"])

    def test_draft_requires_human_checklist_before_marking_sent(self):
        offer=self.store.create_offer({"title":"Agent","company":"Test"})
        application=self.store.apply_to_offer(offer["id"])
        self.store.update_application_draft(application,{"email_to":"rh@test.fr","email_subject":"Candidature","email_body":"Bonjour","letter":"Lettre","resume_id":None,"checklist":{"destinataire_verifie":True,"champs_sensibles_vides":True,"validation_humaine":False}})
        with self.assertRaisesRegex(ValueError,"vérifications humaines"): self.store.mark_application_sent(application)
        detail=self.store.application_detail(application); detail["checklist"]["validation_humaine"]=True
        self.store.update_application_draft(application,{**{key:detail[key] for key in ("email_to","email_subject","email_body","letter","resume_id")},"checklist":detail["checklist"]})
        self.store.mark_application_sent(application)
        sent=self.store.application_detail(application)
        self.assertEqual(sent["status"],"Candidature envoyée"); self.assertIsNotNone(sent["sent_at"])

if __name__=="__main__": unittest.main()
