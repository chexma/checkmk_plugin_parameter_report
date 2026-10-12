"""Settings of a report run: defaults, validation and the config file.

One place for every tunable value, shared by the CLI and the web pages.
No reportlab or Checkmk imports: settings travel to worker processes.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from .i18n import DATE_FORMATS, LANGUAGES

# Page sizes in PDF points, portrait (width, height)
PAGE_SIZES = {
    "A4": (595.2756, 841.8898),
    "A3": (841.8898, 1190.5512),
    "letter": (612.0, 792.0),
}
COLLECT_MODES = ("subprocess", "inline")
MM = 72 / 25.4
# Right end of the footer, kept free for the report-wide page number (stamped when merging)
PAGE_NUMBER_SPACE = 32 * MM
CELL_PAD = 2.0  # points left and right of every canvas table cell
MIN_PARAM_CHARS = 20
DEFAULT_CONFIG = Path("etc/parameter_report.toml")  # relative to OMD_ROOT
PROFILE_NAME = re.compile(r"[a-z0-9_-]{1,40}")
NAME_RULE = "Profile names: a-z, 0-9, _ and -, at most 40 characters."
# Set per run (CLI/web page), never in the base file or a profile
RUN_KEYS = ("output", "status_file", "started_by", "profile", "mail_to")
# How a report is produced and how long reports are kept: site-wide, base only
GLOBAL_KEYS = ("collect_mode", "workers", "chunk_size", "retention_days")
# local@domain.tld without characters that could start another address or header
ADDRESS = re.compile(r"[^@\s,<>\"'()\[\];:\\]+@[^@\s,<>\"'()\[\];:\\]+\.[^@\s,<>\"'()\[\];:\\]+")
MAX_ADDRESS = 254
# Texts of the cover page
COVER_LINES = ("cover_title", "cover_company", "cover_customer", "cover_author", "classification")
MAX_COVER_LINE = 200
MAX_COVER_NOTE = 2000


class SettingsError(ValueError):
    """Invalid setting; the message is shown to the user as is."""


def check_address(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_ADDRESS
        or not ADDRESS.fullmatch(value)
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or value.startswith("-")  # sendmail would read it as an option
        or "" in value.rpartition("@")[2].split(".")  # empty domain label
    ):
        raise SettingsError(f"{field}: {value!r} is not a valid mail address")
    return value


def default_workers() -> int:
    # At most 2: beyond that the render phase needs more memory than collecting
    # the configuration, for a few seconds less runtime (see docs, Measurements)
    return min(2, max(1, (os.cpu_count() or 2) // 2))


@dataclass(frozen=True)
class Settings:
    output: Path | None = None
    hosts: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    host_tags: tuple[tuple[str, str], ...] = ()
    host_labels: tuple[tuple[str, str], ...] = ()
    changed_only: bool = False
    language: str = "en"  # texts of the PDF and the mail
    date_format: str = "YYYY-MM-DD"  # a key of DATE_FORMATS
    collect_mode: str = "subprocess"
    workers: int = field(default_factory=default_workers)
    chunk_size: int = 50
    retention_days: int = 0
    status_file: Path | None = None
    started_by: str = ""
    mail_to: tuple[str, ...] = ()  # set per run: mail the report to these addresses
    page_size: str = "A4"
    portrait: bool = False
    margin_mm: float = 12.0
    font_size: float = 6.5
    line_height: float = 8.0
    column_widths: tuple[int, int, int, int] = (34, 22, 14, 16)
    min_split_lines: int = 2
    max_param_chars: int = 20000
    max_cover_error_chars: int = 500
    highlight_color: str = "#fde68a"
    toc: bool = True  # clickable list of all hosts after the cover page
    cover_title: str = ""
    cover_company: str = ""
    cover_customer: str = ""
    cover_author: str = ""
    cover_note: str = ""
    classification: str = ""  # also in the footer of every page
    profile: str = ""  # profile of a run (run file, metadata)

    @property
    def page(self) -> tuple[float, float]:
        width, height = PAGE_SIZES[self.page_size]
        return (width, height) if self.portrait else (height, width)

    @property
    def margin(self) -> float:
        return self.margin_mm * MM

    @property
    def char_width(self) -> float:
        return 0.6 * self.font_size  # Courier: every glyph is 0.6 em wide

    @property
    def param_column_chars(self) -> int:
        usable = self.page[0] - 2 * self.margin - 10 * CELL_PAD
        return int(usable / self.char_width) - sum(self.column_widths)


FIELD_NAMES = frozenset(f.name for f in fields(Settings))
_INTS = {"workers", "chunk_size", "min_split_lines", "max_param_chars"}
_INTS |= {"max_cover_error_chars", "retention_days"}
_FLOATS = {"margin_mm", "font_size", "line_height"}
_BOOLS = {"changed_only", "portrait", "toc"}
_PATHS = {"output", "status_file"}
_LISTS = {"hosts", "folders", "mail_to"}
_PAIRS = {"host_tags", "host_labels"}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _pairs(name: str, values: object) -> tuple[tuple[str, str], ...]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise SettingsError(f"{name}: list of NAME:VALUE expected")
    pairs = []
    for value in values:
        if not isinstance(value, str) or ":" not in value or not value.split(":", 1)[0]:
            raise SettingsError(f"{name}: '{value}' is not in the format NAME:VALUE")
        key, val = value.split(":", 1)
        pairs.append((key, val))
    return tuple(pairs)


def _convert(name: str, value: object) -> object:
    if name in _INTS:
        if not _is_int(value):
            raise SettingsError(f"{name}: integer expected, not {value!r}")
        return value
    if name in _FLOATS:
        if not (_is_int(value) or isinstance(value, float)) or not math.isfinite(value):
            raise SettingsError(f"{name}: finite number expected, not {value!r}")
        return float(value)
    if name in _BOOLS:
        if not isinstance(value, bool):
            raise SettingsError(f"{name}: true or false expected, not {value!r}")
        return value
    if name in _PATHS:
        if not isinstance(value, (str, Path)):
            raise SettingsError(f"{name}: path expected, not {value!r}")
        return Path(value).expanduser()
    if name in _LISTS:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise SettingsError(f"{name}: list of texts expected")
        return tuple(v.strip("/") for v in value) if name == "folders" else tuple(value)
    if name in _PAIRS:
        return _pairs(name, value)
    if name == "column_widths":
        if isinstance(value, str):
            try:
                value = [int(part) for part in value.split(",")]
            except ValueError:
                value = None
        if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(map(_is_int, value)):
            raise SettingsError(
                "column_widths: four integers expected (service,plugin,item,service groups)"
            )
        return tuple(value)
    if not isinstance(value, str):
        raise SettingsError(f"{name}: text expected, not {value!r}")
    return value


def _range(name: str, value: float, low: float, high: float | None = None) -> None:
    if value < low or (high is not None and value > high):
        bound = f"{low} to {high}" if high is not None else f"at least {low}"
        raise SettingsError(f"{name}: {value} is out of range ({bound})")


def _choice(name: str, value: str, allowed: object) -> None:
    if value not in allowed:
        raise SettingsError(f"{name}: unknown value '{value}', allowed: {', '.join(allowed)}")


def validate(s: Settings) -> None:
    _choice("collect_mode", s.collect_mode, COLLECT_MODES)
    _choice("page_size", s.page_size, list(PAGE_SIZES))
    _choice("language", s.language, LANGUAGES)
    _choice("date_format", s.date_format, list(DATE_FORMATS))
    _range("workers", s.workers, 1, 64)
    _range("chunk_size", s.chunk_size, 1, 10000)
    _range("retention_days", s.retention_days, 0, 3650)
    _range("margin_mm", s.margin_mm, 5, 40)
    _range("font_size", s.font_size, 4, 14)
    _range("min_split_lines", s.min_split_lines, 1, 20)
    _range("max_param_chars", s.max_param_chars, 100, 1_000_000)
    # 5000 characters still fit one cover table row, even in portrait
    _range("max_cover_error_chars", s.max_cover_error_chars, 50, 5000)
    _range("line_height", s.line_height, s.font_size, 3 * s.font_size)
    if any(width < 4 for width in s.column_widths):
        raise SettingsError("column_widths: every column needs at least 4 characters")
    if s.param_column_chars < MIN_PARAM_CHARS:
        raise SettingsError(
            f"column_widths: only {s.param_column_chars} characters left for the parameter "
            f"column, at least {MIN_PARAM_CHARS} needed"
        )
    for name in COVER_LINES:
        value = getattr(s, name)
        if len(value) > MAX_COVER_LINE or "\n" in value or "\r" in value:
            raise SettingsError(f"{name}: one line of at most {MAX_COVER_LINE} characters expected")
    if len(s.cover_note) > MAX_COVER_NOTE:
        raise SettingsError(f"cover_note: at most {MAX_COVER_NOTE} characters allowed")
    for address in s.mail_to:
        check_address(address, "mail_to")
    if s.profile and not PROFILE_NAME.fullmatch(s.profile):
        raise SettingsError(f"profile: {NAME_RULE}")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", s.highlight_color):
        raise SettingsError(
            f"highlight_color: '{s.highlight_color}' is not a color in the format #RRGGBB"
        )


def build_settings(
    file_values: Mapping[str, object] | None = None,
    cli_values: Mapping[str, object] | None = None,
) -> Settings:
    """Defaults < config file < command line; None in cli_values means 'not given'."""
    merged: dict[str, object] = {}
    for source in (file_values or {}, cli_values or {}):
        for name, value in source.items():
            if value is None:
                continue
            if name not in FIELD_NAMES:
                raise SettingsError(f"Unknown setting: {name}")
            merged[name] = _convert(name, value)
    settings = Settings(**merged)
    validate(settings)
    return settings
