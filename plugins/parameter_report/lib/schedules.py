"""Scheduled reports: which schedule is due, its state, and starting the run.

Runs every minute in Checkmk's ui-job-scheduler (registered in gui/pages.py),
like Checkmk's own report scheduler: due when the last scheduled time is
newer than the last run and at most an hour ago.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from . import config_file, profiles, runstate
from .schedule_config import Schedule
from .settings import SettingsError, build_settings

STATE_FILE = Path("var/parameter_report/schedule_state.json")
WINDOW = 3600  # seconds after the scheduled time in which a run is still started
logger = logging.getLogger("cmk.web.parameter_report")
Start = Callable[[Path, "str | None", Sequence[str]], None]


def load_state(root: Path) -> dict[str, dict]:
    try:
        data = json.loads((root / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: entry for key, entry in data.items() if _valid_entry(entry)}


def _valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    last_run, error = entry.get("last_run"), entry.get("last_error")
    return (
        isinstance(last_run, (int, float))
        and not isinstance(last_run, bool)
        and (error is None or isinstance(error, str))
    )


def save_state(root: Path, state: dict[str, dict]) -> None:
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_atomic(path, json.dumps(state, indent=2), backup=False)


def rename_state(root: Path, old: str, new: str) -> None:
    state = load_state(root)
    if old in state:
        state[new] = state.pop(old)
        save_state(root, state)


def drop_state(root: Path, name: str) -> None:
    state = load_state(root)
    if state.pop(name, None) is not None:
        save_state(root, state)


def _scheduled(schedule: Schedule, now: datetime) -> float:
    from cmk.utils.schedule import last_scheduled_time

    return last_scheduled_time(schedule.cmk_period(), schedule.timeofday(), now)


def due(schedule: Schedule, last_run: float, now: datetime) -> bool:
    if not schedule.enabled:
        return False
    scheduled = _scheduled(schedule, now)
    # 0 <= age: on the DST spring-forward day the scheduled time is normalised to a time
    # after "now"; without this a run would be started on every pass until then.
    return scheduled > last_run and 0 <= now.timestamp() - scheduled < WINDOW


def missed(schedule: Schedule, last_run: float, now: datetime, has_state: bool) -> bool:
    """The appointment passed without a run (the window closed, e.g. a long manual run).

    Recorded only when the entry already has a state entry (it ran or failed before) or the
    appointment lies within the last 24 h. A schedule that was just saved must not report
    an appointment of days ago, which it never could have kept.
    """
    if not schedule.enabled:
        return False
    scheduled = _scheduled(schedule, now)
    age = now.timestamp() - scheduled
    return scheduled > last_run and age >= WINDOW and (has_state or age <= 24 * 3600)


def start_scheduled(
    root: Path, profile: str | None, recipients: Sequence[str], user: str = "scheduler"
) -> None:
    from . import gui_backend  # run file + detached start, as for the web page

    settings = build_settings(profiles.resolve(root, profile), {"mail_to": list(recipients)})
    run_file = gui_backend.write_run_file(settings, root, datetime.now(), user)
    try:
        gui_backend.start_report(run_file, user, root)
    except gui_backend.StartError as exc:
        if not exc.spawned:
            run_file.unlink(missing_ok=True)
        raise


def _entries(root: Path) -> list[str | None]:
    return [None, *profiles.list_profiles(root)]


def run_due(root: Path, now: datetime | None = None, start: Start | None = None) -> str | None:
    """Start at most one due schedule; returns its name ("" = base) or None."""
    now = now or datetime.now()
    start = start or start_scheduled
    state = load_state(root)
    updates: dict[str, dict] = {}
    names = {"" if e is None else e for e in _entries(root)}
    gone_keys = [key for key in state if key not in names]
    changed = bool(gone_keys)
    started = None
    for entry in _entries(root):
        key = "" if entry is None else entry
        try:
            schedule = profiles.load_schedule(root, entry)
        except SettingsError as exc:
            logger.warning("parameter_report: schedule of %s not readable: %s", key or "base", exc)
            continue
        last_run = state.get(key, {}).get("last_run", 0.0)
        if schedule is None:
            continue
        if not due(schedule, last_run, now):
            if missed(schedule, last_run, now, key in state):
                updates[key] = {
                    "last_run": now.timestamp(),
                    "last_error": "skipped: another report was running until the window closed",
                }
                logger.warning(
                    "parameter_report: scheduled report %s skipped: another report was running "
                    "until the window closed",
                    key or "base",
                )
                changed = True
            continue
        try:
            with runstate.RunLock(runstate.lock_path(root)):
                pass
        except runstate.LockedError:
            continue  # another report runs: try again next minute (within the window)
        try:
            build_settings(profiles.resolve(root, entry))
            start(root, entry, schedule.recipients)
        except Exception as exc:
            if getattr(exc, "locked", False):
                continue  # a manual run took the lock meanwhile: retry next minute
            # invalid configuration, start failed: do not retry every minute
            updates[key] = {"last_run": now.timestamp(), "last_error": str(exc)}
            logger.warning("parameter_report: scheduled report %s failed: %s", key or "base", exc)
        else:
            updates[key] = {"last_run": now.timestamp(), "last_error": None}
            started = key
        changed = True
        if started is not None:
            break
    if changed:
        fresh = load_state(root)  # a rename/drop from the web meanwhile is kept
        for key in gone_keys:
            fresh.pop(key, None)
        fresh.update({key: value for key, value in updates.items() if key in names})
        try:
            save_state(root, fresh)
        except OSError as exc:
            logger.warning("parameter_report: schedule state not saved: %s", exc)
    return started
