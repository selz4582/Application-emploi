"""Serveur HTTP local sans dépendance tierce."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import base64, json, mimetypes, os, platform, secrets, shutil, threading, webbrowser
import sys
import html
from urllib.parse import urlparse, parse_qs, quote
from core import STATUSES, Store, build_email, duplicate_candidates, now
from connectors import ExternalJobPageConnector, FranceTravailConnector, SireneConnector
from documents import application_email, backup_health, backup_path, create_backup, create_external_backup, delete_backup, delete_resume, ensure_automatic_backup, export_applications_csv, export_data_json, export_path, list_backups, restore_backup, resume_path, save_resume, set_preferred_resume, verify_resume
from settings import SettingsStore
from auth import GoogleAuth
from version import APP_VERSION

APP_NAME="Carnet Emploi 42"
BROWSER_OPEN_DELAY=10
BUNDLE_ROOT=Path(getattr(sys,"_MEIPASS",Path(__file__).parent))
APP_DIR=Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).parent
DEFAULT_DATA=(Path(os.getenv("LOCALAPPDATA",APP_DIR))/APP_NAME/"data") if getattr(sys,"frozen",False) else APP_DIR/"data"
STATIC_ROOT=BUNDLE_ROOT/"static"; DATA=Path(os.getenv("CARNET_EMPLOI_DATA_DIR",DEFAULT_DATA)); store=Store(DATA/"emploi.sqlite3"); REQUEST_LOCK=threading.RLock()
INCIDENT_LOG_LIMIT=512*1024

def settings(): return SettingsStore(DATA/"configuration.json")
AUTH_MANAGERS={}
def auth_manager(port):
    key=(str(DATA.resolve()),int(port))
    if key not in AUTH_MANAGERS: AUTH_MANAGERS[key]=GoogleAuth(settings(),DATA,f"http://127.0.0.1:{port}")
    return AUTH_MANAGERS[key]

def record_incident(path,exc,method="HTTP"):
    """Journalise seulement des métadonnées non sensibles et retourne un identifiant court."""
    incident=secrets.token_hex(6); logs=DATA/"logs"; target=logs/"incidents.jsonl"
    try:
        logs.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.stat().st_size>=INCIDENT_LOG_LIMIT:
            rotated=logs/"incidents.1.jsonl"; rotated.unlink(missing_ok=True); target.replace(rotated)
        entry={"incident":incident,"created_at":now(),"method":method,"path":urlparse(path).path,"error_type":type(exc).__name__}
        with target.open("a",encoding="utf-8") as stream: stream.write(json.dumps(entry,ensure_ascii=False)+"\n")
    except OSError: pass
    return incident

def support_report():
    """Produit un diagnostic partageable sans chemins locaux, coordonnées ni secrets."""
    integrity=store.rows("PRAGMA integrity_check")[0]["integrity_check"]
    foreign_keys=len(store.rows("PRAGMA foreign_key_check"))
    counts={table:store.rows(f"SELECT count(*) total FROM {table}")[0]["total"] for table in ("offers","applications","resumes","establishments","contacts")}
    backups=list_backups(DATA/"backups")
    status=settings().status()
    incidents=[]; log=DATA/"logs"/"incidents.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8",errors="replace").splitlines()[-50:]:
            try:
                item=json.loads(line)
                incidents.append({key:item.get(key) for key in ("incident","created_at","method","path","error_type")})
            except json.JSONDecodeError: continue
    return {"application":APP_NAME,"application_version":APP_VERSION,"generated_at":now(),"system":{"platform":platform.system(),"release":platform.release(),"python":sys.version.split()[0]},"database":{"integrity":integrity,"foreign_key_errors":foreign_keys,"schema_version":store.schema_version(),"counts":counts},"backups":{"total":len(backups),"valid":sum(bool(item["valid"]) for item in backups),"latest_created_at":next((item.get("created_at") for item in backups if item["valid"]),None)},"services":{key:bool(status.get(key)) for key in ("france_travail","insee","external_backup","google_sso")},"recent_incidents":incidents}

class Handler(SimpleHTTPRequestHandler):
    server_version=APP_NAME
    sys_version=""

    def end_headers(self):
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Permissions-Policy","camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if urlparse(self.path).path.startswith(("/api/","/auth/")): self.send_header("Cache-Control","no-store")
        super().end_headers()

    def validate_local_request(self,method):
        host=self.headers.get("Host","")
        try: parsed_host=urlparse("//"+host); hostname=parsed_host.hostname; port=parsed_host.port or 80
        except ValueError: hostname=None; port=None
        expected_port=self.server.server_address[1]
        if hostname not in {"127.0.0.1","localhost"} or port!=expected_port:
            self.send_json({"error":"Hôte local non autorisé"},421); return False
        origin=self.headers.get("Origin","")
        if method=="POST" and origin:
            try: parsed_origin=urlparse(origin); origin_port=parsed_origin.port or (443 if parsed_origin.scheme=="https" else 80)
            except ValueError: parsed_origin=None; origin_port=None
            if not parsed_origin or parsed_origin.scheme!="http" or parsed_origin.hostname not in {"127.0.0.1","localhost"} or origin_port!=expected_port:
                self.send_json({"error":"Origine de requête non autorisée"},403); return False
        return True
    def log_request(self, code="-", size="-"):
        """N'affiche que les vraies erreurs HTTP, pas les réponses 200 normales."""
        try: status = int(code)
        except (TypeError, ValueError): status = 0
        if status >= 400: super().log_request(code, size)

    def send_json(self,obj,status=200):
        body=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def send_redirect(self,url,cookie=None):
        self.send_response(302); self.send_header("Location",url)
        if cookie: self.send_header("Set-Cookie",cookie)
        self.send_header("Content-Length","0"); self.end_headers()
    def send_html(self,body,status=200):
        payload=body.encode(); self.send_response(status); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def auth(self): return auth_manager(self.server.server_address[1])
    def identity(self): return self.auth().identity_from_headers(self.headers)
    def require_authentication(self,path,method):
        public={"/api/health","/api/configuration/status","/auth/status"}
        bootstrap=path=="/api/configuration" and method=="POST" and not self.auth().enabled
        if not self.auth().enabled or path.startswith("/auth/") or path in public or bootstrap: return True
        if self.identity(): return True
        if path.startswith("/api/") or method=="POST": self.send_json({"error":"Connexion Google requise"},401)
        else: self.send_redirect("/auth/login")
        return False
    def send_file(self,path: Path,content_type="application/zip",download_name=None):
        body=path.read_bytes(); name=download_name or path.name; fallback=''.join(character if character.isascii() and (character.isalnum() or character in "._- ") else '_' for character in name) or "document"; disposition=f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name)}'; self.send_response(200); self.send_header("Content-Type",content_type); self.send_header("Content-Disposition",disposition); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def send_download(self,body,content_type,name):
        fallback=''.join(character if character.isascii() and (character.isalnum() or character in "._- ") else '_' for character in name) or "document"; self.send_response(200); self.send_header("Content-Type",content_type); self.send_header("Content-Disposition",f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name)}'); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def send_json_download(self,obj,filename):
        body=json.dumps(obj,ensure_ascii=False,indent=2).encode(); self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Disposition",f'attachment; filename="{filename}"'); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def body(self):
        try:
            length=int(self.headers.get("Content-Length","0"))
        except (TypeError,ValueError) as exc:
            raise ValueError("Taille de requête invalide") from exc
        if length < 0: raise ValueError("Taille de requête invalide")
        if length > 42 * 1024 * 1024: raise ValueError("Requête trop volumineuse")
        try: data=json.loads(self.rfile.read(length) or b"{}")
        except (ValueError,json.JSONDecodeError): raise ValueError("JSON invalide")
        def check(value, key="champ"):
            limits={"content":40*1024*1024,"description":10000,"email_body":10000,"letter":10000,"motivation":4000,"source_url":2000}
            if isinstance(value,str) and len(value)>limits.get(key,4000): raise ValueError(f"Le champ {key} dépasse la taille autorisée")
            if isinstance(value,dict):
                for name,item in value.items(): check(item,str(name))
            elif isinstance(value,list):
                if len(value)>1000: raise ValueError("La liste contient trop d'éléments")
                for item in value: check(item,key)
        check(data); return data
    def do_GET(self):
        if not self.validate_local_request("GET"): return None
        with REQUEST_LOCK:
            path=urlparse(self.path).path
            try:
                if path.startswith("/auth/"): return self.handle_auth_GET()
                if not self.require_authentication(path,"GET"): return None
                return self.handle_GET()
            except Exception as exc:
                return self.send_unexpected_error(path,exc)

    def send_unexpected_error(self,path,exc):
        """Répond sans divulguer le détail potentiellement sensible de l'exception."""
        incident=record_incident(path,exc,getattr(self,"command","HTTP"))
        self.log_error("Erreur interne non gérée (%s, incident %s)",type(exc).__name__,incident)
        message="Erreur interne locale. Réessayez ou redémarrez l’application."
        if path.startswith("/api/"): return self.send_json({"error":message,"incident":incident},500)
        return self.send_html(
            "<!doctype html><html lang='fr'><meta charset='utf-8'>"
            "<title>Erreur · Carnet Emploi 42</title><h1>Impossible d’afficher cette page</h1>"
            f"<p>{message}</p><p>Incident : {incident}</p><p><a href='/'>Revenir à l’accueil</a></p>",500)
    def handle_auth_GET(self):
        parsed=urlparse(self.path); path=parsed.path; manager=self.auth()
        try:
            if path=="/auth/login":
                if not manager.enabled: return self.send_html("<!doctype html><html lang='fr'><meta charset='utf-8'><title>Configuration requise</title><body><h1>Google SSO n’est pas configuré</h1><p>Ouvrez l’application locale puis renseignez le client Google dans Mon profil.</p><a href='/'>Retour à l’application</a></body></html>")
                if self.identity(): return self.send_redirect("/")
                return self.send_html("<!doctype html><html lang='fr'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Connexion · Carnet Emploi 42</title><body style='font-family:system-ui;max-width:560px;margin:10vh auto;padding:24px'><h1>Carnet Emploi 42</h1><p>Connectez-vous avec le compte Google autorisé pour ouvrir vos données locales.</p><p><a href='/auth/google/start' style='display:inline-block;padding:12px 18px;background:#176b52;color:white;border-radius:8px;text-decoration:none'>Continuer avec Google</a></p><small>La base et les CV restent sur cet ordinateur.</small></body></html>")
            if path=="/auth/google/start": return self.send_redirect(manager.authorization_url())
            if path=="/auth/google/callback":
                query=parse_qs(parsed.query)
                if query.get("error"): raise ValueError("Connexion Google annulée")
                identity=manager.complete(query.get("code",[""])[0],query.get("state",[""])[0])
                return self.send_redirect("/",manager.session_cookie(identity))
            if path=="/auth/logout": return self.send_redirect("/auth/login",manager.clear_cookie())
            if path=="/auth/status":
                identity=self.identity(); return self.send_json({"enabled":manager.enabled,"authenticated":bool(identity),"identity":identity or {}})
            return self.send_html("Page introuvable",404)
        except ValueError as exc: return self.send_html(f"<!doctype html><html lang='fr'><meta charset='utf-8'><h1>Connexion impossible</h1><p>{html.escape(str(exc))}</p><a href='/auth/login'>Réessayer</a></html>",400)
    def handle_GET(self):
        p=urlparse(self.path)
        if p.path=="/api/dashboard":
            store.maintain(); result=store.dashboard(); result["backup_status"]=backup_health(store,DATA/"backups"); return self.send_json(result)
        if p.path=="/api/offers": return self.send_json(store.dashboard()["offers"])
        if p.path=="/api/profile":
            rows=store.rows("SELECT * FROM profile WHERE id=1"); return self.send_json(rows[0] if rows else {})
        if p.path=="/api/companies":
            q=parse_qs(p.query); size=q.get("workforce",[""])[0]
            sql="SELECT c.*,count(e.id) establishments FROM companies c LEFT JOIN establishments e ON e.company_id=c.id AND e.active=1 WHERE c.active=1"; args=[]
            if size: sql+=" AND (c.workforce=? OR e.workforce=?)"; args=[size,size]
            return self.send_json(store.rows(sql+" GROUP BY c.id ORDER BY c.name",args))
        if p.path=="/api/establishments":
            q=parse_qs(p.query); cid=q.get("company_id",[""])[0]; include_inactive=q.get("include_inactive",[""])[0]=="1"
            sql="SELECT e.*,c.name company FROM establishments e LEFT JOIN companies c ON c.id=e.company_id WHERE e.postcode LIKE '42%'"
            if not include_inactive: sql+=" AND e.active=1"
            if cid: sql+=" AND e.company_id=?"
            return self.send_json(store.rows(sql+" ORDER BY e.city,e.name",(cid,) if cid else ()))
        if p.path=="/api/contacts":
            query=parse_qs(p.query); raw=query.get("establishment_id",[""])[0]
            try: establishment_id=int(raw) if raw else None
            except ValueError: return self.send_json({"error":"Établissement invalide"},400)
            return self.send_json(store.contacts(establishment_id,query.get("include_inactive",[""])[0]=="1"))
        if p.path=="/api/resumes":
            rows=store.rows("SELECT id,filename,extracted,verified_json,preferred,created_at FROM resumes ORDER BY id")
            for row in rows: row["verified"]=json.loads(row.pop("verified_json"))
            return self.send_json(rows)
        if p.path.startswith("/api/resumes/") and p.path.endswith("/download"):
            try:
                path,filename=resume_path(store,DATA/"documents",int(p.path.split("/")[3]))
                return self.send_file(path,mimetypes.guess_type(filename)[0] or "application/octet-stream",filename)
            except (ValueError,IndexError) as exc: return self.send_json({"error":str(exc)},404)
        if p.path=="/api/trash": return self.send_json(store.rows("""SELECT o.*,c.name company FROM offers o
            LEFT JOIN companies c ON c.id=o.company_id WHERE o.deleted_at IS NOT NULL ORDER BY o.deleted_at DESC"""))
        if p.path.startswith("/api/applications/") and p.path.endswith("/email"):
            try:
                content,filename=application_email(store,DATA/"documents",int(p.path.split("/")[3])); return self.send_download(content,"message/rfc822",filename)
            except (ValueError,IndexError) as exc: return self.send_json({"error":str(exc)},400)
        if p.path.startswith("/api/applications/"):
            try: return self.send_json(store.application_detail(int(p.path.split("/")[3])))
            except ValueError as e: return self.send_json({"error":str(e)},404)
        if p.path=="/api/applications": return self.send_json({"applications":store.applications(),"statuses":list(STATUSES)})
        if p.path=="/api/statistics":
            period=parse_qs(p.query).get("period",["month"])[0]
            if period not in {"week","month"}: return self.send_json({"error":"Période inconnue"},400)
            return self.send_json(store.statistics(period))
        if p.path=="/api/backups": return self.send_json(list_backups(DATA/"backups"))
        if p.path=="/api/backups/download":
            try: return self.send_file(backup_path(DATA/"backups",parse_qs(p.query).get("name",[""])[0]))
            except ValueError as e: return self.send_json({"error":str(e)},404)
        if p.path=="/api/exports/download":
            try:
                path=export_path(DATA/"exports",parse_qs(p.query).get("name",[""])[0])
                content_type="application/json; charset=utf-8" if path.suffix.lower()==".json" else "text/csv; charset=utf-8"
                return self.send_file(path,content_type)
            except ValueError as e: return self.send_json({"error":str(e)},404)
        if p.path=="/api/health":
            store.rows("SELECT 1"); return self.send_json({"status":"ok","application":APP_NAME,"version":APP_VERSION})
        if p.path=="/api/configuration/status":
            status=settings().status()
            if self.auth().enabled and not self.identity(): return self.send_json({"google_sso":True})
            return self.send_json(status)
        if p.path=="/api/diagnostics":
            integrity=store.rows("PRAGMA integrity_check")[0]["integrity_check"]
            foreign_keys=store.rows("PRAGMA foreign_key_check")
            external=settings().value("external_backup_directory")
            free_bytes=shutil.disk_usage(DATA).free
            healthy=integrity=="ok" and not foreign_keys
            return self.send_json({"status":"ok" if healthy and free_bytes>=100*1024*1024 else "warning" if healthy else "error","application_version":APP_VERSION,"python":sys.version.split()[0],"database":str(Path(store.path).resolve()),"data_directory":str(DATA.resolve()),"integrity":integrity,"foreign_key_errors":len(foreign_keys),"schema_version":store.schema_version(),"free_bytes":free_bytes,"low_disk_space":free_bytes<100*1024*1024,"backups":len(list_backups(DATA/"backups")),"writable":os.access(DATA,os.W_OK),"external_backup_directory":external})
        if p.path=="/api/diagnostics/report": return self.send_json_download(support_report(),"carnet-emploi-42-diagnostic.json")
        return self.serve_static(p.path)
    def serve_static(self,path):
        rel="index.html" if path=="/" else path.lstrip("/"); static_root=STATIC_ROOT.resolve(); target=(static_root/rel).resolve()
        try: target.relative_to(static_root)
        except ValueError: return self.send_error(404)
        if not target.is_file(): return self.send_error(404)
        body=target.read_bytes(); self.send_response(200); self.send_header("Content-Type",mimetypes.guess_type(target.name)[0] or "application/octet-stream"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if not self.validate_local_request("POST"): return None
        with REQUEST_LOCK:
            path=urlparse(self.path).path
            if not self.require_authentication(path,"POST"): return None
            return self.handle_POST()
    def handle_POST(self):
        try:
            d=self.body(); p=urlparse(self.path).path
            if p=="/api/profile": store.upsert_profile(d); return self.send_json({"ok":True,"scores_recalculated":store.recalculate_offer_scores()})
            if p=="/api/configuration":
                settings().update(d.get("values",{})); return self.send_json(settings().status())
            if p=="/api/configuration/clear":
                settings().clear(d.get("keys",[])); return self.send_json(settings().status())
            if p=="/api/offers": return self.send_json(store.create_offer(d),201)
            if p=="/api/offers/recalculate": return self.send_json({"count":store.recalculate_offer_scores()})
            if p.startswith("/api/offers/") and p.endswith("/update"):
                return self.send_json(store.update_offer(int(p.split("/")[3]),d))
            if p.startswith("/api/offers/") and p.endswith("/apply"):
                return self.send_json({"id":store.apply_to_offer(int(p.split("/")[3]))},201)
            if p=="/api/resumes":
                result=save_resume(store, DATA/"documents", d.get("filename",""), d.get("content","")); store.recalculate_offer_scores(); return self.send_json(result,201)
            if p.startswith("/api/resumes/") and p.endswith("/verify"):
                resume_id=int(p.split("/")[3]); verify_resume(store,resume_id,d.get("sections",{})); return self.send_json({"ok":True,"scores_recalculated":store.recalculate_offer_scores()})
            if p.startswith("/api/resumes/") and p.endswith("/preferred"):
                set_preferred_resume(store,int(p.split("/")[3])); return self.send_json({"ok":True,"scores_recalculated":store.recalculate_offer_scores()})
            if p.startswith("/api/resumes/") and p.endswith("/delete"):
                delete_resume(store,DATA/"documents",int(p.split("/")[3])); return self.send_json({"ok":True,"scores_recalculated":store.recalculate_offer_scores()})
            if p=="/api/maintenance": return self.send_json({"notifications_created":store.maintain()})
            if p.startswith("/api/applications/") and p.endswith("/draft"):
                store.update_application_draft(int(p.split("/")[3]),d); return self.send_json({"ok":True})
            if p.startswith("/api/applications/") and p.endswith("/sent"):
                store.mark_application_sent(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p.startswith("/api/applications/") and p.endswith("/delete-draft"):
                store.delete_application_draft(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p.startswith("/api/applications/"):
                store.update_application(int(p.split("/")[3]),d); return self.send_json({"ok":True})
            if p.startswith("/api/notifications/") and p.endswith("/read"):
                store.read_notification(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p.startswith("/api/notifications/") and p.endswith("/delete"):
                store.delete_notification(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p=="/api/backup":
                path=create_backup(store,DATA,DATA/"backups"); return self.send_json({"filename":path.name,"path":str(path)})
            if p=="/api/backup/external":
                directory=settings().value("external_backup_directory")
                if not directory: raise ValueError("Configurez CARNET_EMPLOI_BACKUP_DIR avant d'utiliser la sauvegarde externe")
                path=create_external_backup(store,DATA,Path(directory)); return self.send_json({"filename":path.name,"path":str(path)})
            if p=="/api/backup/restore":
                result=restore_backup(store,DATA,DATA/"backups",d.get("filename",""),d.get("content","")); store.__init__(store.path); return self.send_json(result)
            if p=="/api/backups/restore-local":
                path=backup_path(DATA/"backups",str(d.get("filename","")))
                result=restore_backup(store,DATA,DATA/"backups",path.name,base64.b64encode(path.read_bytes()).decode()); store.__init__(store.path); return self.send_json(result)
            if p=="/api/backups/delete":
                delete_backup(DATA/"backups",str(d.get("filename",""))); return self.send_json({"ok":True})
            if p=="/api/export/csv":
                path=export_applications_csv(store,DATA/"exports"/f"candidatures-{now()[:10]}.csv"); return self.send_json({"filename":path.name,"path":str(path)})
            if p=="/api/export/json":
                stamp=now().replace("-","").replace(":","").replace("+","-")
                path=export_data_json(store,DATA/"exports"/f"carnet-emploi-42-{stamp}.json"); return self.send_json({"filename":path.name})
            if p.startswith("/api/offers/") and p.endswith("/trash"):
                store.trash_offer(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p.startswith("/api/offers/") and p.endswith("/restore"):
                store.restore_offer(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p=="/api/companies/import":
                ids=[]
                for x in d.get("establishments",[]):
                    cid=store.execute("INSERT INTO companies(siren,name,workforce,source_url,checked_at) VALUES(?,?,?,?,?) ON CONFLICT(siren) DO UPDATE SET name=excluded.name,workforce=excluded.workforce,checked_at=excluded.checked_at",(x.get("siren"),x["company"],x.get("workforce",""),"https://annuaire-entreprises.data.gouv.fr/",now()))
                    if not cid: cid=store.rows("SELECT id FROM companies WHERE siren=?",(x.get("siren"),))[0]["id"]
                    ids.append(store.execute("INSERT INTO establishments(company_id,siret,name,address,postcode,city,workforce,active) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(siret) DO UPDATE SET name=excluded.name,address=excluded.address,active=excluded.active",(cid,x.get("siret"),x["name"],x.get("address",""),x.get("postcode",""),x.get("city",""),x.get("workforce",""),int(x.get("active",True)))))
                return self.send_json({"ok":True,"count":len(ids)})
            if p=="/api/establishments/manual": return self.send_json({"id":store.add_manual_establishment(d)},201)
            if p.startswith("/api/establishments/") and p.endswith("/update"):
                store.update_establishment(int(p.split("/")[3]),d); return self.send_json({"ok":True})
            if p.startswith("/api/establishments/") and p.endswith("/active"):
                store.set_establishment_active(int(p.split("/")[3]),bool(d.get("active"))); return self.send_json({"ok":True})
            if p.startswith("/api/establishments/") and p.endswith("/delete"):
                store.delete_establishment(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p=="/api/sirene/search": return self.send_json(SireneConnector(settings().value("insee_api_token")).search_loire(d.get("query",""),d.get("workforce",""),d.get("limit",50)))
            if p=="/api/france-travail/search":
                connector=FranceTravailConnector(settings().value("france_travail_client_id"),settings().value("france_travail_client_secret"))
                return self.send_json(connector.search_loire(d.get("keyword",""),d.get("city",""),d.get("limit",20)))
            if p=="/api/external-offers/import":
                return self.send_json(ExternalJobPageConnector().import_url(d.get("url","")))
            if p=="/api/external-offers/search":
                return self.send_json(ExternalJobPageConnector().search(d.get("keyword",""),d.get("city",""),d.get("providers",[]),d.get("limit",10)))
            if p=="/api/contacts":
                return self.send_json({"id":store.add_public_contact(d)},201)
            if p.startswith("/api/contacts/") and p.endswith("/update"):
                store.update_public_contact(int(p.split("/")[3]),d); return self.send_json({"ok":True})
            if p.startswith("/api/contacts/") and p.endswith("/active"):
                store.set_contact_active(int(p.split("/")[3]),bool(d.get("active"))); return self.send_json({"ok":True})
            if p.startswith("/api/contacts/") and p.endswith("/delete"):
                store.delete_contact(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p=="/api/spontaneous":
                position=d.get("position","").strip()
                if not position: raise ValueError("Un poste précis est obligatoire")
                establishment_ids=list(dict.fromkeys(d.get("establishment_ids",[])))
                if not establishment_ids: raise ValueError("Sélectionnez au moins un établissement")
                resume_id=d.get("resume_id") or None
                if resume_id is not None and not store.rows("SELECT id FROM resumes WHERE id=?",(resume_id,)):
                    raise ValueError("CV introuvable")
                profile=store.rows("SELECT * FROM profile WHERE id=1"); candidate=((profile[0].get("first_name","")+" "+profile[0].get("last_name","")).strip() if profile else "Candidat")
                targets=[]; warnings=[]
                for eid in establishment_ids:
                    est=store.rows("SELECT * FROM establishments WHERE id=?",(eid,))
                    if not est: raise ValueError("Un établissement sélectionné est introuvable")
                    contacts=store.rows("SELECT * FROM contacts WHERE establishment_id=? AND active=1 ORDER BY confidence DESC",(eid,)); chosen=next((x for x in contacts if x["id"]==d.get("contact_ids",{}).get(str(eid))),None)
                    email=chosen["email"] if chosen else ""; warnings += duplicate_candidates(store,eid,position,email)
                    targets.append((eid,est[0],chosen,email))
                if warnings and not d.get("confirm_duplicates"):
                    return self.send_json({"confirmation_required":True,"duplicate_warnings":warnings,"applications":[]})
                made=[]
                for eid,est,chosen,email in targets:
                    draft=build_email(position,candidate,est["name"],d.get("motivation","")); aid=store.execute("INSERT INTO applications(establishment_id,position,resume_id,letter,email_to,email_subject,email_body,checklist_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(eid,position,resume_id,d.get("letter",""),email,draft["subject"],draft["body"],json.dumps({"destinataire_verifie":bool(chosen),"champs_sensibles_vides":True,"validation_humaine":False}),now())); made.append(aid)
                return self.send_json({"applications":made,"duplicate_warnings":warnings},201)
            return self.send_json({"error":"Route API introuvable"},404)
        except (ValueError,RuntimeError) as e: return self.send_json({"error":str(e)},400)
        except Exception as exc:
            return self.send_unexpected_error(p if "p" in locals() else "/api/",exc)

def run_server(port=8765, open_browser=True):
    url=f"http://127.0.0.1:{port}"
    try: server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    except OSError as exc:
        raise RuntimeError(f"Impossible de démarrer sur le port {port}. Fermez l'autre fenêtre {APP_NAME} puis réessayez.") from exc
    print("="*58)
    print(f" {APP_NAME} {APP_VERSION} est démarré correctement")
    print(f" Ouvrez : {url}")
    if open_browser: print(f" Le navigateur s'ouvrira automatiquement dans {BROWSER_OPEN_DELAY} secondes.")
    print(" Les codes HTTP 200 signifient que tout fonctionne.")
    print(" Fermez cette fenêtre ou appuyez sur Ctrl+C pour arrêter.")
    print("="*58,flush=True)
    if open_browser: threading.Timer(BROWSER_OPEN_DELAY,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print(f"\n{APP_NAME} arrêté.")
    finally: server.server_close()

if __name__=="__main__":
    if "--version" in sys.argv:
        print(f"{APP_NAME} {APP_VERSION}")
        raise SystemExit(0)
    if "--disable-google-sso" in sys.argv:
        settings().clear(["google_client_id","google_client_secret","google_allowed_email"])
        print("Google SSO est désactivé. Relancez Carnet Emploi 42 normalement.")
        raise SystemExit(0)
    try:
        automatic=ensure_automatic_backup(store,DATA,DATA/"backups")
        if automatic: print(f"Sauvegarde automatique vérifiée : {automatic.name}")
    except (OSError,ValueError) as exc:
        print(f"AVERTISSEMENT : sauvegarde automatique impossible : {exc}")
    try: run_server()
    except RuntimeError as exc:
        print(f"\nERREUR : {exc}")
        raise SystemExit(1)
