from datetime import date, datetime

from app.workers.filters import (
    attachment_matches,
    date_in_range,
    evaluate_attachment,
    extension_matches,
    keyword_matches,
    normalize_extension,
)


def test_normalize_extension_variants():
    assert normalize_extension(".PDF") == ".pdf"
    assert normalize_extension("pdf") == ".pdf"
    assert normalize_extension("Rechnung.PDF") == ".pdf"
    assert normalize_extension("archiv.tar.gz") == ".gz"


def test_extension_matches_case_insensitive():
    assert extension_matches("Rechnung.PDF", {".pdf"}) is True
    assert extension_matches("rechnung.pdf", {".PDF"}) is True
    assert extension_matches("rechnung.docx", {".pdf"}) is False


def test_extension_matches_requires_allowed_set():
    assert extension_matches("rechnung.pdf", set()) is False


def test_extension_matches_no_dot_in_filename():
    assert extension_matches("rechnung", {".pdf"}) is False


def test_keyword_matches_case_insensitive_and_multiple():
    assert keyword_matches("Zahlungserinnerung Nr. 42", ["avis", "zahlungserinnerung"]) is True
    assert keyword_matches("AVIS de paiement", ["avis"]) is True
    assert keyword_matches("Rechnung", ["avis", "zahlungserinnerung"]) is False


def test_keyword_matches_empty_list_never_matches():
    assert keyword_matches("beliebiger Text", []) is False
    assert keyword_matches(None, []) is False


def test_keyword_matches_none_text_with_keywords():
    assert keyword_matches(None, ["avis"]) is False


def test_date_in_range_bounds_inclusive():
    d = date(2026, 8, 16)
    assert date_in_range(d, date(2026, 8, 1), date(2026, 8, 31)) is True
    assert date_in_range(d, date(2026, 8, 17), None) is False
    assert date_in_range(d, None, date(2026, 8, 15)) is False
    assert date_in_range(d, None, None) is True


def test_date_in_range_accepts_datetime():
    dt = datetime(2026, 8, 16, 14, 30)
    assert date_in_range(dt, date(2026, 8, 1), date(2026, 8, 31)) is True


def test_date_in_range_none_received_never_matches():
    assert date_in_range(None, None, None) is False


def test_attachment_matches_excludes_when_keyword_in_filename():
    # Ausschluss-Filter: "avis" im Dateinamen -> NICHT herunterladen
    assert attachment_matches(
        attachment_filename="Avis_2026.pdf",
        email_subject="Guten Tag",
        allowed_extensions={".pdf"},
        keywords=["avis"],
    ) is False


def test_attachment_matches_excludes_when_keyword_only_in_subject():
    assert attachment_matches(
        attachment_filename="rechnung_001.pdf",
        email_subject="Ihre Zahlungserinnerung",
        allowed_extensions={".pdf"},
        keywords=["zahlungserinnerung"],
    ) is False


def test_attachment_matches_downloads_when_no_keyword_present():
    assert attachment_matches(
        attachment_filename="rechnung_001.pdf",
        email_subject="Ihre Bestellung",
        allowed_extensions={".pdf"},
        keywords=["avis", "zahlungserinnerung"],
    ) is True


def test_attachment_matches_wrong_extension_never_matches():
    assert attachment_matches(
        attachment_filename="avis.docx",
        email_subject="Avis",
        allowed_extensions={".pdf"},
        keywords=["avis"],
    ) is False


def test_attachment_matches_no_keywords_means_extension_only():
    assert attachment_matches(
        attachment_filename="beliebig.pdf",
        email_subject="irrelevant",
        allowed_extensions={".pdf"},
        keywords=[],
    ) is True


def test_evaluate_attachment_reports_extension_reason():
    result = evaluate_attachment(
        attachment_filename="beleg.docx",
        email_subject="irrelevant",
        allowed_extensions={".pdf"},
        keywords=["avis"],
    )
    assert result.matches is False
    assert result.reason == "extension"
    assert result.matched_keyword is None


def test_evaluate_attachment_reports_matched_keyword_from_filename():
    result = evaluate_attachment(
        attachment_filename="Avis_2026.pdf",
        email_subject="Guten Tag",
        allowed_extensions={".pdf"},
        keywords=["Avis", "Mahnung"],
    )
    assert result.matches is False
    assert result.reason == "keyword"
    assert result.matched_keyword == "Avis"


def test_evaluate_attachment_reports_matched_keyword_from_subject():
    result = evaluate_attachment(
        attachment_filename="rechnung_001.pdf",
        email_subject="Ihre Zahlungserinnerung",
        allowed_extensions={".pdf"},
        keywords=["Zahlungserinnerung"],
    )
    assert result.matches is False
    assert result.reason == "keyword"
    assert result.matched_keyword == "Zahlungserinnerung"


def test_evaluate_attachment_matches_has_no_reason():
    result = evaluate_attachment(
        attachment_filename="rechnung_001.pdf",
        email_subject="Ihre Bestellung",
        allowed_extensions={".pdf"},
        keywords=["avis"],
    )
    assert result.matches is True
    assert result.reason is None
    assert result.matched_keyword is None
