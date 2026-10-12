"""Command line entry point of cmk-parameter-report.

The CLI is the only way a report is built: a GUI page starts it as a
process, reads the status file and offers the finished PDFs.
"""

from __future__ import annotations

import argparse
import importlib
import multiprocessing
import os
import sys
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import config_file, mailer, profiles, runstate
from .model import (
    CollectResult,
    HostReport,
    ReportSummary,
    only_differences,
    only_rule_values,
    used_timeperiods,
)
from .report import ReportError, build_report, terminate, worker_init
from .settings import Settings, SettingsError, build_settings

COLLECT_TARGET = "cmk_addons.plugins.parameter_report.lib.collector:collect"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse would exit with 2, which means "hosts with errors" here
        raise SettingsError(f"Invalid call: {message}")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="cmk-parameter-report",
        description="PDF report of the effective parameters of all hosts of this site.",
        epilog="All settings are read from ~/etc/parameter_report.toml (the base), optionally "
        "with a profile from ~/etc/parameter_report.d/ (--profile), or from one complete file "
        "(--config); edited on the web page Setup > Maintenance > Check Parameter Report.",
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument(
        "--profile",
        metavar="NAME",
        help="profile from ~/etc/parameter_report.d/ on top of the base",
    )
    source.add_argument(
        "--config", type=Path, help="complete TOML file instead of ~/etc/parameter_report.toml"
    )
    p.add_argument(
        "--mail",
        action="store_true",
        help="mail the report to the recipients of the base configuration or profile "
        "([schedule] recipients)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="print timings and peak memory")
    return p


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _settings_from_args(argv: Sequence[str] | None, root: Path) -> tuple[Settings, bool]:
    argv = list(sys.argv[1:] if argv is None else argv)
    values = vars(_parser().parse_args(argv))
    recipients: list[str] = []
    if values["mail"]:
        if values["config"] is not None:
            raise SettingsError("--mail cannot be used with --config (run files carry mail_to)")
        if values["profile"] is not None:
            profiles.check_name(values["profile"])
        schedule = profiles.load_schedule(root, values["profile"])
        if not schedule or not schedule.recipients:
            raise SettingsError("--mail: no recipients configured ([schedule] recipients)")
        recipients = list(schedule.recipients)
    if values["config"] is not None:
        file_values = config_file.settings_part(config_file.load(values["config"]))
    else:
        if values["profile"] is not None:
            profiles.check_name(values["profile"])  # "" must not fall back to the base
        file_values = profiles.resolve(root, values["profile"])
    if recipients:
        file_values["mail_to"] = recipients
    return build_settings(file_values), values["verbose"]


def _call(target: str, kwargs: dict[str, object]) -> tuple[CollectResult, int]:
    """Run the collector; returns its result and the peak RSS (MB) of this process."""
    module_name, function_name = target.split(":")
    result = getattr(importlib.import_module(module_name), function_name)(**kwargs)
    return result, runstate.peak_rss_mb()


def _collect(settings: Settings) -> tuple[CollectResult, int]:
    kwargs: dict[str, object] = {
        "host_names": list(settings.hosts) or None,
        "folders": settings.folders,
        "host_tags": settings.host_tags,
        "host_labels": settings.host_labels,
    }
    if settings.collect_mode == "inline":
        return _call(COLLECT_TARGET, kwargs)
    # Own process: the Checkmk configuration is freed before rendering starts
    pool = ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn"), initializer=worker_init
    )
    try:
        collected = pool.submit(_call, COLLECT_TARGET, kwargs).result()
    except BaseException:
        terminate(pool)
        raise
    pool.shutdown(wait=True)
    return collected


def main(argv: Sequence[str] | None = None) -> int:
    if not os.environ.get("OMD_SITE"):
        _err("Error: cmk-parameter-report must run as site user.")
        return 1
    root = Path(os.environ["OMD_ROOT"])
    try:
        settings, verbose = _settings_from_args(argv, root)
    except SettingsError as exc:
        _err(f"Error: {exc}")
        return 1
    now = datetime.now()
    output = settings.output or runstate.default_output(root, now)
    status_path = settings.status_file or runstate.default_status_file(root)
    for label, directory in (("Output directory", output.parent), ("Status file", status_path)):
        if label == "Status file":
            directory = directory.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if not os.access(directory, os.W_OK):
                raise PermissionError("not writable")
        except OSError as exc:
            name = output.parent if label == "Output directory" else status_path
            _err(f"Error: {label} {name} not usable: {exc}")
            return 1
    try:
        with runstate.RunLock(runstate.lock_path(root)):
            return _run(settings, output, status_path, now, verbose)
    except runstate.LockedError as exc:
        _err(f"Error: {exc}")
        return 1


def _finish(status: runstate.StatusFile | None, state: str, message: str) -> None:
    if status is None:
        return
    try:
        status.update(state=state, exit_code=1, message=message)
    except OSError:
        pass  # the status file itself is broken; stderr still has the message


