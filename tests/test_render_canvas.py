from datetime import datetime

import pytest
from cmk_addons.plugins.parameter_report.lib.model import (
    HostReport,
    Origin,
    ParamSet,
    ParamValue,
    ServiceReport,
)
from cmk_addons.plugins.parameter_report.lib.render_canvas import (
    KEY,
    TEXT,
    column_layout,
    layout_runs,
    lines_on_this_page,
    param_runs,
    render_hosts,
    wrap_spans,
)
from cmk_addons.plugins.parameter_report.lib.settings import Settings
from pypdf import PdfReader

NOW = datetime(2026, 10, 9, 12, 0)


def test_wrap_spans_breaks_at_spaces():
    assert wrap_spans("aaa bbb ccc", 7) == ((0, 7), (8, 11))


def test_wrap_spans_hard_breaks_long_words():
    assert wrap_spans("abcdefghij", 4) == ((0, 4), (4, 8), (8, 10))


def test_wrap_spans_empty_text():
    assert wrap_spans("", 5) == ((0, 0),)


def test_layout_runs_keeps_styles_across_lines():
    lines = layout_runs((("key:", KEY, True), (" value value", TEXT, True)), 10)
    assert lines == (
        (("key:", KEY, True), (" value", TEXT, True)),
        (("value", TEXT, True),),
    )


def test_param_runs_highlight_rule_values_only():
    ps = ParamSet(
        None,
        (
            ParamValue("levels", (90.0, 95.0), Origin.RULE),
            ParamValue("trend", 24, Origin.DEFAULT),
        ),
    )
    assert param_runs(ps, 1000) == (
        ("levels:", KEY, True),
        (" 90.0 / 95.0", TEXT, True),
        (" · ", TEXT, False),
        ("trend:", KEY, False),
        (" 24", TEXT, False),
    )


def test_param_runs_empty_and_truncated():
    assert param_runs(ParamSet(None, ()), 100) == (("none", TEXT, False),)
    runs = param_runs(ParamSet(None, (ParamValue("x", "y" * 500, Origin.RULE),)), 100)
    assert runs[1][0].endswith("[truncated]") and len(runs[1][0]) < 130


@pytest.mark.parametrize(
    "row, free, page, min_split, expected",
    [
        (5, 10, 40, 2, 5),  # fits
        (5, 3, 40, 2, 0),  # fits on a fresh page: move whole
        (100, 10, 40, 2, 10),  # taller than a page: split here
        (100, 1, 40, 2, 0),  # too few lines left for a split
        (100, 40, 40, 2, 40),  # fresh page
        (100, 1, 1, 2, 1),  # fresh page smaller than min_split: no endless loop
    ],
)
def test_lines_on_this_page(row, free, page, min_split, expected):
    assert lines_on_this_page(row, free, page, min_split) == expected


def test_column_layout_follows_widths():
    narrow = column_layout(Settings(column_widths=(20, 22, 14, 16)))
    wide = column_layout(Settings(column_widths=(40, 22, 14, 16)))
    assert wide[1][0] > narrow[1][0]
    assert wide[3][1] == narrow[3][1] - 20


def test_portrait_page(tmp_path):
    out = tmp_path / "h.pdf"
    render_hosts([HostReport("h")], out, NOW, Settings(portrait=True))
    box = PdfReader(out).pages[0].mediabox
    assert box.width < box.height


def test_bigger_font_needs_more_pages(tmp_path):
    services = tuple(
        ServiceReport(f"svc {i}", "df", None, (ParamSet(None, ()),)) for i in range(200)
    )
    small, big = tmp_path / "small.pdf", tmp_path / "big.pdf"
    render_hosts([HostReport("h", services=services)], small, NOW, Settings())
    render_hosts(
        [HostReport("h", services=services)],
        big,
        NOW,
        Settings(font_size=10, line_height=12, column_widths=(20, 14, 8, 8)),
    )
    assert len(PdfReader(big).pages) > len(PdfReader(small).pages)
