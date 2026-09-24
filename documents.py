"""Gestion locale des CV, exports et sauvegardes sans dépendance externe."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import importlib
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from xml.etree import ElementTree
from core import SCHEMA_VERSION

ALLOWED_RESUME_EXTENSIONS = {".docx", ".odt", ".pdf"}
MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_BACKUP_BYTES = 30 * 1024 * 1024
MAX_BACKUP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_BACKUP_ENTRIES = 100


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
        raise ValueError("Seuls les CV PDF, DOCX et ODT sont acceptés")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("Contenu du CV invalide") from exc
    if not content or len(content) > MAX_RESUME_BYTES:
        raise ValueError("Le CV doit avoir une taille comprise entre 1 octet et 10 Mo")
    extension = Path(filename).suffix.lower()
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("Le fichier n'est pas un document PDF valide")
    if extension in {".docx", ".odt"} and not content.startswith(b"PK"):
        raise ValueError("Le fichier n'est pas un document DOCX ou ODT valide")
    return filename, content


def extract_document_text(content: bytes, extension: str) -> str:
    """Extrait uniquement le texte; le document original n'est jamais modifié."""
    if extension.lower() == ".pdf":
        if importlib.util.find_spec("pypdf") is None:
            raise ValueError("La lecture PDF nécessite le module pypdf. Relancez installer-dependances.bat")
        pdf = importlib.import_module("pypdf")
        try:
            reader = pdf.PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Les PDF protégés par mot de passe ne sont pas acceptés")
            text = " ".join(page.extract_text() or "" for page in reader.pages)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Structure du PDF illisible") from exc
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("Aucun texte n'a été trouvé dans ce PDF. Utilisez un PDF texte, un DOCX ou un ODT")
        return text
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
        "INSERT INTO resumes(filename,path,extracted,verified_json,preferred,created_at) VALUES(?,?,?,?,?,?)",
        (filename, str(destination), text, json.dumps({"experiences": [], "competences": [], "diplomes": [], "formations": [], "langues": []}), int(not existing), _now()),
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


def delete_resume(store, documents_dir: Path, resume_id: int) -> None:
    """Supprime un CV inutilisé et choisit un nouveau CV par défaut si nécessaire."""
    rows = store.rows("SELECT * FROM resumes WHERE id=?", (resume_id,))
    if not rows: raise ValueError("CV introuvable")
    if store.rows("SELECT id FROM applications WHERE resume_id=? LIMIT 1", (resume_id,)):
        raise ValueError("Ce CV est utilisé par une candidature. Choisissez d'abord un autre CV dans son dossier")
    documents_root = documents_dir.resolve()
    path = Path(rows[0]["path"]).resolve()
    try: path.relative_to(documents_root)
    except ValueError as exc: raise ValueError("Chemin du CV invalide") from exc
    was_preferred = bool(rows[0]["preferred"])
    with store.connect() as db:
        db.execute("DELETE FROM resumes WHERE id=?", (resume_id,))
        if was_preferred:
            replacement = db.execute("SELECT id FROM resumes ORDER BY id LIMIT 1").fetchone()
            if replacement: db.execute("UPDATE resumes SET preferred=1 WHERE id=?", (replacement["id"],))
        path.unlink(missing_ok=True)


def resume_path(store, documents_dir: Path, resume_id: int) -> tuple[Path,str]:
    """Retourne un CV uniquement s'il appartient au dossier local réservé aux documents."""
    rows=store.rows("SELECT filename,path FROM resumes WHERE id=?",(resume_id,))
    if not rows: raise ValueError("CV introuvable")
    root=documents_dir.resolve(); path=Path(rows[0]["path"]).resolve()
    try: path.relative_to(root)
    except ValueError as exc: raise ValueError("Chemin du CV invalide") from exc
    if not path.is_file(): raise ValueError("Le fichier du CV est introuvable")
    return path,safe_filename(rows[0]["filename"])


