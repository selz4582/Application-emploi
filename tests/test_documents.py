import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core import Store
from documents import create_backup, export_applications_csv, save_resume, verify_backup, verify_resume


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

    def test_only_two_resumes(self):
        self.add_resume("un.docx"); self.add_resume("deux.docx")
        with self.assertRaisesRegex(ValueError, "Deux CV maximum"): self.add_resume("trois.docx")

    def test_verification_has_only_required_sections(self):
        resume = self.add_resume()
        verify_resume(self.store, resume["id"], {"competences": ["Accueil"]})
        verified = json.loads(self.store.rows("SELECT verified_json FROM resumes")[0]["verified_json"])
        self.assertEqual(verified["competences"], ["Accueil"])
        with self.assertRaises(ValueError): verify_resume(self.store, resume["id"], {"permis": ["B"]})

    def test_backup_is_unique_complete_and_valid(self):
        self.add_resume()
        first = create_backup(self.store, self.data, self.data / "backups")
        second = create_backup(self.store, self.data, self.data / "backups")
        self.assertNotEqual(first, second); self.assertEqual(verify_backup(first)["format"], 1)
        with zipfile.ZipFile(first) as archive: self.assertTrue(any(x.startswith("documents/") for x in archive.namelist()))

    def test_csv_export_has_header(self):
        path = export_applications_csv(self.store, self.data / "exports" / "candidatures.csv")
        self.assertTrue(path.read_text(encoding="utf-8-sig").startswith("id,position"))
