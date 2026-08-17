# PDF Download M365

Lädt E-Mail-Anhänge (PDF und weitere konfigurierbare Dateiendungen) aus bestimmten Postfächern
mehrerer Microsoft-365-Tenants automatisch herunter, dedupliziert sie, protokolliert alles und
stellt Statistiken sowie eine Kalenderübersicht über eine Weboberfläche bereit.

## Architektur (Kurzüberblick)

- **FastAPI + Jinja2/HTMX** – Weboberfläche auf Port 4000
- **SQLAlchemy (async) + Alembic** – App-Datenbank (Tenants, Postfächer, Jobs, Filter, Logs)
- **Procrastinate** – Postgres-native Task-Queue für die Hintergrund-Worker (kein Redis nötig)
- **msgraph-sdk + azure-identity** – Zugriff auf Microsoft Graph (Client-Secret oder Zertifikat pro Tenant)
- **Postgres** – einzige Infrastruktur-Abhängigkeit neben der App selbst

Details und Design-Entscheidungen: siehe `PLAN.md`-artige Beschreibung im ursprünglichen
Planungsdokument dieses Projekts (Task-Kette, Dedup-Strategie, Datenmodell).

## Voraussetzungen

- Docker + Docker Compose
- Für jeden M365-Tenant: eine App-Registrierung in Entra ID (siehe unten)

## Setup

1. `.env` aus der Vorlage erstellen:

```bash
cp .env.example .env
```

2. Verschlüsselungs-Schlüssel erzeugen und in `.env` als `MASTER_ENCRYPTION_KEY` eintragen:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

3. Postgres-Zugangsdaten in `.env` nach Bedarf anpassen (`POSTGRES_USER`, `POSTGRES_PASSWORD`,
   `POSTGRES_DB`, `DATABASE_URL`). Optional: `ADMIN_PASSWORD` setzen, um die Weboberfläche mit
   HTTP-Basic-Auth zu schützen (empfohlen, da dort Tenant-Zugangsdaten verwaltet werden).

4. Benötigte Ordner anlegen (sind bewusst in `.gitignore`, existieren bei einem frischen Checkout
   also noch nicht - manche Docker-Setups legen fehlende Bind-Mount-Ordner nicht zuverlässig
   selbst an):

```bash
mkdir -p data/postgres Download
```

5. Container starten:

```bash
docker compose up -d --build
```

Die Weboberfläche ist danach unter **http://localhost:4000** erreichbar. Postgres-Daten liegen
in `./data/postgres/`, heruntergeladene Anhänge standardmäßig in `./Download/` – beide werden als
Bind-Mounts direkt im Projektordner abgelegt (nicht in einem benannten Docker-Volume).

**Download-Ordner an einen anderen Ort legen:** `DOWNLOAD_HOST_DIR` in `.env` auf den gewünschten
Host-Pfad setzen (z. B. `DOWNLOAD_HOST_DIR=D:/Freigaben/PDF-Ablage`), Zielordner vorher anlegen,
danach `docker compose up -d` (siehe Hinweis unten zu `.env`-Änderungen). `DOWNLOAD_ROOT` selbst
(der Pfad *innerhalb* des Containers) muss dafür nicht angefasst werden.

**Auf nativem Linux-Docker** (anders als Docker Desktop unter Windows/Mac, das das meist
transparent überbrückt) läuft die App bewusst nicht als root - daher muss die UID/GID des
Container-Prozesses zum Besitzer der gemounteten Host-Ordner passen, sonst gibt es
"Permission denied". Das passiert **automatisch**: `scripts/entrypoint.sh` liest beim Start per
`stat` den tatsächlichen Besitzer von `DOWNLOAD_HOST_DIR` auf dem Host aus und übernimmt dessen
UID/GID - kein manuelles Nachschlagen mit `id -u`/`id -g` nötig. Gehört der Ordner ausnahmsweise
`root` (z.B. weil er gerade erst neu angelegt wurde), weicht der Container auf UID/GID `1000` aus
und übereignet sich nur die oberste Ordnerebene selbst (nicht rekursiv).

Nur für Sonderfälle (z.B. Netzlaufwerk, gewünschter abweichender Besitzer) lässt sich das in
`.env` manuell überschreiben - gesetzte Werte haben dann Vorrang vor der Auto-Erkennung:

```bash
# In .env:
PUID=1000
PGID=1000
```

Der Container prüft die Schreibrechte beim Start zusätzlich selbst und bricht mit einer klaren
Fehlermeldung ab, falls trotzdem etwas nicht passt - sichtbar sowohl in `docker compose logs app`
als auch auf der `/system`-Seite in der Weboberfläche.

Bei jedem Start führt `scripts/entrypoint.sh` automatisch `alembic upgrade head` aus und wendet
das Procrastinate-Schema nur beim allerersten Start an (die dazugehörige Sentinel-Tabelle wird
geprüft, da `procrastinate schema --apply` nur für eine leere Datenbank gedacht ist). Unterbrochene
Hintergrund-Jobs werden dank der Job-Persistenz von Procrastinate in Postgres nach einem Neustart
automatisch fortgesetzt.

