"""Domaine et persistance locale de Cap Emploi 42."""
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
CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, establishment_id INTEGER REFERENCES establishments(id), name TEXT DEFAULT '', role TEXT DEFAULT '', email TEXT NOT NULL, source_url TEXT NOT NULL, checked_at TEXT NOT NULL, confidence TEXT DEFAULT 'moyen', UNIQUE(email, establishment_id));
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), establishment_id INTEGER REFERENCES establishments(id), title TEXT NOT NULL, city TEXT DEFAULT '', contract TEXT DEFAULT '', work_time TEXT DEFAULT '', description TEXT DEFAULT '', sector TEXT DEFAULT '', published_at TEXT, expires_at TEXT, handicap_explicit INTEGER DEFAULT 0, status TEXT DEFAULT 'À étudier', application_type TEXT DEFAULT 'offre', applied_at TEXT, expected_reply TEXT, followup_at TEXT, next_action TEXT DEFAULT '', deleted_at TEXT);
CREATE TABLE IF NOT EXISTS offer_sources(id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id) ON DELETE CASCADE, source TEXT NOT NULL, url TEXT NOT NULL, reference TEXT DEFAULT '', UNIQUE(source,url));
CREATE TABLE IF NOT EXISTS scores(offer_id INTEGER PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE, total INTEGER NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journeys(offer_id INTEGER PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE, outbound_departure TEXT, outbound_arrival TEXT, outbound_minutes INTEGER, return_departure TEXT, return_arrival TEXT, return_minutes INTEGER, verified INTEGER DEFAULT 0, warning TEXT DEFAULT 'Trajet à vérifier');
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id), establishment_id INTEGER REFERENCES establishments(id), position TEXT NOT NULL, resume_id INTEGER REFERENCES resumes(id), letter TEXT DEFAULT '', email_to TEXT DEFAULT '', email_subject TEXT DEFAULT '', email_body TEXT DEFAULT '', checklist_json TEXT DEFAULT '{}', status TEXT DEFAULT 'Candidature préparée', sent_at TEXT, response_at TEXT, expected_reply TEXT, followup_at TEXT, next_action TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications(id), message TEXT NOT NULL, due_at TEXT, read_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learnings(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL);
"""

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(applications)")}
            for name, definition in (("response_at", "TEXT"), ("expected_reply", "TEXT"), ("followup_at", "TEXT"), ("next_action", "TEXT DEFAULT ''")):
                if name not in columns: db.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def rows(self, sql: str, args=()):
        with self.connect() as db: return [dict(x) for x in db.execute(sql, args)]

    def execute(self, sql: str, args=()):
        with self.connect() as db:
            cur = db.execute(sql, args); db.commit(); return cur.lastrowid

    def upsert_profile(self, data):
        allowed = ("first_name","last_name","civility","city","department","title","email","phone","hobbies","summary")
        vals = [str(data.get(k, "")) for k in allowed]
        with self.connect() as db:
            db.execute(f"INSERT INTO profile(id,{','.join(allowed)}) VALUES(1,{','.join('?' for _ in allowed)}) ON CONFLICT(id) DO UPDATE SET " + ','.join(f"{k}=excluded.{k}" for k in allowed), vals)

    def dashboard(self):
        offers = self.rows("""SELECT o.*,c.name company,e.name establishment,s.total score,s.details_json,j.outbound_minutes,j.return_minutes,
          group_concat(os.source, ', ') sources,max(os.url) source_url FROM offers o LEFT JOIN companies c ON c.id=o.company_id
          LEFT JOIN establishments e ON e.id=o.establishment_id LEFT JOIN scores s ON s.offer_id=o.id
          LEFT JOIN journeys j ON j.offer_id=o.id LEFT JOIN offer_sources os ON os.offer_id=o.id
          WHERE o.deleted_at IS NULL GROUP BY o.id ORDER BY COALESCE(o.applied_at,o.published_at) DESC""")
        for offer in offers:
            offer["score_details"] = json.loads(offer.pop("details_json") or "[]")
        notes = self.rows("SELECT * FROM notifications WHERE read_at IS NULL ORDER BY due_at")
        return {"offers": offers, "notifications": notes, "statuses": STATUSES}

    def create_offer(self, data: dict) -> dict:
        """Enregistre une offre saisie par l'utilisateur et son score explicable."""
        title, company_name = str(data.get("title", "")).strip(), str(data.get("company", "")).strip()
        if not title or not company_name: raise ValueError("Le poste et l'entreprise sont obligatoires")
        published_at = str(data.get("published_at", "")).strip() or None
        if published_at:
            try: date.fromisoformat(published_at[:10])
            except ValueError as exc: raise ValueError("La date de publication est invalide") from exc
        source_url = str(data.get("source_url", "")).strip()
        if source_url and not source_url.startswith(("http://", "https://")): raise ValueError("Le lien de l'offre doit commencer par http:// ou https://")
        with self.connect() as db:
            company = db.execute("SELECT id FROM companies WHERE lower(name)=lower(?) AND active=1 ORDER BY id LIMIT 1", (company_name,)).fetchone()
            company_id = company["id"] if company else db.execute("INSERT INTO companies(name,source_url,checked_at) VALUES(?,?,?)", (company_name,source_url,now())).lastrowid
            offer_id = db.execute("""INSERT INTO offers(company_id,title,city,contract,work_time,description,sector,published_at,status)
                VALUES(?,?,?,?,?,?,?,?,?)""", (company_id,title,str(data.get("city", "")).strip(),str(data.get("contract", "")).strip(),
                str(data.get("work_time", "")).strip(),str(data.get("description", "")).strip(),str(data.get("sector", "")).strip(),published_at,"À étudier")).lastrowid
            if source_url: db.execute("INSERT INTO offer_sources(offer_id,source,url,reference) VALUES(?,?,?,?)", (offer_id,"Saisie manuelle",source_url,str(data.get("reference", "")).strip()))
            journey = None; outbound, returning = data.get("outbound_minutes"), data.get("return_minutes")
            if outbound not in (None, "") or returning not in (None, ""):
                try: outbound, returning = int(outbound), int(returning)
                except (TypeError, ValueError) as exc: raise ValueError("Les deux durées de trajet doivent être renseignées en minutes") from exc
                if min(outbound, returning) < 0: raise ValueError("Les durées de trajet doivent être positives")
                journey = {"outbound_minutes":outbound,"return_minutes":returning,"verified":1}
                db.execute("INSERT INTO journeys(offer_id,outbound_minutes,return_minutes,verified,warning) VALUES(?,?,?,?,?)", (offer_id,outbound,returning,1,""))
            profile = db.execute("SELECT title,summary FROM profile WHERE id=1").fetchone()
            resume = db.execute("SELECT extracted FROM resumes ORDER BY preferred DESC,id LIMIT 1").fetchone()
            offer = {"title":title,"sector":str(data.get("sector", "")),"description":str(data.get("description", ""))}
            result = score_offer(offer,{"positions":profile["title"] if profile else "","sector":profile["summary"] if profile else ""},resume["extracted"] if resume else "",journey)
            db.execute("INSERT INTO scores(offer_id,total,details_json) VALUES(?,?,?)", (offer_id,result["total"],json.dumps(result["details"],ensure_ascii=False)))
        return {"id":offer_id,"score":result}

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

    def applications(self):
        return self.rows("""SELECT a.id,a.position,a.status,a.sent_at,a.expected_reply,a.followup_at,a.next_action,
          a.created_at,a.email_to,e.name establishment,COALESCE(e.city,o.city) city,COALESCE(c.name,oc.name) company,
          CASE WHEN a.offer_id IS NULL THEN 'Spontanée' ELSE 'Offre' END application_type
          FROM applications a LEFT JOIN establishments e ON e.id=a.establishment_id
          LEFT JOIN companies c ON c.id=e.company_id LEFT JOIN offers o ON o.id=a.offer_id
          LEFT JOIN companies oc ON oc.id=o.company_id ORDER BY COALESCE(a.sent_at,a.created_at) DESC""")

    def update_application(self, application_id: int, values: dict):
        allowed = {"status", "sent_at", "response_at", "expected_reply", "followup_at", "next_action"}
        unknown = set(values) - allowed
        if unknown: raise ValueError("Champ de suivi non autorisé")
        if "status" in values and values["status"] not in STATUSES: raise ValueError("Statut inconnu")
        if not values: raise ValueError("Aucune modification")
        assignments = ",".join(f"{key}=?" for key in values)
        args = [values[key] or None for key in values] + [application_id]
        with self.connect() as db:
            cursor = db.execute(f"UPDATE applications SET {assignments} WHERE id=?", args)
            if not cursor.rowcount: raise ValueError("Candidature introuvable")

    def statistics(self, period="month", today: date | None = None):
        today = today or date.today(); days = 7 if period == "week" else 31
        start = (today - timedelta(days=days - 1)).isoformat()
        rows = self.rows("SELECT * FROM applications WHERE substr(COALESCE(sent_at,created_at),1,10)>=?", (start,))
        total=len(rows); responses=sum(x["status"] in {"Entretien ou test","Acceptée","Refusée"} for x in rows)
        interviews=sum(x["status"]=="Entretien ou test" for x in rows)
        spontaneous=sum(x["offer_id"] is None for x in rows)
        delays=[]
        for item in rows:
            if item["sent_at"] and item["response_at"]:
                delays.append((date.fromisoformat(item["response_at"][:10])-date.fromisoformat(item["sent_at"][:10])).days)
        return {"period":period,"total":total,"responses":responses,"response_rate":round(responses*100/total,1) if total else 0,
                "interviews":interviews,"interview_rate":round(interviews*100/total,1) if total else 0,
                "accepted":sum(x["status"]=="Acceptée" for x in rows),"refused":sum(x["status"]=="Refusée" for x in rows),
                "average_delay":round(sum(delays)/len(delays),1) if delays else None,"spontaneous":spontaneous,"offers":total-spontaneous}

    def maintain(self, today: date | None = None):
        """Crée les relances, clôt les silences à 60 jours et purge les éléments échus."""
        today = today or date.today()
        created = 0
        with self.connect() as db:
            applications = db.execute("SELECT * FROM applications WHERE sent_at IS NOT NULL").fetchall()
            protected = {"Entretien ou test", "Acceptée", "Refusée", "Abandonnée"}
            for app in applications:
                sent = date.fromisoformat(app["sent_at"][:10])
                if app["status"] not in protected and (today - sent).days >= 60:
                    db.execute("UPDATE applications SET status='Sans réponse' WHERE id=?", (app["id"],))
                due = date.fromisoformat(app["expected_reply"][:10]) if app["expected_reply"] else sent + timedelta(days=10)
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
    email = str(data.get("email", "")).strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise ValueError("Adresse électronique invalide")
    if not str(data.get("source_url", "")).startswith(("http://","https://")): raise ValueError("Une page source publique est obligatoire")
    if any(x in email.lower() for x in ("gmail.com","hotmail.","outlook.","yahoo.")): raise ValueError("Les adresses privées ne sont pas conservées")
    return True

def build_email(position: str, candidate: str, establishment: str, motivation: str):
    lines=[f"Bonjour,", f"Je vous propose ma candidature au poste de {position} au sein de {establishment}.", motivation.strip(), "Vous trouverez mon CV et ma lettre en pièces jointes.", "Je reste disponible pour un échange.", f"Cordialement, {candidate}"]
    return {"subject":f"Candidature – {position} – {candidate}", "body":"\n".join(x for x in lines if x)[:4000]}

def duplicate_candidates(store: Store, establishment_id: int, position: str, email: str):
    return store.rows("""SELECT a.id,a.position,a.email_to,a.sent_at FROM applications a WHERE a.establishment_id=? AND (lower(a.position)=lower(?) OR lower(a.email_to)=lower(?))""", (establishment_id,position,email))
