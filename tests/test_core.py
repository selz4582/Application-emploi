import io, json, tempfile, unittest
import urllib.error
from unittest.mock import patch
from datetime import date
from pathlib import Path
from core import Store, build_email, duplicate_candidates, hidden_offer, score_offer, validate_public_contact
from connectors import FranceTravailConnector, SireneConnector

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
    def test_public_contact_is_attached_to_active_establishment(self):
        cid=self.store.execute("INSERT INTO companies(name) VALUES(?)",("Test",)); eid=self.store.execute("INSERT INTO establishments(company_id,name,postcode,city) VALUES(?,?,?,?)",(cid,"Test Loire","42000","Saint-Étienne"))
        contact=self.store.add_public_contact({"establishment_id":eid,"name":"Accueil RH","role":"Recrutement","email":"RH@Test.fr","source_url":"https://test.fr/contact","confidence":"élevé"})
        self.assertEqual(self.store.contacts(eid)[0]["id"],contact); self.assertEqual(self.store.contacts(eid)[0]["email"],"rh@test.fr")
        with self.assertRaisesRegex(ValueError,"existe déjà"): self.store.add_public_contact({"establishment_id":eid,"email":"rh@test.fr","source_url":"https://test.fr/contact"})

    def test_establishment_can_be_added_without_external_api(self):
        establishment=self.store.add_manual_establishment({"company":"Association Test","name":"Siège","address":"1 rue Test","postcode":"42000","city":"Saint-Étienne"})
        row=self.store.rows("SELECT e.*,c.name company FROM establishments e JOIN companies c ON c.id=e.company_id WHERE e.id=?",(establishment,))[0]
        self.assertEqual(row["company"],"Association Test"); self.assertEqual(row["postcode"],"42000")
        with self.assertRaisesRegex(ValueError,"existe déjà"): self.store.add_manual_establishment({"company":"Association Test","name":"Siège","postcode":"42000","city":"Saint-Étienne"})
        with self.assertRaisesRegex(ValueError,"Loire"): self.store.add_manual_establishment({"company":"Test","name":"Test","postcode":"69000","city":"Lyon"})

    def test_establishment_lifecycle_preserves_used_history(self):
        establishment=self.store.add_manual_establishment({"company":"Test","name":"Agence","postcode":"42100","city":"Saint-Étienne"})
        self.store.update_establishment(establishment,{"company":"Test renommé","name":"Agence Loire","address":"2 rue Test","postcode":"42300","city":"Roanne"})
        row=self.store.rows("SELECT e.*,c.name company FROM establishments e JOIN companies c ON c.id=e.company_id WHERE e.id=?",(establishment,))[0]
        self.assertEqual(row["company"],"Test renommé"); self.assertEqual(row["city"],"Roanne")
        self.store.set_establishment_active(establishment,False); self.assertEqual(self.store.rows("SELECT active FROM establishments WHERE id=?",(establishment,))[0]["active"],0)
        self.store.execute("INSERT INTO applications(establishment_id,position,created_at) VALUES(?,?,?)",(establishment,"Agent","2026-09-19"))
        with self.assertRaisesRegex(ValueError,"Désactivez-le"): self.store.delete_establishment(establishment)
        unused=self.store.add_manual_establishment({"company":"Test","name":"Annexe","postcode":"42000","city":"Saint-Étienne"})
        self.store.delete_establishment(unused); self.assertEqual(self.store.rows("SELECT id FROM establishments WHERE id=?",(unused,)),[])

    def test_public_contact_can_be_updated_disabled_and_deleted(self):
        cid=self.store.execute("INSERT INTO companies(name) VALUES(?)",("Test",)); eid=self.store.execute("INSERT INTO establishments(company_id,name,postcode) VALUES(?,?,?)",(cid,"Test Loire","42000"))
        contact=self.store.add_public_contact({"establishment_id":eid,"name":"Accueil","email":"rh@test.fr","source_url":"https://test.fr/contact"})
        self.store.update_public_contact(contact,{"establishment_id":eid,"name":"RH","role":"Recrutement","email":"emploi@test.fr","source_url":"https://test.fr/emploi","confidence":"élevé"})
        self.assertEqual(self.store.contacts()[0]["email"],"emploi@test.fr")
        self.store.set_contact_active(contact,False); self.assertEqual(self.store.contacts(),[])
        self.assertEqual(len(self.store.contacts(include_inactive=True)),1)
        self.store.delete_contact(contact); self.assertEqual(self.store.contacts(include_inactive=True),[])
    def test_one_email_six_lines(self):
        mail=build_email("Accueil","Anne Dupont","Médiathèque","Votre mission m'intéresse.")
        self.assertLessEqual(len(mail["body"].splitlines()),6); self.assertIn("Accueil",mail["subject"])
    def test_duplicates(self):
        cid=self.store.execute("INSERT INTO companies(name) VALUES(?)",("Test",)); eid=self.store.execute("INSERT INTO establishments(company_id,name,postcode,city) VALUES(?,?,?,?)",(cid,"Test Loire","42000","Saint-Étienne")); self.store.execute("INSERT INTO applications(establishment_id,position,email_to,created_at) VALUES(?,?,?,?)",(eid,"Agent","rh@test.fr","2026-01-01"))
        self.assertEqual(len(duplicate_candidates(self.store,eid,"Agent","autre@test.fr")),1)
        self.assertEqual(duplicate_candidates(self.store,eid,"Comptable",""),[])
    def test_sirene_normalization_active_establishment(self):
        x=SireneConnector.normalize({"siren":"1","siret":"12","etatAdministratifEtablissement":"A","uniteLegale":{"denominationUniteLegale":"ACME"},"adresseEtablissement":{"codePostalEtablissement":"42000","libelleCommuneEtablissement":"SAINT-ETIENNE"}})
        self.assertTrue(x["active"]); self.assertEqual(x["postcode"],"42000")
    def test_connector_requires_token(self):
        with self.assertRaises(RuntimeError): SireneConnector("").search_loire()
    def test_france_travail_connector_requires_credentials_and_normalizes(self):
        with self.assertRaisesRegex(RuntimeError,"Identifiants"): FranceTravailConnector("","").search_loire()
        normalized=FranceTravailConnector.normalize({"id":"123","intitule":"Agent d'accueil","entreprise":{"nom":"Mairie"},"lieuTravail":{"libelle":"42 - Saint-Étienne"},"typeContrat":"CDD","origineOffre":{}})
        self.assertEqual(normalized["reference"],"123"); self.assertEqual(normalized["company"],"Mairie"); self.assertTrue(normalized["source_url"].endswith("/123"))
    def test_france_travail_connector_uses_oauth_then_official_search(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        responses=[Response(json.dumps({"access_token":"token"}).encode()),Response(json.dumps({"resultats":[{"id":"42","intitule":"Agent","entreprise":{"nom":"Test"}}]}).encode())]
        with patch("connectors.urllib.request.urlopen",side_effect=responses) as opened: rows=FranceTravailConnector("client","secret").search_loire("agent","42218",10)
        self.assertEqual(rows[0]["title"],"Agent"); self.assertEqual(opened.call_count,2)
        request=opened.call_args_list[1].args[0]; self.assertIn("departement=42",request.full_url); self.assertEqual(request.headers["Authorization"],"Bearer token")
    def test_official_connector_network_errors_are_user_friendly(self):
        with patch("connectors.urllib.request.urlopen",side_effect=urllib.error.URLError("secret technical detail")):
            with self.assertRaisesRegex(RuntimeError,"temporairement inaccessible") as caught: FranceTravailConnector("client","secret").search_loire()
        self.assertNotIn("secret technical detail",str(caught.exception))
    def test_maintenance_marks_no_reply_without_obsolete_reminder(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,followup_at,next_action,created_at) VALUES(?,?,?,?,?,?)",("Agent","Candidature envoyée","2026-01-01","2026-01-15","Relancer","2026-01-01"))
        self.assertEqual(self.store.maintain(date(2026,3,5)),0)
        self.assertEqual(self.store.rows("SELECT status FROM applications")[0]["status"],"Sans réponse")
        closed=self.store.rows("SELECT followup_at,next_action FROM applications")[0]; self.assertIsNone(closed["followup_at"]); self.assertEqual(closed["next_action"],"")
        self.assertEqual(self.store.rows("SELECT * FROM notifications"),[])
        self.assertEqual(self.store.maintain(date(2026,3,5)),0)
    def test_tracking_update_validates_status(self):
        app=self.store.execute("INSERT INTO applications(position,created_at) VALUES(?,?)",("Agent","2026-09-01"))
        self.store.update_application(app,{"status":"À candidater","next_action":"Relancer"})
        row=self.store.applications()[0]
        self.assertEqual(row["status"],"À candidater"); self.assertEqual(row["next_action"],"Relancer")
        with self.assertRaisesRegex(ValueError,"Confirmez d'abord"): self.store.update_application(app,{"status":"Candidature envoyée"})
        with self.assertRaisesRegex(ValueError,"non autorisé"): self.store.update_application(app,{"sent_at":"2026-09-02"})
        with self.assertRaisesRegex(ValueError,"Statut inconnu"): self.store.update_application(app,{"status":"Peut-être"})
        sent=self.store.execute("INSERT INTO applications(position,status,sent_at,followup_at,next_action,created_at) VALUES(?,?,?,?,?,?)",("Accueil","Candidature envoyée","2026-09-10","2026-09-20","Relancer","2026-09-10"))
        self.store.update_application(sent,{"status":"Refusée"})
        closed=self.store.rows("SELECT * FROM applications WHERE id=?",(sent,))[0]
        self.assertEqual(closed["response_at"],date.today().isoformat()); self.assertIsNone(closed["followup_at"]); self.assertIsNone(closed["next_action"])
    def test_statistics_week_and_month(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,response_at,created_at) VALUES(?,?,?,?,?)",("Agent","Refusée","2026-09-10","2026-09-12","2026-09-10"))
        self.store.execute("INSERT INTO applications(position,status,created_at) VALUES(?,?,?)",("Accueil","Candidature préparée","2026-08-20"))
        stats=self.store.statistics("month",date(2026,9,13))
        self.assertEqual(stats["total"],1); self.assertEqual(stats["drafts"],1); self.assertEqual(stats["responses"],1); self.assertEqual(stats["response_rate"],100); self.assertEqual(stats["average_delay"],2)
        self.assertEqual(self.store.statistics("week",date(2026,9,13))["total"],1)
    def test_announced_reply_date_drives_reminder(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,expected_reply,created_at) VALUES(?,?,?,?,?)",("Agent","Candidature envoyée","2026-09-01","2026-09-20","2026-09-01"))
        self.assertEqual(self.store.maintain(date(2026,9,19)),0)
        self.assertEqual(self.store.maintain(date(2026,9,20)),1)

    def test_followup_date_has_priority_and_closed_statuses_have_no_reminder(self):
        self.store.execute("INSERT INTO applications(position,status,sent_at,expected_reply,followup_at,created_at) VALUES(?,?,?,?,?,?)",("Agent","Candidature envoyée","2026-09-01","2026-09-10","2026-09-20","2026-09-01"))
        self.assertEqual(self.store.maintain(date(2026,9,15)),0)
        self.assertEqual(self.store.maintain(date(2026,9,20)),1)
        closed=self.store.execute("INSERT INTO applications(position,status,sent_at,followup_at,created_at) VALUES(?,?,?,?,?)",("Accueil","Refusée","2026-09-01","2026-09-10","2026-09-01"))
        self.assertEqual(self.store.maintain(date(2026,9,21)),0)
        self.assertEqual(self.store.rows("SELECT * FROM notifications WHERE application_id=?",(closed,)),[])

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

    def test_manual_journey_keeps_verification_date_and_source(self):
        self.store.create_offer({"title":"Agent","company":"Test","outbound_minutes":"20","return_minutes":"25"})
        offer=self.store.dashboard()["offers"][0]
        self.assertEqual(offer["journey_source"],"Saisie manuelle")
        self.assertTrue(offer["journey_checked_at"])

    def test_profile_and_offer_fields_have_length_limits(self):
        with self.assertRaisesRegex(ValueError,"200 caractères"): self.store.create_offer({"title":"x"*201,"company":"Test"})
        with self.assertRaisesRegex(ValueError,"80 caractères"): self.store.upsert_profile({"first_name":"x"*81})
        with self.assertRaisesRegex(ValueError,"profil invalide"): self.store.upsert_profile({"email":"adresse-invalide"})

    def test_offer_can_be_edited_and_score_is_recalculated(self):
        self.store.upsert_profile({"title":"Agent accueil","summary":"culture"})
        offer=self.store.create_offer({"title":"Comptable","company":"Ancienne","city":"Roanne"})
        old_score=offer["score"]["total"]
        result=self.store.update_offer(offer["id"],{"title":"Agent accueil","company":"Nouvelle","city":"Saint-Étienne","contract":"CDI","sector":"culture","description":"Accueil du public","source_url":"https://example.org/nouvelle","outbound_minutes":"10","return_minutes":"12"})
        displayed=self.store.dashboard()["offers"][0]
        self.assertGreater(result["score"]["total"],old_score)
        self.assertEqual(displayed["company"],"Nouvelle")
        self.assertEqual(displayed["contract"],"CDI")
        self.assertEqual(displayed["return_minutes"],12)
        self.assertEqual(displayed["source_url"],"https://example.org/nouvelle")

    def test_offer_edit_rejects_partial_journey_without_changing_offer(self):
        offer=self.store.create_offer({"title":"Agent","company":"Test"})
        with self.assertRaisesRegex(ValueError,"deux durées"):
            self.store.update_offer(offer["id"],{"title":"Titre modifié","company":"Test","outbound_minutes":"20"})
        self.assertEqual(self.store.dashboard()["offers"][0]["title"],"Agent")

    def test_scores_can_be_recalculated_after_profile_and_verified_cv_change(self):
        offer=self.store.create_offer({"title":"Agent accueil","company":"Test","description":"relation usagers"})
        self.assertEqual(offer["score"]["total"],0)
        self.store.upsert_profile({"title":"Agent accueil"})
        self.store.execute("""INSERT INTO resumes(filename,path,extracted,verified_json,preferred,created_at)
                           VALUES(?,?,?,?,?,?)""", ("cv.docx","cv.docx","texte brut sans validation",json.dumps({"competences":["relation usagers"]}),1,"2026-09-18"))
        self.assertEqual(self.store.recalculate_offer_scores(),1)
        score=self.store.dashboard()["offers"][0]
        self.assertEqual(score["score"],54)
        self.assertEqual(score["score_details"][3]["points"],4)

    def test_unverified_resume_extraction_is_not_used_for_scoring(self):
        self.store.execute("""INSERT INTO resumes(filename,path,extracted,verified_json,preferred,created_at)
                           VALUES(?,?,?,?,?,?)""", ("cv.docx","cv.docx","accueil relation usagers","{}",1,"2026-09-18"))
        offer=self.store.create_offer({"title":"Accueil","company":"Test","description":"relation usagers"})
        self.assertEqual(offer["score"]["details"][3]["points"],0)

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
        with self.assertRaisesRegex(ValueError,"ne peut plus modifier"): self.store.update_application_draft(application,{"email_to":"autre@test.fr","checklist":{}})
        with self.assertRaisesRegex(ValueError,"déjà marquée"): self.store.mark_application_sent(application)

    def test_only_an_unsent_draft_can_be_deleted(self):
        offer=self.store.create_offer({"title":"Agent","company":"Test"})
        draft=self.store.apply_to_offer(offer["id"])
        self.store.execute("INSERT INTO notifications(application_id,message,created_at) VALUES(?,?,?)",(draft,"Test","2026-09-18"))
        self.store.delete_application_draft(draft)
        self.assertEqual(self.store.applications(),[]); self.assertEqual(self.store.rows("SELECT * FROM notifications"),[])
        replacement=self.store.apply_to_offer(offer["id"])
        self.store.execute("UPDATE applications SET status='Candidature envoyée',sent_at=? WHERE id=?",("2026-09-18",replacement))
        with self.assertRaisesRegex(ValueError,"non envoyé"): self.store.delete_application_draft(replacement)

if __name__=="__main__": unittest.main()
