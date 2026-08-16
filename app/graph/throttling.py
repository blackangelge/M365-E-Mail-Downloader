"""Pro-Tenant Concurrency-Begrenzung für Graph-API-Aufrufe.

Ergänzt das eingebaute Retry/Throttling-Middleware der msgraph-sdk (das bereits `Retry-After`
auf 429-Antworten honoriert): verhindert, dass viele parallel laufende Job-/Mailbox-Tasks
gegen denselben Tenant gemeinsam erst ein Throttling auslösen.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

_MAX_CONCURRENT_REQUESTS_PER_TENANT = 4

_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS_PER_TENANT)
)


def semaphore_for_tenant(tenant_id: str) -> asyncio.Semaphore:
    return _semaphores[tenant_id]
