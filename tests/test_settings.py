import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from settings import SettingsStore


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"configuration.json"; self.settings=SettingsStore(self.path)
    def tearDown(self): self.temp.cleanup()

    def test_settings_are_saved_once_without_returning_secrets_in_status(self):
        self.settings.update({"france_travail_client_id":"client","france_travail_client_secret":"secret","insee_api_token":"token"})
        self.assertTrue(self.settings.status()["france_travail"]); self.assertTrue(self.settings.status()["insee"])
        self.assertNotIn("secret",json.dumps(self.settings.status())); self.assertEqual(self.settings.value("france_travail_client_secret"),"secret")
        if os.name != "nt": self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_blank_values_preserve_existing_configuration_and_clear_is_explicit(self):
        self.settings.update({"insee_api_token":"premier"}); self.settings.update({"insee_api_token":""})
        self.assertEqual(self.settings.value("insee_api_token"),"premier")
        self.settings.clear(["insee_api_token"]); self.assertEqual(self.settings.value("insee_api_token"),"")

    def test_environment_has_priority_over_local_file(self):
        self.settings.update({"insee_api_token":"local"})
        with patch.dict("os.environ",{"INSEE_API_TOKEN":"environment"}): self.assertEqual(self.settings.value("insee_api_token"),"environment")

    def test_unknown_or_oversized_setting_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"non autorisé"): self.settings.update({"unknown":"value"})
        with self.assertRaisesRegex(ValueError,"trop longue"): self.settings.update({"insee_api_token":"x"*2049})
