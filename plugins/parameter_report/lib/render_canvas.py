"""Host pages, drawn directly on the PDF canvas.

Table cells use Courier, so a text's width is its length times the character
width and line breaking is plain arithmetic instead of measuring every word
as Platypus does: about six times faster with half the memory. Texts shared
with the cover page come from render.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from .i18n import text
from .model import HostReport, Origin, ParamSet, ServiceReport, display_name
from .render import (
    always_prefix,
    empty_services_text,
    footer_left,
    format_value,
    header,
    info_rows,
    title,
    truncate,
)
from .settings import CELL_PAD, PAGE_NUMBER_SPACE, Settings

CELL_FONT, CELL_BOLD = "Courier", "Courier-Bold"
LABEL_FONT, LABEL_BOLD = "Helvetica", "Helvetica-Bold"
INFO_SIZE = 7.0
INFO_LEAD = 8.5
INFO_LABEL_WIDTH = 30 * mm
TITLE_SIZE = 14.0
HEADER_SIZE = 7.0
FOOTER_Y = 7 * mm
FOOTER_SPACE = 6 * mm
DARK = HexColor("#1f3a5f")
GRID = HexColor("#b0b7c3")
SHADE = HexColor("#f2f5f9")
MARK = HexColor("#b45309")
ERROR = HexColor("#b91c1c")
SEPARATOR = " · "

# A cell is a tuple of lines, a line a tuple of runs (text, kind, highlighted)
TEXT, KEY, NOTE = "text", "key", "note"
Run = tuple[str, str, bool]
Line = tuple[Run, ...]
EMPTY_CELL: tuple[Line, ...] = ((),)


@lru_cache(maxsize=65536)
def wrap_spans(text: str, width: int) -> tuple[tuple[int, int], ...]:
    """Break text at spaces into lines of at most width characters: (start, end) each."""
    lines = []
    start, end = 0, len(text)
    while start < end:
        if end - start <= width:
            lines.append((start, end))
            break
        cut = text.rfind(" ", start, start + width + 1)
        if cut <= start:  # no space within the line: hard break
            lines.append((start, start + width))
            start += width
        else:
            lines.append((start, cut))
            start = cut + 1
    return tuple(lines) or ((0, 0),)


@lru_cache(maxsize=65536)
def layout_runs(runs: tuple[Run, ...], width: int) -> tuple[Line, ...]:
    """Wrap styled runs into lines of at most width characters."""
    text = "".join(run[0] for run in runs)
    bounds = []
    pos = 0
    for run_text, kind, highlighted in runs:
        bounds.append((pos, pos + len(run_text), kind, highlighted))
        pos += len(run_text)
    lines = []
    for start, end in wrap_spans(text, width):
        lines.append(
            tuple(
                (text[max(a, start) : min(b, end)], kind, highlighted)
                for a, b, kind, highlighted in bounds
                if max(a, start) < min(b, end)
            )
        )
    return tuple(lines)


def _plain(text: str) -> tuple[Run, ...]:
    return ((text, TEXT, False),)


def param_runs(ps: ParamSet, max_chars: int, language: str = "en") -> tuple[Run, ...]:
    if not ps.values:
        return ((text(language, "no_values"), TEXT, False),)
    runs: list[Run] = []
    for index, pv in enumerate(ps.values):
        if index:
            runs.append((SEPARATOR, TEXT, False))
        value = truncate(format_value(pv.value), max_chars, language)
        rule = pv.origin is Origin.RULE
        runs.append((f"{pv.key}:", KEY, rule))
        runs.append((f" {value}", TEXT, rule))
    return tuple(runs)


def column_layout(settings: Settings) -> tuple[tuple[float, int], ...]:
    """Left edge (points) and width (characters) of the five table columns."""
    service, plugin, item, groups = settings.column_widths
    chars = (service, plugin, item, settings.param_column_chars, groups)
    columns = []
    x = settings.margin
    for width in chars:
        columns.append((x, width))
        x += width * settings.char_width + 2 * CELL_PAD
    return tuple(columns)


def lines_on_this_page(row_lines: int, free_lines: int, page_lines: int, min_split: int) -> int:
    """How many lines of a table row go on the current page; 0 means new page first."""
    if free_lines >= page_lines:  # fresh page: always make progress
        return min(row_lines, max(free_lines, 1))
    if row_lines <= free_lines:
        return row_lines
    if row_lines <= page_lines:  # fits on a fresh page: move it whole
        return 0
    return free_lines if free_lines >= min_split else 0


class _Writer:
    def __init__(self, path: Path, generated_at: datetime, settings: Settings) -> None:
        self.s = settings
        self.page_width, self.page_height = settings.page
        self.columns = column_layout(settings)
        last_x, last_chars = self.columns[-1]
        self.table_right = last_x + last_chars * settings.char_width + 2 * CELL_PAD
        self.table_width = self.table_right - settings.margin
        self.top = self.page_height - settings.margin
        self.bottom = settings.margin + FOOTER_SPACE
        self.highlight = HexColor(settings.highlight_color)
        self.canvas = Canvas(str(path), pagesize=settings.page, pageCompression=1)
        self.canvas.setTitle(title(settings))
        self.header = header(settings.language)
        self.generated_at = generated_at
        self.host_name = ""
        self.host_page = 0
        self.page_open = False
        self.y = self.top

    # --- pages ---------------------------------------------------------------

    def _close_page(self) -> None:
        if self.page_open:
            self._footer()
            self.canvas.showPage()
            self.page_open = False

    def _new_page(self) -> None:
        self._close_page()
        self.page_open = True
        self.host_page += 1
        self.y = self.top

    def _footer(self) -> None:
        c = self.canvas
        c.setFont(LABEL_FONT, 7)
        c.setFillColor(black)
        c.drawString(
            self.s.margin,
            FOOTER_Y,
            footer_left(self.host_name, self.generated_at, self.s),
        )
        c.drawRightString(
            self.page_width - self.s.margin - PAGE_NUMBER_SPACE,
            FOOTER_Y,
            text(self.s.language, "page_of_host", page=self.host_page),
        )
        if self.s.classification:
            c.drawCentredString(self.page_width / 2, FOOTER_Y, self.s.classification)

    def _ensure_space(self, height: float) -> None:
        if self.y - height < self.bottom:
            self._new_page()

    def close(self) -> None:
        if self.page_open:
            self._footer()
        self.canvas.save()

    # --- blocks outside the table --------------------------------------------

    def _text(self, x: float, text: str, font: str, size: float, color: Color) -> None:
        self.canvas.setFont(font, size)
        self.canvas.setFillColor(color)
        self.canvas.drawString(x, self.y - size, text)

    def _paragraph(self, text: str, color: Color) -> None:
        chars = int(self.table_width / (0.6 * INFO_SIZE))
        for start, end in wrap_spans(text, chars):
            self._ensure_space(INFO_LEAD)
            self._text(self.s.margin, text[start:end], CELL_FONT, INFO_SIZE, color)
            self.y -= INFO_LEAD

    def _info_row(self, label: str, value: str) -> None:
        chars = int((self.table_width - INFO_LABEL_WIDTH) / (0.6 * INFO_SIZE))
        for index, (start, end) in enumerate(wrap_spans(value, chars)):
            self._ensure_space(INFO_LEAD)
            if index == 0:
                self._text(self.s.margin, label, LABEL_BOLD, INFO_SIZE, DARK)
            x = self.s.margin + INFO_LABEL_WIDTH
            self._text(x, value[start:end], CELL_FONT, INFO_SIZE, black)
            self.y -= INFO_LEAD
        self.y -= 2

    # --- table -----------------------------------------------------------------

    @property
    def _header_height(self) -> float:
        return self.s.line_height + 2 * CELL_PAD

    def _free_lines(self) -> int:
        return int((self.y - self.bottom - 2 * CELL_PAD) // self.s.line_height)

    def _page_lines(self) -> int:
        usable = self.top - self._header_height - self.bottom - 2 * CELL_PAD
        return int(usable // self.s.line_height)

    def _table_header(self) -> None:
        c = self.canvas
        height = self._header_height
        c.setFillColor(DARK)
        c.rect(self.s.margin, self.y - height, self.table_width, height, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(LABEL_BOLD, HEADER_SIZE)
        baseline = self.y - CELL_PAD - 0.8 * self.s.line_height
        for (x, _chars), column_title in zip(self.columns, self.header):
            c.drawString(x + CELL_PAD, baseline, column_title)
        self.y -= height

    def _row(self, cells: Sequence[tuple[Line, ...]], shade: bool) -> None:
        total = max(len(cell) for cell in cells)
        first = 0
        while first < total:
            take = lines_on_this_page(
                total - first, self._free_lines(), self._page_lines(), self.s.min_split_lines
            )
            if take <= 0:
                self._new_page()
                self._table_header()
                continue
            self._row_part(cells, first, take, shade)
            first += take

    def _row_part(
        self, cells: Sequence[tuple[Line, ...]], first: int, take: int, shade: bool
    ) -> None:
        c, s = self.canvas, self.s
        top = self.y
        height = take * s.line_height + 2 * CELL_PAD
        if shade:
            c.setFillColor(SHADE)
            c.rect(s.margin, top - height, self.table_width, height, stroke=0, fill=1)
        for (x, _chars), cell in zip(self.columns, cells):
            for index, line in enumerate(cell[first : first + take]):
                self._line(x + CELL_PAD, top - CELL_PAD - index * s.line_height, line)
        c.setStrokeColor(GRID)
        c.setLineWidth(0.25)
        c.rect(s.margin, top - height, self.table_width, height, stroke=1, fill=0)
        for x, _chars in self.columns[1:]:
            c.line(x, top, x, top - height)
        self.y = top - height

    def _line(self, x: float, line_top: float, line: Line) -> None:
        c, s = self.canvas, self.s
        baseline = line_top - 0.8 * s.line_height
        for run_text, kind, highlighted in line:
            width = len(run_text) * s.char_width
            if highlighted:
                c.setFillColor(self.highlight)
                c.rect(x, line_top - s.line_height, width, s.line_height, stroke=0, fill=1)
            c.setFont(CELL_BOLD if kind == KEY else CELL_FONT, s.font_size)
            c.setFillColor(DARK if kind == KEY else MARK if kind == NOTE else black)
            c.drawString(x, baseline, run_text)
            x += width

    def _service(self, svc: ServiceReport, shade: bool) -> None:
        columns, limit, lang = self.columns, self.s.max_param_chars, self.s.language
        notes: tuple[Run, ...] = ()
        if svc.enforced:
            notes += ((f" {text(lang, 'enforced')}", NOTE, False),)
        if svc.timespecific:
            notes += ((f" {text(lang, 'time_specific')}", NOTE, False),)
        standard = next((ps for ps in svc.param_sets if ps.timeperiod is None), ParamSet(None, ()))
        prefix = always_prefix(standard, lang)
        standard_runs: tuple[Run, ...] = ((prefix, TEXT, False),) if prefix else ()
        cells = [
            layout_runs(((svc.description, TEXT, False), *notes), columns[0][1]),
            layout_runs(_plain(svc.check_plugin), columns[1][1]),
            layout_runs(_plain(svc.item or "–"), columns[2][1]),
            layout_runs((*standard_runs, *param_runs(standard, limit, lang)), columns[3][1]),
            layout_runs(_plain(", ".join(svc.servicegroups) or "–"), columns[4][1]),
        ]
        self._row(cells, shade)
        for ps in svc.param_sets:
            if ps.timeperiod is None:
                continue
            label = f"» {text(lang, 'time_period')} {display_name(ps.timeperiod, ps.alias)}: "
            runs = ((label, TEXT, False), *param_runs(ps, limit, lang))
            cell = layout_runs(runs, columns[3][1])
            self._row([EMPTY_CELL, EMPTY_CELL, EMPTY_CELL, cell, EMPTY_CELL], shade)

    # --- host ------------------------------------------------------------------

    def host(self, host: HostReport) -> None:
        self._close_page()  # the previous host's last footer still shows that host
        self.host_name = host.name
        self.host_page = 0
        self._new_page()
        self.canvas.bookmarkPage(host.name)
        self.canvas.addOutlineEntry(host.name, host.name, level=0)
        self._text(self.s.margin, host.name, LABEL_BOLD, TITLE_SIZE, black)
        self.y -= TITLE_SIZE + 8
        if host.error:
            self._paragraph(text(self.s.language, "collect_error", error=host.error), ERROR)
            return
        for label, value in info_rows(host, self.s.language):
            self._info_row(label, value)
        self.y -= 6
        if not host.services:
            self._paragraph(empty_services_text(self.s), black)
            return
        self._table_header()
        for index, svc in enumerate(host.services):
            self._service(svc, shade=bool(index % 2))


def render_hosts(
    hosts: Sequence[HostReport],
    path: Path,
    generated_at: datetime,
    settings: Settings | None = None,
) -> None:
    """Write the hosts, in the given order, into one PDF file."""
    writer = _Writer(path, generated_at, settings or Settings())
    for host in hosts:
        writer.host(host)
    writer.close()
