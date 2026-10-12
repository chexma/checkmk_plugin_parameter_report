"""Report page (current run, list of reports), status, download and registration.

Setup > Maintenance > Check Parameter Report; create_page.py starts reports.

Uses internal cmk.gui APIs of Checkmk 2.5 (no stable API exists for pages);
all logic lives in lib/gui_backend.py.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

from cmk.gui.breadcrumb import make_simple_page_breadcrumb
from cmk.gui.exceptions import HTTPRedirect
from cmk.gui.htmllib.header import make_header
from cmk.gui.htmllib.html import html
from cmk.gui.http import ContentDispositionType, request, response
from cmk.gui.logged_in import user
from cmk.gui.main_menu import main_menu_registry
from cmk.gui.pages import AjaxPage, Page, PageContext, PageEndpoint, PageResult, page_registry
from cmk.gui.permissions import (
    Permission,
    PermissionSection,
    permission_registry,
    permission_section_registry,
)
from cmk.gui.type_defs import IconNames, StaticIcon
from cmk.gui.utils.csrf_token import check_csrf_token
from cmk.gui.utils.escaping import escape
from cmk.gui.utils.html import HTML
from cmk.gui.utils.transaction_manager import transactions
from cmk.gui.utils.urls import makeuri_contextless
from cmk.gui.wato._main_module_topics import MainModuleTopicMaintenance
from cmk.gui.watolib.main_menu import ABCMainModule, MainModuleTopic, main_module_registry

from ..lib import gui_backend as backend
from ..lib import schedules
from ..lib.settings import SettingsError
from .common import CSS, PERMISSION, plain, root, state_result
from .config_page import CONFIG_PAGE, ParameterReportConfigPage
from .create_page import CREATE_PAGE, ParameterReportCreatePage

SECTION = PermissionSection(name="parameter_report", title="Check Parameter Report")
USE = Permission(
    section=SECTION,
    name="use",
    title="Use the Check Parameter Report",
    description="Create, download and delete parameter reports of this site.",
    defaults=["admin"],
)
TITLE = "Check Parameter Report"
CRON_NAME = "parameter_report_schedules"
PAGE = "parameter_report.py"

POLL_JS = """
(function () {
  if (!%(active)s) { return; }
  function poll() {
    fetch("ajax_parameter_report_status.py", {credentials: "same-origin"})
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var s = data.result;
        if (!s || typeof s !== "object") { setTimeout(poll, 3000); return; }
        document.getElementById("pr_title").textContent = s.title;
        document.getElementById("pr_progress").textContent = s.progress;
        document.getElementById("pr_bar").style.width = (s.percent || 0) + "%%";
        document.getElementById("pr_track").style.display = s.percent === null ? "none" : "";
        if (!s.active) { window.location.href = "parameter_report.py"; return; }
        setTimeout(poll, 3000);
      })
      .catch(function () { setTimeout(poll, 3000); });
  }
  setTimeout(poll, 3000);
})();
"""

# Messages after POST-redirect-GET: only these keys, never text from the URL
MESSAGES = {
    "started": "Report started.",
    "cancel": "Cancel requested.",
    "not_running": "No report is running.",
    "deleted": "Report deleted.",
}

# A click on a report's title unfolds the details of its row
DETAILS_JS = (
    "this.setAttribute('aria-expanded'," "this.closest('.pr_row').classList.toggle('pr_open'));"
)

# The status strip edge uses Checkmk's OK/WARN/CRIT colors under own class names (the
# theme styles .state0-3 itself); result pills in the reports table use the native
# state classes. Everything else inherits the theme, so facelift and modern-dark both work.
STATE_CLASS = {
    "": "pr_idle",
    "starting": "pr_warn",
    "collecting": "pr_warn",
    "rendering": "pr_warn",
    "merging": "pr_warn",
    "done": "pr_ok",
    "failed": "pr_crit",
    "cancelled": "pr_warn",
}


def _site() -> str:
    return os.environ["OMD_SITE"]


def _download_url(name: str) -> str:
    return makeuri_contextless(request, [("name", name)], filename="parameter_report_download.py")


class MainModuleParameterReport(ABCMainModule):
    @property
    def topic(self) -> MainModuleTopic:
        return MainModuleTopicMaintenance

    @property
    def mode_or_url(self) -> str:
        return PAGE

    @property
    def title(self) -> str:
        return TITLE

    @property
    def icon(self) -> StaticIcon:
        return StaticIcon(IconNames.report)

    @property
    def permission(self) -> str:
        return PERMISSION

    @property
    def description(self) -> str:
        return "PDF with the effective parameters of all hosts of this site"

    @property
    def sort_index(self) -> int:
        return 90

    @property
    def is_show_more(self) -> bool:
        return False


def _handle_action(action: str, root: Path, site: str) -> str:
    if action == "cancel":
        return "cancel" if backend.cancel_report(root) else "not_running"
    if action == "delete":
        backend.delete_report(root, site, request.get_str_input_mandatory("name"))
        return "deleted"
    raise backend.NotFound(action)


def _render_status(summary: dict[str, object], existing: set[str]) -> None:
    html.h2("Current run")
    state = "pr_crit" if summary["crashed"] else STATE_CLASS.get(str(summary["state"]), "pr_idle")
    html.open_div(class_=["pr_status", state], id_="pr_status")
    html.open_div(class_="pr_head")
    html.span(plain(summary["title"]), id_="pr_title")
    html.span(plain(summary["progress"]), id_="pr_progress")
    html.open_div(class_="pr_meta")
    if summary["started_at"]:
        html.write_text(f"Started {summary['started_at']} by {summary['started_by']}")
    if summary["message"]:
        html.write_text(f" · {summary['message']}")
    html.close_div()
    html.close_div()
    if summary["report"] in existing:  # not after the report was deleted
        html.buttonlink(_download_url(str(summary["report"])), "Download PDF")
    if summary["active"]:
        with html.form_context(
            "parameter_report_cancel",
            method="POST",
            onsubmit="return confirm('Cancel the running report?');",
        ):
            html.hidden_field("_action", "cancel")
            html.button("_cancel", "Cancel")
            html.hidden_fields()
    hide_track = "display:none" if summary["percent"] is None else None
    html.open_div(class_="pr_track", id_="pr_track", style=hide_track)
    html.div("", id_="pr_bar", style=f"width:{summary['percent'] or 0}%")
    html.close_div()
    html.close_div()
    if summary["crashed"]:
        html.show_warning(
            "The last run stopped unexpectedly (process ended). You can start a new report."
        )


def _render_top(active: bool) -> None:
    html.open_div(class_="pr_top")
    if active:
        html.span("A report is running. Wait until it has finished or cancel it.", class_="pr_note")
    else:
        html.buttonlink(CREATE_PAGE, "Create report", class_="hot")
    html.buttonlink(CONFIG_PAGE, "Configuration")
    html.close_div()


def _result_text(report: backend.ReportInfo) -> str:
    """What went wrong; empty for a report without errors (the badge says OK)."""
    parts = []
    if report.exit_code == 2:
        parts.append(f"{report.failed_hosts} host(s) with errors")
    elif report.exit_code != 0:
        parts.append(f"exit code {report.exit_code}")
    if report.mail and report.mail.get("error"):
        parts.append("mail failed")
    return ", ".join(parts)


def _mail_status(report: backend.ReportInfo) -> str:
    """Short mail outcome for the result line; empty without a mail step."""
    mail = report.mail
    if not mail:
        return ""
    if mail.get("error"):
        return "mail failed"
    return "mailed" if mail.get("attached") else "mailed without attachment"


def _mail_text(report: backend.ReportInfo) -> str:
    mail = report.mail
    if not mail:
        return ""
    if mail.get("error"):
        return f"failed: {mail['error']}"
    to = ", ".join(str(a) for a in mail.get("to") or [])  # type: ignore[attr-defined]
    if mail.get("attached"):
        return f"sent to {to}"
    return f"sent without attachment (size limit) to {to}"


def _values(values: Mapping[str, object], unit: str = "") -> str:
    parts = []
    for key, value in values.items():
        if isinstance(value, float):
            value = f"{value:.1f}"
        elif not isinstance(value, (str, int)):
            value = json.dumps(value)
        parts.append(f"{key} {value}{unit}")
    return " \u00b7 ".join(parts) or "\u2013"


def _details(report: backend.ReportInfo) -> None:
    """Folded part of a report row: settings, timings, memory and mail of the run."""
    settings = {
        k: v
        for k, v in report.settings.items()
        if k not in backend.OUTPUT_KEYS and k != "profile" and v not in (None, "", [], {})
    }
    rows = [
        ("Settings", ", ".join(f"{k}: {_setting(v)}" for k, v in settings.items()) or "\u2013"),
        ("Timings", _values(report.timings, " s")),
        ("Peak memory", _values(report.peaks, " MB")),
    ]
    if report.mail:
        rows.append(("Mail", _mail_text(report)))
    rows.append(("File", report.name))
    html.open_div(class_="pr_details")
    html.write_html(HTML.without_escaping("<dl>"))
    for label, value in rows:
        html.write_html(HTML.without_escaping(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>"))
    html.write_html(HTML.without_escaping("</dl>"))
    html.close_div()


def _count(number: int, noun: str) -> str:
    return f"{number:,} {noun}" + ("" if number == 1 else "s")


def _setting(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _report_row(index: int, report: backend.ReportInfo, latest: bool) -> None:
    html.open_div(class_=["pr_row", "pr_sel"] if latest else "pr_row", role="row")

    html.open_div(class_="pr_cell", role="cell")
    html.open_div(class_="pr_name")
    html.open_a(
        href="javascript:void(0)",
        class_="pr_toggle",
        onclick=DETAILS_JS,
        title="Show or hide the details of this run",
        **{"aria-expanded": "false"},
    )
    html.span(plain(report.title or TITLE), class_="pr_text")
    html.close_a()
    if report.profile:
        html.span(plain(report.profile), class_=["pr_tag", "pr_plain"], title="Profile")
    if latest:
        html.span("Latest", class_="pr_tag")
    html.close_div()
    by = f" \u00b7 by {report.started_by}" if report.started_by else ""
    html.div(plain(f"{report.created}{by}"), class_="pr_sub pr_num")
    html.close_div()

    html.open_div(class_="pr_cell", role="cell")
    html.div(
        plain(f"{_count(report.hosts, 'host')} \u00b7 {_count(report.services, 'service')}"),
        class_="pr_num",
    )
    if report.changed_only:
        html.div("only changed parameters", class_="pr_sub")
    html.close_div()

    html.open_div(class_="pr_cell", role="cell")
    state_result(backend.report_state(report), _result_text(report))
    facts = [backend.format_duration(report.duration), backend.format_size(report.size)]
    if mail := _mail_status(report):
        facts.append(mail)
    html.div(plain(" \u00b7 ".join(facts)), class_="pr_sub pr_num")
    html.close_div()

    html.open_div(class_="pr_cell pr_actions", role="cell")
    html.buttonlink(_download_url(report.name), "Download")
    with html.form_context(
        f"parameter_report_delete_{index}",
        method="POST",
        onsubmit="return confirm('Delete this report?');",
    ):
        html.hidden_field("_action", "delete")
        html.hidden_field("name", report.name)
        html.button("_delete", "Delete report")
        html.hidden_fields()
    html.close_div()

    _details(report)
    html.close_div()


def _render_reports(reports: list[backend.ReportInfo], latest: str | None) -> None:
    html.h2("Reports")
    if not reports:
        html.p("No reports yet. Created reports are listed here for download.")
        return
    html.open_div(class_=["pr_list", "pr_runs"], role="table", **{"aria-label": "Reports"})
    html.open_div(class_="pr_row pr_head", role="row")
    for title in ("Report", "Scope", "Result", ""):
        html.div(title, class_="pr_cell", role="columnheader")
    html.close_div()
    for index, report in enumerate(reports):
        _report_row(index, report, report.name == latest)
    html.close_div()


class ParameterReportPage(Page):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission(PERMISSION)
        base, site = root(), _site()
        error = None
        if request.has_var("_action") and transactions.check_transaction():
            check_csrf_token()
            try:
                message = _handle_action(request.get_str_input_mandatory("_action"), base, site)
            except (
                SettingsError,
                backend.StartError,
                backend.NotFound,
                OSError,
            ) as exc:
                error = str(exc) or "Not found"
            else:
                raise HTTPRedirect(makeuri_contextless(request, [("_message", message)], PAGE))
        breadcrumb = make_simple_page_breadcrumb(main_menu_registry.menu_setup(), TITLE)
        make_header(html, TITLE, breadcrumb)
        html.write_html(HTML.without_escaping(f"<style>{CSS}</style>"))
        if message := MESSAGES.get(request.get_str_input("_message") or ""):
            html.show_message(message)
        if error:
            html.show_error(plain(error))
        _, config_error = backend.effective_defaults(base)
        if config_error:
            html.show_warning(plain(f"Configuration file: {config_error}"))
        summary = backend.status_summary(backend.read_status(base))
        backend.prune_reports(base, site)
        reports = backend.list_reports(base, site)
        _render_top(bool(summary["active"]))
        _render_status(summary, {report.name for report in reports})
        _render_reports(reports, None if summary["active"] else summary["report"])
        html.javascript(POLL_JS % {"active": "true" if summary["active"] else "false"})
        html.footer()
        return None


class ParameterReportStatus(AjaxPage):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission(PERMISSION)
        return backend.status_summary(backend.read_status(root()))


class ParameterReportDownload(Page):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission(PERMISSION)
        try:
            path = backend.report_path(root(), _site(), request.get_str_input("name") or "")
        except backend.NotFound:
            response.status_code = 404
            response.set_data(b"Report not found")
            return None
        response.set_content_type("application/pdf")
        response.set_content_disposition(ContentDispositionType.ATTACHMENT, path.name)
        response.set_data(path.read_bytes())
        return None


def register() -> None:
    permission_section_registry.register(SECTION)
    permission_registry.register(USE)
    page_registry.register(PageEndpoint("parameter_report", ParameterReportPage()))
    page_registry.register(PageEndpoint("parameter_report_create", ParameterReportCreatePage()))
    page_registry.register(PageEndpoint("parameter_report_config", ParameterReportConfigPage()))
    page_registry.register(PageEndpoint("ajax_parameter_report_status", ParameterReportStatus()))
    page_registry.register(PageEndpoint("parameter_report_download", ParameterReportDownload()))
    main_module_registry.register(MainModuleParameterReport)
    try:
        from cmk.gui.cron import CronJob, cron_job_registry

        cron_job_registry.register(
            CronJob(
                name=CRON_NAME,
                callable=_run_schedules,
                interval=timedelta(minutes=1),
            )
        )
    except Exception:  # scheduler API changed: pages and manual runs keep working
        logging.getLogger("cmk.web").exception("parameter_report: scheduler not registered")


def _run_schedules(_config: object) -> None:
    try:
        schedules.run_due(root())
    except Exception:  # never raise into Checkmk's job scheduler
        logging.getLogger("cmk.web").exception("parameter_report: scheduled reports failed")


def scheduler_registered() -> bool:
    try:
        from cmk.gui.cron import cron_job_registry

        return CRON_NAME in cron_job_registry
    except Exception:
        return False
