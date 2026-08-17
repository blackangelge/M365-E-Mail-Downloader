"""Gibt 'yes' aus, wenn das Procrastinate-Schema bereits existiert (Sentinel-Tabelle
'procrastinate_jobs'), sonst 'no'. Siehe entrypoint.sh: 'procrastinate schema --apply' ist laut
CLI-Hilfe nur für eine leere Datenbank gedacht und darf bei einem Neustart nicht erneut laufen."""
from __future__ import annotations

import psycopg

from app.config import get_settings

url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")

with psycopg.connect(url) as conn, conn.cursor() as cur:
    cur.execute("SELECT to_regclass('public.procrastinate_jobs')")
    print("yes" if cur.fetchone()[0] else "no")