**Wichtig bei Änderungen an `.env`:** `docker compose restart` liest die Datei NICHT erneut ein
(Umgebungsvariablen werden nur beim Erstellen eines Containers übernommen). Nach jeder `.env`-
Änderung stattdessen:

```bash
docker compose up -d
```

Das erstellt nur den `app`-Container neu (erkennbar an der geänderten Konfiguration), Postgres
und seine Daten bleiben unangetastet.

## Azure-Vorbereitung pro Tenant (einmalig, manuell)

Diese Schritte kann die Anwendung nicht automatisieren:

1. In Entra ID (Azure AD) des Zielmandanten eine **App-Registrierung** anlegen.
2. **API-Berechtigung** `Mail.Read` vom Typ **Application** (nicht "Delegated") hinzufügen und
   **Admin-Consent** erteilen.
3. Zugangsdaten anlegen: entweder ein **Client-Secret** oder ein **Zertifikat** (öffentlicher
   Schlüssel bei der App-Registrierung hochladen, privater Schlüssel + Zertifikat als PEM für
   diese Anwendung bereithalten).
4. **Empfohlen:** Da `Mail.Read` (Application) standardmäßig Zugriff auf *alle* Postfächer des
   Tenants gewährt, zusätzlich per Exchange-Online-PowerShell eine
   **Application Access Policy** einrichten, die den Zugriff der App auf die tatsächlich
   konfigurierten Postfächer beschränkt:

```powershell
New-ApplicationAccessPolicy -AppId <Client-ID> -PolicyScopeGroupId <Verteilerliste-mit-Postfaechern> -AccessRight RestrictAccess -Description "PDF Download M365"
```

## Erste Schritte in der Weboberfläche

1. **Tenants** → neuen Tenant mit Azure Tenant ID, Client ID und Client-Secret oder Zertifikat anlegen.
2. **Postfächer** → Postfach-Adresse(n) für diesen Tenant hinzufügen, per "Verbindung testen" prüfen.
   Jedes Postfach wird **inklusive aller Unterordner des Posteingangs** (beliebig verschachtelt,
   z. B. durch Regeln oder manuelle Sortierung entstanden) durchsucht, nicht nur der Posteingang
   selbst - die Ordnerstruktur wird bei jedem Sync-Lauf automatisch neu ermittelt.
3. **Filter** → einen wiederverwendbaren Filter anlegen: Datumsbereich (optional), Dateiendungen
   (z. B. `.pdf`), Ausschluss-Keywords (z. B. `Avis, Zahlungserinnerung` - kommagetrennt oder je
   eine Zeile, beliebig lang). Filter werden hier zentral gepflegt und können von mehreren Jobs
   gemeinsam genutzt werden - **die Keywords sind ein Ausschluss-Filter**: Anhänge, deren Betreff
   oder Dateiname eines der Wörter enthält, werden NICHT heruntergeladen; alle anderen (mit
   passender Endung) schon. Wird ein Filter nachträglich geändert, werden zuvor deswegen
   ausgeschlossene Nachrichten automatisch erneut geprüft (kein manuelles Neu-Anstoßen nötig).
4. **Jobs** → Job anlegen: Postfächer auswählen, einen bestehenden Filter zuweisen, Zielordner
   unter `Download/` und Prüfintervall festlegen.
5. Mit **„Jetzt ausführen“** einen Job sofort testen, statt auf das nächste Intervall zu warten.
6. **Dashboard** zeigt Kennzahlen (heute/gestern/Woche), **Kalender** zeigt Downloads, Duplikate
   UND per Filter ausgeschlossene Anhänge pro Tag (jeweils mit Grund, z. B. welches Keyword
   getroffen hat), **Logs** erlaubt die durchsuchbare Volltext-Historie aller Downloads/Duplikate.
   Auf der **Postfächer**-Seite ist während eines laufenden Syncs live sichtbar, wie viele Ordner
   und Nachrichten bereits verarbeitet wurden, sowie der Zeitpunkt des nächsten geplanten Syncs.

Heruntergeladene Dateien liegen unter `Download/<Zielordner>/<Jahr>_<Monat>_<Tag>_<Dateiname>.<ext>`
und werden nach dem Schreiben read-only markiert – die Anwendung fasst sie danach nicht mehr an;
eine externe Software holt sie von dort ab.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Die Unit-Tests decken die reine Filter-, Dedup- und Storage-Logik ab (`tests/unit/`), ohne eine
laufende Datenbank oder Graph-Zugriff zu benötigen.

## Hinweise

- Die genauen Kiota-Request-Builder-Pfade der `msgraph-sdk` (z. B.
  `messages.delta`, `attachments.by_attachment_id`) können sich zwischen SDK-Versionen leicht
  ändern – bei einem `pip install`-Update lohnt ein Blick in die SDK-Release-Notes, falls die
  Graph-Aufrufe in `app/graph/` fehlschlagen.
- `RELOAD=false` in `.env` setzen, um den Auto-Reload bei Code-Änderungen im Produktivbetrieb zu
  deaktivieren.
