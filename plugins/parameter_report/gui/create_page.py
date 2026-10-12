"""Create report page: choose the base or a profile and start a report."""

from __future__ import annotations

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
from ..lib import profiles
from ..lib.settings import SettingsError
from .common import CSS, PERMISSION, plain, root
from .config_page import CONFIG_PAGE

TITLE = "Create report"
CREATE_PAGE = "parameter_report_create.py"
REPORT_PAGE = "parameter_report.py"


def _run_profile(base: Path) -> tuple[str | None, str | None]:
    """(profile to run or None for the base, error message)"""
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


def _has_recipients(base: Path, profile: str | None) -> bool:
    try:
        schedule = profiles.load_schedule(base, profile)
    except (OSError, SettingsError, profiles.ProfileError):
        return False
    return bool(schedule and schedule.recipients)


def _start(base: Path) -> None:
    profile = request.get_str_input("profile", "") or None
    if profile:
        profiles.check_name(profile)
    backend.start_profile(base, str(user.id), profile, send_mail=bool(request.var("send_mail")))


def _render_form(run_profile: str | None, base: Path, active: bool) -> None:
    with html.form_context("parameter_report_start", method="POST"):
        html.hidden_field("_action", "start")
        forms.header("Profile", isopen=True)
        forms.section("Profile")
        choices = [("", "Base")] + [(p, p) for p in profiles.list_profiles(base)]
        html.dropdown(
            "profile",
            choices,
            deflt=run_profile or "",
            onchange=f"location.href='{CREATE_PAGE}?profile='+encodeURIComponent(this.value)",
        )
        edit = makeuri_contextless(request, [("profile", run_profile or "")], CONFIG_PAGE)
        html.a("Edit", href=edit, class_="pr_hint")
        try:
            summary = backend.profile_summary(base, run_profile)
        except (OSError, SettingsError):
            summary = []  # the configuration warning above already names the error
        for label, value in summary:
            forms.section(label)
            html.write_text(value)
        if _has_recipients(base, run_profile):
            forms.section("Mail")
            html.checkbox("send_mail", False, label="Send by mail to the profile's recipients")
        forms.end()
        html.open_div(class_="pr_top")
        if active:
            html.span(
                "A report is running. Wait until it has finished or cancel it.", class_="pr_note"
            )
        else:
            html.button("_start", "Create report", cssclass="hot")
        html.buttonlink(REPORT_PAGE, "Back to reports")
        html.close_div()
        html.span(
            "Settings are taken from the base configuration or the profile; change them on the "
            "Configuration page.",
            class_="pr_hint",
        )
        html.hidden_fields()


class ParameterReportCreatePage(Page):
    def page(self, ctx: PageContext) -> PageResult:
        user.need_permission(PERMISSION)
        base = root()
        error = None
        if request.var("_action") == "start" and transactions.check_transaction():
            check_csrf_token()
            try:
                _start(base)
            except (SettingsError, backend.StartError, OSError) as exc:
                error = str(exc)
            else:  # the report page shows the progress
                raise HTTPRedirect(
                    makeuri_contextless(request, [("_message", "started")], REPORT_PAGE)
                )
        breadcrumb = make_simple_page_breadcrumb(main_menu_registry.menu_setup(), TITLE)
        make_header(html, TITLE, breadcrumb)
        html.write_html(HTML.without_escaping(f"<style>{CSS}</style>"))
        if error:
            html.show_error(plain(error))
        run_profile, run_error = _run_profile(base)
        if run_error:
            html.show_warning(plain(run_error))
        _, config_error = backend.effective_defaults(base, run_profile)
        if config_error and run_profile:
            html.show_warning(plain(f"Profile {run_profile}: {config_error}"))
        elif config_error:
            html.show_warning(plain(f"Configuration file: {config_error}"))
        active = bool(backend.status_summary(backend.read_status(base))["active"])
        _render_form(run_profile, base, active)
        html.footer()
        return None
