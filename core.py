"""Domaine et persistance locale de Carnet Emploi 42."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("À étudier", "À candidater", "Candidature préparée", "Candidature envoyée",
            "Entretien ou test", "Acceptée", "Refusée", "Sans réponse", "Abandonnée")
SENSITIVE = ("salaire", "handicap", "santé", "droit au travail", "nationalité",
             "casier judiciaire", "disponibilité", "mobilité", "permis", "consentement")

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1), first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', civility TEXT DEFAULT '', city TEXT DEFAULT '', department TEXT DEFAULT '42', title TEXT DEFAULT '', email TEXT DEFAULT '', phone TEXT DEFAULT '', hobbies TEXT DEFAULT '', summary TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS resumes(id INTEGER PRIMARY KEY, filename TEXT NOT NULL, path TEXT NOT NULL, extracted TEXT DEFAULT '', verified_json TEXT DEFAULT '{}', preferred INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, siren TEXT UNIQUE, name TEXT NOT NULL, activity TEXT DEFAULT '', workforce TEXT DEFAULT '', source_url TEXT DEFAULT '', checked_at TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS establishments(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), siret TEXT UNIQUE, name TEXT NOT NULL, address TEXT DEFAULT '', postcode TEXT DEFAULT '', city TEXT DEFAULT '', workforce TEXT DEFAULT '', active INTEGER DEFAULT 1, latitude REAL, longitude REAL);
CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, establishment_id INTEGER REFERENCES establishments(id), name TEXT DEFAULT '', role TEXT DEFAULT '', email TEXT NOT NULL, source_url TEXT NOT NULL, checked_at TEXT NOT NULL, confidence TEXT DEFAULT 'moyen', active INTEGER DEFAULT 1, UNIQUE(email, establishment_id));
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), establishment_id INTEGER REFERENCES establishments(id), title TEXT NOT NULL, city TEXT DEFAULT '', contract TEXT DEFAULT '', work_time TEXT DEFAULT '', description TEXT DEFAULT '', sector TEXT DEFAULT '', published_at TEXT, expires_at TEXT, handicap_explicit INTEGER DEFAULT 0, status TEXT DEFAULT 'À étudier', application_type TEXT DEFAULT 'offre', applied_at TEXT, expected_reply TEXT, followup_at TEXT, next_action TEXT DEFAULT '', deleted_at TEXT);
CREATE TABLE IF NOT EXISTS offer_sources(id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id) ON DELETE CASCADE, source TEXT NOT NULL, url TEXT NOT NULL, reference TEXT DEFAULT '', UNIQUE(source,url));
CREATE TABLE IF NOT EXISTS scores(offer_id INTEGER PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE, total INTEGER NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journeys(offer_id INTEGER PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE, outbound_departure TEXT, outbound_arrival TEXT, outbound_minutes INTEGER, return_departure TEXT, return_arrival TEXT, return_minutes INTEGER, verified INTEGER DEFAULT 0, warning TEXT DEFAULT 'Trajet à vérifier', checked_at TEXT, source TEXT DEFAULT 'Saisie manuelle');
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id), establishment_id INTEGER REFERENCES establishments(id), position TEXT NOT NULL, resume_id INTEGER REFERENCES resumes(id), letter TEXT DEFAULT '', email_to TEXT DEFAULT '', email_subject TEXT DEFAULT '', email_body TEXT DEFAULT '', checklist_json TEXT DEFAULT '{}', status TEXT DEFAULT 'Candidature préparée', sent_at TEXT, response_at TEXT, expected_reply TEXT, followup_at TEXT, next_action TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications(id), message TEXT NOT NULL, due_at TEXT, read_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learnings(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL);
"""

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class ClosingConnection(sqlite3.Connection):
    """Transaction SQLite qui ferme aussi réellement le fichier en sortant du with."""
    def __exit__(self, exc_type, exc_value, traceback):
        try: return super().__exit__(exc_type, exc_value, traceback)
        finally: self.close()

