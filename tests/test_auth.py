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
        self.assertEqual(query["nonce"],[self.auth.pending[query["state"][0]]["nonce"]])

    def test_callback_validates_google_identity_and_creates_persistent_cookie(self):
        url=self.auth.authorization_url(); state=parse_qs(urlparse(url).query)["state"][0]
        nonce=self.auth.pending[state]["nonce"]
        with patch.object(self.auth,"_post_json",return_value={"id_token":"proof"}),patch.object(self.auth,"_get_json",return_value={"aud":"client.apps.googleusercontent.com","iss":"https://accounts.google.com","nonce":nonce,"email_verified":"true","email":"anne@example.fr","name":"Anne"}):
            identity=self.auth.complete("code",state)
        cookie=self.auth.session_cookie(identity); headers={"Cookie":cookie.split(";",1)[0]}
        self.assertEqual(self.auth.identity_from_headers(headers)["email"],"anne@example.fr"); self.assertTrue((self.data/"auth-secret.key").is_file())
        headers["Cookie"]=headers["Cookie"]+"tampered"; self.assertIsNone(self.auth.identity_from_headers(headers))

    def test_callback_rejects_expired_state_and_wrong_audience(self):
        self.auth.pending["expired"]={"verifier":"v","expires":time.time()-1}
        with self.assertRaisesRegex(ValueError,"expiré"): self.auth.complete("code","expired")
        url=self.auth.authorization_url(); state=parse_qs(urlparse(url).query)["state"][0]
        with patch.object(self.auth,"_post_json",return_value={"id_token":"proof"}),patch.object(self.auth,"_get_json",return_value={"aud":"other","iss":"https://accounts.google.com","nonce":self.auth.pending[state]["nonce"],"email_verified":"true"}):
            with self.assertRaisesRegex(ValueError,"invalide"): self.auth.complete("code",state)

    def test_callback_rejects_an_id_token_with_the_wrong_nonce(self):
        state=parse_qs(urlparse(self.auth.authorization_url()).query)["state"][0]
        identity={"aud":"client.apps.googleusercontent.com","iss":"https://accounts.google.com","nonce":"autre-tentative","email_verified":"true","email":"anne@example.fr"}
        with patch.object(self.auth,"_post_json",return_value={"id_token":"proof"}),patch.object(self.auth,"_get_json",return_value=identity):
            with self.assertRaisesRegex(ValueError,"invalide"): self.auth.complete("code",state)

    def test_first_google_account_is_bound_and_another_account_is_rejected(self):
        first_url=self.auth.authorization_url(); first_state=parse_qs(urlparse(first_url).query)["state"][0]
        with patch.object(self.auth,"_post_json",return_value={"id_token":"one"}),patch.object(self.auth,"_get_json",return_value={"aud":"client.apps.googleusercontent.com","iss":"accounts.google.com","nonce":self.auth.pending[first_state]["nonce"],"email_verified":True,"email":"Anne@Example.fr"}): self.auth.complete("code",first_state)
        self.assertEqual(self.settings.value("google_allowed_email"),"anne@example.fr")
        second_url=self.auth.authorization_url(); second_state=parse_qs(urlparse(second_url).query)["state"][0]
        with patch.object(self.auth,"_post_json",return_value={"id_token":"two"}),patch.object(self.auth,"_get_json",return_value={"aud":"client.apps.googleusercontent.com","iss":"accounts.google.com","nonce":self.auth.pending[second_state]["nonce"],"email_verified":True,"email":"other@example.fr"}):
            with self.assertRaisesRegex(ValueError,"n'est pas autorisé"): self.auth.complete("code",second_state)

    def test_session_is_revoked_when_allowed_account_changes(self):
        identity={"email":"anne@example.fr","name":"Anne"}
        cookie=self.auth.session_cookie(identity).split(";",1)[0]
        self.assertEqual(self.auth.identity_from_headers({"Cookie":cookie})["email"],"anne@example.fr")
        self.settings.update({"google_allowed_email":"other@example.fr"})
        self.assertIsNone(self.auth.identity_from_headers({"Cookie":cookie}))

    def test_malformed_cookie_is_rejected_and_pending_states_are_bounded(self):
        self.assertIsNone(self.auth.identity_from_headers({"Cookie":"carnet_emploi_session=\"unterminated"}))
        for _ in range(30):
            self.auth.authorization_url()
        self.assertLessEqual(len(self.auth.pending),20)
