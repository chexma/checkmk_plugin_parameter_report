"""Configuration page: base file (and, from Task 5, profiles) of the parameter report."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cmk.gui import forms
from cmk.gui.breadcrumb import make_simple_page_breadcrumb
from cmk.gui.exceptions import HTTPRedirect
from cmk.gui.htmllib.header import make_header
from cmk.gui.htmllib.html import html
from cmk.gui.http import request
from cmk.gui.logged_in import user
from cmk.gui.main_menu import main_menu_registry
from cmk.gui.pages import Page, PageContext, PageResult
from cmk.gui.utils.csrf_token import check_csrf_token
from cmk.gui.utils.html import HTML
from cmk.gui.utils.transaction_manager import transactions
from cmk.gui.utils.urls import makeuri_contextless

from ..lib import gui_backend as backend
from ..lib import mailer, profiles
from ..lib import schedule_config as sc
from ..lib import schedules
from ..lib.settings import SettingsError
from .common import (
    CSS,
    GROUPS,
    MORE_JS,
    PERMISSION,
    close_more_menu,
    field_sections,
    open_more_menu,
    plain,
    root,
    state_result,
)

TITLE = "Check Parameter Report configuration"
CONFIG_PAGE = "parameter_report_config.py"
REPORT_PAGE = "parameter_report.py"
MESSAGES = {
    "saved": "Configuration saved.",
    "profile_created": "Profile created.",
    "profile_copied": "Profile copied.",
    "profile_renamed": "Profile renamed.",
    "profile_deleted": "Profile deleted.",
    "started": "Report started.",
    "test_mail": "Test mail sent to the saved recipients.",
}


def _selected(base: Path) -> tuple[str | None, str | None]:
    """(profile being edited or None for the base, error message)"""
    name = request.get_str_input("profile", "") or ""
    if not name:
        return None, None
    try:
        profiles.check_name(name)
    except profiles.ProfileError as exc:
        return None, str(exc)
    if name not in profiles.list_profiles(base):
        return None, f"Profile {name} not found."
    return name, None


def _handle_action(action: str, base: Path) -> tuple[str, list[tuple[str, str]]]:
    """(message key, extra URL variables for the redirect)"""
    if action == "save":
        profile = request.get_str_input("profile", "") or None
        if profile:
            profiles.check_name(profile)
        if request.has_var("_test_mail"):  # button inside the form: sends, does not save
            backend.send_test_mail(base, profile)
            return "test_mail", [("profile", profile or "")]
        values = {f.name: request.get_str_input(f.name, "") or "" for f in backend.FORM_FIELDS}
        try:
            mtime = int(request.get_str_input("mtime", "0") or "0")
        except ValueError:
            mtime = -1  # tampered request: treated as a conflict
        description = (request.get_str_input("description", "") or "").strip()
        schedule_values = mail_values = None
        if request.has_var("sched_present"):  # the form rendered the fields (not in error state)
            schedule_values = {
                k: request.get_str_input("sched_" + k, "") or "" for k in sc.SCHEDULE_KEYS
            }
        if request.has_var("mail_present") and not profile:
            mail_values = {k: request.get_str_input("mail_" + k, "") or "" for k in sc.MAIL_KEYS}
        backend.save_config(
            values, base, str(user.id), mtime, profile, description, schedule_values, mail_values
        )
        return "saved", [("profile", profile or "")]
    if action == "profile":
        source = request.get_str_input("profile", "") or ""
        target = (request.get_str_input("name", "") or "").strip()
        if source:
            profiles.check_name(source)
        if request.has_var("_run_now"):
            backend.run_now(base, str(user.id), source or None)
            return "started", [("profile", source)]
        if request.has_var("_new"):
            profiles.create(base, target)
            return "profile_created", [("profile", target)]
        if request.has_var("_copy"):
            profiles.copy(base, source, target)
            return "profile_copied", [("profile", target)]
        if request.has_var("_rename"):
            profiles.rename(base, source, target)
            schedules.rename_state(base, source, target)
            return "profile_renamed", [("profile", target)]
        if request.has_var("_delete"):
            profiles.delete(base, source)
            schedules.drop_state(base, source)
            return "profile_deleted", []
    raise backend.NotFound(action)


@dataclass(frozen=True)
class _Row:
    schedule: str  # "" without an enabled schedule, "–" when not readable
    next_run: str
    last: tuple[str, str, str]  # state, time, result (backend.last_run)
    can_run: bool


def _schedule_cells(
    base: Path,
    name: str | None,
    state: dict[str, dict],
    newest: dict[str, backend.ReportInfo],
    active: bool,
) -> _Row:
    """Schedule, next run, last run and "can run now" of one entry."""
    dash = "\u2013"
    try:
        schedule = profiles.load_schedule(base, name)
    except (OSError, SettingsError):
        return _Row(dash, "", ("none", "", ""), False)
    key = name or ""
    last = backend.last_run(state.get(key), newest.get(key), active)
    if schedule is None:
        return _Row("", "", last, False)
    can_run = bool(schedule.recipients or schedule.enabled)
    if not schedule.enabled:
        return _Row("", "", last, can_run)
    try:
        from cmk.utils.schedule import next_scheduled_time

        next_run = datetime.fromtimestamp(
            next_scheduled_time(schedule.cmk_period(), schedule.timeofday())
        ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        next_run = dash
    return _Row(schedule.describe(), next_run, last, can_run)


def _newest_scheduled(base: Path) -> dict[str, backend.ReportInfo]:
    newest: dict[str, backend.ReportInfo] = {}
    for report in backend.list_reports(base, os.environ.get("OMD_SITE", "")):  # newest first
        if report.started_by == "scheduler":
            newest.setdefault(report.profile, report)
    return newest


def _run_now_form(index: str, name: str) -> None:
    with html.form_context(f"pr_profile_run_{index}", method="POST"):
        html.hidden_field("_action", "profile")
        html.hidden_field("profile", name)
        html.button("_run_now", "Run now")
        html.hidden_fields()


def _entry_row(
    index: str,
    name: str | None,
    description: str,
    row: _Row,
    selected: bool,
    invalid: bool = False,
) -> None:
    """One line of the tree: the base (name None) or a profile."""
    classes = ["pr_row", "pr_base" if name is None else "pr_child"]
    if selected:
        classes.append("pr_sel")
    html.open_div(class_=classes, role="row")
    url = makeuri_contextless(request, [("profile", name)] if name else [], CONFIG_PAGE)

    html.open_div(class_="pr_cell pr_entry", role="cell")
    html.open_div(class_="pr_name")
    html.a(plain(name or "Base (all reports)"), href=url)
    if selected:
        html.span("Editing", class_="pr_tag")
    html.close_div()
    if description:
        html.div(plain(description), class_=["pr_sub", "pr_invalid"] if invalid else "pr_sub")
    html.close_div()

    html.open_div(class_="pr_cell pr_sched", role="cell")
    if row.schedule:
        html.div(plain(row.schedule))
        if row.next_run:
            html.div(plain(f"next {row.next_run}"), class_="pr_sub pr_num")
    else:
        html.div("Not scheduled", class_="pr_sub")
    html.close_div()

    state, when, result = row.last
    html.open_div(class_="pr_cell pr_last", role="cell")
    if state == "none":
        html.div("Never run", class_="pr_sub")
    else:
        state_result(state, result, title=f"{when}: {result}")
        html.div(plain(when), class_="pr_sub pr_num")
    html.close_div()

    html.open_div(class_="pr_cell pr_actions", role="cell")
    html.buttonlink(url, "Edit")
    if row.can_run:
        _run_now_form(index, name or "")
    if name is not None:
        _more_menu(index, name)
    html.close_div()
    html.close_div()


def _more_menu(index: str, name: str) -> None:
    """Copy, rename and delete of one profile, folded away behind "More"."""
    open_more_menu(f"More actions for {name}")
    with html.form_context(f"pr_profile_{index}", method="POST"):
        html.hidden_field("_action", "profile")
        html.hidden_field("profile", name)
        html.text_input("name", "", size=18, placeholder="new name")
        html.open_div(class_="pr_menu_buttons")
        html.button("_copy", "Copy")
        html.button("_rename", "Rename")
        html.close_div()
        html.div("Cron jobs using the old name will fail.", class_="pr_sub")
        html.hidden_fields()
    with html.form_context(
        f"pr_profile_delete_{index}",
        method="POST",
        onsubmit="return confirm('Delete this profile?');",
    ):
        html.hidden_field("_action", "profile")
        html.hidden_field("profile", name)
        html.button("_delete", "Delete profile", cssclass="pr_danger")
        html.hidden_fields()
    close_more_menu()


def _render_table(base: Path, selected: str | None) -> None:
    html.h2("Base and profiles")
    state = schedules.load_state(base)
    newest = _newest_scheduled(base)
    active = backend.read_status(base).active
    html.open_div(
        class_=["pr_list", "pr_tree"], role="table", **{"aria-label": "Base and profiles"}
    )
    html.open_div(class_="pr_row pr_head", role="row")
    for title in ("Name", "Schedule", "Last run", ""):
        html.div(title, class_="pr_cell", role="columnheader")
    html.close_div()
    _entry_row(
        "base",
        None,
        "Applies to every report; profiles change only what they set.",
        _schedule_cells(base, None, state, newest, active),
        selected is None,
    )
    for index, name in enumerate(profiles.list_profiles(base)):
        try:
            description, invalid = profiles.load_profile(base, name)[1], False
        except SettingsError:
            description, invalid = "invalid \u2013 edit to repair", True
        row = _schedule_cells(base, name, state, newest, active)
        _entry_row(str(index), name, description, row, selected == name, invalid)
    html.open_div(class_="pr_row pr_child pr_new", role="row")
    with html.form_context("pr_profile_new", method="POST"):
        html.hidden_field("_action", "profile")
        html.text_input("name", "", size=20, placeholder="profile name")
        html.button("_new", "New profile")
        html.hidden_fields()
    html.close_div()
    html.close_div()
    html.javascript(MORE_JS)


def _render_form(base: Path, selected: str | None) -> None:
    try:
        form = backend.config_form(base, selected)
    except (OSError, SettingsError) as exc:
        form = backend.ConfigForm({}, 0, None)
        html.show_warning(plain(f"Configuration file cannot be read: {exc}"))
    if form.error and form.base_error:
        html.show_warning(plain(form.error))
    elif form.error:
        html.show_warning(
            plain(f"{form.error} Saving replaces the file; the old one is kept as .bak.")
        )
    html.h2(f"Profile {selected}" if selected else "Base (all reports)")
    placeholders = backend.inherited_settings(base, selected)
    with html.form_context("parameter_report_config", method="POST"):
        html.hidden_field("_action", "save")
        html.hidden_field("mtime", str(form.mtime))
        html.hidden_field("profile", selected or "")
        if selected:
            forms.header("Profile", isopen=True)
            forms.section("Description")
            html.text_input("description", form.description, size=50)
            forms.end()
        for title, group in GROUPS:
            if group != "run":
                # placeholders: what an empty field means, values: what the file sets
                field_sections(title, group, placeholders, group == "selection", form.values)
        if not form.error:  # in an error state no marker is sent: saving keeps the tables
            _schedule_sections(base, form, selected)
        else:
            html.show_warning("Schedule and mail cannot be edited until the file is repaired.")
        if not selected:  # advanced settings are site-wide (only the base sets them): last
            (title,) = (title for title, group in GROUPS if group == "run")
            field_sections(title, "run", placeholders, False, form.values)
        html.button("_save", "Save", cssclass="hot")
        if selected:
            hint = (
                f"Saving rewrites ~/etc/parameter_report.d/{selected}.toml; comments are not "
                f"kept, the previous version is saved as {selected}.toml.bak. "
                "Empty fields use the base value."
            )
        else:
            hint = (
                "Saving rewrites ~/etc/parameter_report.toml; comments are not kept, the "
                "previous version is saved as parameter_report.toml.bak."
            )
        html.span(hint, class_="pr_hint")
        html.hidden_fields()


def _schedule_sections(base: Path, form: backend.ConfigForm, selected: str | None) -> None:
    from .pages import scheduler_registered  # lazy: pages imports this module

    sched = form.schedule
    html.hidden_field("sched_present", "1")
    forms.header("Schedule and mail", isopen=False)
    if not mailer.sendmail_available(base):
        forms.section("Warning")
        html.show_warning(
            "sendmail is missing; install and configure a mail transfer agent "
            "(e.g. Postfix) on the host."
        )
    if not scheduler_registered():
        forms.section("Warning")
        html.show_warning(
            "Scheduler not available: scheduled reports do not run. Manual runs and cron work."
        )
    forms.section("Enabled")
    html.checkbox("sched_enabled", deflt=bool(sched.get("enabled")))
    forms.section("Period")
    html.dropdown(
        "sched_period",
        [
            ("day", "Daily"),
            ("week", "Weekly"),
            ("month_begin", "Monthly (day of month)"),
            ("month_end", "Monthly (counted from the end: 1 = last day)"),
        ],
        deflt=sched.get("period") or "day",
    )
    forms.section("Day")
    html.text_input("sched_day", sched.get("day", ""), size=5)
    html.span("Weekly: 0 = Monday \u2026 6 = Sunday. Monthly: 1..28.", class_="pr_hint")
    forms.section("Time")
    html.text_input("sched_time", sched.get("time", ""), size=8, placeholder="06:00")
    forms.section("Recipients")
    html.text_input("sched_recipients", sched.get("recipients", ""), size=60)
    html.span(
        "Separated by commas. Not inherited from the base configuration. "
        "Without recipients reports are created without mail.",
        class_="pr_hint",
    )
    forms.section("Test mail")
    if not sched.get("recipients"):
        html.span("Save recipients first.", class_="pr_hint")
    elif not mailer.sendmail_available(base):
        html.span("Needs sendmail (see the warning above).", class_="pr_hint")
    else:
        html.button("_test_mail", "Send test mail")
        html.span(
            "Short mail without a report to the saved recipients; unsaved changes are not used.",
            class_="pr_hint",
        )
    forms.end()
    if not selected and form.mail:
        html.hidden_field("mail_present", "1")
        forms.header("Mail settings", isopen=False)
        forms.section("Sender address")
        try:
            sender_hint = mailer.default_sender()
        except Exception:
            sender_hint = ""
        html.text_input(
            "mail_sender", form.mail.get("sender", ""), size=40, placeholder=sender_hint
        )
        forms.section("Maximum attachment size (MB)")
        html.text_input(
            "mail_max_attachment_mb",
            form.mail.get("max_attachment_mb", ""),
            size=6,
            placeholder="10",
        )
        forms.end()


class ParameterReportConfigPage(Page):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission(PERMISSION)
        base = root()
        error = None
        if request.has_var("_action") and transactions.check_transaction():
            check_csrf_token()
            try:
                message, extra = _handle_action(request.get_str_input_mandatory("_action"), base)
            except (
                SettingsError,
                backend.NotFound,
                backend.ConfigConflict,
                backend.StartError,
                OSError,
            ) as exc:
                error = str(exc) or "Not found"
            else:
                raise HTTPRedirect(
                    makeuri_contextless(request, [("_message", message), *extra], CONFIG_PAGE)
                )
        selected, select_error = _selected(base)
        breadcrumb = make_simple_page_breadcrumb(main_menu_registry.menu_setup(), TITLE)
        make_header(html, TITLE, breadcrumb)
        html.write_html(HTML.without_escaping(f"<style>{CSS}</style>"))
        if message := MESSAGES.get(request.get_str_input("_message") or ""):
            html.show_message(message)
        if error:
            html.show_error(plain(error))
        html.open_div(class_="pr_top")
        html.buttonlink(REPORT_PAGE, "Back to reports")
        html.close_div()
        if select_error:
            html.show_warning(plain(select_error))
        _render_table(base, selected)
        _render_form(base, selected)
        html.footer()
        return None