def _run(settings: Settings, output: Path, status_path: Path, now: datetime, verbose: bool) -> int:
    site = os.environ["OMD_SITE"]
    started_by = settings.started_by or os.environ.get("USER") or site
    # Handlers first: a cancel right after the start must still end in a final state
    previous = runstate.install_cancel_handlers()
    status = None
    try:
        status = runstate.StatusFile(status_path, started_by=started_by, output=output)
        runstate.cleanup_stale_temp(output.parent, site)
        return _build(settings, output, status, now, started_by, verbose)
    except runstate.Cancelled as exc:
        _finish(status, "cancelled", f"cancelled ({exc})")
        _err("Cancelled.")
        return 1
    except Exception as exc:
        # Every run must leave a final state for callers that poll the status file
        _finish(status, "failed", f"Unexpected error: {exc}")
        _err(f"Error: {exc}")
        return 1
    finally:
        runstate.restore_handlers(previous)


def _fail(status: runstate.StatusFile, message: str) -> int:
    status.update(state="failed", exit_code=1, message=message)
    _err(f"Error: {message}")
    return 1


def _base_retention() -> int:
    """retention_days of the base: site-wide, whatever a --config file says."""
    try:
        return build_settings(profiles.base_values(Path(os.environ["OMD_ROOT"]))).retention_days
    except SettingsError:
        return 0  # broken base: delete nothing


def _build(
    settings: Settings,
    output: Path,
    status: runstate.StatusFile,
    now: datetime,
    started_by: str,
    verbose: bool,
) -> int:
    status.update(state="collecting")
    try:
        result, collect_peak = _collect(settings)
    except Exception as exc:
        return _fail(status, f"Could not load the configuration: {exc}")
    peaks = {"collect": collect_peak}
    for name in result.unknown_hosts:
        _err(f"Warning: unknown host: {name}")
    if result.untested_version:
        _err(f"Warning: Checkmk {result.version} is not tested with this report.")
    hosts: tuple[HostReport, ...] = only_differences(result.hosts)
    if settings.changed_only:
        hosts = only_rule_values(hosts)
    if not hosts:
        return _fail(status, "No hosts found for the report.")

    failed = tuple((h.name, h.error) for h in hosts if h.error)
    summary = ReportSummary(
        site=result.site,
        version=result.version,
        edition=result.edition,
        generated_at=now,
        host_count=len(hosts),
        service_count=sum(len(h.services) for h in hosts),
        failed_hosts=failed,
        timeperiods=used_timeperiods(hosts, result.timeperiods),
    )

    def progress(done: int, total: int) -> None:
        state = "merging" if done == total else "rendering"
        status.update(state=state, phase_progress=[done, total])

    status.update(state="rendering", phase_progress=[0, len(hosts)])
    try:
        built = build_report(hosts, summary, output, settings, progress=progress)
    except (ReportError, OSError) as exc:
        return _fail(status, str(exc))
    timings = {**result.timings, **built.timings}
    peaks["main"] = runstate.peak_rss_mb()
    peaks["render_worker"] = built.worker_peak_mb

    exit_code = 2 if failed else 0
    metadata: dict[str, object] = {
        "site": os.environ["OMD_SITE"],
        "settings": asdict(settings),
        "output": output,
        "started_by": started_by,
        "started_at": now.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "exit_code": exit_code,
        "hosts": summary.host_count,
        "services": summary.service_count,
        "failed_hosts": [list(item) for item in failed],
        "timings": {phase: round(seconds, 2) for phase, seconds in timings.items()},
        "peak_rss_mb": peaks,
    }
    # Before mailing: the report must be visible even if the mail step hangs or is killed
    runstate.write_metadata(output, metadata)

    if settings.mail_to:
        try:
            mail_settings = profiles.load_mail(Path(os.environ["OMD_ROOT"]))
        except SettingsError as exc:
            mail = {
                "to": list(settings.mail_to),
                "sent": False,
                "attached": False,
                "error": f"mail settings: {exc}",
            }
        else:
            mail = mailer.send_report(output, summary, settings, mail_settings)
        if mail["error"]:
            _err(f"Warning: mail not sent: {mail['error']}")
        runstate.write_metadata(output, {**metadata, "mail": mail})
    runstate.apply_retention(output.parent, os.environ["OMD_SITE"], _base_retention())
    message = f"{len(failed)} host(s) with errors, see cover page" if failed else None
    status.update(state="done", exit_code=exit_code, message=message)

    if verbose:
        for phase, seconds in timings.items():
            _err(f"{phase:14s} {seconds:7.2f}s")
        for scope in ("main", "collect", "render_worker"):
            _err(f"peak {scope:13s} {peaks[scope]:5d} MB")
    print(output)
    if failed:
        _err(f"Warning: {message}.")
    return exit_code
