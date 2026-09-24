import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from unittest.mock import patch
from pathlib import Path

from core import Store
from documents import application_email, backup_path, create_backup, create_external_backup, decode_resume, delete_backup, delete_resume, ensure_automatic_backup, export_applications_csv, export_path, extract_document_text, list_backups, restore_backup, resume_path, save_resume, set_preferred_resume, verify_backup, verify_resume


def docx_bytes(text="Accueil relation usagers"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="urn:w"><w:p><w:t>{text}</w:t></w:p></w:document>')
    return stream.getvalue()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.store = Store(self.data / "emploi.sqlite3")

    def tearDown(self): self.temp.cleanup()

    def add_resume(self, name="cv.docx"):
        encoded = base64.b64encode(docx_bytes()).decode()
        return save_resume(self.store, self.data / "documents", name, encoded)

    def test_resume_is_copied_and_extracted(self):
        resume = self.add_resume()
        self.assertEqual(resume["extracted"], "Accueil relation usagers")
        row = self.store.rows("SELECT * FROM resumes")[0]
        self.assertEqual(Path(row["path"]).read_bytes(), docx_bytes())

    def test_pdf_resume_is_validated_and_text_is_extracted(self):
        content = b"%PDF-1.7\n% test"
        self.assertEqual(decode_resume("cv.pdf", base64.b64encode(content).decode()), ("cv.pdf", content))
        reader = type("Reader", (), {"is_encrypted": False, "pages": [type("Page", (), {"extract_text": lambda self: "Accueil  des usagers"})()]})()
        module = type("PdfModule", (), {"PdfReader": lambda stream: reader})
        with patch("documents.importlib.util.find_spec", return_value=object()), patch("documents.importlib.import_module", return_value=module):
            self.assertEqual(extract_document_text(content, ".pdf"), "Accueil des usagers")
        with self.assertRaisesRegex(ValueError, "PDF valide"):
            decode_resume("cv.pdf", base64.b64encode(b"not a pdf").decode())

    def test_scanned_pdf_without_text_is_rejected_clearly(self):
        reader = type("Reader", (), {"is_encrypted": False, "pages": [type("Page", (), {"extract_text": lambda self: ""})()]})()
        module = type("PdfModule", (), {"PdfReader": lambda stream: reader})
        with patch("documents.importlib.util.find_spec", return_value=object()), patch("documents.importlib.import_module", return_value=module):
            with self.assertRaisesRegex(ValueError, "Aucun texte"):
                extract_document_text(b"%PDF-1.7", ".pdf")

    def test_only_two_resumes(self):
        self.add_resume("un.docx"); self.add_resume("deux.docx")
        with self.assertRaisesRegex(ValueError, "Deux CV maximum"): self.add_resume("trois.docx")

    def test_verification_has_only_required_sections(self):
        resume = self.add_resume()
        verify_resume(self.store, resume["id"], {"competences": ["Accueil"]})
        verified = json.loads(self.store.rows("SELECT verified_json FROM resumes")[0]["verified_json"])
        self.assertEqual(verified["competences"], ["Accueil"])
        with self.assertRaises(ValueError): verify_resume(self.store, resume["id"], {"permis": ["B"]})

    def test_one_resume_can_be_selected_as_preferred(self):
        first=self.add_resume("un.docx"); second=self.add_resume("deux.docx")
        set_preferred_resume(self.store,first["id"]); set_preferred_resume(self.store,second["id"])
        rows=self.store.rows("SELECT id,preferred FROM resumes ORDER BY id")
        self.assertEqual([row["preferred"] for row in rows],[0,1])
        with self.assertRaisesRegex(ValueError,"introuvable"): set_preferred_resume(self.store,999)

    def test_unused_resume_can_be_deleted_and_preference_moves(self):
        first=self.add_resume("un.docx"); second=self.add_resume("deux.docx")
        self.assertEqual(self.store.rows("SELECT preferred FROM resumes WHERE id=?",(first["id"],))[0]["preferred"],1)
        delete_resume(self.store,self.data/"documents",first["id"])
        remaining=self.store.rows("SELECT * FROM resumes")[0]
        self.assertEqual(remaining["id"],second["id"]); self.assertEqual(remaining["preferred"],1)
        self.assertFalse(any((self.data/"documents").glob("*un.docx")))

    def test_resume_used_by_application_cannot_be_deleted(self):
        resume=self.add_resume(); self.store.execute("INSERT INTO applications(position,resume_id,created_at) VALUES(?,?,?)",("Agent",resume["id"],"2026-09-18"))
        with self.assertRaisesRegex(ValueError,"utilisé par une candidature"): delete_resume(self.store,self.data/"documents",resume["id"])

    def test_resume_download_path_cannot_escape_documents_directory(self):
        resume=self.add_resume("CV été.docx")
        path,filename=resume_path(self.store,self.data/"documents",resume["id"])
        self.assertTrue(path.is_file()); self.assertEqual(filename,"CV été.docx")
        outside=self.data/"outside.docx"; outside.write_bytes(docx_bytes())
        malicious=self.store.execute("INSERT INTO resumes(filename,path,created_at) VALUES(?,?,?)",("outside.docx",str(outside),"2026-09-24"))
        with self.assertRaisesRegex(ValueError,"Chemin du CV invalide"): resume_path(self.store,self.data/"documents",malicious)

    def test_application_email_contains_message_letter_and_selected_resume(self):
        resume=self.add_resume("CV candidature.docx")
        application=self.store.execute("INSERT INTO applications(position,resume_id,email_to,email_subject,email_body,letter,created_at) VALUES(?,?,?,?,?,?,?)",("Agent",resume["id"],"recrutement@example.test","Candidature Agent","Bonjour,\nVoici ma candidature.","Ma motivation", "2026-09-24"))
        content,filename=application_email(self.store,self.data/"documents",application)
        message=BytesParser(policy=policy.default).parsebytes(content)
        self.assertEqual(filename,f"candidature-{application}.eml"); self.assertEqual(message["To"],"recrutement@example.test"); self.assertEqual(message["Subject"],"Candidature Agent")
        attachments={part.get_filename():part.get_payload(decode=True) for part in message.iter_attachments()}
        self.assertIn("lettre-motivation.txt",attachments); self.assertIn("CV candidature.docx",attachments)
        self.assertEqual(attachments["CV candidature.docx"],docx_bytes())
        self.store.execute("UPDATE applications SET email_subject=? WHERE id=?",("Sujet\nBcc: pirate@example.test",application))
        with self.assertRaisesRegex(ValueError,"retour à la ligne interdit"): application_email(self.store,self.data/"documents",application)

    def test_backup_is_unique_complete_and_valid(self):
        self.add_resume()
        first = create_backup(self.store, self.data, self.data / "backups")
        second = create_backup(self.store, self.data, self.data / "backups")
        self.assertTrue(first.name.startswith("carnet-emploi-42-"))
        self.assertNotEqual(first, second); manifest=verify_backup(first); self.assertEqual(manifest["format"],2); self.assertEqual(manifest["schema_version"],self.store.schema_version()); self.assertIn("emploi.sqlite3",manifest["files"])
        with zipfile.ZipFile(first) as archive: self.assertTrue(any(x.startswith("documents/") for x in archive.namelist()))
        listed=list_backups(self.data/"backups")
        self.assertEqual(len(listed),2); self.assertTrue(all(item["valid"] for item in listed))
        self.assertEqual(backup_path(self.data/"backups",first.name),first)
        with self.assertRaisesRegex(ValueError,"invalide"): backup_path(self.data/"backups","../outside.zip")
        delete_backup(self.data/"backups",first.name)
        self.assertFalse(first.exists()); self.assertEqual(len(list_backups(self.data/"backups")),1)

    def test_backup_can_be_safely_restored(self):
        self.store.upsert_profile({"first_name":"Avant"}); self.add_resume()
        archive=create_backup(self.store,self.data,self.data/"backups")
        self.store.upsert_profile({"first_name":"Après"})
        result=restore_backup(self.store,self.data,self.data/"backups",archive.name,base64.b64encode(archive.read_bytes()).decode())
        self.assertEqual(self.store.rows("SELECT first_name FROM profile")[0]["first_name"],"Avant")
        self.assertTrue((self.data/"backups"/result["safety_backup"]).is_file())
        self.assertTrue(any((self.data/"documents").iterdir()))

    def test_failed_database_swap_restores_previous_database_and_documents(self):
        self.store.upsert_profile({"first_name":"Avant"}); self.add_resume("avant.docx")
        archive=create_backup(self.store,self.data,self.data/"backups")
        self.store.upsert_profile({"first_name":"Après"})
        original_replace=os.replace
        def fail_new_database(source,destination):
            if Path(source).name==".restore-emploi.sqlite3": raise OSError("échec simulé")
            return original_replace(source,destination)
        with patch("documents.os.replace",side_effect=fail_new_database):
            with self.assertRaisesRegex(OSError,"échec simulé"):
                restore_backup(self.store,self.data,self.data/"backups",archive.name,base64.b64encode(archive.read_bytes()).decode())
        self.assertEqual(self.store.rows("SELECT first_name FROM profile")[0]["first_name"],"Après")
        self.assertTrue(any((self.data/"documents").glob("*avant.docx")))
        self.assertFalse((self.data/".restore-emploi.sqlite3").exists())

    def test_backup_can_be_copied_to_an_external_directory(self):
        external=self.data/"usb"/"archives"
        copied=create_external_backup(self.store,self.data,external)
        self.assertEqual(copied.parent,external.resolve()); self.assertEqual(verify_backup(copied)["format"],2)
        self.assertEqual(len(list_backups(self.data/"backups")),1)

    def test_daily_automatic_backup_is_unique_and_retains_seven_days(self):
        backups=self.data/"backups"
        self.assertIsNone(ensure_automatic_backup(self.store,self.data,backups,date(2026,9,1)))
        self.store.upsert_profile({"first_name":"Anne"})
        first=ensure_automatic_backup(self.store,self.data,backups,date(2026,9,1))
        self.assertEqual(ensure_automatic_backup(self.store,self.data,backups,date(2026,9,1)),first)
        for offset in range(1,9): ensure_automatic_backup(self.store,self.data,backups,date(2026,9,1)+timedelta(days=offset))
        automatic=list(backups.glob("carnet-emploi-42-auto-*.zip"))
        self.assertEqual(len(automatic),7); self.assertTrue(all(verify_backup(path)["format"]==2 for path in automatic))
        self.assertFalse(any("20260901" in path.name for path in automatic)); self.assertTrue(any("20260909" in path.name for path in automatic))

    def test_restore_rejects_an_invalid_database(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w") as archive:
            archive.writestr("manifest.json",json.dumps({"format":1}))
            archive.writestr("emploi.sqlite3",b"not sqlite")
        with self.assertRaisesRegex(ValueError,"illisible"):
            restore_backup(self.store,self.data,self.data/"backups","bad.zip",base64.b64encode(stream.getvalue()).decode())

    def test_backup_rejects_tampering_and_future_schema_but_accepts_legacy_format(self):
        archive=create_backup(self.store,self.data,self.data/"backups")
        with zipfile.ZipFile(archive) as source:
            entries={name:source.read(name) for name in source.namelist()}
        tampered=self.data/"backups"/"tampered.zip"; entries["emploi.sqlite3"]+=b"alteration"
        with zipfile.ZipFile(tampered,"w",zipfile.ZIP_DEFLATED) as target:
            for name,content in entries.items(): target.writestr(name,content)
        with self.assertRaisesRegex(ValueError,"manifeste"): verify_backup(tampered)
        manifest=json.loads(entries["manifest.json"]); manifest["schema_version"]=self.store.schema_version()+1; entries["manifest.json"]=json.dumps(manifest).encode()
        future=self.data/"backups"/"future.zip"
        with zipfile.ZipFile(future,"w",zipfile.ZIP_DEFLATED) as target:
            for name,content in entries.items(): target.writestr(name,content)
        with self.assertRaisesRegex(ValueError,"version plus récente"): verify_backup(future)
        legacy=self.data/"backups"/"legacy.zip"
        with zipfile.ZipFile(legacy,"w") as target:
            target.writestr("manifest.json",json.dumps({"format":1})); target.writestr("emploi.sqlite3",b"legacy")
        self.assertEqual(verify_backup(legacy)["format"],1)

    def test_csv_export_has_header(self):
        company=self.store.execute("INSERT INTO companies(name) VALUES(?)",("Entreprise Test",))
        offer=self.store.execute("INSERT INTO offers(company_id,title,city) VALUES(?,?,?)",(company,"Agent","Roanne"))
        self.store.execute("INSERT INTO applications(offer_id,position,status,created_at,next_action) VALUES(?,?,?,?,?)",(offer,"Agent","Candidature préparée","2026-09-19","Relire"))
        path = export_applications_csv(self.store, self.data / "exports" / "candidatures.csv")
        content=path.read_text(encoding="utf-8-sig")
        self.assertTrue(content.startswith("id,position,entreprise,etablissement,commune,type"))
        self.assertIn("Entreprise Test,,Roanne,Offre",content); self.assertIn("Relire",content)
        self.assertEqual(export_path(self.data/"exports",path.name),path)
        with self.assertRaisesRegex(ValueError,"invalide"): export_path(self.data/"exports","../candidatures.csv")
