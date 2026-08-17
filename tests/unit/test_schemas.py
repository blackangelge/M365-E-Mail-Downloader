from app.schemas import parse_keywords


def test_parse_keywords_splits_on_comma():
    result = parse_keywords("Avis, Zahlungserinnerung, Mahnung")
    assert result == [("avis", "Avis"), ("zahlungserinnerung", "Zahlungserinnerung"), ("mahnung", "Mahnung")]


def test_parse_keywords_splits_on_newline():
    result = parse_keywords("Avis\nZahlungserinnerung\nMahnung")
    assert [normalized for normalized, _ in result] == ["avis", "zahlungserinnerung", "mahnung"]


def test_parse_keywords_mixed_separators_and_blank_lines():
    result = parse_keywords("Avis, Zahlungserinnerung\nMahnung\n\n  Kopie  ,Entwurf")
    assert [normalized for normalized, _ in result] == ["avis", "zahlungserinnerung", "mahnung", "kopie", "entwurf"]


def test_parse_keywords_strips_surrounding_whitespace():
    result = parse_keywords("  Kopie  ,  Entwurf  ")
    assert result == [("kopie", "Kopie"), ("entwurf", "Entwurf")]


def test_parse_keywords_collapses_internal_whitespace():
    result = parse_keywords("Zahlungs   erinnerung")
    assert result == [("zahlungs erinnerung", "Zahlungs erinnerung")]


def test_parse_keywords_deduplicates_case_insensitive_keeping_first_spelling():
    result = parse_keywords("Avis, AVIS, avis")
    assert result == [("avis", "Avis")]


def test_parse_keywords_empty_string_yields_empty_list():
    assert parse_keywords("") == []
    assert parse_keywords("   \n  \n") == []
