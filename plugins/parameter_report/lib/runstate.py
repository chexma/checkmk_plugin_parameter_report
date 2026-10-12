"""State of a report run, shared with callers such as a GUI page.

A lock allows one run per site, a status file shows progress and outcome,
metadata next to each PDF record how it was made, retention limits the
number of kept reports, and leftovers of killed runs are cleaned up.
"""

from __future__ import annotations

import fcntl
import json
import os
import resource
import secrets
import shutil
import signal
import time
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import IO

TEMP_PREFIX = ".parameter_report-"
SITE_MARKER = ".site"  # inside a temp dir: the site that created it
REPORT_GLOB = "parameter_report_*.pdf"


class Cancelled(BaseException):
    """SIGTERM/SIGINT; BaseException so no 'except Exception' swallows it."""


class LockedError(Exception):
    """Another run holds the lock; the message names it."""


def reports_dir(root: Path) -> Path:
    return root / "var" / "parameter_report"


def default_output(root: Path, now: datetime) -> Path:
    name = f"parameter_report_{now:%Y-%m-%d_%H%M%S}_{secrets.token_hex(3)}.pdf"
    return reports_dir(root) / name


def default_status_file(root: Path) -> Path:
    return reports_dir(root) / "status.json"


def lock_path(root: Path) -> Path:
    return root / "tmp" / "run" / "parameter_report.lock"


class RunLock:
    """flock-based: released by the kernel when the process ends, even on kill -9."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            holder = fh.read().strip() or "unknown process"
            fh.close()
            raise LockedError(f"A report is already running ({holder})") from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"PID {os.getpid()}, since {datetime.now():%Y-%m-%d %H:%M:%S}")
        fh.flush()
        self._fh = fh
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._fh is None:
            return
        self._fh.seek(0)
        self._fh.truncate()
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StatusFile:
    """JSON status of the current run, replaced atomically on every update."""

    def __init__(self, path: Path, *, started_by: str, output: Path) -> None:
        self.path = path
        self.data: dict[str, object] = {
            "state": "starting",
            "pid": os.getpid(),
            "started_at": _now(),
            "updated_at": _now(),
            "started_by": started_by,
            "phase_progress": None,
            "output": str(output),
            "exit_code": None,
            "message": None,
        }
        self._write()

    def update(self, **changes: object) -> None:
        self.data.update(changes)
        self.data["updated_at"] = _now()
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.path)


def write_metadata(pdf: Path, data: dict[str, object]) -> None:
    pdf.with_suffix(".json").write_text(json.dumps(data, indent=2, default=str))


def apply_retention(
    directory: Path, site: str, max_age_days: int, now: float | None = None
) -> list[Path]:
    """Delete reports of this site older than max_age_days (0 = keep them).

    Only reports whose metadata names this site count, so other sites' and
    hand-placed files in a shared directory survive.
    """
    if max_age_days <= 0:
        return []
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    removed = []
    for pdf in directory.glob(REPORT_GLOB):
        try:
            meta = json.loads(pdf.with_suffix(".json").read_text())
            mtime = pdf.stat().st_mtime
        except (OSError, ValueError):
            continue  # vanished meanwhile, or not written by this tool
        if isinstance(meta, dict) and meta.get("site") == site and mtime < cutoff:
            pdf.unlink(missing_ok=True)
            pdf.with_suffix(".json").unlink(missing_ok=True)
            pdf.with_suffix(".toml").unlink(missing_ok=True)  # run file of the web page
            removed.append(pdf)
    return removed


def cleanup_stale_temp(directory: Path, site: str) -> int:
    """Remove temp dirs of killed runs of this site.

    Safe because the per-site lock excludes parallel runs; dirs of other sites
    (shared output directory) are left alone.
    """
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.glob(f"{TEMP_PREFIX}*"):
        try:
            owner = (path / SITE_MARKER).read_text().strip()
        except OSError:
            continue
        if owner == site:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def _cancel(signum: int, _frame: FrameType | None) -> None:
    # One-shot: a second signal must not interrupt the cleanup of the first
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise Cancelled(signal.Signals(signum).name)


def install_cancel_handlers() -> dict[int, object]:
    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, _cancel)
    return previous


def restore_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def peak_rss_mb() -> int:
    """Peak RSS in MB of this process.

    VmHWM, not getrusage: on Linux ru_maxrss keeps the RSS the parent had when
    the process was forked, so spawned helpers would report the parent's peak.
    """
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