def application_email(store, documents_dir: Path, application_id: int) -> tuple[bytes,str]:
    """Construit un fichier EML local à ouvrir dans la messagerie, sans aucun envoi."""
    detail=store.application_detail(application_id)
    recipient=str(detail.get("email_to") or "").strip(); subject=str(detail.get("email_subject") or "").strip(); body=str(detail.get("email_body") or "").strip()
    if not recipient or not subject or not body: raise ValueError("Renseignez le destinataire, l’objet et le message avant de télécharger le courriel")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",recipient): raise ValueError("L’adresse du destinataire est invalide")
    if any(character in recipient+subject for character in "\r\n"): raise ValueError("Les en-têtes du courriel contiennent un retour à la ligne interdit")
    message=EmailMessage(); message["X-Unsent"]="1"; message["To"]=recipient; message["Subject"]=subject; message.set_content(body)
    letter=str(detail.get("letter") or "").strip()
    if letter: message.add_attachment(letter,subtype="plain",filename="lettre-motivation.txt")
    if detail.get("resume_id"):
        path,filename=resume_path(store,documents_dir,int(detail["resume_id"])); media=mimetypes.guess_type(filename)[0] or "application/octet-stream"; maintype,subtype=media.split("/",1)
        message.add_attachment(path.read_bytes(),maintype=maintype,subtype=subtype,filename=filename)
    return message.as_bytes(),f"candidature-{application_id}.eml"


def create_backup(store, data_dir: Path, backup_dir: Path, filename_prefix="carnet-emploi-42", stamp=None) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"{filename_prefix}-{stamp}.zip"
    snapshot = data_dir / f".snapshot-{stamp}.sqlite3"
    source = sqlite3.connect(store.path)
    destination = sqlite3.connect(snapshot)
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    try:
        with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
            files={"emploi.sqlite3":{"sha256":_file_digest(snapshot),"size":snapshot.stat().st_size}}
            archive.write(snapshot, "emploi.sqlite3")
            docs = data_dir / "documents"
            if docs.exists():
                for item in docs.iterdir():
                    if item.is_file():
                        archive_name=f"documents/{item.name}"
                        files[archive_name]={"sha256":_file_digest(item),"size":item.stat().st_size}
                        archive.write(item,archive_name)
            archive.writestr("manifest.json",json.dumps({"format":2,"created_at":_now(),"schema_version":store.schema_version(),"files":files},ensure_ascii=False,indent=2))
    finally:
        snapshot.unlink(missing_ok=True)
    return target


def ensure_automatic_backup(store, data_dir: Path, backup_dir: Path, current_day=None, keep=7) -> Path | None:
    """Crée au plus une archive automatique par jour et conserve les plus récentes."""
    current_day = current_day or date.today()
    meaningful = sum(store.rows(f"SELECT count(*) AS total FROM {table}")[0]["total"] for table in ("profile","resumes","offers","applications"))
    if not meaningful: return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    prefix=f"carnet-emploi-42-auto-{current_day.strftime('%Y%m%d')}"
    existing=sorted(backup_dir.glob(prefix+"-*.zip"))
    if existing: return existing[-1]
    stamp=f"{current_day.strftime('%Y%m%d')}-{datetime.now().strftime('%H%M%S-%f')}"
    created=create_backup(store,data_dir,backup_dir,"carnet-emploi-42-auto",stamp)
    automatic=sorted(backup_dir.glob("carnet-emploi-42-auto-*.zip"),key=lambda path:path.name,reverse=True)
    for obsolete in automatic[max(1,int(keep)):]: obsolete.unlink(missing_ok=True)
    return created


def create_external_backup(store, data_dir: Path, external_dir: Path) -> Path:
    """Crée une archive puis la copie vers un emplacement externe configuré."""
    external_dir = external_dir.expanduser().resolve()
    external_dir.mkdir(parents=True, exist_ok=True)
    if not external_dir.is_dir(): raise ValueError("Le dossier de sauvegarde externe est invalide")
    local = create_backup(store, data_dir, data_dir / "backups")
    target = external_dir / local.name
    if target.exists(): raise ValueError("Une sauvegarde externe portant ce nom existe déjà")
    shutil.copy2(local, target)
    verify_backup(target)
    return target


