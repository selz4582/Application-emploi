"""Authentification Google OpenID Connect pour le serveur local."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
SESSION_COOKIE = "carnet_emploi_session"
STATE_TTL_SECONDS = 600
SESSION_TTL_SECONDS = 30 * 24 * 3600
MAX_PENDING_STATES = 20


class GoogleAuth:
    def __init__(self, settings, data_dir: Path, base_url: str):
        self.settings, self.data_dir, self.base_url = settings, Path(data_dir), base_url.rstrip("/")
        self.pending = {}

    @property
    def enabled(self):
        return bool(self.settings.value("google_client_id") and self.settings.value("google_client_secret"))

    @property
    def redirect_uri(self): return self.base_url + "/auth/google/callback"

    def authorization_url(self):
        if not self.enabled: raise ValueError("Google SSO n'est pas encore configuré")
        state=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(64); nonce=secrets.token_urlsafe(32)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.pending[state]={"verifier":verifier,"nonce":nonce,"expires":time.time()+STATE_TTL_SECONDS}
        self._prune_states()
        query=urllib.parse.urlencode({"client_id":self.settings.value("google_client_id"),"redirect_uri":self.redirect_uri,"response_type":"code","scope":"openid email profile","state":state,"nonce":nonce,"code_challenge":challenge,"code_challenge_method":"S256","prompt":"select_account"})
        return AUTHORIZE_URL+"?"+query

    def complete(self, code: str, state: str):
        pending=self.pending.pop(state,None)
        if not pending or pending["expires"]<time.time(): raise ValueError("La tentative de connexion Google a expiré")
        payload=self._post_json(TOKEN_URL,{"code":code,"client_id":self.settings.value("google_client_id"),"client_secret":self.settings.value("google_client_secret"),"redirect_uri":self.redirect_uri,"grant_type":"authorization_code","code_verifier":pending["verifier"]})
        id_token=payload.get("id_token","")
        if not id_token: raise ValueError("Google n'a pas fourni de preuve d'identité")
        identity=self._get_json(TOKENINFO_URL+"?"+urllib.parse.urlencode({"id_token":id_token}))
        if identity.get("aud")!=self.settings.value("google_client_id") or identity.get("iss") not in {"accounts.google.com","https://accounts.google.com"} or not hmac.compare_digest(str(identity.get("nonce","")),pending["nonce"]): raise ValueError("La preuve d'identité Google est invalide")
        if str(identity.get("email_verified","")).lower()!="true": raise ValueError("L'adresse Google n'est pas vérifiée")
        email=str(identity.get("email","")).strip().lower()
        allowed=self.settings.value("google_allowed_email").strip().lower()
        if allowed and email!=allowed: raise ValueError("Ce compte Google n'est pas autorisé pour cette application")
        if not allowed: self.settings.update({"google_allowed_email":email})
        return {"email":email,"name":identity.get("name", ""),"picture":identity.get("picture", "")}

    def session_cookie(self, identity):
        payload={"email":identity["email"],"name":identity.get("name", ""),"picture":identity.get("picture", ""),"exp":int(time.time()+SESSION_TTL_SECONDS)}
        encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode()).rstrip(b"=").decode()
        signature=hmac.new(self._secret(),encoded.encode(),hashlib.sha256).hexdigest()
        return f"{SESSION_COOKIE}={encoded}.{signature}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax"

    def clear_cookie(self): return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

    def identity_from_headers(self, headers):
        if not self.enabled: return {"email":"local","name":"Utilisateur local","picture":""}
        cookie=SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel=cookie.get(SESSION_COOKIE)
        if not morsel or "." not in morsel.value: return None
        encoded,signature=morsel.value.rsplit(".",1)
        expected=hmac.new(self._secret(),encoded.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected): return None
        try:
            padded=encoded+"="*(-len(encoded)%4); payload=json.loads(base64.urlsafe_b64decode(padded))
        except (ValueError,json.JSONDecodeError): return None
        email=str(payload.get("email", "")).strip().lower()
        allowed=self.settings.value("google_allowed_email").strip().lower()
        if payload.get("exp",0)<time.time() or not email: return None
        # Une session créée avant un changement de compte autorisé doit être
        # révoquée immédiatement, même si sa signature et sa date sont valides.
        if allowed and email != allowed: return None
        payload["email"]=email
        return payload

    def _secret(self):
        path=self.data_dir/"auth-secret.key"; self.data_dir.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32)); path.chmod(0o600)
        return path.read_bytes()

    def _prune_states(self):
        valid=((key,value) for key,value in self.pending.items() if value["expires"]>=time.time())
        newest=sorted(valid,key=lambda item:item[1]["expires"],reverse=True)[:MAX_PENDING_STATES]
        self.pending=dict(newest)

    @staticmethod
    def _post_json(url,values):
        request=urllib.request.Request(url,data=urllib.parse.urlencode(values).encode(),headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json"})
        return GoogleAuth._open_json(request)

    @staticmethod
    def _get_json(url): return GoogleAuth._open_json(urllib.request.Request(url,headers={"Accept":"application/json"}))

    @staticmethod
    def _open_json(request):
        try:
            with urllib.request.urlopen(request,timeout=20) as response: return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError("Google a refusé la connexion. Vérifiez le client OAuth et l'URI de redirection") from exc
        except (urllib.error.URLError,TimeoutError) as exc:
            raise ValueError("Google est temporairement inaccessible") from exc
        except json.JSONDecodeError as exc: raise ValueError("La réponse de Google est illisible") from exc
