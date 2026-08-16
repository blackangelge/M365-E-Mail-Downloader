"""Procrastinate-App-Singleton: Postgres-natives Task-Queue-Backend (kein Redis/RabbitMQ nötig).

Job-Persistenz liegt vollständig in Postgres (`procrastinate_jobs` etc., verwaltet über
`procrastinate schema --apply`, siehe scripts/entrypoint.sh). Das liefert Restart-Resumability
"for free": startet der Container neu, übernimmt der neue Worker verwaiste `doing`-Jobs eines
toten Workers automatisch - siehe app/main.py für den Start/Stop des Workers im Lifespan.
"""
from __future__ import annotations

import re

from procrastinate import App, PsycopgConnector, RetryStrategy

from app.config import get_settings
from app.graph.errors import TransientGraphError


def _to_psycopg_conninfo(sqlalchemy_url: str) -> str:
    """Wandelt eine SQLAlchemy-URL (z.B. "postgresql+psycopg://user:pw@host/db") in eine
    psycopg-taugliche conninfo-URL ("postgresql://user:pw@host/db") um."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", sqlalchemy_url)


_settings = get_settings()

# Graph-Aufrufe: transiente Fehler (Netzwerk, 5xx, erschöpftes Throttling) werden bis zu 8x mit
# exponentiellem Backoff wiederholt. PermanentGraphError (siehe app/graph/errors.py) ist davon
# bewusst ausgenommen - solche Tasks schlagen sofort fehl und werden im UI sichtbar, statt
# sinnlos gegen ungültige Zugangsdaten zu retryen.
graph_retry_strategy = RetryStrategy(
    max_attempts=8,
    exponential_wait=2,
    retry_exceptions={TransientGraphError, ConnectionError, TimeoutError},
)

app = App(connector=PsycopgConnector(conninfo=_to_psycopg_conninfo(_settings.database_url)))
