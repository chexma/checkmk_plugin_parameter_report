"""Configuration files: read, write (own TOML writer) and replace atomically.

tomllib only reads TOML; the values of a report are simple (texts, numbers,
booleans, lists), so a small writer suffices. dump() checks its own output by
loading it again.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

from .settings import FIELD_NAMES, Settings, SettingsError, build_settings

SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Hosts to include", ("hosts", "folders", "host_tags", "host_labels")),
    ("Report content", ("changed_only", "language", "date_format")),
    (
        "Layout",
        (
            "page_size",
            "portrait",
            "margin_mm",
            "font_size",
            "line_height",
            "column_widths",
            "min_split_lines",
            "max_param_chars",
            "max_cover_error_chars",
            "highlight_color",
            "toc",
        ),
    ),
    (
        "Cover page",
        (
            "cover_title",
            "cover_company",
            "cover_customer",
            "cover_author",
            "cover_note",
            "classification",
        ),
    ),
    (
        "Advanced settings",
        ("collect_mode", "workers", "chunk_size", "retention_days"),
    ),
    ("Output", ("output", "status_file", "started_by", "profile", "mail_to")),
)


TABLES = ("schedule", "mail")


def load(path: Path, extra: tuple[str, ...] = ()) -> dict[str, object]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise SettingsError(
            f"Configuration file {path} is not readable: {exc.strerror or exc}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Configuration file {path} is not valid TOML: {exc}") from exc
    unknown = sorted(set(data) - FIELD_NAMES - set(extra) - set(TABLES))
    if unknown:
        raise SettingsError(f"Configuration file {path}: unknown setting(s): {', '.join(unknown)}")
    for table in TABLES:
        if table in data and not isinstance(data[table], dict):
            raise SettingsError(f"Configuration file {path}: [{table}] must be a table")
    return data


def settings_part(values: Mapping[str, object]) -> dict[str, object]:
    """The report settings of a loaded file, without tables and description."""
    return {k: v for k, v in values.items() if k not in TABLES and k != "description"}


def _plain(name: str, value: object) -> object:
    """Settings value -> value as build_settings accepts it (lists, texts)."""
    if name in ("host_tags", "host_labels"):
        return [f"{key}:{val}" for key, val in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def to_values(settings: Settings, only_changed: bool) -> dict[str, object]:
    defaults = Settings()
    values = {}
    for f in fields(Settings):
        value = getattr(settings, f.name)
        if value is None or (only_changed and value == getattr(defaults, f.name)):
            continue
        values[f.name] = _plain(f.name, value)
    return values


def _string(text: str) -> str:
    out = []
    for char in text:
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SettingsError(f"{value!r} cannot be written to the configuration file")
        return repr(value)
    if isinstance(value, (str, Path)):
        return _string(str(value))
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise SettingsError(f"{value!r} cannot be written to the configuration file")


def dump(
    values: Mapping[str, object],
    header: str = "",
    description: str = "",
    tables: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    """TOML text of the given values, in the order of the web page sections."""
    lines = [f"# {header}"] if header else []
    if description:
        lines += ([""] if lines else []) + ["# Profile", f"description = {_string(description)}"]
    for title, names in SECTIONS:
        present = [name for name in names if name in values]
        if not present:
            continue
        if lines:
            lines.append("")
        lines.append(f"# {title}")
        lines += [f"{name} = {_value(values[name])}" for name in present]
    written = {name: table for name, table in (tables or {}).items() if table}
    for name, table in written.items():
        lines += ["", f"[{name}]"] + [f"{k} = {_value(v)}" for k, v in table.items()]
    text = "\n".join(lines) + "\n"
    try:
        loaded = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Configuration not written: {exc}") from None
    if any(loaded.pop(name, None) != dict(table) for name, table in written.items()):
        raise SettingsError("Configuration not written: it would not load back unchanged")
    if loaded.pop("description", "") != description or build_settings(loaded) != build_settings(
        None, values
    ):
        raise SettingsError("Configuration not written: it would not load back unchanged")
    return text


def write_atomic(path: Path, text: str, backup: bool) -> None:
    """Replace path by text; on any error the old file stays as it was."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
