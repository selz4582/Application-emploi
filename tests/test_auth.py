import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from auth import GoogleAuth
from settings import SettingsStore


class GoogleAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.data=Path(self.temp.name); self.settings=SettingsStore(self.data/"configuration.json")
        self.settings.update({"google_client_id":"client.apps.googleusercontent.com","google_client_secret":"secret"})
        self.auth=GoogleAuth(self.settings,self.data,"http://127.0.0.1:8765")
    def tearDown(self): self.temp.cleanup()

    def test_authorization_url_uses_state_pkce_and_exact_local_callback(self):
        url=self.auth.authorization_url(); query=parse_qs(urlparse(url).query)
        self.assertEqual(query["client_id"],["client.apps.googleusercontent.com"]); self.assertEqual(query["redirect_uri"],["http://127.0.0.1:8765/auth/google/callback"])
        self.assertEqual(query["scope"],["openid email profile"]); self.assertEqual(query["code_challenge_method"],["S256"]); self.assertIn(query["state"][0],self.auth.pending)

    def test_callback_validates_google_identity_and_creates_persistent_cookie(self):
        url=self.auth.authorization_url(); state=parse_qs(urlparse(url).query)["state"][0]
        with patch.object(self.auth,"_post_json",return_value={"id_token":"proof"}),patch.object(self.auth,"_get_json",return_value={"aud":"client.apps.googleusercontent.com","iss":"https://accounts.google.com","email_verified":"true","email":"anne@example.fr","name":"Anne"}):
            identity=self.auth.complete("code",state)
        cookie=self.auth.session_cookie(identity); headers={"Cookie":cookie.split(";",1)[0]}
        self.assertEqual(self.auth.identity_from_headers(headers)["email"],"anne@example.fr"); self.assertTrue((self.data/"auth-secret.key").is_file())
        headers["Cookie"]=headers["Cookie"]+"tampered"; self.assertIsNone(self.auth.identity_from_headers(headers))

    def test_callback_rejects_expired_state_and_wrong_audience(self):
        self.auth.pending["expired"]={"verifier":"v","expires":time.time()-1}
        with self.assertRaisesRegex(ValueError,"expiré"): self.auth.complete("code","expired")
        url=self.auth.authorization_url(); state=parse_qs(urlparse(url).query)["state"][0]
        with patch.object(self.auth,"_post_json",return_value={"id_token":"proof"}),patch.object(self.auth,"_get_json",return_value={"aud":"other","iss":"https://accounts.google.com","email_verified":"true"}):
            with self.assertRaisesRegex(ValueError,"invalide"): self.auth.complete("code",state)