def verify_backup(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist()
            if len(infos)>MAX_BACKUP_ENTRIES: raise ValueError("La sauvegarde contient trop de fichiers")
            if sum(info.file_size for info in infos)>MAX_BACKUP_UNCOMPRESSED_BYTES: raise ValueError("La sauvegarde décompressée est trop volumineuse")
            names = {info.filename for info in infos}
            if len(names)!=len(infos): raise ValueError("La sauvegarde contient des fichiers en double")
            for name in names:
                candidate=Path(name)
                if candidate.is_absolute() or ".." in candidate.parts or (name not in {"emploi.sqlite3","manifest.json"} and not name.startswith("documents/")):
                    raise ValueError("La sauvegarde contient un chemin non autorisé")
            if not {"emploi.sqlite3", "manifest.json"}.issubset(names): raise ValueError("Sauvegarde incomplète")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") not in {1,2}: raise ValueError("Version de sauvegarde incompatible")
            if archive.testzip() is not None: raise ValueError("Sauvegarde endommagée")
            if manifest.get("format")==2:
                if int(manifest.get("schema_version",0))>SCHEMA_VERSION: raise ValueError("Cette sauvegarde provient d'une version plus récente de l'application")
                files=manifest.get("files")
                if not isinstance(files,dict) or set(files)!=(names-{"manifest.json"}): raise ValueError("Manifeste de sauvegarde incohérent")
                for name,expected in files.items():
                    if not isinstance(expected,dict) or expected.get("size")!=archive.getinfo(name).file_size or expected.get("sha256")!=hashlib.sha256(archive.read(name)).hexdigest():
                        raise ValueError(f"Le fichier {name} ne correspond pas au manifeste")
            return manifest
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError("Fichier de sauvegarde invalide") from exc


def list_backups(backup_dir: Path) -> list[dict]:
    """Inventorie les archives locales sans empêcher l'affichage si l'une est invalide."""
    if not backup_dir.exists(): return []
    result = []
    for path in sorted(backup_dir.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            manifest = verify_backup(path)
            result.append({"filename":path.name,"size":path.stat().st_size,"created_at":manifest.get("created_at"),"schema_version":manifest.get("schema_version"),"valid":True,"error":""})
        except ValueError as exc:
            result.append({"filename":path.name,"size":path.stat().st_size,"created_at":None,"valid":False,"error":str(exc)})
    return result


def backup_health(store, backup_dir: Path, current_day=None) -> dict:
    """Résume la fraîcheur des sauvegardes sans créer d'archive implicitement."""
    current_day=current_day or date.today()
    meaningful=sum(store.rows(f"SELECT count(*) total FROM {table}")[0]["total"] for table in ("profile","resumes","offers","applications"))
    if not meaningful: return {"level":"empty","needed":False,"latest_created_at":None,"age_days":None}
    latest=None
    if backup_dir.exists():
        for path in sorted(backup_dir.glob("*.zip"),key=lambda item:item.stat().st_mtime,reverse=True):
            try: latest=verify_backup(path); break
            except ValueError: continue
    if not latest: return {"level":"missing","needed":True,"latest_created_at":None,"age_days":None}
    created=latest.get("created_at")
    try: age=max(0,(current_day-datetime.fromisoformat(created.replace("Z","+00:00")).date()).days)
    except (AttributeError,TypeError,ValueError): age=None
    stale=age is None or age>7
    return {"level":"stale" if stale else "recent","needed":stale,"latest_created_at":created,"age_days":age}


def backup_path(backup_dir: Path, filename: str) -> Path:
    """Résout uniquement un nom d'archive situé directement dans le dossier dédié."""
    if Path(filename).name != filename or Path(filename).suffix.lower() != ".zip":
        raise ValueError("Nom de sauvegarde invalide")
    root = backup_dir.resolve(); path = (root / filename).resolve()
    try: path.relative_to(root)
    except ValueError as exc: raise ValueError("Chemin de sauvegarde invalide") from exc
    if not path.is_file(): raise ValueError("Sauvegarde introuvable")
    return path


def delete_backup(backup_dir: Path, filename: str) -> None:
    """Supprime uniquement une archive explicitement sélectionnée dans le dossier local."""
    path = backup_path(backup_dir, filename)
    path.unlink()


def restore_backup(store, data_dir: Path, backup_dir: Path, filename: str, encoded: str) -> dict:
    """Vérifie puis restaure une archive, après une sauvegarde de sécurité automatique."""
    if Path(filename).suffix.lower() != ".zip": raise ValueError("La sauvegarde doit être un fichier ZIP")
    try: content = base64.b64decode(encoded, validate=True)
    except ValueError as exc: raise ValueError("Contenu de sauvegarde invalide") from exc
    if not content or len(content) > MAX_BACKUP_BYTES:
        raise ValueError("La sauvegarde doit avoir une taille comprise entre 1 octet et 30 Mo")
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="restore-", dir=data_dir) as temporary:
        staging = Path(temporary); archive_path = staging / "upload.zip"; archive_path.write_bytes(content)
        manifest = verify_backup(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                path = Path(name)
                if path.is_absolute() or ".." in path.parts or (name != "emploi.sqlite3" and name != "manifest.json" and not name.startswith("documents/")):
                    raise ValueError("La sauvegarde contient un chemin non autorisé")
            archive.extractall(staging / "content")
        restored_db = staging / "content" / "emploi.sqlite3"
        try:
            connection = sqlite3.connect(restored_db)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            raise ValueError("La base de la sauvegarde est illisible") from exc
        finally:
            if "connection" in locals(): connection.close()
        required = {"profile","resumes","companies","establishments","offers","applications"}
        if integrity != "ok" or not required.issubset(tables): raise ValueError("La base de la sauvegarde est incomplète ou endommagée")
        safety = create_backup(store,data_dir,backup_dir)
        documents = data_dir / "documents"; old_documents = staging / "old-documents"
        incoming_documents = staging / "content" / "documents"
        database=Path(store.path).resolve(); old_database=staging/"old-emploi.sqlite3"
        replacement=database.with_name(".restore-emploi.sqlite3")
        sidecars=[Path(str(database)+suffix) for suffix in ("-wal","-shm")]
        old_sidecars=[]
        try:
            shutil.copy2(restored_db,replacement)
            if documents.exists(): documents.rename(old_documents)
            if incoming_documents.exists(): shutil.copytree(incoming_documents,documents)
            else: documents.mkdir(parents=True,exist_ok=True)
            for sidecar in sidecars:
                if sidecar.exists():
                    old=staging/sidecar.name; os.replace(sidecar,old); old_sidecars.append((sidecar,old))
            os.replace(database,old_database)
            try: os.replace(replacement,database)
            except Exception:
                os.replace(old_database,database)
                raise
        except Exception:
            replacement.unlink(missing_ok=True)
            if old_database.exists() and not database.exists(): os.replace(old_database,database)
            for sidecar,old in old_sidecars:
                if old.exists(): os.replace(old,sidecar)
            shutil.rmtree(documents,ignore_errors=True)
            if old_documents.exists(): old_documents.rename(documents)
            raise
    return {"created_at":manifest.get("created_at"),"safety_backup":safety.name}


def export_applications_csv(store, destination: Path) -> Path:
    rows = store.rows("""SELECT a.id,a.position,COALESCE(c.name,oc.name,'') AS entreprise,
                         COALESCE(e.name,'') AS etablissement,COALESCE(e.city,o.city,'') AS commune,
                         CASE WHEN a.offer_id IS NULL THEN 'Spontanée' ELSE 'Offre' END AS type,
                         a.email_to,a.status,a.sent_at,a.response_at,a.expected_reply,a.followup_at,a.next_action,a.created_at
                         FROM applications a LEFT JOIN establishments e ON e.id=a.establishment_id
                         LEFT JOIN companies c ON c.id=e.company_id LEFT JOIN offers o ON o.id=a.offer_id
                         LEFT JOIN companies oc ON oc.id=o.company_id ORDER BY a.created_at""")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id","position","entreprise","etablissement","commune","type","email_to","status","sent_at","response_at","expected_reply","followup_at","next_action","created_at"]
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields); writer.writeheader(); writer.writerows(rows)
    return destination


def export_path(export_dir: Path, filename: str) -> Path:
    """Retourne uniquement un export CSV créé dans le répertoire prévu."""
    if Path(filename).name != filename or Path(filename).suffix.lower() != ".csv": raise ValueError("Nom d'export invalide")
    root=export_dir.resolve(); path=(root/filename).resolve()
    try: path.relative_to(root)
    except ValueError as exc: raise ValueError("Chemin d'export invalide") from exc
    if not path.is_file(): raise ValueError("Export introuvable")
    return path


def _empty_verification():
    return {key: [] for key in ("experiences", "competences", "diplomes", "formations", "langues")}


def _file_digest(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
