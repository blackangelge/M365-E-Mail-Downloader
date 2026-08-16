"""Tenants / App-Registrierungen verwalten (Secret- oder Zertifikat-Auth)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import AuthType, Tenant
from app.security.crypto import encrypt_secret
from app.web.templating import templates

router = APIRouter(prefix="/tenants")


@router.get("")
async def list_tenants(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(select(Tenant).order_by(Tenant.name))
    return templates.TemplateResponse(
        request, "tenants/list.html", {"active_nav": "tenants", "tenants": result.scalars().all()}
    )


@router.get("/new")
async def new_tenant_form(request: Request):
    return templates.TemplateResponse(
        request, "tenants/form.html", {"active_nav": "tenants", "tenant": None}
    )


@router.post("")
async def create_tenant(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    azure_tenant_id: Annotated[str, Form()],
    azure_client_id: Annotated[str, Form()],
    auth_type: Annotated[str, Form()],
    client_secret: Annotated[str, Form()] = "",
    certificate_password: Annotated[str, Form()] = "",
    certificate_file: UploadFile | None = None,
):
    tenant = Tenant(
        name=name.strip(),
        azure_tenant_id=azure_tenant_id.strip(),
        azure_client_id=azure_client_id.strip(),
        auth_type=AuthType(auth_type),
    )
    await _apply_credentials(tenant, auth_type, client_secret, certificate_password, certificate_file)
    session.add(tenant)
    await session.commit()
    return RedirectResponse(f"/tenants?msg=Tenant+{name}+angelegt", status_code=303)


@router.get("/{tenant_id}/edit")
async def edit_tenant_form(request: Request, tenant_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    tenant = await session.get(Tenant, tenant_id)
    return templates.TemplateResponse(request, "tenants/form.html", {"active_nav": "tenants", "tenant": tenant})


@router.post("/{tenant_id}")
async def update_tenant(
    request: Request,
    tenant_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    azure_tenant_id: Annotated[str, Form()],
    azure_client_id: Annotated[str, Form()],
    auth_type: Annotated[str, Form()],
    is_active: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    certificate_password: Annotated[str, Form()] = "",
    certificate_file: UploadFile | None = None,
):
    tenant = await session.get(Tenant, tenant_id)
    tenant.name = name.strip()
    tenant.azure_tenant_id = azure_tenant_id.strip()
    tenant.azure_client_id = azure_client_id.strip()
    tenant.auth_type = AuthType(auth_type)
    tenant.is_active = bool(is_active)
    # Zugangsdaten nur ersetzen, wenn im Formular tatsächlich etwas Neues eingegeben wurde -
    # sonst bleiben die bestehenden verschlüsselten Werte unangetastet (maskierte Anzeige im UI).
    changed = await _apply_credentials(tenant, auth_type, client_secret, certificate_password, certificate_file, only_if_provided=True)
    if changed:
        tenant.credentials_version += 1
    await session.commit()
    return RedirectResponse(f"/tenants?msg=Tenant+{name}+aktualisiert", status_code=303)


@router.post("/{tenant_id}/delete")
async def delete_tenant(tenant_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    tenant = await session.get(Tenant, tenant_id)
    if tenant:
        await session.delete(tenant)
        await session.commit()
    return RedirectResponse("/tenants?msg=Tenant+gel%C3%B6scht", status_code=303)


async def _apply_credentials(
    tenant: Tenant,
    auth_type: str,
    client_secret: str,
    certificate_password: str,
    certificate_file: UploadFile | None,
    only_if_provided: bool = False,
) -> bool:
    """Setzt die verschlüsselten Zugangsdaten am Tenant. Gibt zurück, ob tatsächlich etwas
    geändert wurde (relevant für credentials_version-Bump beim Update).

    `.strip()` auf Secret/Passwort ist bewusst: Copy-Paste aus dem Azure-Portal (oder aus einem
    Passwort-Manager) hängt häufig einen führenden/nachgestellten Zeilenumbruch oder Leerzeichen
    an - das würde den Vergleich bei Azure sonst stillschweigend als "falsches Secret" scheitern
    lassen, ohne dass der eingefügte Wert sichtbar falsch aussieht.
    """
    client_secret = client_secret.strip()
    certificate_password = certificate_password.strip()

    changed = False
    if auth_type == AuthType.CLIENT_SECRET.value:
        if client_secret:
            tenant.client_secret_encrypted = encrypt_secret(client_secret)
            changed = True
        elif not only_if_provided:
            tenant.client_secret_encrypted = None
    elif auth_type == AuthType.CERTIFICATE.value:
        if certificate_file is not None and certificate_file.filename:
            content = await certificate_file.read()
            if content:
                tenant.certificate_pem_encrypted = encrypt_secret(content)
                changed = True
        if certificate_password:
            tenant.certificate_password_encrypted = encrypt_secret(certificate_password)
            changed = True
    return changed