class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(applications)")}
            for name, definition in (("response_at", "TEXT"), ("expected_reply", "TEXT"), ("followup_at", "TEXT"), ("next_action", "TEXT DEFAULT ''")):
                if name not in columns: db.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")
            contact_columns = {row[1] for row in db.execute("PRAGMA table_info(contacts)")}
            if "active" not in contact_columns: db.execute("ALTER TABLE contacts ADD COLUMN active INTEGER DEFAULT 1")
            journey_columns = {row[1] for row in db.execute("PRAGMA table_info(journeys)")}
            for name, definition in (("checked_at","TEXT"),("source","TEXT DEFAULT 'Saisie manuelle'")):
                if name not in journey_columns: db.execute(f"ALTER TABLE journeys ADD COLUMN {name} {definition}")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def rows(self, sql: str, args=()):
        with self.connect() as db: return [dict(x) for x in db.execute(sql, args)]

    def execute(self, sql: str, args=()):
        with self.connect() as db:
            cur = db.execute(sql, args); db.commit(); return cur.lastrowid

    def upsert_profile(self, data):
        allowed = ("first_name","last_name","civility","city","department","title","email","phone","hobbies","summary")
        limits = {"first_name":80,"last_name":80,"civility":30,"city":120,"department":10,"title":160,"email":254,"phone":40,"hobbies":2000,"summary":4000}
        vals = [limited_text(data.get(k, ""),limits[k],k) for k in allowed]
        email = vals[allowed.index("email")]
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise ValueError("Adresse électronique du profil invalide")
        with self.connect() as db:
            db.execute(f"INSERT INTO profile(id,{','.join(allowed)}) VALUES(1,{','.join('?' for _ in allowed)}) ON CONFLICT(id) DO UPDATE SET " + ','.join(f"{k}=excluded.{k}" for k in allowed), vals)

    def dashboard(self):
        offers = self.rows("""SELECT o.*,c.name company,e.name establishment,s.total score,s.details_json,j.outbound_minutes,j.return_minutes,j.checked_at journey_checked_at,j.source journey_source,
          group_concat(os.source, ', ') sources,max(os.url) source_url,max(os.reference) source_reference FROM offers o LEFT JOIN companies c ON c.id=o.company_id
          LEFT JOIN establishments e ON e.id=o.establishment_id LEFT JOIN scores s ON s.offer_id=o.id
          LEFT JOIN journeys j ON j.offer_id=o.id LEFT JOIN offer_sources os ON os.offer_id=o.id
          WHERE o.deleted_at IS NULL GROUP BY o.id ORDER BY COALESCE(o.applied_at,o.published_at) DESC""")
        for offer in offers:
            offer["score_details"] = json.loads(offer.pop("details_json") or "[]")
        notes = self.rows("SELECT * FROM notifications WHERE read_at IS NULL ORDER BY due_at")
        return {"offers": offers, "notifications": notes, "statuses": STATUSES}

    def create_offer(self, data: dict) -> dict:
        """Enregistre une offre saisie par l'utilisateur et son score explicable."""
        title, company_name = limited_text(data.get("title", ""),200,"poste"), limited_text(data.get("company", ""),200,"entreprise")
        if not title or not company_name: raise ValueError("Le poste et l'entreprise sont obligatoires")
        published_at = str(data.get("published_at", "")).strip() or None
        if published_at:
            try: date.fromisoformat(published_at[:10])
            except ValueError as exc: raise ValueError("La date de publication est invalide") from exc
        source_url = str(data.get("source_url", "")).strip()
        source_name = limited_text(data.get("source", "Saisie manuelle"),100,"source") or "Saisie manuelle"
        if source_url and not source_url.startswith(("http://", "https://")): raise ValueError("Le lien de l'offre doit commencer par http:// ou https://")
        with self.connect() as db:
            if source_url and db.execute("SELECT 1 FROM offer_sources WHERE url=?",(source_url,)).fetchone(): raise ValueError("Cette offre a déjà été importée")
            company = db.execute("SELECT id FROM companies WHERE lower(name)=lower(?) AND active=1 ORDER BY id LIMIT 1", (company_name,)).fetchone()
            company_id = company["id"] if company else db.execute("INSERT INTO companies(name,source_url,checked_at) VALUES(?,?,?)", (company_name,source_url,now())).lastrowid
            offer_id = db.execute("""INSERT INTO offers(company_id,title,city,contract,work_time,description,sector,published_at,status)
                VALUES(?,?,?,?,?,?,?,?,?)""", (company_id,title,str(data.get("city", "")).strip(),str(data.get("contract", "")).strip(),
                str(data.get("work_time", "")).strip(),str(data.get("description", "")).strip(),str(data.get("sector", "")).strip(),published_at,"À étudier")).lastrowid
            if source_url: db.execute("INSERT INTO offer_sources(offer_id,source,url,reference) VALUES(?,?,?,?)", (offer_id,source_name,source_url,str(data.get("reference", "")).strip()))
            journey = None; outbound, returning = data.get("outbound_minutes"), data.get("return_minutes")
            if outbound not in (None, "") or returning not in (None, ""):
                try: outbound, returning = int(outbound), int(returning)
                except (TypeError, ValueError) as exc: raise ValueError("Les deux durées de trajet doivent être renseignées en minutes") from exc
                if min(outbound, returning) < 0: raise ValueError("Les durées de trajet doivent être positives")
                journey = {"outbound_minutes":outbound,"return_minutes":returning,"verified":1}
                db.execute("INSERT INTO journeys(offer_id,outbound_minutes,return_minutes,verified,warning,checked_at,source) VALUES(?,?,?,?,?,?,?)", (offer_id,outbound,returning,1,"",now(),"Saisie manuelle"))
            profile = db.execute("SELECT title,summary FROM profile WHERE id=1").fetchone()
            resume = db.execute("SELECT extracted,verified_json FROM resumes ORDER BY preferred DESC,id LIMIT 1").fetchone()
            offer = {"title":title,"sector":str(data.get("sector", "")),"description":str(data.get("description", ""))}
            result = score_offer(offer,{"positions":profile["title"] if profile else "","sector":profile["summary"] if profile else ""},resume_scoring_text(resume),journey)
            db.execute("INSERT INTO scores(offer_id,total,details_json) VALUES(?,?,?)", (offer_id,result["total"],json.dumps(result["details"],ensure_ascii=False)))
        return {"id":offer_id,"score":result}

    def update_offer(self, offer_id: int, data: dict) -> dict:
        """Modifie une offre locale et recalcule immédiatement son score."""
        title, company_name = limited_text(data.get("title", ""),200,"poste"), limited_text(data.get("company", ""),200,"entreprise")
        if not title or not company_name: raise ValueError("Le poste et l'entreprise sont obligatoires")
        published_at = str(data.get("published_at", "")).strip() or None
        if published_at:
            try: date.fromisoformat(published_at[:10])
            except ValueError as exc: raise ValueError("La date de publication est invalide") from exc
        source_url = str(data.get("source_url", "")).strip()
        source_name = limited_text(data.get("source", "Saisie manuelle"),100,"source") or "Saisie manuelle"
        if source_url and not source_url.startswith(("http://", "https://")):
            raise ValueError("Le lien de l'offre doit commencer par http:// ou https://")
        outbound, returning = data.get("outbound_minutes"), data.get("return_minutes")
        if (outbound in (None, "")) != (returning in (None, "")):
            raise ValueError("Les deux durées de trajet doivent être renseignées en minutes")
        if outbound not in (None, ""):
            try: outbound, returning = int(outbound), int(returning)
            except (TypeError, ValueError) as exc: raise ValueError("Les durées de trajet doivent être des nombres entiers") from exc
            if min(outbound, returning) < 0: raise ValueError("Les durées de trajet doivent être positives")
        with self.connect() as db:
            if not db.execute("SELECT id FROM offers WHERE id=? AND deleted_at IS NULL", (offer_id,)).fetchone():
                raise ValueError("Offre introuvable")
            company = db.execute("SELECT id FROM companies WHERE lower(name)=lower(?) AND active=1 ORDER BY id LIMIT 1", (company_name,)).fetchone()
            company_id = company["id"] if company else db.execute("INSERT INTO companies(name,source_url,checked_at) VALUES(?,?,?)", (company_name,source_url,now())).lastrowid
            db.execute("""UPDATE offers SET company_id=?,title=?,city=?,contract=?,work_time=?,description=?,sector=?,published_at=? WHERE id=?""",
                       (company_id,title,str(data.get("city", "")).strip(),str(data.get("contract", "")).strip(),str(data.get("work_time", "")).strip(),str(data.get("description", "")).strip(),str(data.get("sector", "")).strip(),published_at,offer_id))
            db.execute("DELETE FROM offer_sources WHERE offer_id=?", (offer_id,))
            if source_url:
                db.execute("INSERT INTO offer_sources(offer_id,source,url,reference) VALUES(?,?,?,?)", (offer_id,source_name,source_url,str(data.get("reference", "")).strip()))
            db.execute("DELETE FROM journeys WHERE offer_id=?", (offer_id,))
            journey = None
            if outbound not in (None, ""):
                journey = {"outbound_minutes":outbound,"return_minutes":returning,"verified":1}
                db.execute("INSERT INTO journeys(offer_id,outbound_minutes,return_minutes,verified,warning,checked_at,source) VALUES(?,?,?,?,?,?,?)", (offer_id,outbound,returning,1,"",now(),"Saisie manuelle"))
            profile = db.execute("SELECT title,summary FROM profile WHERE id=1").fetchone()
            resume = db.execute("SELECT extracted,verified_json FROM resumes ORDER BY preferred DESC,id LIMIT 1").fetchone()
            offer = {"title":title,"sector":str(data.get("sector", "")),"description":str(data.get("description", ""))}
            result = score_offer(offer,{"positions":profile["title"] if profile else "","sector":profile["summary"] if profile else ""},resume_scoring_text(resume),journey)
            db.execute("""INSERT INTO scores(offer_id,total,details_json) VALUES(?,?,?) ON CONFLICT(offer_id) DO UPDATE SET total=excluded.total,details_json=excluded.details_json""", (offer_id,result["total"],json.dumps(result["details"],ensure_ascii=False)))
        return {"id":offer_id,"score":result}

    def recalculate_offer_scores(self) -> int:
        """Recalcule toutes les offres actives après un changement de profil ou de CV."""
        with self.connect() as db:
            profile = db.execute("SELECT title,summary FROM profile WHERE id=1").fetchone()
            resume = db.execute("SELECT extracted,verified_json FROM resumes ORDER BY preferred DESC,id LIMIT 1").fetchone()
            offers = db.execute("""SELECT o.*,j.outbound_minutes,j.return_minutes,j.verified
                                   FROM offers o LEFT JOIN journeys j ON j.offer_id=o.id
                                   WHERE o.deleted_at IS NULL""").fetchall()
            criteria = {"positions":profile["title"] if profile else "","sector":profile["summary"] if profile else ""}
            cv_text = resume_scoring_text(resume)
            for offer in offers:
                journey = dict(offer) if offer["outbound_minutes"] is not None or offer["return_minutes"] is not None else None
                result = score_offer(dict(offer),criteria,cv_text,journey)
                db.execute("""INSERT INTO scores(offer_id,total,details_json) VALUES(?,?,?)
                           ON CONFLICT(offer_id) DO UPDATE SET total=excluded.total,details_json=excluded.details_json""",
                           (offer["id"],result["total"],json.dumps(result["details"],ensure_ascii=False)))
        return len(offers)

    def apply_to_offer(self, offer_id: int) -> int:
        """Crée un brouillon relié à l'offre, sans jamais envoyer de message."""
        offer = self.rows("SELECT o.*,c.name company FROM offers o JOIN companies c ON c.id=o.company_id WHERE o.id=? AND o.deleted_at IS NULL", (offer_id,))
        if not offer: raise ValueError("Offre introuvable")
        if self.rows("SELECT id FROM applications WHERE offer_id=?", (offer_id,)): raise ValueError("Une candidature existe déjà pour cette offre")
        profile = self.rows("SELECT * FROM profile WHERE id=1")
        candidate = ((profile[0].get("first_name", "")+" "+profile[0].get("last_name", "")).strip() if profile else "Candidat")
        draft = build_email(offer[0]["title"],candidate,offer[0]["company"],"")
        resume = self.rows("SELECT id FROM resumes ORDER BY preferred DESC,id LIMIT 1")
        return self.execute("""INSERT INTO applications(offer_id,position,resume_id,email_subject,email_body,checklist_json,created_at)
            VALUES(?,?,?,?,?,?,?)""", (offer_id,offer[0]["title"],resume[0]["id"] if resume else None,draft["subject"],draft["body"],
            '{"destinataire_verifie": false, "champs_sensibles_vides": true, "validation_humaine": false}',now()))

    def contacts(self, establishment_id: int | None = None, include_inactive: bool = False) -> list[dict]:
        sql = """SELECT ct.*,e.name establishment,e.city FROM contacts ct
                 JOIN establishments e ON e.id=ct.establishment_id WHERE e.active=1"""
        args = ()
        if not include_inactive: sql += " AND ct.active=1"
        if establishment_id is not None:
            sql += " AND ct.establishment_id=?"; args = (establishment_id,)
        return self.rows(sql + " ORDER BY e.name,ct.confidence DESC,ct.name", args)

    def add_manual_establishment(self, data: dict) -> int:
        """Ajoute une cible locale sans dépendre du connecteur SIRENE."""
        company_name=limited_text(data.get("company",""),200,"entreprise")
        name=limited_text(data.get("name","") or company_name,200,"établissement")
        city=limited_text(data.get("city",""),120,"commune")
        postcode=limited_text(data.get("postcode",""),5,"code postal")
        address=limited_text(data.get("address",""),300,"adresse")
        if not company_name or not name or not city or not postcode: raise ValueError("Entreprise, établissement, code postal et commune sont obligatoires")
        if not re.fullmatch(r"42\d{3}",postcode): raise ValueError("Le code postal doit correspondre à la Loire (42)")
        with self.connect() as db:
            company=db.execute("SELECT id FROM companies WHERE lower(name)=lower(?) AND active=1 ORDER BY id LIMIT 1",(company_name,)).fetchone()
            company_id=company["id"] if company else db.execute("INSERT INTO companies(name,source_url,checked_at) VALUES(?,?,?)",(company_name,"Saisie locale",now())).lastrowid
            duplicate=db.execute("SELECT id FROM establishments WHERE company_id=? AND lower(name)=lower(?) AND postcode=? AND lower(city)=lower(?)",(company_id,name,postcode,city)).fetchone()
            if duplicate: raise ValueError("Cet établissement existe déjà")
            return db.execute("INSERT INTO establishments(company_id,name,address,postcode,city,active) VALUES(?,?,?,?,?,1)",(company_id,name,address,postcode,city)).lastrowid

    def update_establishment(self, establishment_id: int, data: dict) -> None:
        """Corrige les informations locales d'un établissement existant."""
        company_name=limited_text(data.get("company",""),200,"entreprise")
        name=limited_text(data.get("name",""),200,"établissement")
        city=limited_text(data.get("city",""),120,"commune")
        postcode=limited_text(data.get("postcode",""),5,"code postal")
        address=limited_text(data.get("address",""),300,"adresse")
        if not company_name or not name or not city or not postcode: raise ValueError("Entreprise, établissement, code postal et commune sont obligatoires")
        if not re.fullmatch(r"42\d{3}",postcode): raise ValueError("Le code postal doit correspondre à la Loire (42)")
        with self.connect() as db:
            if not db.execute("SELECT id FROM establishments WHERE id=?",(establishment_id,)).fetchone(): raise ValueError("Établissement introuvable")
            company=db.execute("SELECT id FROM companies WHERE lower(name)=lower(?) AND active=1 ORDER BY id LIMIT 1",(company_name,)).fetchone()
            company_id=company["id"] if company else db.execute("INSERT INTO companies(name,source_url,checked_at) VALUES(?,?,?)",(company_name,"Correction locale",now())).lastrowid
            duplicate=db.execute("SELECT id FROM establishments WHERE company_id=? AND lower(name)=lower(?) AND postcode=? AND lower(city)=lower(?) AND id<>?",(company_id,name,postcode,city,establishment_id)).fetchone()
            if duplicate: raise ValueError("Cet établissement existe déjà")
            db.execute("UPDATE establishments SET company_id=?,name=?,address=?,postcode=?,city=? WHERE id=?",(company_id,name,address,postcode,city,establishment_id))

    def set_establishment_active(self, establishment_id: int, active: bool) -> None:
        with self.connect() as db:
            cursor=db.execute("UPDATE establishments SET active=? WHERE id=?",(int(active),establishment_id))
            if not cursor.rowcount: raise ValueError("Établissement introuvable")

    def delete_establishment(self, establishment_id: int) -> None:
        """Supprime seulement une cible inutilisée afin de préserver tout l'historique."""
        if not self.rows("SELECT id FROM establishments WHERE id=?",(establishment_id,)): raise ValueError("Établissement introuvable")
        if self.rows("SELECT id FROM applications WHERE establishment_id=? LIMIT 1",(establishment_id,)) or self.rows("SELECT id FROM offers WHERE establishment_id=? LIMIT 1",(establishment_id,)) or self.rows("SELECT id FROM contacts WHERE establishment_id=? LIMIT 1",(establishment_id,)):
            raise ValueError("Cet établissement est utilisé. Désactivez-le pour conserver l'historique")
        self.execute("DELETE FROM establishments WHERE id=?",(establishment_id,))

    def add_public_contact(self, data: dict) -> int:
        validate_public_contact(data)
        try: establishment_id = int(data.get("establishment_id"))
        except (TypeError, ValueError) as exc: raise ValueError("Établissement obligatoire") from exc
        if not self.rows("SELECT id FROM establishments WHERE id=? AND active=1", (establishment_id,)):
            raise ValueError("Établissement actif introuvable")
        email = str(data["email"]).strip().lower()
        if self.rows("SELECT id FROM contacts WHERE establishment_id=? AND lower(email)=?", (establishment_id,email)):
            raise ValueError("Ce contact existe déjà pour cet établissement")
        confidence = str(data.get("confidence", "moyen"))
        if confidence not in {"faible", "moyen", "élevé"}: raise ValueError("Niveau de confiance inconnu")
        return self.execute("""INSERT INTO contacts(establishment_id,name,role,email,source_url,checked_at,confidence)
            VALUES(?,?,?,?,?,?,?)""", (establishment_id,str(data.get("name", "")).strip(),str(data.get("role", "")).strip(),email,
            str(data["source_url"]).strip(),now(),confidence))

    def update_public_contact(self, contact_id: int, data: dict) -> None:
        validate_public_contact(data)
        try: establishment_id = int(data.get("establishment_id"))
        except (TypeError, ValueError) as exc: raise ValueError("Établissement obligatoire") from exc
        if not self.rows("SELECT id FROM establishments WHERE id=? AND active=1", (establishment_id,)):
            raise ValueError("Établissement actif introuvable")
        email = str(data["email"]).strip().lower(); confidence = str(data.get("confidence", "moyen"))
        if confidence not in {"faible","moyen","élevé"}: raise ValueError("Niveau de confiance inconnu")
        duplicate = self.rows("SELECT id FROM contacts WHERE establishment_id=? AND lower(email)=? AND id<>?", (establishment_id,email,contact_id))
        if duplicate: raise ValueError("Ce contact existe déjà pour cet établissement")
        with self.connect() as db:
            cursor = db.execute("""UPDATE contacts SET establishment_id=?,name=?,role=?,email=?,source_url=?,checked_at=?,confidence=? WHERE id=?""",
                (establishment_id,limited_text(data.get("name",""),160,"nom"),limited_text(data.get("role",""),160,"fonction"),email,str(data["source_url"]).strip(),now(),confidence,contact_id))
            if not cursor.rowcount: raise ValueError("Contact introuvable")

    def set_contact_active(self, contact_id: int, active: bool) -> None:
        with self.connect() as db:
            cursor=db.execute("UPDATE contacts SET active=? WHERE id=?",(int(active),contact_id))
            if not cursor.rowcount: raise ValueError("Contact introuvable")

    def delete_contact(self, contact_id: int) -> None:
        with self.connect() as db:
            cursor=db.execute("DELETE FROM contacts WHERE id=?",(contact_id,))
            if not cursor.rowcount: raise ValueError("Contact introuvable")

    def applications(self):
        return self.rows("""SELECT a.id,a.position,a.status,a.sent_at,a.expected_reply,a.followup_at,a.next_action,
          a.created_at,a.email_to,e.name establishment,COALESCE(e.city,o.city) city,COALESCE(c.name,oc.name) company,
          CASE WHEN a.offer_id IS NULL THEN 'Spontanée' ELSE 'Offre' END application_type
          FROM applications a LEFT JOIN establishments e ON e.id=a.establishment_id
          LEFT JOIN companies c ON c.id=e.company_id LEFT JOIN offers o ON o.id=a.offer_id
          LEFT JOIN companies oc ON oc.id=o.company_id ORDER BY COALESCE(a.sent_at,a.created_at) DESC""")

    def application_detail(self, application_id: int) -> dict:
        rows = self.rows("""SELECT a.*,r.filename resume_filename,COALESCE(e.name,c.name) recipient_name
          FROM applications a LEFT JOIN resumes r ON r.id=a.resume_id
          LEFT JOIN establishments e ON e.id=a.establishment_id LEFT JOIN offers o ON o.id=a.offer_id
          LEFT JOIN companies c ON c.id=o.company_id WHERE a.id=?""", (application_id,))
        if not rows: raise ValueError("Candidature introuvable")
        detail = rows[0]
        detail["checklist"] = json.loads(detail.pop("checklist_json") or "{}")
        return detail

    def update_application_draft(self, application_id: int, values: dict) -> None:
        """Met à jour le brouillon local sans effectuer aucun envoi."""
        allowed = {"letter", "email_to", "email_subject", "email_body", "resume_id", "checklist"}
        if set(values) - allowed: raise ValueError("Champ de brouillon non autorisé")
        application = self.rows("SELECT id,sent_at FROM applications WHERE id=?", (application_id,))
        if not application:
            raise ValueError("Candidature introuvable")
        if application[0]["sent_at"]: raise ValueError("Une candidature envoyée ne peut plus modifier son brouillon")
        email = str(values.get("email_to", "")).strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Adresse électronique invalide")
        checklist = values.get("checklist", {})
        checklist_keys = {"destinataire_verifie", "champs_sensibles_vides", "validation_humaine"}
        if set(checklist) - checklist_keys: raise ValueError("Étape de contrôle inconnue")
        normalized = {key: bool(checklist.get(key, False)) for key in checklist_keys}
        resume_id = values.get("resume_id") or None
        if resume_id is not None and not self.rows("SELECT id FROM resumes WHERE id=?", (resume_id,)):
            raise ValueError("CV introuvable")
        with self.connect() as db:
            db.execute("""UPDATE applications SET letter=?,email_to=?,email_subject=?,email_body=?,resume_id=?,checklist_json=? WHERE id=?""",
                (str(values.get("letter", "")),email,str(values.get("email_subject", "")),str(values.get("email_body", "")),
                 resume_id,json.dumps(normalized,ensure_ascii=False),application_id))

    def mark_application_sent(self, application_id: int) -> None:
        detail = self.application_detail(application_id)
        if detail["sent_at"]: raise ValueError("Cette candidature est déjà marquée comme envoyée")
        if not detail["email_to"]: raise ValueError("Le destinataire doit être renseigné")
        required = ("destinataire_verifie", "champs_sensibles_vides", "validation_humaine")
        if not all(detail["checklist"].get(key) for key in required):
            raise ValueError("Toutes les vérifications humaines doivent être cochées")
        sent_at = now()
        self.execute("UPDATE applications SET status='Candidature envoyée',sent_at=?,followup_at=? WHERE id=?",
            (sent_at,(date.today()+timedelta(days=10)).isoformat(),application_id))

    def delete_application_draft(self, application_id: int) -> None:
        """Supprime un brouillon accidentel, mais jamais une candidature déjà envoyée."""
        rows = self.rows("SELECT status,sent_at FROM applications WHERE id=?", (application_id,))
        if not rows: raise ValueError("Candidature introuvable")
        application = rows[0]
        if application["sent_at"] or application["status"] != "Candidature préparée":
            raise ValueError("Seul un brouillon non envoyé peut être supprimé")
        with self.connect() as db:
            db.execute("DELETE FROM notifications WHERE application_id=?", (application_id,))
            db.execute("DELETE FROM applications WHERE id=?", (application_id,))

    def update_application(self, application_id: int, values: dict):
        values = dict(values)
        allowed = {"status", "response_at", "expected_reply", "followup_at", "next_action"}
        unknown = set(values) - allowed
        if unknown: raise ValueError("Champ de suivi non autorisé")
        if "status" in values and values["status"] not in STATUSES: raise ValueError("Statut inconnu")
        if not values: raise ValueError("Aucune modification")
        rows = self.rows("SELECT sent_at,response_at FROM applications WHERE id=?", (application_id,))
        if not rows: raise ValueError("Candidature introuvable")
        sent_at = rows[0]["sent_at"]
        post_send = {"Candidature envoyée","Entretien ou test","Acceptée","Refusée","Sans réponse"}
        if values.get("status") in post_send and not sent_at:
            raise ValueError("Confirmez d'abord l'envoi depuis le dossier de candidature")
        response_statuses = {"Entretien ou test","Acceptée","Refusée"}
        if values.get("status") in response_statuses and not rows[0]["response_at"] and not values.get("response_at"):
            values["response_at"] = date.today().isoformat()
        if values.get("status") in {"Acceptée","Refusée","Sans réponse","Abandonnée"}:
            values.setdefault("followup_at",None); values.setdefault("next_action","")
        for field in ("response_at","expected_reply","followup_at"):
            if values.get(field):
                try: date.fromisoformat(str(values[field])[:10])
                except ValueError as exc: raise ValueError(f"Date invalide pour {field}") from exc
        assignments = ",".join(f"{key}=?" for key in values)
        args = [values[key] or None for key in values] + [application_id]
        with self.connect() as db:
            cursor = db.execute(f"UPDATE applications SET {assignments} WHERE id=?", args)
            if not cursor.rowcount: raise ValueError("Candidature introuvable")

    def statistics(self, period="month", today: date | None = None):
        today = today or date.today(); days = 7 if period == "week" else 31
        start = (today - timedelta(days=days - 1)).isoformat()
        rows = self.rows("SELECT * FROM applications WHERE sent_at IS NOT NULL AND substr(sent_at,1,10)>=?", (start,))
        drafts = self.rows("SELECT id FROM applications WHERE sent_at IS NULL AND substr(created_at,1,10)>=?", (start,))
        total=len(rows); responses=sum(x["status"] in {"Entretien ou test","Acceptée","Refusée"} for x in rows)
        interviews=sum(x["status"]=="Entretien ou test" for x in rows)
        spontaneous=sum(x["offer_id"] is None for x in rows)
        delays=[]
        for item in rows:
            if item["sent_at"] and item["response_at"]:
                delays.append((date.fromisoformat(item["response_at"][:10])-date.fromisoformat(item["sent_at"][:10])).days)
        return {"period":period,"total":total,"drafts":len(drafts),"responses":responses,"response_rate":round(responses*100/total,1) if total else 0,
                "interviews":interviews,"interview_rate":round(interviews*100/total,1) if total else 0,
                "accepted":sum(x["status"]=="Acceptée" for x in rows),"refused":sum(x["status"]=="Refusée" for x in rows),
                "average_delay":round(sum(delays)/len(delays),1) if delays else None,"spontaneous":spontaneous,"offers":total-spontaneous}

    def maintain(self, today: date | None = None):
        """Crée les relances, clôt les silences à 60 jours et purge les éléments échus."""
        today = today or date.today()
        created = 0
        with self.connect() as db:
            applications = db.execute("SELECT * FROM applications WHERE sent_at IS NOT NULL").fetchall()
            closed = {"Acceptée", "Refusée", "Sans réponse", "Abandonnée"}
            for app in applications:
                sent = date.fromisoformat(app["sent_at"][:10])
                if app["status"] in closed: continue
                if app["status"] != "Entretien ou test" and (today - sent).days >= 60:
                    db.execute("UPDATE applications SET status='Sans réponse',followup_at=NULL,next_action='' WHERE id=?", (app["id"],))
                    continue
                due_value = app["followup_at"] or app["expected_reply"]
                if app["status"] == "Entretien ou test" and not due_value: continue
                due = date.fromisoformat(due_value[:10]) if due_value else sent + timedelta(days=10)
                if today >= due and not db.execute("SELECT 1 FROM notifications WHERE application_id=? AND message LIKE 'Relance%'", (app["id"],)).fetchone():
                    db.execute("INSERT INTO notifications(application_id,message,due_at,created_at) VALUES(?,?,?,?)", (app["id"], "Relance de candidature à effectuer", due.isoformat(), now())); created += 1
            cutoff = (today - timedelta(days=30)).isoformat()
            db.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
            db.execute("DELETE FROM offers WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))
        return created

    def trash_offer(self, offer_id: int):
        if not self.rows("SELECT id FROM offers WHERE id=? AND deleted_at IS NULL", (offer_id,)):
            raise ValueError("Offre introuvable")
        self.execute("UPDATE offers SET deleted_at=? WHERE id=?", (now(), offer_id))

    def restore_offer(self, offer_id: int):
        if not self.rows("SELECT id FROM offers WHERE id=? AND deleted_at IS NOT NULL", (offer_id,)):
            raise ValueError("Offre absente de la corbeille")
        self.execute("UPDATE offers SET deleted_at=NULL WHERE id=?", (offer_id,))

    def read_notification(self, notification_id: int):
        self.execute("UPDATE notifications SET read_at=? WHERE id=?", (now(), notification_id))

    def delete_notification(self, notification_id: int):
        self.execute("DELETE FROM notifications WHERE id=?", (notification_id,))

def terms(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zà-ÿ0-9+#.]{2,}", text.lower())}

def limited_text(value, maximum: int, label: str) -> str:
    text = str(value or "").strip()
    if len(text) > maximum: raise ValueError(f"Le champ {label} dépasse {maximum} caractères")
    return text

def resume_scoring_text(resume) -> str:
    """N'utilise que les éléments de CV relus; l'extraction brute reste informative."""
    if not resume: return ""
    try: verified = json.loads(resume["verified_json"] or "{}")
    except (json.JSONDecodeError, TypeError): return ""
    return " ".join(str(item) for values in verified.values() if isinstance(values, list) for item in values)

def score_offer(offer: dict, criteria: dict, cv_text: str = "", journey: dict | None = None):
    """Score explicable. Une donnée inconnue produit 0 point, jamais un malus."""
    title_hit = bool(terms(offer.get("title", "")) & terms(criteria.get("positions", "")))
    sector_hit = bool(terms(offer.get("sector", "")) & terms(criteria.get("sector", "")))
    corpus = offer.get("description", "") + " " + offer.get("title", "")
    cv_overlap = terms(corpus) & terms(cv_text)
    transit_ok = bool(journey and journey.get("outbound_minutes") is not None and journey.get("return_minutes") is not None and journey["outbound_minutes"] <= 60 and journey["return_minutes"] <= 60)
    details = [
        {"criterion":"Correspondance avec le poste", "points":50 if title_hit else 0, "maximum":50, "reason":"Intitulé correspondant" if title_hit else "Correspondance non vérifiée"},
        {"criterion":"Correspondance avec le secteur", "points":20 if sector_hit else 0, "maximum":20, "reason":"Secteur correspondant" if sector_hit else "Secteur inconnu ou différent"},
        {"criterion":"Transports collectifs", "points":20 if transit_ok else 0, "maximum":20, "reason":"Aller-retour compatible (60 min maximum)" if transit_ok else "Trajet non vérifié ou incompatible"},
        {"criterion":"Compétences et expériences des CV", "points":min(10, len(cv_overlap)*2), "maximum":10, "reason":f"{len(cv_overlap)} terme(s) vérifié(s) en commun"},
    ]
    return {"total": sum(x["points"] for x in details), "details": details}

def hidden_offer(offer: dict, score: int | None, journey: dict | None = None) -> list[str]:
    reasons=[]; corpus=(offer.get("title","")+" "+offer.get("sector","")+" "+offer.get("description","")).lower()
    for word in ("cadre","industrie","santé","social","jeune actif","stage","alternance"):
        if word in corpus: reasons.append(f"Catégorie masquée : {word}")
    if score is not None and score < 70: reasons.append("Score inférieur à 70")
    if offer.get("published_at"):
        try:
            if date.fromisoformat(offer["published_at"][:10]) < date.today()-timedelta(days=30): reasons.append("Offre publiée depuis plus de 30 jours")
        except ValueError: pass
    if journey and journey.get("verified") and (journey.get("outbound_minutes", 999)>60 or journey.get("return_minutes",999)>60): reasons.append("Aucun trajet compatible")
    return reasons

def validate_public_contact(data: dict):
    email = limited_text(data.get("email", ""),254,"e-mail")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise ValueError("Adresse électronique invalide")
    source_url = limited_text(data.get("source_url", ""),2000,"source")
    limited_text(data.get("name", ""),160,"nom"); limited_text(data.get("role", ""),160,"fonction")
    if not source_url.startswith(("http://","https://")): raise ValueError("Une page source publique est obligatoire")
    if any(x in email.lower() for x in ("gmail.com","hotmail.","outlook.","yahoo.")): raise ValueError("Les adresses privées ne sont pas conservées")
    return True

def build_email(position: str, candidate: str, establishment: str, motivation: str):
    lines=[f"Bonjour,", f"Je vous propose ma candidature au poste de {position} au sein de {establishment}.", motivation.strip(), "Vous trouverez mon CV et ma lettre en pièces jointes.", "Je reste disponible pour un échange.", f"Cordialement, {candidate}"]
    return {"subject":f"Candidature – {position} – {candidate}", "body":"\n".join(x for x in lines if x)[:4000]}

def duplicate_candidates(store: Store, establishment_id: int, position: str, email: str):
    return store.rows("""SELECT a.id,a.position,a.email_to,a.sent_at FROM applications a
                       WHERE a.establishment_id=? AND (lower(a.position)=lower(?) OR (?<>'' AND lower(a.email_to)=lower(?)))""",
                      (establishment_id,position,email,email))
