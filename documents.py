"""Gestion locale des CV, exports et sauvegardes sans dépendance externe."""
from __future__ import annotations

import base64
import csv
import io
import json
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

ALLOWED_RESUME_EXTENSIONS = {".docx", ".odt"}
MAX_RESUME_BYTES = 10 * 1024 * 1024


def safe_filename(name: str) -> str:
    """Conserve un nom lisible tout en empêchant les traversées de répertoire."""
    name = Path(name).name.strip()
    cleaned = re.sub(r"[^\w.() -]", "_", name, flags=re.UNICODE)
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Nom de fichier invalide")
    return cleaned


def decode_resume(filename: str, encoded: str) -> tuple[str, bytes]:
    filename = safe_filename(filename)
    if Path(filename).suffix.lower() not in ALLOWED_RESUME_EXTENSIONS:
        raise ValueError("Seuls les CV DOCX et ODT sont acceptés")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("Contenu du CV invalide") from exc
    if not content or len(content) > MAX_RESUME_BYTES:
        raise ValueError("Le CV doit avoir une taille comprise entre 1 octet et 10 Mo")
    if not content.startswith(b"PK"):
        raise ValueError("Le fichier n'est pas un document DOCX ou ODT valide")
    return filename, content


def extract_document_text(content: bytes, extension: str) -> str:
    """Extrait uniquement le texte; le document original n'est jamais modifié."""
    member = "word/document.xml" if extension.lower() == ".docx" else "content.xml"
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read(member)
        root = ElementTree.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("Structure du document illisible") from exc
    chunks = [text.strip() for text in root.itertext() if text.strip()]
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def save_resume(store, documents_dir: Path, filename: str, encoded: str) -> dict:
    existing = store.rows("SELECT id FROM resumes")
    if len(existing) >= 2:
        raise ValueError("Deux CV maximum sont autorisés")
    filename, content = decode_resume(filename, encoded)
    text = extract_document_text(content, Path(filename).suffix)
    documents_dir.mkdir(parents=True, exist_ok=True)
    destination = documents_dir / f"cv-{len(existing) + 1}-{filename}"
    destination.write_bytes(content)
    resume_id = store.execute(
        "INSERT INTO resumes(filename,path,extracted,verified_json,created_at) VALUES(?,?,?,?,?)",
        (filename, str(destination), text, json.dumps({"experiences": [], "competences": [], "diplomes": [], "formations": [], "langues": []}), _now()),
    )
    return {"id": resume_id, "filename": filename, "extracted": text, "verified": _empty_verification()}


def verify_resume(store, resume_id: int, sections: dict) -> None:
    allowed = {"experiences", "competences", "diplomes", "formations", "langues"}
    if set(sections) - allowed:
        raise ValueError("Rubrique de CV non autorisée")
    normalized = {key: [str(value).strip() for value in sections.get(key, []) if str(value).strip()] for key in allowed}
    if not store.rows("SELECT id FROM resumes WHERE id=?", (resume_id,)):
        raise ValueError("CV introuvable")
    store.execute("UPDATE resumes SET verified_json=? WHERE id=?", (json.dumps(normalized, ensure_ascii=False), resume_id))


def set_preferred_resume(store, resume_id: int) -> None:
    """Sélectionne un unique CV par défaut pour les futurs brouillons."""
    if not store.rows("SELECT id FROM resumes WHERE id=?", (resume_id,)):
        raise ValueError("CV introuvable")
    with store.connect() as db:
        db.execute("UPDATE resumes SET preferred=0")
        db.execute("UPDATE resumes SET preferred=1 WHERE id=?", (resume_id,))


def create_backup(store, data_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"cap-emploi-42-{stamp}.zip"
    snapshot = data_dir / f".snapshot-{stamp}.sqlite3"
    source = sqlite3.connect(store.path)
    destination = sqlite3.connect(snapshot)
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    try:
        with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "emploi.sqlite3")
            docs = data_dir / "documents"
            if docs.exists():
                for item in docs.iterdir():
                    if item.is_file(): archive.write(item, f"documents/{item.name}")
            archive.writestr("manifest.json", json.dumps({"format": 1, "created_at": _now()}, ensure_ascii=False))
    finally:
        snapshot.unlink(missing_ok=True)
    return target


def verify_backup(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not {"emploi.sqlite3", "manifest.json"}.issubset(names): raise ValueError("Sauvegarde incomplète")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != 1: raise ValueError("Version de sauvegarde incompatible")
            if archive.testzip() is not None: raise ValueError("Sauvegarde endommagée")
            return manifest
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError("Fichier de sauvegarde invalide") from exc


def export_applications_csv(store, destination: Path) -> Path:
    rows = store.rows("""SELECT a.id,a.position,e.name AS etablissement,e.city,a.email_to,a.status,a.sent_at,a.created_at
                         FROM applications a LEFT JOIN establishments e ON e.id=a.establishment_id ORDER BY a.created_at""")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "position", "etablissement", "city", "email_to", "status", "sent_at", "created_at"]
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields); writer.writeheader(); writer.writerows(rows)
    return destination


def _empty_verification():
    return {key: [] for key in ("experiences", "competences", "diplomes", "formations", "langues")}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
