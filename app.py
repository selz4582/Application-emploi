"""Serveur HTTP local sans dépendance tierce."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import base64, json, mimetypes, os, threading, webbrowser
import sys
from urllib.parse import urlparse, parse_qs
from core import STATUSES, Store, build_email, duplicate_candidates, now
from connectors import SireneConnector
from documents import backup_path, create_backup, delete_backup, delete_resume, export_applications_csv, list_backups, restore_backup, save_resume, set_preferred_resume, verify_resume

ROOT=Path(__file__).parent; DATA=ROOT/"data"; store=Store(DATA/"emploi.sqlite3")

class Handler(SimpleHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        """N'affiche que les vraies erreurs HTTP, pas les réponses 200 normales."""
        try: status = int(code)
        except (TypeError, ValueError): status = 0
        if status >= 400: super().log_request(code, size)

    def send_json(self,obj,status=200):
        body=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def send_file(self,path: Path):
        body=path.read_bytes(); self.send_response(200); self.send_header("Content-Type","application/zip"); self.send_header("Content-Disposition",f'attachment; filename="{path.name}"'); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def body(self):
        length=int(self.headers.get("Content-Length","0"))
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
        p=urlparse(self.path)
        if p.path=="/api/dashboard": store.maintain(); return self.send_json(store.dashboard())
        if p.path=="/api/offers": return self.send_json(store.dashboard()["offers"])
        if p.path=="/api/profile":
            rows=store.rows("SELECT * FROM profile WHERE id=1"); return self.send_json(rows[0] if rows else {})
        if p.path=="/api/companies":
            q=parse_qs(p.query); size=q.get("workforce",[""])[0]
            sql="SELECT c.*,count(e.id) establishments FROM companies c LEFT JOIN establishments e ON e.company_id=c.id AND e.active=1 WHERE c.active=1"; args=[]
            if size: sql+=" AND (c.workforce=? OR e.workforce=?)"; args=[size,size]
            return self.send_json(store.rows(sql+" GROUP BY c.id ORDER BY c.name",args))
        if p.path=="/api/establishments":
            q=parse_qs(p.query); cid=q.get("company_id",[""])[0]
            return self.send_json(store.rows("SELECT * FROM establishments WHERE active=1 AND postcode LIKE '42%'"+(" AND company_id=?" if cid else "")+" ORDER BY city,name",(cid,) if cid else ()))
        if p.path=="/api/contacts":
            query=parse_qs(p.query); raw=query.get("establishment_id",[""])[0]
            try: establishment_id=int(raw) if raw else None
            except ValueError: return self.send_json({"error":"Établissement invalide"},400)
            return self.send_json(store.contacts(establishment_id,query.get("include_inactive",[""])[0]=="1"))
        if p.path=="/api/resumes":
            rows=store.rows("SELECT id,filename,extracted,verified_json,preferred,created_at FROM resumes ORDER BY id")
            for row in rows: row["verified"]=json.loads(row.pop("verified_json"))
            return self.send_json(rows)
        if p.path=="/api/trash": return self.send_json(store.rows("""SELECT o.*,c.name company FROM offers o
            LEFT JOIN companies c ON c.id=o.company_id WHERE o.deleted_at IS NOT NULL ORDER BY o.deleted_at DESC"""))
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
        if p.path=="/api/diagnostics":
            integrity=store.rows("PRAGMA integrity_check")[0]["integrity_check"]
            return self.send_json({"python":sys.version.split()[0],"database":str(Path(store.path).resolve()),"data_directory":str(DATA.resolve()),"integrity":integrity,"backups":len(list_backups(DATA/"backups")),"writable":os.access(DATA,os.W_OK)})
        return self.serve_static(p.path)
    def serve_static(self,path):
        rel="index.html" if path=="/" else path.lstrip("/"); static_root=(ROOT/"static").resolve(); target=(static_root/rel).resolve()
        try: target.relative_to(static_root)
        except ValueError: return self.send_error(404)
        if not target.is_file(): return self.send_error(404)
        body=target.read_bytes(); self.send_response(200); self.send_header("Content-Type",mimetypes.guess_type(target.name)[0] or "application/octet-stream"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        try:
            d=self.body(); p=urlparse(self.path).path
            if p=="/api/profile": store.upsert_profile(d); return self.send_json({"ok":True,"scores_recalculated":store.recalculate_offer_scores()})
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
            if p.startswith("/api/applications/"):
                store.update_application(int(p.split("/")[3]),d); return self.send_json({"ok":True})
            if p.startswith("/api/notifications/") and p.endswith("/read"):
                store.read_notification(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p.startswith("/api/notifications/") and p.endswith("/delete"):
                store.delete_notification(int(p.split("/")[3])); return self.send_json({"ok":True})
            if p=="/api/backup":
                path=create_backup(store,DATA,DATA/"backups"); return self.send_json({"filename":path.name,"path":str(path)})
            if p=="/api/backup/restore":
                result=restore_backup(store,DATA,DATA/"backups",d.get("filename",""),d.get("content","")); store.__init__(store.path); return self.send_json(result)
            if p=="/api/backups/restore-local":
                path=backup_path(DATA/"backups",str(d.get("filename","")))
                result=restore_backup(store,DATA,DATA/"backups",path.name,base64.b64encode(path.read_bytes()).decode()); store.__init__(store.path); return self.send_json(result)
            if p=="/api/backups/delete":
                delete_backup(DATA/"backups",str(d.get("filename",""))); return self.send_json({"ok":True})
            if p=="/api/export/csv":
                path=export_applications_csv(store,DATA/"exports"/f"candidatures-{now()[:10]}.csv"); return self.send_json({"filename":path.name,"path":str(path)})
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
            if p=="/api/sirene/search": return self.send_json(SireneConnector(os.getenv("INSEE_API_TOKEN","")).search_loire(d.get("query",""),d.get("workforce",""),d.get("limit",50)))
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
                profile=store.rows("SELECT * FROM profile WHERE id=1"); candidate=((profile[0].get("first_name","")+" "+profile[0].get("last_name","")).strip() if profile else "Candidat")
                made=[]; warnings=[]
                for eid in d.get("establishment_ids",[]):
                    est=store.rows("SELECT * FROM establishments WHERE id=?",(eid,))
                    if not est: continue
                    contacts=store.rows("SELECT * FROM contacts WHERE establishment_id=? ORDER BY confidence DESC",(eid,)); chosen=next((x for x in contacts if x["id"]==d.get("contact_ids",{}).get(str(eid))),None)
                    email=chosen["email"] if chosen else ""; warnings += duplicate_candidates(store,eid,position,email)
                    draft=build_email(position,candidate,est[0]["name"],d.get("motivation","")); aid=store.execute("INSERT INTO applications(establishment_id,position,resume_id,letter,email_to,email_subject,email_body,checklist_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(eid,position,d.get("resume_id"),d.get("letter",""),email,draft["subject"],draft["body"],json.dumps({"destinataire_verifie":bool(chosen),"champs_sensibles_vides":True,"validation_humaine":False}),now())); made.append(aid)
                return self.send_json({"applications":made,"duplicate_warnings":warnings},201)
            raise ValueError("Route inconnue")
        except (ValueError,RuntimeError) as e: return self.send_json({"error":str(e)},400)
        except Exception as e: return self.send_json({"error":"Erreur locale : "+str(e)},500)

def run_server(port=8765, open_browser=True):
    url=f"http://127.0.0.1:{port}"
    try: server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    except OSError as exc:
        raise RuntimeError(f"Impossible de démarrer sur le port {port}. Fermez l'autre fenêtre Cap Emploi puis réessayez.") from exc
    print("="*58)
    print(" Cap Emploi 42 est démarré correctement")
    print(f" Ouvrez : {url}")
    print(" Les codes HTTP 200 signifient que tout fonctionne.")
    print(" Fermez cette fenêtre ou appuyez sur Ctrl+C pour arrêter.")
    print("="*58,flush=True)
    if open_browser: threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nCap Emploi 42 arrêté.")
    finally: server.server_close()

if __name__=="__main__":
    try: run_server()
    except RuntimeError as exc:
        print(f"\nERREUR : {exc}")
        raise SystemExit(1)
