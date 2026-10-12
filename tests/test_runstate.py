import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from cmk_addons.plugins.parameter_report.lib import runstate


def test_default_paths(tmp_path):
    out = runstate.default_output(tmp_path, datetime(2026, 10, 9, 15, 4, 5))
    assert out.parent == tmp_path / "var" / "parameter_report"
    assert re.fullmatch(r"parameter_report_2026-10-09_150405_[0-9a-f]{6}\.pdf", out.name)
    assert runstate.default_status_file(tmp_path) == tmp_path / "var/parameter_report/status.json"
    assert runstate.lock_path(tmp_path) == tmp_path / "tmp/run/parameter_report.lock"


def test_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "run" / "pr.lock"
    with runstate.RunLock(path):
        with pytest.raises(runstate.LockedError, match=f"PID {os.getpid()}"):
            runstate.RunLock(path).__enter__()
    with runstate.RunLock(path):
        pass


def test_lock_released_when_holder_is_killed(tmp_path):
    path = tmp_path / "pr.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from cmk_addons.plugins.parameter_report.lib.runstate import RunLock\n"
            f"lock = RunLock(__import__('pathlib').Path({str(path)!r}))\n"
            "lock.__enter__()\n"
            "print('locked', flush=True)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "locked"
    with pytest.raises(runstate.LockedError):
        runstate.RunLock(path).__enter__()
    holder.kill()
    holder.wait()
    with runstate.RunLock(path):
        pass


def test_status_file_updates_atomically(tmp_path):
    path = tmp_path / "var" / "status.json"
    status = runstate.StatusFile(path, started_by="cmkadmin", output=tmp_path / "r.pdf")
    first = json.loads(path.read_text())
    assert first["state"] == "starting"
    assert first["pid"] == os.getpid()
    assert first["started_by"] == "cmkadmin"
    assert first["output"] == str(tmp_path / "r.pdf")
    assert first["exit_code"] is None
    status.update(state="rendering", phase_progress=[10, 100])
    second = json.loads(path.read_text())
    assert second["state"] == "rendering" and second["phase_progress"] == [10, 100]
    assert [p.name for p in path.parent.iterdir()] == ["status.json"]


def test_write_metadata(tmp_path):
    pdf = tmp_path / "parameter_report_x.pdf"
    runstate.write_metadata(pdf, {"exit_code": 0, "output": pdf})
    assert json.loads(pdf.with_suffix(".json").read_text()) == {
        "exit_code": 0,
        "output": str(pdf),
    }


def _report(directory, name, mtime, site="cmk", profile=None):
    pdf = directory / name
    pdf.write_bytes(b"%PDF")
    meta = {"site": site}
    if profile is not None:
        meta["settings"] = {"profile": profile}
    pdf.with_suffix(".json").write_text(json.dumps(meta))
    os.utime(pdf, (mtime, mtime))
    return pdf


def test_retention_by_age(tmp_path):
    day = 86400
    old = _report(tmp_path, "parameter_report_a.pdf", 10 * day - 1)
    young = _report(tmp_path, "parameter_report_b.pdf", 10 * day + 1)
    foreign = _report(tmp_path, "parameter_report_c.pdf", 1, site="other")
    assert runstate.apply_retention(tmp_path, "cmk", 7, now=17 * day) == [old]
    assert young.exists() and foreign.exists()
    assert runstate.apply_retention(tmp_path, "cmk", 0) == []  # 0 keeps everything


def test_cleanup_stale_temp(tmp_path):
    stale = tmp_path / ".parameter_report-abc"
    stale.mkdir()
    (stale / "chunk.pdf").write_text("x")
    (stale / runstate.SITE_MARKER).write_text("cmk")
    keep = tmp_path / "keep"
    keep.mkdir()
    assert runstate.cleanup_stale_temp(tmp_path, "cmk") == 1
    assert not stale.exists() and keep.exists()
    assert runstate.cleanup_stale_temp(tmp_path / "missing", "cmk") == 0


def test_cancel_handlers_raise_and_restore():
    previous = runstate.install_cancel_handlers()
    try:
        with pytest.raises(runstate.Cancelled, match="SIGTERM"):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
    finally:
        runstate.restore_handlers(previous)
    assert signal.getsignal(signal.SIGTERM) == previous[signal.SIGTERM]


def test_peak_rss_is_this_process_only():
    before = runstate.peak_rss_mb()
    ballast = bytearray(200 * 1024 * 1024)
    ballast[::4096] = b"x" * len(ballast[::4096])
    assert runstate.peak_rss_mb() >= before + 150
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cmk_addons.plugins.parameter_report.lib import runstate; "
            "print(runstate.peak_rss_mb())",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(child.stdout) < 150  # a fresh process does not inherit the parent's peak


def test_retention_only_touches_reports_of_this_site(tmp_path):
    mine_old = _report(tmp_path, "parameter_report_a.pdf", 1000)
    mine_new = _report(tmp_path, "parameter_report_b.pdf", 3000)
    foreign = _report(tmp_path, "parameter_report_c.pdf", 500, site="other")
    hand = tmp_path / "parameter_report_hand.pdf"
    hand.write_bytes(b"%PDF")
    os.utime(hand, (100, 100))
    assert runstate.apply_retention(tmp_path, "cmk", 1, now=1000 + 86400 + 1) == [mine_old]
    assert mine_new.exists() and foreign.exists() and hand.exists()


def test_retention_tolerates_vanishing_files(tmp_path, monkeypatch):
    _report(tmp_path, "parameter_report_a.pdf", 1000)
    kept = _report(tmp_path, "parameter_report_b.pdf", 2000)
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self.name == "parameter_report_a.pdf":
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert runstate.apply_retention(tmp_path, "cmk", 1, now=2000 + 2 * 86400) == [kept]


def test_cleanup_only_removes_temp_dirs_of_this_site(tmp_path):
    dirs = {}
    for name, marker in [("mine", "cmk"), ("foreign", "other"), ("unmarked", None)]:
        path = tmp_path / f".parameter_report-{name}"
        path.mkdir()
        if marker:
            (path / runstate.SITE_MARKER).write_text(marker)
        dirs[name] = path
    assert runstate.cleanup_stale_temp(tmp_path, "cmk") == 1
    assert not dirs["mine"].exists()
    assert dirs["foreign"].exists() and dirs["unmarked"].exists()


def test_second_signal_during_cancel_is_ignored():
    previous = runstate.install_cancel_handlers()
    try:
        with pytest.raises(runstate.Cancelled):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
        os.kill(os.getpid(), signal.SIGTERM)  # must not raise again
        time.sleep(0.2)
    finally:
        runstate.restore_handlers(previous)


def test_retention_removes_run_file(tmp_path):
    pdf = tmp_path / "parameter_report_a.pdf"
    pdf.write_bytes(b"%PDF")
    pdf.with_suffix(".json").write_text('{"site": "cmk"}')
    pdf.with_suffix(".toml").write_text("min_split_lines = 1\n")
    os.utime(pdf, (1000, 1000))
    assert runstate.apply_retention(tmp_path, "cmk", 1) == [pdf]
    assert not pdf.with_suffix(".toml").exists()
