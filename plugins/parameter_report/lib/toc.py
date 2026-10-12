"""Table of contents: a clickable list of all hosts after the cover page.

Page numbers are known only after the host chunks are rendered. The number of
contents pages follows from the layout alone, so report.py can compute the
final page numbers first, render the contents with them and add the links
after merging (render() returns where each entry was drawn).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .i18n import text
from .settings import MM, Settings

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
FONT_SIZE = 8
LINE = 11.0  # points per entry
HEADING_SIZE = 14
HEADING_SPACE = 24.0  # reserved on every page, so all pages hold the same number of rows
COLUMN_MIN = 80 * MM
COLUMN_GAP = 6 * MM
FOOTER_Y = 7 * MM
FOOTER_SPACE = 4 * MM
ERROR = Color(0.75, 0.1, 0.1)  # hosts with errors
ERROR_RGB = b".75 .1 .1 rg"  # how reportlab writes ERROR into the page (tests)
ELLIPSIS = "…"
LEADER = " ."
LEADER_COLOR = Color(0.6, 0.6, 0.6)

Rect = tuple[float, float, float, float]
PageLinks = list[tuple[Rect, int]]  # clickable area and 0-based target page


@dataclass(frozen=True)
class Entry:
    host: str
    page: int  # 1-based page number in the final report
    failed: bool = False
    appendix: bool = False  # after the hosts, separated by one empty row


@dataclass(frozen=True)
class Layout:
    columns: int
    rows: int
    column_width: float

    @property
    def per_page(self) -> int:
        return self.columns * self.rows


def layout(settings: Settings) -> Layout:
    width, height = settings.page
    usable_width = width - 2 * settings.margin
    columns = max(1, int((usable_width + COLUMN_GAP) // (COLUMN_MIN + COLUMN_GAP)))
    column_width = (usable_width - (columns - 1) * COLUMN_GAP) / columns
    bottom = max(settings.margin, FOOTER_Y + FOOTER_SPACE)  # clear of the footer line
    rows = max(1, int((height - settings.margin - HEADING_SPACE - bottom) // LINE))
    return Layout(columns, rows, column_width)


def page_count(hosts: int, settings: Settings, appendix: int = 0) -> int:
    slots = hosts + (appendix + 1 if appendix else 0)
    return math.ceil(slots / layout(settings).per_page) if slots > 0 else 0


def _slots(entries: Sequence[Entry]) -> list[Entry | None]:
    hosts = [e for e in entries if not e.appendix]
    appendix = [e for e in entries if e.appendix]
    return [*hosts, None, *appendix] if appendix else hosts


def _fit(text: str, width: float) -> str:
    if stringWidth(text, FONT, FONT_SIZE) <= width:
        return text
    while text and stringWidth(text + ELLIPSIS, FONT, FONT_SIZE) > width:
        text = text[:-1]
    return text + ELLIPSIS


def _leader(canvas: Canvas, start: float, end: float, y: float) -> None:
    """Dots from the end of the name to the page number, for the eye to follow."""
    dot = stringWidth(LEADER, FONT, FONT_SIZE)
    count = int((end - start - MM) // dot)
    if count > 0:
        canvas.setFillColor(LEADER_COLOR)
        canvas.drawRightString(end + MM, y, LEADER * count)


def render(entries: Sequence[Entry], path: Path, settings: Settings) -> list[PageLinks]:
    """Draw the entries column by column; returns the links of every page."""
    grid = layout(settings)
    width, height = settings.page
    canvas = Canvas(str(path), pagesize=settings.page, pageCompression=1)
    top = height - settings.margin - HEADING_SPACE
    slots = _slots(entries)
    links: list[PageLinks] = []
    for start in range(0, len(slots), grid.per_page):
        page_links: PageLinks = []
        if not links:
            canvas.setFont(FONT, HEADING_SIZE)
            heading = text(settings.language, "contents")
            canvas.drawString(settings.margin, height - settings.margin - HEADING_SIZE, heading)
        for index, entry in enumerate(slots[start : start + grid.per_page]):
            if entry is None:  # empty row before the appendix entries
                continue
            font = BOLD if entry.appendix else FONT
            canvas.setFont(font, FONT_SIZE)
            column, row = divmod(index, grid.rows)
            x = settings.margin + column * (grid.column_width + COLUMN_GAP)
            y = top - (row + 1) * LINE
            number = str(entry.page)
            room = grid.column_width - stringWidth(number, font, FONT_SIZE) - 2 * MM
            name = _fit(entry.host, room)
            canvas.setFillColor(ERROR if entry.failed else black)
            canvas.drawString(x, y, name)
            canvas.drawRightString(x + grid.column_width, y, number)
            _leader(canvas, x + stringWidth(name, font, FONT_SIZE), x + room, y)
            page_links.append(((x, y - 2, x + grid.column_width, y + LINE - 2), entry.page - 1))
        canvas.setFillColor(black)
        if settings.classification:
            canvas.setFont(FONT, 7)
            canvas.drawCentredString(width / 2, FOOTER_Y, settings.classification)
        canvas.showPage()
        links.append(page_links)
    canvas.save()
    return links
