import string
from datetime import datetime

import pytest
from cmk_addons.plugins.parameter_report.lib import i18n


def _fields(text):
    return {name for _literal, name, _spec, _conv in string.Formatter().parse(text) if name}


def test_languages():
    assert i18n.LANGUAGES[0] == "en" and "de" in i18n.LANGUAGES


@pytest.mark.parametrize("language", i18n.LANGUAGES)
def test_every_language_has_every_text_with_the_same_placeholders(language):
    english, other = i18n.table("en"), i18n.table(language)
    assert set(other) == set(english)
    for key, text in english.items():
        assert _fields(other[key]) == _fields(text), key


def test_text():
    assert i18n.text("en", "classification") == "Classification"
    assert i18n.text("de", "classification") == "Klassifizierung"
    assert i18n.text("en", "page_of_host", page=2) == "page 2 of host"
    assert i18n.text("de", "page_of_host", page=2) == "Seite 2 des Hosts"


def test_unknown_language_is_an_error():
    with pytest.raises(KeyError):
        i18n.table("xx")


@pytest.mark.parametrize(
    "date_format, expected",
    [
        ("YYYY-MM-DD", "2026-10-09 15:04"),
        ("DD.MM.YYYY", "09.10.2026 15:04"),
        ("DD/MM/YYYY", "09/10/2026 15:04"),
        ("MM/DD/YYYY", "10/09/2026 03:04 PM"),
    ],
)
def test_timestamp(date_format, expected):
    assert i18n.timestamp(datetime(2026, 10, 9, 15, 4), date_format) == expected
