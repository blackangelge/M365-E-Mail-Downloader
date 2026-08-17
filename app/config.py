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

    # Nur zur Anzeige in der Weboberfläche: der tatsächliche Host-Pfad, auf den DOWNLOAD_ROOT
    # gemountet ist (siehe docker-compose.yml). Der Container selbst arbeitet ausschließlich mit
    # download_root - dieser Wert dient nur dazu, dem Nutzer den echten Speicherort anzuzeigen,
    # statt immer nur den intern gleichbleibenden Container-Pfad.
    download_host_dir: str = "./Download"

    app_timezone: str = "Europe/Berlin"

    port: int = 4000
    host: str = "0.0.0.0"
    reload: bool = True

    admin_username: str = "admin"
    admin_password: str = ""

    log_level: str = "INFO"

    # Anzahl gleichzeitig laufender Procrastinate-Tasks. Procrastinate-Default ist 1 (streng
    # sequenziell) - bei großen Postfächern mit tausenden zu prüfenden Nachrichten (jede ein
    # eigener evaluate_job_for_message-Task mit einem Graph-API-Aufruf) führt das zu absurd langen
    # Laufzeiten, obwohl die Tasks fast nur auf Netzwerk-I/O warten und sich hervorragend
    # parallelisieren lassen. Die Per-Tenant-Semaphore (app/graph/throttling.py) begrenzt dabei
    # weiterhin, wie viele Graph-Aufrufe gleichzeitig gegen DENSELBEN Tenant laufen.
    worker_concurrency: int = 10

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
