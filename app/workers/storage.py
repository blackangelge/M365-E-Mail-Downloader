"""Ablage der heruntergeladenen Anhänge unter DOWNLOAD_ROOT.

Schema: Download/<job_folder>/<Jahr>_<Monat>_<Tag>_<Dateiname>
  - <job_folder> ist pro Job frei definierbar (Job.target_subfolder)
  - Datum = Empfangsdatum der E-Mail
  - Bei Namenskollision (derselbe Zielname existiert schon - Inhaltsgleichheit wurde bereits
    vorher per Dedup abgefangen, siehe app/workers/dedup.py) wird vor der Endung ein
    Zähler-Suffix "_1", "_2", ... angehängt, bis der Name frei ist.

Schreibsemantik: einmal geschrieben, wird eine Datei von dieser App NIE wieder geöffnet, verändert
oder gelöscht - eine externe Software holt sie sich aus dem Ordner ab. `write_once()` schreibt
deshalb atomar (Temp-Datei + os.replace) und setzt die Datei anschließend read-only.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ \-]+")


def sanitize_path_segment(value: str) -> str:
    """Entfernt Zeichen, die in Ordner-/Dateinamen problematisch sind, sowie Path-Traversal."""
    value = value.strip().replace("/", "_").replace("\\", "_")
    value = _UNSAFE_CHARS.sub("_", value)
    value = value.strip(" .")  # führende/trailing Punkte/Leerzeichen (Windows-Kompatibilität)
    return value or "unbenannt"


def build_target_path(download_root: Path, job_folder: str, received_at: datetime, filename: str) -> Path:
    safe_folder = sanitize_path_segment(job_folder)
    safe_filename = sanitize_path_segment(filename)

    date_prefix = received_at.strftime("%Y_%m_%d")
    stem, ext = os.path.splitext(safe_filename)
    base_name = f"{date_prefix}_{stem}{ext}"

    target_dir = download_root / safe_folder
    candidate = target_dir / base_name
    counter = 1
    while candidate.exists():
        candidate = target_dir / f"{date_prefix}_{stem}_{counter}{ext}"
        counter += 1
    return candidate


def write_once(path: Path, content: bytes) -> None:
    """Schreibt `content` atomar nach `path` (Temp-Datei im selben Verzeichnis + os.replace)
    und setzt die Datei anschließend read-only (0o444) als zusätzliche Absicherung dafür, dass
    die App die Datei danach nie wieder verändert."""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    try:
        os.chmod(path, 0o444)
    except OSError:
        pass  # z.B. auf manchen Windows-Bind-Mounts nicht unterstützt - kein harter Fehler
