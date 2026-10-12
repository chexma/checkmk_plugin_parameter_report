"""Cover page, time periods appendix (Platypus) and texts shared with render_canvas.py.

render_canvas.py draws the host pages. Works only on model.py and settings.py,
so it runs in worker processes and in tests without a Checkmk site.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .i18n import text, timestamp
from .model import (
    HostReport,
    ParamSet,
    ReportSummary,
    TimePeriod,
    covers_whole_day,
    day_groups,
    display_name,
)
from .settings import Settings

DARK = colors.HexColor("#1f3a5f")

_BASE = getSampleStyleSheet()
TITLE = ParagraphStyle("title", parent=_BASE["Heading1"], fontSize=14, spaceAfter=2 * mm)
CELL = ParagraphStyle("cell", parent=_BASE["BodyText"], fontSize=7, leading=8.5)

INFO_STYLE = TableStyle(
    [
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), DARK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
)
HEADER_KEYS = ("column_service", "column_plugin", "column_item", "column_parameter")
HEADER_KEYS += ("column_service_groups",)


def title(settings: Settings) -> str:
    return settings.cover_title or text(settings.language, "title")


def truncate(value: str, max_chars: int, language: str) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]} \u2026 {text(language, 'truncated')}"


def format_value(value: object, _nested: bool = False) -> str:
    """Human readable form of a parameter value (tuples as 'a / b')."""
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    match value:
        # Password fields after Checkmk's postprocessing: never print the secret
        case ("cmk_postprocessed", "explicit_password", _):
            return "***"
        case ("cmk_postprocessed", "stored_password", (password_id, _)):
            return f"Password store: {password_id}"
    if isinstance(value, (tuple, list)):
        if not value:
            return "–"
        text = " / ".join(format_value(v, True) for v in value)
        return f"({text})" if _nested else text
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k}: {format_value(v)}" for k, v in value.items()) + "}"
    return str(value)


def always_prefix(ps: ParamSet, language: str) -> str:
    """Start of the standard row when always active time periods are counted in."""
    if not ps.always:
        return ""
    names = ", ".join(display_name(name, alias) for name, alias in ps.always)
    return f"[{text(language, 'always_active')}: {names}] "


def _p(text: str, style: ParagraphStyle = CELL) -> Paragraph:
    return Paragraph(escape(text), style)


def _join(values: Sequence[str]) -> str:
    return ", ".join(values) or "–"


def _pairs(mapping: Mapping[str, str], sources: Mapping[str, str] | None = None) -> str:
    sources = sources or {}
    parts = []
    for key, value in sorted(mapping.items()):
        source = f" ({sources[key]})" if key in sources else ""
        parts.append(f"{key}:{value}{source}")
    return _join(parts)


def info_rows(host: HostReport, language: str = "en") -> list[tuple[str, str]]:
    """Label and text of the host head block."""
    if host.addresses:
        addresses = ", ".join(host.addresses)
    else:
        addresses = "–" if host.is_cluster else text(language, "via_dns")
    rows = [
        ("addresses", addresses),
        ("tags", _pairs(host.tags)),
        ("labels", _pairs(host.labels, host.label_sources)),
        ("host_groups", _join(host.hostgroups)),
        ("contact_groups", _join(host.contactgroups)),
        ("cluster_nodes" if host.is_cluster else "parents", _join(host.parents)),
        ("agent_mode", host.agent_mode or "–"),
        ("data_sources", _join(host.datasources)),
    ]
    return [(text(language, key), value) for key, value in rows]


def header(language: str) -> tuple[str, ...]:
    """Titles of the five service table columns."""
    return tuple(text(language, key) for key in HEADER_KEYS)


def empty_services_text(settings: Settings) -> str:
    return text(settings.language, "no_changed" if settings.changed_only else "no_services")


def footer_left(host: str, generated_at: datetime, settings: Settings) -> str:
    created = timestamp(generated_at, settings.date_format)
    return f"{host}  ·  {text(settings.language, 'footer_created', time=created)}"


FRONT_TITLE = ParagraphStyle(
    "front", parent=_BASE["Title"], fontSize=28, leading=34, alignment=0, spaceAfter=6 * mm
)
FRONT_SUBTITLE = ParagraphStyle(
    "front_sub", parent=_BASE["Heading2"], fontSize=16, leading=20, textColor=DARK
)
NOTE = ParagraphStyle("note", parent=_BASE["BodyText"], fontSize=10, leading=14)
NOTE_LABEL = ParagraphStyle("note_label", parent=NOTE, fontName="Helvetica-Bold", textColor=DARK)
HEADING = ParagraphStyle("heading", parent=_BASE["Heading2"], fontSize=12)
COVER_STYLE = TableStyle(
    [
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
)


def _cover_rows(summary: ReportSummary, settings: Settings) -> list[tuple[str, str]]:
    """Recipient details and report facts in one table; unset texts are left out."""
    lang = settings.language
    rows = [
        ("company", settings.cover_company),
        ("author", settings.cover_author),
        ("classification", settings.classification),
        ("created", timestamp(summary.generated_at, settings.date_format)),
        ("site", summary.site),
        ("checkmk", f"{summary.version} ({summary.edition})"),
        ("hosts", str(summary.host_count)),
        ("services", str(summary.service_count)),
    ]
    if settings.changed_only:
        rows.append(("mode", text(lang, "mode_changed_only")))
    return [(text(lang, key), value) for key, value in rows if value]


def render_cover(summary: ReportSummary, path: Path, settings: Settings | None = None) -> None:
    settings = settings or Settings()
    doc = BaseDocTemplate(
        str(path),
        pagesize=settings.page,
        leftMargin=settings.margin,
        rightMargin=settings.margin,
        topMargin=settings.margin,
        bottomMargin=settings.margin,
        title=title(settings),
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(doc.pagesize[0] / 2, 7 * mm, settings.classification)
        canvas.restoreState()

    extra = {"onPage": footer} if settings.classification else {}
    doc.addPageTemplates([PageTemplate(id="cover", frames=[frame], **extra)])
    lang = settings.language
    legend = (
        f'<font backcolor="{settings.highlight_color}">{escape(text(lang, "highlighted"))}'
        f"</font>{escape(text(lang, 'legend'))}"
    )
    story: list[object] = [
        Spacer(1, 10 * mm),
        Paragraph(escape(title(settings)), FRONT_TITLE),
    ]
    if settings.cover_customer:
        story.append(Paragraph(escape(settings.cover_customer), FRONT_SUBTITLE))
    story += [
        Spacer(1, 8 * mm),
        Table(
            [
                (_p(label, NOTE_LABEL), _p(value, NOTE))
                for label, value in _cover_rows(summary, settings)
            ],
            colWidths=[40 * mm, doc.width - 40 * mm],
            hAlign="LEFT",
            style=COVER_STYLE,
        ),
    ]
    if settings.cover_note:
        note = "<br/>".join(escape(line) for line in settings.cover_note.splitlines())
        story += [Spacer(1, 6 * mm), Paragraph(note, NOTE)]
    story += [Spacer(1, 6 * mm), Paragraph(legend, _BASE["BodyText"])]
    if summary.failed_hosts:
        limit = settings.max_cover_error_chars
        # the full error is on the host's own page; a cover row must fit on one page
        errors = [
            [_p(host), _p(truncate(error, limit, lang))] for host, error in summary.failed_hosts
        ]
        story += [
            Spacer(1, 8 * mm),
            Paragraph(
                escape(f"{text(lang, 'hosts_with_errors')} ({len(summary.failed_hosts)})"),
                HEADING,
            ),
            Table(
                errors,
                colWidths=[60 * mm, doc.width - 60 * mm],
                hAlign="LEFT",
                repeatRows=0,
                style=INFO_STYLE,
            ),
        ]
    doc.build(story)


def _range_text(ranges: Sequence[tuple[str, str]], language: str) -> str:
    if not ranges:
        return "–"
    if covers_whole_day(ranges):
        return text(language, "all_day")
    return ", ".join(f"{start}–{end}" for start, end in ranges)


def timeperiod_rows(
    period: TimePeriod, labels: Mapping[str, str], language: str
) -> list[tuple[str, str]]:
    """Label and text of one time period in the appendix."""
    if period.builtin:
        return [("", text(language, "builtin_always"))]
    if not period.defined:
        return [("", text(language, "undefined"))]
    rows = []
    for first, last, ranges in day_groups(period.days):
        days = text(language, first)
        if last != first:
            days += f"–{text(language, last)}"
        rows.append((days, _range_text(ranges, language)))
    for index, (date, ranges) in enumerate(period.exceptions):
        label = text(language, "exceptions") if index == 0 else ""
        rows.append((label, f"{date} {_range_text(ranges, language)}"))
    if period.exclude:
        names = ", ".join(labels.get(name, name) for name in period.exclude)
        rows.append((text(language, "excluded"), names))
    if period.always:
        rows.append(("", text(language, "always_active")))
    return rows or [("", "–")]  # defined, but without any time range


def render_timeperiods(periods: Sequence[TimePeriod], path: Path, settings: Settings) -> None:
    """Appendix: definitions of the time periods used in the report."""
    doc = BaseDocTemplate(
        str(path),
        pagesize=settings.page,
        leftMargin=settings.margin,
        rightMargin=settings.margin,
        topMargin=settings.margin,
        bottomMargin=settings.margin,
        title=title(settings),
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(doc.pagesize[0] / 2, 7 * mm, settings.classification)
        canvas.restoreState()

    extra = {"onPage": footer} if settings.classification else {}
    doc.addPageTemplates([PageTemplate(id="timeperiods", frames=[frame], **extra)])
    lang = settings.language
    labels = {p.name: p.label for p in periods}
    story: list[object] = [Paragraph(escape(text(lang, "time_periods")), TITLE)]
    for period in periods:
        rows = [[label, _p(value)] for label, value in timeperiod_rows(period, labels, lang)]
        story.append(
            KeepTogether(
                [
                    Spacer(1, 3 * mm),
                    Paragraph(escape(period.label), NOTE_LABEL),
                    Table(
                        rows,
                        colWidths=[40 * mm, doc.width - 40 * mm],
                        hAlign="LEFT",
                        style=INFO_STYLE,
                    ),
                ]
            )
        )
    doc.build(story)
