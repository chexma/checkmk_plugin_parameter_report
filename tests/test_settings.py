from pathlib import Path

import pytest
from cmk_addons.plugins.parameter_report.lib import settings as settings_module
from cmk_addons.plugins.parameter_report.lib.settings import (
    Settings,
    SettingsError,
    build_settings,
    default_workers,
)


def test_defaults():
    s = build_settings()
    assert s.collect_mode == "subprocess"
    assert s.chunk_size == 50
    assert s.min_split_lines == 2
    assert s.column_widths == (34, 22, 14, 16)
    assert s.highlight_color == "#fde68a"
    assert s.page == pytest.approx((841.8898, 595.2756))
    assert s.param_column_chars == 107


@pytest.mark.parametrize("cpus, expected", [(None, 1), (1, 1), (2, 1), (4, 2), (10, 2), (64, 2)])
def test_default_workers(monkeypatch, cpus, expected):
    monkeypatch.setattr(settings_module.os, "cpu_count", lambda: cpus)
    assert default_workers() == expected


def test_cli_overrides_file_overrides_defaults():
    s = build_settings(
        {"chunk_size": 7, "min_split_lines": 3}, {"chunk_size": 9, "min_split_lines": None}
    )
    assert s.chunk_size == 9
    assert s.min_split_lines == 3


def test_conversions():
    s = build_settings(
        {"column_widths": [30, 20, 10, 10], "output": "~/x.pdf"},
        {
            "folders": ["/linux/web/", "db"],
            "host_tags": ["criticality:prod"],
            "host_labels": ["os:linux:ubuntu"],
            "margin_mm": 10,
        },
    )
    assert s.column_widths == (30, 20, 10, 10)
    assert s.output == Path("~/x.pdf").expanduser()
    assert s.folders == ("linux/web", "db")
    assert s.host_tags == (("criticality", "prod"),)
    assert s.host_labels == (("os", "linux:ubuntu"),)
    assert s.margin_mm == 10.0


def test_column_widths_from_cli_string():
    assert build_settings(None, {"column_widths": "20,20,10,10"}).column_widths == (20, 20, 10, 10)


def test_portrait_page():
    s = build_settings(None, {"portrait": True, "page_size": "A3"})
    assert s.page == pytest.approx((841.8898, 1190.5512))


@pytest.mark.parametrize(
    "values, message",
    [
        ({"renderer": "canvas"}, "Unknown setting: renderer"),
        ({"collect_mode": "thread"}, "collect_mode"),
        ({"page_size": "A5"}, "page_size"),
        ({"workers": 0}, "workers"),
        ({"chunk_size": 0}, "chunk_size"),
        ({"min_split_lines": -1}, "min_split_lines"),
        ({"profile": "../x"}, "profile"),
        ({"mail_to": ["bad"]}, "mail_to"),
        ({"retention_days": -1}, "retention_days"),
        ({"retention_days": 4000}, "retention_days"),
        ({"margin_mm": 2}, "margin_mm"),
        ({"font_size": 30}, "font_size"),
        ({"line_height": 5.0}, "line_height"),
        ({"min_split_lines": 0}, "min_split_lines"),
        ({"max_param_chars": 10}, "max_param_chars"),
        ({"max_cover_error_chars": 10}, "max_cover_error_chars"),
        ({"column_widths": [2, 22, 14, 16]}, "column_widths"),
        ({"column_widths": [80, 40, 40, 40]}, "parameter column"),
        ({"portrait": True, "column_widths": [50, 30, 20, 20]}, "parameter column"),
        ({"font_size": 14, "line_height": 16}, "parameter column"),
        ({"highlight_color": "gelb"}, "highlight_color"),
        ({"host_tags": ["nocolon"]}, "host_tags"),
        ({"host_labels": [":value"]}, "host_labels"),
        ({"nonsense": 1}, "nonsense"),
        ({"cover_title": "a\nb"}, "cover_title"),
        ({"cover_company": "x" * 201}, "cover_company"),
        ({"cover_customer": "x" * 201}, "cover_customer"),
        ({"cover_author": "a\rb"}, "cover_author"),
        ({"classification": "a\nb"}, "classification"),
        ({"cover_note": "x" * 2001}, "cover_note"),
    ],
)
def test_validation_errors(values, message):
    with pytest.raises(SettingsError, match=message):
        build_settings(None, values)


@pytest.mark.parametrize(
    "values",
    [
        {"chunk_size": "50"},
        {"chunk_size": True},
        {"font_size": "6"},
        {"changed_only": "yes"},
        {"column_widths": "a,b,c,d"},
        {"column_widths": [1, 2, 3]},
        {"hosts": [1, 2]},
        {"cover_note": ["a", "b"]},
    ],
)
def test_wrong_types_are_rejected(values):
    with pytest.raises(SettingsError, match=next(iter(values))):
        build_settings(values, None)


def test_settings_are_hashable_free_of_mutables():
    # Settings travel to worker processes and must be immutable
    s = Settings()
    with pytest.raises(AttributeError):
        s.chunk_size = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    "values, message",
    [
        ({"font_size": float("nan")}, "font_size"),
        ({"margin_mm": float("inf")}, "margin_mm"),
        ({"line_height": float("nan")}, "line_height"),
        ({"line_height": 400.0}, "line_height"),
        ({"max_cover_error_chars": 100000}, "max_cover_error_chars"),
    ],
)
def test_values_that_would_fail_after_collecting(values, message):
    with pytest.raises(SettingsError, match=message):
        build_settings(None, values)


def test_cover_texts_at_their_limits():
    note = "x" * 1990 + "\nsecond"
    s = build_settings(None, {"cover_title": "x" * 200, "cover_note": note})
    assert s.cover_title == "x" * 200 and s.cover_note == note


def test_language_and_date_format():
    s = build_settings()
    assert s.language == "en" and s.date_format == "YYYY-MM-DD"
    s = build_settings({"language": "de", "date_format": "DD.MM.YYYY"})
    assert s.language == "de" and s.date_format == "DD.MM.YYYY"
    with pytest.raises(SettingsError, match="language: unknown value 'xx'"):
        build_settings({"language": "xx"})
    with pytest.raises(SettingsError, match="date_format: unknown value"):
        build_settings({"date_format": "%d.%m."})
