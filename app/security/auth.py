"""Optionales HTTP-Basic-Gate für die gesamte Weboberfläche.

Aktiv, sobald ADMIN_PASSWORD gesetzt ist (siehe Settings.auth_enabled). Da die UI
Tenant-Client-Secrets und Zertifikate verwaltet, ist dies als sinnvoller Default-Schutz
gedacht, auch wenn der Nutzer es nicht explizit gefordert hat.
"""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

_security = HTTPBasic(auto_error=False)


async def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)] = None,
) -> None:
    settings = get_settings()
    if not settings.auth_enabled:
        return  # kein Passwort konfiguriert -> Gate deaktiviert

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Anmeldung erforderlich",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        raise unauthorized

    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise unauthorized
