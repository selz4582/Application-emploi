"""Configuration locale des services facultatifs, sans jamais renvoyer les secrets au client."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ALLOWED_SETTINGS = {
    "france_travail_client_id": "FRANCE_TRAVAIL_CLIENT_ID",
    "france_travail_client_secret": "FRANCE_TRAVAIL_CLIENT_SECRET",
    "insee_api_token": "INSEE_API_TOKEN",
    "external_backup_directory": "CARNET_EMPLOI_BACKUP_DIR",
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
}
MAX_SETTING_LENGTH = 2048


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Le fichier de configuration locale est illisible") from exc
        if not isinstance(payload, dict):
            raise ValueError("Le fichier de configuration locale est invalide")
        return {key: str(value) for key, value in payload.items() if key in ALLOWED_SETTINGS and isinstance(value, str)}

    def update(self, values: dict) -> None:
        if not isinstance(values, dict) or set(values) - set(ALLOWED_SETTINGS):
            raise ValueError("Paramètre de configuration non autorisé")
        current = self.load()
        for key, raw in values.items():
            value = str(raw).strip()
            if len(value) > MAX_SETTING_LENGTH:
                raise ValueError(f"La valeur {key} est trop longue")
            if value:
                current[key] = value
        self._write(current)

    def clear(self, keys: list[str]) -> None:
        if not isinstance(keys, list) or any(key not in ALLOWED_SETTINGS for key in keys):
            raise ValueError("Paramètre de configuration non autorisé")
        current = self.load()
        for key in keys:
            current.pop(key, None)
        self._write(current)

    def value(self, key: str) -> str:
        if key not in ALLOWED_SETTINGS:
            raise ValueError("Paramètre de configuration non autorisé")
        environment = os.getenv(ALLOWED_SETTINGS[key], "").strip()
        return environment or self.load().get(key, "").strip()

    def status(self) -> dict:
        external = self.value("external_backup_directory")
        return {
            "france_travail": bool(self.value("france_travail_client_id") and self.value("france_travail_client_secret")),
            "insee": bool(self.value("insee_api_token")),
            "external_backup": bool(external),
            "external_backup_directory": external,
            "google_sso": bool(self.value("google_client_id") and self.value("google_client_secret")),
        }

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".configuration-", suffix=".json", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(values, stream, ensure_ascii=False, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
