"""Zentrale, typisierte Konfiguration der Anwendung (aus Umgebungsvariablen / .env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://pdfdl:pdfdl@localhost:5432/pdfdl"

    master_encryption_key: str = ""

    download_root: Path = Path("/data/Download")

    app_timezone: str = "Europe/Berlin"

    port: int = 4000
    host: str = "0.0.0.0"
    reload: bool = True

    admin_username: str = "admin"
    admin_password: str = ""

    log_level: str = "INFO"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
