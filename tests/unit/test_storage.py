from datetime import datetime
from pathlib import Path

from app.workers.storage import build_target_path, sanitize_path_segment, write_once


def test_sanitize_path_segment_removes_unsafe_chars():
    assert sanitize_path_segment("firma/../../etc") == "firma_.._.._etc"
    assert sanitize_path_segment("  rechnung.pdf  ") == "rechnung.pdf"
    assert sanitize_path_segment("") == "unbenannt"


def test_build_target_path_uses_year_month_day_prefix(tmp_path: Path):
    received = datetime(2026, 8, 16, 10, 0)
    target = build_target_path(tmp_path, "job-a", received, "rechnung.pdf")
    assert target == tmp_path / "job-a" / "2026_08_16_rechnung.pdf"


def test_build_target_path_collision_gets_counter_suffix(tmp_path: Path):
    received = datetime(2026, 8, 16, 10, 0)
    first = build_target_path(tmp_path, "job-a", received, "rechnung.pdf")
    write_once(first, b"content-1")

    second = build_target_path(tmp_path, "job-a", received, "rechnung.pdf")
    assert second == tmp_path / "job-a" / "2026_08_16_rechnung_1.pdf"
    write_once(second, b"content-2")

    third = build_target_path(tmp_path, "job-a", received, "rechnung.pdf")
    assert third == tmp_path / "job-a" / "2026_08_16_rechnung_2.pdf"


def test_write_once_is_atomic_and_read_only(tmp_path: Path):
    target = tmp_path / "sub" / "file.pdf"
    write_once(target, b"hello world")

    assert target.read_bytes() == b"hello world"
    # keine .tmp-* Dateien duerfen im Zielverzeichnis zurueckbleiben
    leftovers = [p for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []
