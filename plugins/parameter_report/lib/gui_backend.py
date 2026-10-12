"""Logic of the web page, without any cmk.gui import.

gui/pages.py only renders and handles requests; everything that can be
tested without a running GUI lives here.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import config_file, profiles, runstate
from . import schedule_config as sc
from . import schedules
from .i18n import DATE_FORMATS, LANGUAGES
from .settings import (
    COLLECT_MODES,
    DEFAULT_CONFIG,
    GLOBAL_KEYS,
    PAGE_SIZES,
    Settings,
    SettingsError,
    build_settings,
)

TRUE_VALUES = frozenset({"on", "1", "true", "yes"})


@dataclass(frozen=True)
class Field:
    name: str  # Settings field
    title: str
    group: str  # selection | content | run | layout | cover
    kind: str  # text | textarea | list | pairs | int | float | bool | choice
    help: str = ""
    choices: tuple[str, ...] = ()


FORM_FIELDS: tuple[Field, ...] = (
    Field("hosts", "Hosts", "selection", "list", "Host names, separated by commas. Empty: all."),
    Field(
        "folders",
        "Folders",
        "selection",
        "list",
        "Setup folders incl. subfolders, e.g. linux/web. Several: any of them.",
    ),
    Field(
        "host_tags",
        "Host tags",
        "selection",
        "pairs",
        "GROUP:TAG, separated by commas. Several: all must match.",
    ),
    Field(
        "host_labels",
        "Host labels",
        "selection",
        "pairs",
        "KEY:VALUE, separated by commas. Several: all must match.",
    ),
    Field("changed_only", "Only changed parameters", "content", "bool"),
    Field(
        "language",
        "Language",
        "content",
        "choice",
        "Texts of the PDF and the mail; values from Checkmk stay as they are.",
        LANGUAGES,
    ),
    Field("date_format", "Date format", "content", "choice", "", tuple(DATE_FORMATS)),
    Field(
        "collect_mode",
        "Collect",
        "run",
        "choice",
        "subprocess: load the configuration in a separate process",
        COLLECT_MODES,
    ),
    Field("workers", "Render processes", "run", "int", "More: faster, but more memory."),
    Field("chunk_size", "Hosts per render chunk", "run", "int"),
    Field("retention_days", "Delete reports after (days)", "run", "int", "0 keeps them forever."),
    Field("page_size", "Page size", "layout", "choice", "", tuple(PAGE_SIZES)),
    Field("portrait", "Portrait", "layout", "bool"),
    Field("margin_mm", "Margin (mm)", "layout", "float"),
    Field("font_size", "Font size (pt)", "layout", "float"),
    Field("line_height", "Line height (pt)", "layout", "float"),
    Field(
        "column_widths",
        "Column widths",
        "layout",
        "text",
        "Characters for service, plugin, item, service groups.",
    ),
    Field("min_split_lines", "Minimum lines before a page break", "layout", "int"),
    Field("max_param_chars", "Maximum characters per value", "layout", "int"),
    Field(
        "max_cover_error_chars", "Maximum characters per error on the cover page", "layout", "int"
    ),
    Field("highlight_color", "Highlight color", "layout", "text", "#RRGGBB"),
    Field(
        "toc", "Table of contents", "layout", "bool", "Clickable list of all hosts after the cover."
    ),
    Field("cover_title", "Title", "cover", "text", "Empty: Check Parameter Report."),
    Field("cover_company", "Company", "cover", "text", "Company creating the report."),
    Field("cover_customer", "Customer", "cover", "text", "Customer or recipient."),
    Field("cover_author", "Author", "cover", "text", "Person creating the report."),
    Field("cover_note", "Note", "cover", "textarea", "Paragraph, e.g. purpose or scope."),
    Field(
        "classification",
        "Classification",
        "cover",
        "text",
        "E.g. Confidential; also in the footer of every page.",
    ),
)


OUTPUT_KEYS = ("output", "status_file", "started_by")  # not on the page; kept when saving


class ConfigConflict(Exception):
    """The configuration file changed after the page was shown."""


@dataclass(frozen=True)
class ConfigForm:
    values: dict[str, str]  # texts for the keys set in the file
    mtime: int
    error: str | None
    description: str = ""
    base_error: bool = False  # the error is in the base file, not in the edited profile
    schedule: dict[str, str] = dc_field(default_factory=dict)
    mail: dict[str, str] = dc_field(default_factory=dict)  # base only


def _config_path(root: Path, profile: str | None) -> Path:
    return profiles.profile_path(root, profile) if profile else root / DEFAULT_CONFIG


def config_mtime(root: Path, profile: str | None = None) -> int:
    try:
        return _config_path(root, profile).stat().st_mtime_ns
    except FileNotFoundError:
        return 0


def inherited_settings(root: Path, profile: str | None) -> Settings:
    """What an empty field of the Configuration form means."""
    if not profile:
        return build_settings()
    try:
        return build_settings(profiles.base_values(root))
    except SettingsError:
        return build_settings()


def config_form(root: Path, profile: str | None = None) -> ConfigForm:
    mtime = config_mtime(root, profile)
    names = {f.name for f in FORM_FIELDS}
    try:
        if profile:
            try:
                base = profiles.base_values(root)
                build_settings(base)
            except SettingsError as exc:
                return ConfigForm(
                    {},
                    mtime,
                    f"Base configuration: {exc} Fix the base first.",
                    "",
                    True,
                    sc.schedule_to_form(None),
                )
            own, description = profiles.load_profile(root, profile)
            settings = build_settings({**base, **own})
        else:
            own, description = profiles.base_values(root), ""
            settings = build_settings(own)
            mail = sc.mail_to_form(profiles.load_mail(root))
        schedule = sc.schedule_to_form(profiles.load_schedule(root, profile))
    except profiles.ProfileNotFound:
        raise
    except SettingsError as exc:
        return ConfigForm({}, mtime, str(exc), schedule=sc.schedule_to_form(None))
    values = {name: display_value(settings, name) for name in own if name in names}
    return ConfigForm(values, mtime, None, description, False, schedule, {} if profile else mail)


def _raw_tables(path: Path) -> dict[str, object]:
    """[schedule]/[mail] of a file that does not validate (e.g. an unknown key)."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {key: data[key] for key in config_file.TABLES if isinstance(data.get(key), dict)}


def save_config(
    values: Mapping[str, str],
    root: Path,
    user: str,
    expected_mtime: int,
    profile: str | None = None,
    description: str = "",
    schedule_values: Mapping[str, str] | None = None,
    mail_values: Mapping[str, str] | None = None,
) -> None:
    path = _config_path(root, profile)
    if profile and not path.is_file():
        raise profiles.ProfileNotFound(f"Profile {profile} not found.")
    if config_mtime(root, profile) != expected_mtime:
        raise ConfigConflict("The configuration was changed meanwhile. Reload the page.")
    new = form_values_to_cli(values)
    if profile:  # advanced settings are site-wide: only the base sets them
        new = {k: v for k, v in new.items() if k not in GLOBAL_KEYS}
    if profile:
        try:
            base = profiles.base_values(root)
            inherited = build_settings(base)
        except SettingsError as exc:
            raise SettingsError(f"Fix the configuration first: {exc}") from None
    else:
        base, inherited = {}, build_settings()
    kinds = {f.name: f.kind for f in FORM_FIELDS}
    for name in list(new):
        # Checkboxes and choices always submit a value; the inherited one is not stored
        if kinds[name] in ("bool", "choice") and new[name] == getattr(inherited, name):
            del new[name]
    try:
        old_raw = config_file.load(path, extra=("description",)) if path.exists() else {}
    except SettingsError:
        old_raw = _raw_tables(path)  # broken file: replaced (old one in .bak), tables kept
    tables: dict[str, dict[str, object]] = {}
    if schedule_values is None:
        if isinstance(old_raw.get("schedule"), dict):
            tables["schedule"] = old_raw["schedule"]
    else:
        schedule = sc.schedule_from_form(schedule_values)
        if schedule is not None:
            tables["schedule"] = sc.schedule_to_toml(schedule)
    if not profile:
        if mail_values is None:
            if isinstance(old_raw.get("mail"), dict):
                tables["mail"] = old_raw["mail"]
        else:
            tables["mail"] = sc.mail_to_toml(sc.mail_from_form(mail_values))
    header = f"Saved by {user} on {datetime.now():%Y-%m-%d %H:%M} via the web page"
    if profile:
        if (
            len(description) > profiles.MAX_DESCRIPTION
            or "\n" in description
            or "\r" in description
        ):
            raise SettingsError(
                f"description: one line of at most {profiles.MAX_DESCRIPTION} characters expected"
            )
        build_settings(None, {**base, **new})  # the profile must work on top of the base
        text = config_file.dump(new, header, description, tables=tables)
    else:
        try:
            old = profiles.base_values(root)
        except SettingsError:
            old = {}  # broken file: replaced, the old one stays in the .bak
        new.update({key: old[key] for key in OUTPUT_KEYS if key in old})
        build_settings(None, new)
        _check_profiles_with_base(root, new)
        text = config_file.dump(new, header, tables=tables)
    path.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_atomic(path, text, backup=True)


def _check_profiles_with_base(root: Path, base: Mapping[str, object]) -> None:
    broken = []
    for name in profiles.list_profiles(root):
        try:
            own, _description = profiles.load_profile(root, name)
        except SettingsError:
            continue  # already broken on its own; shown when it is edited
        try:
            build_settings(None, {**base, **own})
        except SettingsError as exc:
            broken.append(f"{name} ({exc})")
    if broken:
        raise SettingsError("The change would make these profiles invalid: " + ", ".join(broken))


def effective_defaults(root: Path, profile: str | None = None) -> tuple[Settings, str | None]:
    """Settings without form input: defaults, base file and profile."""
    try:
        return build_settings(profiles.resolve(root, profile)), None
    except SettingsError as exc:
        return build_settings(), str(exc)


def display_value(settings: Settings, name: str) -> str:
    value = getattr(settings, name)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if name in ("host_tags", "host_labels"):
        return ", ".join(f"{key}:{val}" for key, val in value)
    if isinstance(value, tuple):
        return ", ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def form_values_to_cli(values: Mapping[str, str]) -> dict[str, object]:
    """Form strings → values as build_settings expects them; empty = not set."""
    result: dict[str, object] = {}
    for field in FORM_FIELDS:
        raw = (values.get(field.name) or "").strip()
        if field.kind == "bool":
            # An unticked checkbox is not submitted at all: always explicit
            result[field.name] = raw.lower() in TRUE_VALUES
            continue
        if not raw:
            continue
        if field.kind in ("list", "pairs"):
            result[field.name] = _split(raw)
        elif field.kind == "int":
            try:
                result[field.name] = int(raw)
            except ValueError:
                raise SettingsError(f"{field.name}: integer expected, not {raw!r}") from None
        elif field.kind == "float":
            try:
                result[field.name] = float(raw)
            except ValueError:
                raise SettingsError(f"{field.name}: number expected, not {raw!r}") from None
        else:
            result[field.name] = raw
    return result


LOG_FILE = Path("var/log/parameter_report.log")
COMMAND = Path("local/bin/cmk-parameter-report")
START_TIMEOUT = 3.0
FINAL_STATES = frozenset({"done", "failed", "cancelled"})
STATE_TITLES = {
    "starting": "Starting",
    "collecting": "Loading the configuration and collecting hosts",
    "rendering": "Rendering",
    "merging": "Merging the PDF",
    "done": "Finished",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


class StartError(Exception):
    """The report could not be started; the message is shown to the user."""

    def __init__(self, message: str, spawned: bool = False, locked: bool = False) -> None:
        super().__init__(message)
        self.locked = locked  # another report holds the lock: nothing was started
        self.spawned = spawned  # the process was started, it may still read its run file


@dataclass(frozen=True)
class StatusView:
    data: Mapping[str, object]
    active: bool  # a report process is running
    crashed: bool  # status not final, but the process is gone


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _started_since(status_path: Path, started: float) -> bool:
    try:
        data = json.loads(status_path.read_text())
        started_at = datetime.fromisoformat(str(data["started_at"])).timestamp()
    except (OSError, ValueError, KeyError):
        return False
    return started_at >= started - 1  # status has second resolution


RUN_FILE_MAX_AGE = 86400  # run files without a report (failed start) are removed after this


def write_run_file(settings: Settings, root: Path, now: datetime, user: str) -> Path:
    """Complete settings of one web run, next to the report it will produce."""
    output = runstate.default_output(root, now)
    run = replace(
        settings, output=output, status_file=runstate.default_status_file(root), started_by=user
    )
    path = output.with_suffix(".toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"Run file written by {user} on {now:%Y-%m-%d %H:%M} via the web page"
    config_file.write_atomic(
        path, config_file.dump(config_file.to_values(run, False), header), False
    )
    return path


def start_profile(
    root: Path, user: str, profile: str | None = None, send_mail: bool = False
) -> None:
    """Start a report with the saved settings of the base or a profile."""
    _defaults, error = effective_defaults(root)
    if error:
        raise SettingsError(f"Fix the configuration first: {error}")
    if profile:
        try:
            profiles.load_profile(root, profile)
        except profiles.ProfileNotFound:
            raise
        except SettingsError as exc:
            raise SettingsError(f"Fix profile {profile} first: {exc}") from None
    settings = build_settings(profiles.resolve(root, profile))
    if send_mail:
        schedule = profiles.load_schedule(root, profile)
        if schedule is None or not schedule.recipients:
            raise SettingsError("No recipients configured for this profile")
        settings = replace(settings, mail_to=schedule.recipients)
    try:
        run_file = write_run_file(settings, root, datetime.now(), user)
    except OSError as exc:
        raise StartError(f"Run file not writable: {exc}") from None
    try:
        start_report(run_file, user, root)
    except StartError as exc:
        if not exc.spawned:
            run_file.unlink(missing_ok=True)
        raise


def _selection(settings: Settings) -> str:
    parts = []
    if settings.hosts:
        parts.append("hosts: " + ", ".join(settings.hosts))
    if settings.folders:
        parts.append("folders: " + ", ".join(settings.folders))
    if settings.host_tags:
        parts.append("tags: " + ", ".join(f"{k}:{v}" for k, v in settings.host_tags))
    if settings.host_labels:
        parts.append("labels: " + ", ".join(f"{k}:{v}" for k, v in settings.host_labels))
    text = "; ".join(parts) or "all hosts"
    if settings.changed_only:
        text += "; only changed parameters"
    return text


def profile_summary(root: Path, profile: str | None) -> list[tuple[str, str]]:
    """What a report of the base or a profile will contain, for the report page."""
    description = profiles.load_profile(root, profile)[1] if profile else ""
    settings = build_settings(profiles.resolve(root, profile))
    rows = [
        ("Description", description),
        ("Hosts", _selection(settings)),
        ("Title", settings.cover_title),
        ("Customer", settings.cover_customer),
        ("Company", settings.cover_company),
    ]
    schedule = profiles.load_schedule(root, profile)
    if schedule is not None and schedule.enabled:
        rows.append(("Schedule", schedule.describe()))
    if schedule is not None and schedule.recipients:
        rows.append(("Mail to", ", ".join(schedule.recipients)))
    return [(label, value) for label, value in rows if value]


def run_now(root: Path, user: str, profile: str | None) -> None:
    """Run a profile with its saved settings and mail it to its recipients now."""
    schedule = profiles.load_schedule(root, profile)
    if schedule is None or not (schedule.recipients or schedule.enabled):
        raise SettingsError("No schedule or recipients configured")
    _defaults, error = effective_defaults(root, profile)
    if error:
        raise SettingsError(error)
    schedules.start_scheduled(root, profile, schedule.recipients, user)


def send_test_mail(root: Path, profile: str | None) -> tuple[str, ...]:
    """Mail a short test text to the saved recipients of the base or a profile."""
    from . import mailer  # lazy: mailer pulls in the renderers

    schedule = profiles.load_schedule(root, profile)
    recipients = tuple(schedule.recipients) if schedule else ()
    if not recipients:
        raise SettingsError("No recipients configured: save recipients first")
    if not mailer.sendmail_available(root):
        raise SettingsError("sendmail is missing; install and configure a mail transfer agent")
    settings, _error = effective_defaults(root, profile)  # a broken file: default language
    try:
        mailer.send_test(
            recipients,
            settings.language,
            profiles.load_mail(root),
            site=os.environ.get("OMD_SITE", ""),
            entry=profile or "Base",
        )
    except Exception as exc:  # sendmail exit code, timeout
        raise SettingsError(f"Test mail failed: {str(exc) or type(exc).__name__}") from None
    return recipients


def start_report(config: Path, user: str, root: Path) -> None:
    """Start cmk-parameter-report detached from the web server."""
    try:
        with runstate.RunLock(runstate.lock_path(root)):
            pass
    except runstate.LockedError as exc:
        raise StartError(str(exc), locked=True) from None
    reports = runstate.reports_dir(root)
    reports.mkdir(parents=True, exist_ok=True)
    if not os.access(reports, os.W_OK):
        raise StartError(f"Report directory {reports} is not writable")
    log = root / LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    # Apache preloads its own OpenSSL; the report does not need that
    env = {key: value for key, value in os.environ.items() if key != "LD_PRELOAD"}
    command = ["setsid", "--fork", str(root / COMMAND), "--config", str(config)]
    started = time.time()
    with log.open("ab") as out:
        out.write(
            f"--- {datetime.now():%Y-%m-%d %H:%M:%S} started by {user}: {config.name}\n".encode()
        )
        out.flush()
        # setsid --fork returns at once; the report runs in its own session
        subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            cwd=root,
            env=env,
            check=False,
            timeout=10,
        )
    status_path = runstate.default_status_file(root)
    while time.time() < started + START_TIMEOUT:
        if _started_since(status_path, started):
            return
        time.sleep(0.1)
    raise StartError("The report did not start. Last log lines:\n" + _tail(log, 10), spawned=True)


def _is_report_process(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        owner = os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return False
    # Same user: another site's report on this host has its own site user
    return b"cmk-parameter-report" in cmdline and owner == os.getuid()


def read_status(root: Path) -> StatusView:
    try:
        data = json.loads(runstate.default_status_file(root).read_text())
    except (OSError, ValueError):
        return StatusView({}, False, False)
    if not isinstance(data, dict):
        return StatusView({}, False, False)
    if data.get("state") in FINAL_STATES:
        return StatusView(data, False, False)
    alive = _is_report_process(data.get("pid"))
    return StatusView(data, alive, not alive)


def status_summary(view: StatusView) -> dict[str, object]:
    data = view.data
    state = str(data.get("state", ""))
    if view.crashed:
        title = "Stopped unexpectedly (process ended)"
    elif not data:
        title = "No report has run yet"
    else:
        title = STATE_TITLES.get(state, state)
    progress, percent = "", None
    phase = data.get("phase_progress")
    if isinstance(phase, list) and len(phase) == 2 and phase[1]:
        done, total = phase
        progress, percent = f"{done} / {total} hosts", int(100 * done / total)
    output = data.get("output")
    return {
        "state": state,
        "title": title,
        "progress": progress,
        "percent": percent,
        "active": view.active,
        "crashed": view.crashed,
        "started_at": str(data.get("started_at", "")).replace("T", " "),
        "started_by": str(data.get("started_by", "")),
        "message": data.get("message"),
        "exit_code": data.get("exit_code"),
        "report": Path(str(output)).name if output and state == "done" else None,
    }


def cancel_report(root: Path) -> bool:
    """SIGTERM to the running report; never signals a process that is not the report."""
    view = read_status(root)
    if not view.active:
        return False
    try:
        os.kill(int(view.data["pid"]), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False  # ended meanwhile
    return True


REPORT_NAME = re.compile(r"^parameter_report_[A-Za-z0-9_.-]+\.pdf$")


class NotFound(Exception):
    """No report of this site with that name."""


@dataclass(frozen=True)
class ReportInfo:
    name: str
    created: str
    started_by: str
    hosts: int
    services: int
    changed_only: bool
    exit_code: int
    failed_hosts: int
    size: int
    settings: Mapping[str, object]
    timings: Mapping[str, object]
    peaks: Mapping[str, object]
    duration: float | None = None  # start to finish in seconds; None: incomplete metadata
    title: str = ""  # cover_title of the run
    profile: str = ""  # configuration profile of the run
    mail: Mapping[str, object] | None = None  # outcome of the mail step, if it ran


def result_text(report: ReportInfo) -> str:
    if report.exit_code == 0:
        text = "OK"
    elif report.exit_code == 2:
        text = "hosts with errors"
    else:
        text = "failed"
    if report.mail and report.mail.get("error"):
        text += ", mail failed"
    return text


def report_state(report: ReportInfo) -> str:
    """ok | warn | crit: hosts with errors or a failed mail make a report WARN."""
    if report.exit_code == 0:
        return "warn" if report.mail and report.mail.get("error") else "ok"
    return "warn" if report.exit_code == 2 else "crit"


def last_run(
    entry: Mapping[str, object] | None, report: ReportInfo | None, active: bool
) -> tuple[str, str, str]:
    """(state, time, result) of a schedule's last run; `report` is the newest scheduler report.

    state: none | ok | warn | crit | unknown | pending. The report only counts when it
    started at or after the recorded run (minus a minute); otherwise it belongs to an
    older run and must not be paired with the new time.
    """
    if not entry:
        return "none", "", ""
    run = float(entry["last_run"])  # type: ignore[arg-type]
    when = datetime.fromtimestamp(run).strftime("%Y-%m-%d %H:%M")
    if error := entry.get("last_error"):
        return ("warn" if str(error).startswith("skipped") else "crit"), when, str(error)
    if report is not None:
        try:
            started = datetime.fromisoformat(report.created).timestamp()
        except ValueError:
            started = None
        if started is not None and started >= run - 60:
            return report_state(report), when, result_text(report)
    if active:
        return "pending", when, "running"
    return "unknown", when, f"no report (see ~/{LOG_FILE})"


def _metadata(pdf: Path) -> dict[str, object] | None:
    try:
        data = json.loads(pdf.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _duration(meta: Mapping[str, object]) -> float | None:
    try:
        started = datetime.fromisoformat(str(meta["started_at"]))
        finished = datetime.fromisoformat(str(meta["finished_at"]))
    except (KeyError, ValueError):
        return None
    return max((finished - started).total_seconds(), 0.0)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def format_size(size: int) -> str:
    if size < 1_000_000:
        return f"{size // 1000} kB"
    return f"{size / 1_000_000:.1f} MB"


def prune_reports(root: Path, site: str) -> list[Path]:
    """Age-based retention of the base configuration, also when no report runs."""
    reports = runstate.reports_dir(root)
    days = effective_defaults(root)[0].retention_days
    removed = runstate.apply_retention(reports, site, days)
    cutoff = time.time() - RUN_FILE_MAX_AGE
    for run_file in reports.glob("parameter_report_*.toml"):
        try:
            orphan = not run_file.with_suffix(".pdf").exists()
            if orphan and run_file.stat().st_mtime < cutoff:
                run_file.unlink()
        except OSError:
            continue  # vanished meanwhile
    return removed


def list_reports(root: Path, site: str) -> list[ReportInfo]:
    reports = []
    for pdf in runstate.reports_dir(root).glob(runstate.REPORT_GLOB):
        meta = _metadata(pdf)
        try:
            size = pdf.stat().st_size
        except OSError:
            continue
        if meta is None or meta.get("site") != site:
            continue
        settings = meta.get("settings") if isinstance(meta.get("settings"), dict) else {}
        reports.append(
            ReportInfo(
                name=pdf.name,
                created=str(meta.get("started_at", "")).replace("T", " "),
                started_by=str(meta.get("started_by", "")),
                hosts=int(meta.get("hosts", 0)),
                services=int(meta.get("services", 0)),
                changed_only=bool(settings.get("changed_only")),
                exit_code=int(meta.get("exit_code", 0)),
                failed_hosts=len(meta.get("failed_hosts") or []),
                size=size,
                settings=settings,
                timings=meta.get("timings") or {},
                peaks=meta.get("peak_rss_mb") or {},
                duration=_duration(meta),
                title=str(settings.get("cover_title") or ""),
                profile=str(settings.get("profile") or ""),
                mail=meta.get("mail") if isinstance(meta.get("mail"), dict) else None,
            )
        )
    return sorted(reports, key=lambda r: r.created, reverse=True)


def report_path(root: Path, site: str, name: str) -> Path:
    """Path of a report of this site; rejects anything that is not a plain report name."""
    if not REPORT_NAME.fullmatch(name or ""):
        raise NotFound(name)
    pdf = runstate.reports_dir(root) / name
    meta = _metadata(pdf)
    if not pdf.is_file() or meta is None or meta.get("site") != site:
        raise NotFound(name)
    return pdf


def delete_report(root: Path, site: str, name: str) -> None:
    pdf = report_path(root, site, name)
    pdf.unlink(missing_ok=True)
    pdf.with_suffix(".json").unlink(missing_ok=True)
    pdf.with_suffix(".toml").unlink(missing_ok=True)
