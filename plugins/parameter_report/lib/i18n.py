"""Texts of the report and its mail in several languages, and the date formats.

Each language is one file locales/<code>.toml with the same keys as en.toml;
adding a file adds the language. The texts live outside the code so that the
product code stays English. No reportlab or Checkmk imports: settings import it.
"""

from __future__ import annotations

import tomllib
from datetime import datetime
from functools import cache
from pathlib import Path

LOCALES = Path(__file__).with_name("locales")
LANGUAGES: tuple[str, ...] = ("en",) + tuple(
    sorted(p.stem for p in LOCALES.glob("*.toml") if p.stem != "en")
)
# Shown as is in the web page and stored in the configuration files
DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d %H:%M",
    "DD.MM.YYYY": "%d.%m.%Y %H:%M",
    "DD/MM/YYYY": "%d/%m/%Y %H:%M",
    "MM/DD/YYYY": "%m/%d/%Y %I:%M {ampm}",  # not %p: that one follows the process locale
}


@cache
def table(language: str) -> dict[str, str]:
    """All texts of a language; KeyError for a language without a file."""
    if language not in LANGUAGES:
        raise KeyError(language)
    with (LOCALES / f"{language}.toml").open("rb") as fh:
        return tomllib.load(fh)


def text(language: str, key: str, **values: object) -> str:
    template = table(language)[key]
    return template.format(**values) if values else template


def timestamp(when: datetime, date_format: str) -> str:
    return when.strftime(DATE_FORMATS[date_format]).format(ampm="AM" if when.hour < 12 else "PM")
