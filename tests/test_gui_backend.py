import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

import pytest
from cmk_addons.plugins.parameter_report.lib import (
    config_file,
    gui_backend,
    i18n,
    profiles,
    runstate,
    schedules,
)
from cmk_addons.plugins.parameter_report.lib.settings import Settings, SettingsError, build_settings


@pytest.fixture
def root(tmp_path):
    (tmp_path / "etc").mkdir()
    return tmp_path


def _form(**values):
    """Values as the page submits them: the table-of-contents box is ticked by default."""
    return {name: str(value) for name, value in {"toc": "on", **values}.items()}


def test_form_fields_cover_all_tunable_settings():
    names = {f.name for f in gui_backend.FORM_FIELDS}
    hidden = {"output", "status_file", "started_by", "profile", "mail_to"}
    assert names == set(build_settings().__dataclass_fields__) - hidden
    groups = {f.group for f in gui_backend.FORM_FIELDS}
    assert groups == {"selection", "content", "run", "layout", "cover"}


def test_effective_defaults_from_config(root):
    (root / "etc/parameter_report.toml").write_text("workers = 3\nfolders = ['linux']\n")
    defaults, error = gui_backend.effective_defaults(root)
    assert error is None and defaults.workers == 3 and defaults.folders == ("linux",)


def test_effective_defaults_with_broken_config(root):
    (root / "etc/parameter_report.toml").write_text("workers = = 3\n")
    defaults, error = gui_backend.effective_defaults(root)
    assert defaults == build_settings() and "not valid TOML" in error


def test_display_value():
    s = Settings(host_tags=(("criticality", "prod"),), folders=("a", "b"), portrait=True)
    assert gui_backend.display_value(s, "host_tags") == "criticality:prod"
    assert gui_backend.display_value(s, "folders") == "a, b"
    assert gui_backend.display_value(s, "column_widths") == "34, 22, 14, 16"
    assert gui_backend.display_value(s, "portrait") == "yes"
    assert gui_backend.display_value(s, "chunk_size") == "50"


def test_form_values_to_cli():
    cli_values = gui_backend.form_values_to_cli(
        _form(
            hosts="web01, db01",
            host_tags="a:b,c:d",
            workers="3",
            margin_mm="10.5",
            changed_only="on",
            collect_mode="inline",
            folders="",
        )
    )
    assert cli_values["hosts"] == ["web01", "db01"]
    assert cli_values["host_tags"] == ["a:b", "c:d"]
    assert cli_values["workers"] == 3 and cli_values["margin_mm"] == 10.5
    assert cli_values["changed_only"] is True and cli_values["portrait"] is False
    assert "folders" not in cli_values and cli_values["collect_mode"] == "inline"


@pytest.mark.parametrize("field, value", [("workers", "two"), ("margin_mm", "wide")])
def test_form_values_to_cli_rejects_bad_numbers(field, value):
    with pytest.raises(SettingsError, match=field):
        gui_backend.form_values_to_cli({field: value})


FAKE = """#!{python}
import json, os, sys, time
from datetime import datetime
root = {root!r}
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    print("boom: something broke", file=sys.stderr)
    sys.exit(1)
if mode == "silent":
    sys.exit(0)
status = os.path.join(root, "var/parameter_report/status.json")
os.makedirs(os.path.dirname(status), exist_ok=True)
data = {{"state": "collecting", "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"), "argv": sys.argv[1:]}}
open(status, "w").write(json.dumps(data))
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
"""


@pytest.fixture
def fake_root(root):
    command = root / gui_backend.COMMAND
    command.parent.mkdir(parents=True)
    command.write_text(FAKE.format(python=sys.executable, root=str(root)))
    command.chmod(0o755)
    return root


def _status(root):
    return json.loads(runstate.default_status_file(root).read_text())


def test_start_report_when_locked(fake_root):
    with runstate.RunLock(runstate.lock_path(fake_root)):
        with pytest.raises(gui_backend.StartError, match="already running"):
            gui_backend.start_report(fake_root / "x.toml", "cmkadmin", fake_root)


def test_write_run_file(root):
    (root / "etc/parameter_report.toml").write_text('min_split_lines = 4\ncover_company = "ACME"\n')
    settings = build_settings(profiles.resolve(root, None), {"cover_title": "Run"})
    path = gui_backend.write_run_file(settings, root, datetime(2026, 10, 9, 12, 0), "cmkadmin")
    assert path.parent == runstate.reports_dir(root) and path.suffix == ".toml"
    loaded = build_settings(config_file.load(path))
    assert (
        loaded.min_split_lines == 4
        and loaded.cover_company == "ACME"
        and loaded.cover_title == "Run"
    )
    assert loaded.output == path.with_suffix(".pdf")
    assert loaded.status_file == runstate.default_status_file(root)
    assert loaded.started_by == "cmkadmin"
    assert path.name.startswith("parameter_report_2026-10-09_120000_")


def test_start_report(fake_root):
    run_file = fake_root / "var/parameter_report/parameter_report_x.toml"
    run_file.parent.mkdir(parents=True)
    run_file.write_text("min_split_lines = 1\n")
    gui_backend.start_report(run_file, "cmkadmin", fake_root)
    assert _status(fake_root)["argv"] == ["--config", str(run_file)]
    log = (fake_root / gui_backend.LOG_FILE).read_text()
    assert "started by cmkadmin: parameter_report_x.toml" in log


def test_start_report_failure_shows_log(fake_root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    run_file = fake_root / "x.toml"
    run_file.write_text("")
    with pytest.raises(gui_backend.StartError, match="boom: something broke"):
        gui_backend.start_report(run_file, "cmkadmin", fake_root)


def test_start_profile_uses_the_saved_settings(fake_root):
    (fake_root / "etc/parameter_report.toml").write_text('hosts = ["a"]\nworkers = 1\n')
    gui_backend.start_profile(fake_root, "cmkadmin")
    argv = _status(fake_root)["argv"]
    run_file = Path(argv[1])
    assert argv[0] == "--config" and run_file.exists()
    assert tomllib.loads(run_file.read_text())["hosts"] == ["a"]


def test_start_profile_removes_run_file_on_error(fake_root):
    with runstate.RunLock(runstate.lock_path(fake_root)):
        with pytest.raises(gui_backend.StartError):
            gui_backend.start_profile(fake_root, "cmkadmin")
    assert list(runstate.reports_dir(fake_root).glob("*.toml")) == []


def test_start_profile_keeps_run_file_on_start_timeout(fake_root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "silent")  # exits without a status: the start times out
    monkeypatch.setattr(gui_backend, "START_TIMEOUT", 0.3)
    with pytest.raises(gui_backend.StartError, match="did not start"):
        gui_backend.start_profile(fake_root, "cmkadmin")
    assert len(list(runstate.reports_dir(fake_root).glob("*.toml"))) == 1


def test_start_profile_rejects_broken_config(fake_root):
    (fake_root / "etc/parameter_report.toml").write_text("workers = = 3\n")
    with pytest.raises(SettingsError, match="Fix the configuration first"):
        gui_backend.start_profile(fake_root, "cmkadmin")


def test_delete_report_removes_run_file(root):
    pdf = _report(root, "parameter_report_a.pdf")
    pdf.with_suffix(".toml").write_text("min_split_lines = 1\n")
    gui_backend.delete_report(root, "cmk", pdf.name)
    assert not pdf.with_suffix(".toml").exists()


def test_prune_removes_orphan_run_files(root):
    reports = runstate.reports_dir(root)
    reports.mkdir(parents=True, exist_ok=True)
    old = reports / "parameter_report_old.toml"
    new = reports / "parameter_report_new.toml"
    kept = _report(root, "parameter_report_kept.pdf").with_suffix(".toml")
    for path in (old, new, kept):
        path.write_text("min_split_lines = 1\n")
    stale = time.time() - gui_backend.RUN_FILE_MAX_AGE - 10
    os.utime(old, (stale, stale))
    os.utime(kept, (stale, stale))
    gui_backend.prune_reports(root, "cmk")
    assert not old.exists() and new.exists() and kept.exists()


def _write_status(root, **data):
    path = runstate.default_status_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_read_status_states(root):
    assert gui_backend.read_status(root) == gui_backend.StatusView({}, False, False)
    _write_status(root, state="done", pid=1)
    assert not gui_backend.read_status(root).active
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _write_status(root, state="rendering", pid=dead.pid)
    view = gui_backend.read_status(root)
    assert view.crashed and not view.active


def _wait_for_exec(pid, marker):
    # Right after fork, /proc/<pid>/cmdline still shows the parent until exec
    for _ in range(100):
        if marker in Path(f"/proc/{pid}/cmdline").read_bytes():
            return
        time.sleep(0.05)
    raise AssertionError("process did not start")


def test_read_status_active_and_cancel(root):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "cmk-parameter-report"]
    )
    try:
        _wait_for_exec(proc.pid, b"cmk-parameter-report")
        _write_status(root, state="rendering", pid=proc.pid, phase_progress=[10, 40])
        view = gui_backend.read_status(root)
        assert view.active and not view.crashed
        assert gui_backend.cancel_report(root) is True
        assert proc.wait(timeout=10) == -signal.SIGTERM
    finally:
        proc.kill()


def test_cancel_ignores_foreign_process(root):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _write_status(root, state="rendering", pid=proc.pid)
        assert gui_backend.read_status(root).crashed
        assert gui_backend.cancel_report(root) is False
        assert proc.poll() is None  # still running
    finally:
        proc.kill()


def test_status_summary():
    view = gui_backend.StatusView(
        {
            "state": "rendering",
            "phase_progress": [250, 1000],
            "started_at": "2026-10-09T15:00:00",
            "started_by": "cmkadmin",
            "output": "/x/parameter_report_a.pdf",
        },
        True,
        False,
    )
    summary = gui_backend.status_summary(view)
    assert summary["title"] == "Rendering" and summary["percent"] == 25
    assert summary["progress"] == "250 / 1000 hosts" and summary["report"] is None
    done = gui_backend.status_summary(
        gui_backend.StatusView(
            {"state": "done", "output": "/x/parameter_report_a.pdf"}, False, False
        )
    )
    assert done["report"] == "parameter_report_a.pdf"
    crashed = gui_backend.status_summary(
        gui_backend.StatusView({"state": "rendering"}, False, True)
    )
    assert crashed["title"].startswith("Stopped unexpectedly")
    assert gui_backend.status_summary(gui_backend.StatusView({}, False, False))["title"] == (
        "No report has run yet"
    )


def _report(root, name, site="cmk", **meta):
    reports = runstate.reports_dir(root)
    reports.mkdir(parents=True, exist_ok=True)
    pdf = reports / name
    pdf.write_bytes(b"%PDF-1.4 test")
    data = {
        "site": site,
        "started_at": "2026-10-09T15:00:00",
        "started_by": "cmkadmin",
        "hosts": 2,
        "services": 4,
        "exit_code": 0,
        "failed_hosts": [],
        "settings": {"changed_only": False},
        "timings": {"render": 1.0},
        "peak_rss_mb": {"main": 100},
    }
    data.update(meta)
    pdf.with_suffix(".json").write_text(json.dumps(data))
    return pdf


def test_list_reports(root):
    _report(root, "parameter_report_old.pdf", started_at="2026-10-08T06:00:00")
    _report(
        root,
        "parameter_report_new.pdf",
        exit_code=2,
        failed_hosts=[["h", "Boom"]],
        settings={"changed_only": True},
    )
    _report(root, "parameter_report_other.pdf", site="other")
    (runstate.reports_dir(root) / "parameter_report_nometa.pdf").write_bytes(b"%PDF")
    reports = gui_backend.list_reports(root, "cmk")
    assert [r.name for r in reports] == ["parameter_report_new.pdf", "parameter_report_old.pdf"]
    new = reports[0]
    assert new.changed_only and new.exit_code == 2 and new.failed_hosts == 1
    assert new.size == len(b"%PDF-1.4 test") and new.created == "2026-10-09 15:00:00"


def test_list_reports_title(root):
    _report(root, "parameter_report_a.pdf", settings={"cover_title": "Customer AG"})
    _report(root, "parameter_report_b.pdf", started_at="2026-10-08T06:00:00")
    titles = [r.title for r in gui_backend.list_reports(root, "cmk")]
    assert titles == ["Customer AG", ""]


def test_report_duration(root):
    _report(root, "parameter_report_a.pdf", finished_at="2026-10-09T15:01:05")
    _report(root, "parameter_report_b.pdf", started_at="2026-10-09T14:00:00")  # no finished_at
    durations = {r.name: r.duration for r in gui_backend.list_reports(root, "cmk")}
    assert durations == {"parameter_report_a.pdf": 65.0, "parameter_report_b.pdf": None}


@pytest.mark.parametrize(
    "seconds, text",
    [(None, "–"), (0, "0 s"), (59.6, "1 min 00 s"), (65, "1 min 05 s"), (3725, "1 h 02 min")],
)
def test_format_duration(seconds, text):
    assert gui_backend.format_duration(seconds) == text


@pytest.mark.parametrize(
    "size, text",
    [
        (0, "0 kB"),
        (41_400, "41 kB"),
        (999_499, "999 kB"),
        (1_000_000, "1.0 MB"),
        (3_812_000, "3.8 MB"),
    ],
)
def test_format_size(size, text):
    assert gui_backend.format_size(size) == text


def test_prune_reports_uses_config_retention(root):
    old = _report(root, "parameter_report_a.pdf")
    new = _report(root, "parameter_report_b.pdf")
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    assert gui_backend.prune_reports(root, "cmk") == []  # no retention configured
    (root / "etc/parameter_report.toml").write_text("retention_days = 2\n")
    assert gui_backend.prune_reports(root, "cmk") == [old]
    assert new.exists()


def _profile_report(root, name, profile, age_days):
    pdf = _report(root, name, settings={"profile": profile})
    os.utime(pdf, (time.time() - age_days * 86400,) * 2)
    return pdf


def test_report_path_and_delete(root):
    pdf = _report(root, "parameter_report_a.pdf")
    assert gui_backend.report_path(root, "cmk", "parameter_report_a.pdf") == pdf
    gui_backend.delete_report(root, "cmk", "parameter_report_a.pdf")
    assert not pdf.exists() and not pdf.with_suffix(".json").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "parameter_report_a.pdf/../../x",
        "status.json",
        "parameter_report_missing.pdf",
        "parameter_report_other.pdf",
        "",
    ],
)
def test_report_path_rejects_bad_names(root, name):
    _report(root, "parameter_report_other.pdf", site="other")
    with pytest.raises(gui_backend.NotFound):
        gui_backend.report_path(root, "cmk", name)
    with pytest.raises(gui_backend.NotFound):
        gui_backend.delete_report(root, "cmk", name)
    assert (runstate.reports_dir(root) / "parameter_report_other.pdf").exists()


def test_cancel_survives_process_ending_meanwhile(root, monkeypatch):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "cmk-parameter-report"]
    )
    try:
        _wait_for_exec(proc.pid, b"cmk-parameter-report")
        _write_status(root, state="rendering", pid=proc.pid)

        def gone(pid, sig):
            raise ProcessLookupError(pid)

        monkeypatch.setattr(gui_backend.os, "kill", gone)
        assert gui_backend.cancel_report(root) is False
    finally:
        proc.kill()


def test_process_of_another_user_is_not_ours(root, monkeypatch):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "cmk-parameter-report"]
    )
    try:
        _wait_for_exec(proc.pid, b"cmk-parameter-report")
        _write_status(root, state="rendering", pid=proc.pid)
        monkeypatch.setattr(gui_backend.os, "getuid", lambda: 12345)
        view = gui_backend.read_status(root)
        assert not view.active and view.crashed
    finally:
        proc.kill()


def _cfg(root):
    return root / "etc/parameter_report.toml"


def test_config_form_values(root):
    _cfg(root).write_text('min_split_lines = 4\nfolders = ["linux", "db"]\nportrait = true\n')
    form = gui_backend.config_form(root)
    assert form.values == {"min_split_lines": "4", "folders": "linux, db", "portrait": "yes"}
    assert form.mtime == _cfg(root).stat().st_mtime_ns and form.error is None


def test_config_form_values_broken_file(root):
    _cfg(root).write_text("min_split_lines = = 4\n")
    form = gui_backend.config_form(root)
    assert form.values == {} and form.mtime > 0 and "not valid TOML" in form.error


def test_save_config_creates_file(root):
    gui_backend.save_config(_form(min_split_lines="5", cover_company="ACME"), root, "cmkadmin", 0)
    text = _cfg(root).read_text()
    assert text.startswith("# Saved by cmkadmin on ")
    assert build_settings(config_file.load(_cfg(root))) == build_settings(
        None, {"min_split_lines": 5, "cover_company": "ACME"}
    )
    assert not (root / "etc/parameter_report.toml.bak").exists()


def test_save_config_replaces_and_backs_up(root):
    _cfg(root).write_text("min_split_lines = 4\nchunk_size = 7\n")
    mtime = _cfg(root).stat().st_mtime_ns
    gui_backend.save_config(
        _form(min_split_lines="6", changed_only="", collect_mode="subprocess"), root, "u", mtime
    )
    loaded = config_file.load(_cfg(root))
    # empty fields, unticked boxes and choices equal to the built-in default are not stored
    assert loaded == {"min_split_lines": 6}
    assert (
        root / "etc/parameter_report.toml.bak"
    ).read_text() == "min_split_lines = 4\nchunk_size = 7\n"


def test_save_config_keeps_output_keys(root):
    _cfg(root).write_text('min_split_lines = 4\noutput = "/data/r.pdf"\nstarted_by = "cron"\n')
    mtime = _cfg(root).stat().st_mtime_ns
    gui_backend.save_config(_form(min_split_lines="2"), root, "u", mtime)
    assert config_file.load(_cfg(root)) == {
        "min_split_lines": 2,
        "output": "/data/r.pdf",
        "started_by": "cron",
    }


def test_save_config_invalid_writes_nothing(root):
    _cfg(root).write_text("min_split_lines = 4\n")
    mtime = _cfg(root).stat().st_mtime_ns
    with pytest.raises(SettingsError, match="chunk_size"):
        gui_backend.save_config(_form(chunk_size="0"), root, "u", mtime)
    assert _cfg(root).read_text() == "min_split_lines = 4\n"


def test_save_config_conflict(root):
    _cfg(root).write_text("min_split_lines = 4\n")  # created after the page was shown (mtime 0)
    with pytest.raises(gui_backend.ConfigConflict, match="changed meanwhile"):
        gui_backend.save_config(_form(min_split_lines="1"), root, "u", 0)
    assert _cfg(root).read_text() == "min_split_lines = 4\n"


def test_save_config_broken_file_is_replaced(root):
    _cfg(root).write_text("min_split_lines = = 4\n")
    mtime = _cfg(root).stat().st_mtime_ns
    gui_backend.save_config(_form(min_split_lines="1"), root, "u", mtime)
    assert config_file.load(_cfg(root)) == {"min_split_lines": 1}
    assert (root / "etc/parameter_report.toml.bak").read_text() == "min_split_lines = = 4\n"


def _prof(root, name, text):
    d = root / "etc/parameter_report.d"
    d.mkdir(exist_ok=True)
    (d / f"{name}.toml").write_text(text)


def test_config_form_for_profile(root):
    (root / "etc/parameter_report.toml").write_text("min_split_lines = 3\n")
    _prof(root, "k", 'description = "Kunde"\nfolders = ["db"]\n')
    form = gui_backend.config_form(root, "k")
    assert form.values == {"folders": "db"} and form.description == "Kunde" and form.error is None
    assert form.mtime == (root / "etc/parameter_report.d/k.toml").stat().st_mtime_ns
    assert gui_backend.inherited_settings(root, "k").min_split_lines == 3
    assert gui_backend.inherited_settings(root, None).min_split_lines == 2


def test_save_profile(root):
    profiles.create(root, "k")
    mtime = gui_backend.config_mtime(root, "k")
    gui_backend.save_config(_form(folders="db"), root, "u", mtime, "k", "Kunde K")
    assert profiles.load_profile(root, "k") == ({"folders": ["db"]}, "Kunde K")
    assert (root / "etc/parameter_report.d/k.toml.bak").exists()


def test_save_profile_bool_against_inherited(root):
    (root / "etc/parameter_report.toml").write_text("changed_only = true\n")
    profiles.create(root, "k")
    gui_backend.save_config(_form(), root, "u", gui_backend.config_mtime(root, "k"), "k")
    assert profiles.load_profile(root, "k")[0] == {"changed_only": False}  # differs from base
    mtime = gui_backend.config_mtime(root, "k")
    gui_backend.save_config(_form(changed_only="on"), root, "u", mtime, "k")
    assert profiles.load_profile(root, "k")[0] == {}  # equals the inherited value


def test_save_profile_invalid_with_base(root):
    (root / "etc/parameter_report.toml").write_text("margin_mm = 40.0\n")
    profiles.create(root, "k")
    with pytest.raises(SettingsError, match="parameter column"):
        gui_backend.save_config(
            _form(column_widths="80, 40, 40, 40"),
            root,
            "u",
            gui_backend.config_mtime(root, "k"),
            "k",
        )


def test_save_base_reports_profiles_made_invalid(root):
    # valid with the default margin (53 characters left), invalid with 40 mm (12 left)
    _prof(root, "k", "column_widths = [70, 30, 20, 20]\n")
    _prof(root, "ok", "min_split_lines = 1\n")
    with pytest.raises(SettingsError, match=r"would make these profiles invalid: k \("):
        gui_backend.save_config(_form(margin_mm="40"), root, "u", 0)
    assert not (root / "etc/parameter_report.toml").exists()


def test_save_profile_not_found(root):
    with pytest.raises(profiles.ProfileNotFound):
        gui_backend.save_config(_form(), root, "u", 0, "x")


def test_effective_defaults_with_profile(root):
    (root / "etc/parameter_report.toml").write_text("min_split_lines = 3\n")
    _prof(root, "k", "min_split_lines = 7\n")
    defaults, error = gui_backend.effective_defaults(root, "k")
    assert error is None and defaults.min_split_lines == 7 and defaults.profile == "k"


def test_start_profile_with_profile(fake_root):
    _prof(fake_root, "k", 'cover_customer = "K AG"\n')
    gui_backend.start_profile(fake_root, "cmkadmin", "k")
    run_file = Path(_status(fake_root)["argv"][1])
    loaded = config_file.load(run_file)
    assert loaded["profile"] == "k" and loaded["cover_customer"] == "K AG"


def test_start_with_missing_profile(fake_root):
    with pytest.raises(profiles.ProfileNotFound, match="Profile x not found."):
        gui_backend.start_profile(fake_root, "cmkadmin", "x")


def test_broken_profile_isolated(fake_root):
    _prof(fake_root, "bad", "min_split_lines = = 1\n")
    with pytest.raises(SettingsError, match="Fix profile bad first"):
        gui_backend.start_profile(fake_root, "cmkadmin", "bad")
    gui_backend.start_profile(fake_root, "cmkadmin")  # base still starts


def test_list_reports_profile(root):
    _report(root, "parameter_report_a.pdf", settings={"profile": "k"})
    assert gui_backend.list_reports(root, "cmk")[0].profile == "k"


def test_save_checkbox_that_defaults_to_on(root):
    gui_backend.save_config(_form(toc=""), root, "u", 0)
    assert config_file.load(root / "etc/parameter_report.toml") == {"toc": False}
    mtime = gui_backend.config_mtime(root)
    gui_backend.save_config(_form(toc="on"), root, "u", mtime)
    assert config_file.load(root / "etc/parameter_report.toml") == {}


def test_save_and_load_schedule(root):
    sched = {"enabled": "on", "period": "week", "day": "0", "time": "06:00", "recipients": "a@b.de"}
    gui_backend.save_config(_form(), root, "u", 0, None, "", sched, {"max_attachment_mb": "5"})
    form = gui_backend.config_form(root)
    assert (
        form.schedule == {**sched, "recipients": "a@b.de"} and form.mail["max_attachment_mb"] == "5"
    )
    mtime = gui_backend.config_mtime(root)
    gui_backend.save_config(_form(min_split_lines="2"), root, "u", mtime)  # None keeps the tables
    assert profiles.load_schedule(root, None).describe() == "weekly, Mon 06:00"
    assert profiles.load_mail(root).max_attachment_mb == 5


def test_save_schedule_invalid(root):
    with pytest.raises(SettingsError, match="not a valid mail address"):
        gui_backend.save_config(
            _form(), root, "u", 0, None, "", {"enabled": "on", "time": "06:00", "recipients": "x"}
        )
    assert not (root / "etc/parameter_report.toml").exists()


SCHED_K = (
    '\n[schedule]\nenabled = {enabled}\nperiod = "day"\nday = 0\ntime = "06:00"\n'
    'recipients = ["k@b.de"]\n'
)


def test_start_with_mail(fake_root):
    _prof(fake_root, "k", SCHED_K.format(enabled="false"))
    gui_backend.start_profile(fake_root, "u", "k", send_mail=True)
    run_file = Path(_status(fake_root)["argv"][1])
    assert config_file.load(run_file)["mail_to"] == ["k@b.de"]


def test_start_with_mail_needs_recipients(fake_root):
    _prof(fake_root, "k", "")
    with pytest.raises(SettingsError, match="No recipients"):
        gui_backend.start_profile(fake_root, "u", "k", send_mail=True)


def test_run_now(fake_root):
    _prof(fake_root, "k", SCHED_K.format(enabled="true"))
    gui_backend.run_now(fake_root, "cmkadmin", "k")
    run = config_file.load(Path(_status(fake_root)["argv"][1]))
    assert run["mail_to"] == ["k@b.de"] and run["profile"] == "k"
    assert schedules.load_state(fake_root) == {}  # last_run untouched


def test_run_now_without_schedule(fake_root):
    _prof(fake_root, "k", "")
    with pytest.raises(SettingsError, match="No schedule or recipients"):
        gui_backend.run_now(fake_root, "cmkadmin", "k")


def test_report_info_mail(root):
    _report(
        root,
        "parameter_report_a.pdf",
        mail={"to": ["a@b.de"], "sent": True, "attached": True, "error": None},
    )
    assert gui_backend.list_reports(root, "cmk")[0].mail["sent"] is True


def _info(created, exit_code=0, mail=None):
    return gui_backend.ReportInfo(
        name="r.pdf",
        created=created,
        started_by="scheduler",
        hosts=1,
        services=1,
        changed_only=False,
        exit_code=exit_code,
        failed_hosts=0,
        size=1,
        settings={},
        timings={},
        peaks={},
        mail=mail,
    )


def test_last_run():
    from datetime import datetime

    run = datetime(2026, 10, 12, 6, 0).timestamp()
    when = "2026-10-12 06:00"
    entry = {"last_run": run, "last_error": None}
    last = gui_backend.last_run
    assert last(None, None, False) == ("none", "", "")
    assert last({"last_run": run, "last_error": "boom"}, None, False) == ("crit", when, "boom")
    skipped = {"last_run": run, "last_error": "skipped: another report was running"}
    assert last(skipped, None, False)[0] == "warn"
    fresh = _info("2026-10-12 06:00:30")
    assert last(entry, fresh, False) == ("ok", when, "OK")
    assert last(entry, _info("2026-10-12 05:59:30", 2), False) == (
        "warn",
        when,
        "hosts with errors",
    )
    assert last(entry, _info("2026-10-12 06:00:30", 1), False) == ("crit", when, "failed")
    failed_mail = _info("2026-10-12 06:00:30", mail={"error": "x"})
    assert last(entry, failed_mail, False) == ("warn", when, "OK, mail failed")
    # the newest report belongs to an older run: never pair it with the new time
    old = _info("2026-10-11 06:00:30")
    assert last(entry, old, True) == ("pending", when, "running")
    assert last(entry, old, False) == (
        "unknown",
        when,
        "no report (see ~/var/log/parameter_report.log)",
    )
    assert last(entry, None, False)[2].endswith("no report (see ~/var/log/parameter_report.log)")


def test_profile_summary(root):
    (root / "etc/parameter_report.toml").write_text('cover_company = "ACME"\n')
    _prof(
        root,
        "k",
        'description = "Kunde K"\nfolders = ["linux"]\ncover_customer = "K AG"\n'
        + SCHED_K.format(enabled="true"),
    )
    summary = dict(gui_backend.profile_summary(root, "k"))
    assert summary["Description"] == "Kunde K"
    assert summary["Hosts"] == "folders: linux"
    assert summary["Customer"] == "K AG" and summary["Company"] == "ACME"
    assert summary["Schedule"] == "daily, 06:00" and summary["Mail to"] == "k@b.de"
    base = dict(gui_backend.profile_summary(root, None))
    assert base["Hosts"] == "all hosts" and "Schedule" not in base


def test_profile_summary_of_a_broken_profile(root):
    _prof(root, "bad", "min_split_lines = = 1\n")
    with pytest.raises(SettingsError):
        gui_backend.profile_summary(root, "bad")


def test_profile_save_ignores_run_options(root):
    profiles.create(root, "k")
    mtime = gui_backend.config_mtime(root, "k")
    gui_backend.save_config(
        _form(workers="3", retention_days="5", folders="db"), root, "u", mtime, "k"
    )
    assert profiles.load_profile(root, "k")[0] == {"folders": ["db"]}


def test_retention_applies_to_all_reports(root):
    old_a = _report(root, "parameter_report_a.pdf", settings={"profile": "a"})
    old_b = _report(root, "parameter_report_b.pdf", settings={})
    for pdf in (old_a, old_b):
        os.utime(pdf, (time.time() - 3 * 86400,) * 2)
    (root / "etc/parameter_report.toml").write_text("retention_days = 2\n")
    assert sorted(p.name for p in gui_backend.prune_reports(root, "cmk")) == [
        old_a.name,
        old_b.name,
    ]


def test_profile_summary_shows_recipients_of_a_disabled_schedule(root):
    _prof(root, "k", SCHED_K.format(enabled="false"))
    summary = dict(gui_backend.profile_summary(root, "k"))
    assert "Schedule" not in summary and summary["Mail to"] == "k@b.de"


def test_save_repairs_invalid_files_and_keeps_tables(root):
    _prof(root, "k", "workers = 2\n" + SCHED_K.format(enabled="false"))
    gui_backend.save_config(
        _form(folders="db"), root, "u", gui_backend.config_mtime(root, "k"), "k"
    )
    assert profiles.load_profile(root, "k")[0] == {"folders": ["db"]}
    assert profiles.load_schedule(root, "k").recipients == ("k@b.de",)
    (root / "etc/parameter_report.toml").write_text(
        "unknown_key = 3\n\n[mail]\nmax_attachment_mb = 5\n"
    )
    gui_backend.save_config(_form(), root, "u", gui_backend.config_mtime(root))
    assert profiles.load_mail(root).max_attachment_mb == 5


def test_language_and_date_format_fields():
    fields = {f.name: f for f in gui_backend.FORM_FIELDS}
    assert fields["language"].group == fields["date_format"].group == "content"
    assert fields["language"].choices == i18n.LANGUAGES
    assert fields["date_format"].choices == tuple(i18n.DATE_FORMATS)


@pytest.mark.parametrize(
    "exit_code, mail, state",
    [
        (0, None, "ok"),
        (0, {"error": "x"}, "warn"),
        (0, {"to": ["a@b.de"], "attached": True}, "ok"),
        (2, None, "warn"),
        (1, None, "crit"),
    ],
)
def test_report_state(exit_code, mail, state):
    assert gui_backend.report_state(_info("2026-10-12 06:00:30", exit_code, mail)) == state


def test_send_test_mail(root, monkeypatch):
    from cmk_addons.plugins.parameter_report.lib import mailer

    calls = []
    monkeypatch.setattr(mailer, "_send", lambda m, to, sender: calls.append((m, to, sender)))
    monkeypatch.setattr(mailer, "default_sender", lambda: "site@host.example")
    monkeypatch.setattr(mailer, "sendmail_available", lambda root: True)
    _prof(root, "k", 'language = "de"\n' + SCHED_K.format(enabled="false"))
    assert gui_backend.send_test_mail(root, "k") == ("k@b.de",)
    message, to, sender = calls[0]
    assert to == ["k@b.de"] and sender == "site@host.example"
    assert message["Subject"] == "Check Parameter Report: Test-Mail"
    assert "Konfiguration: k" in message.get_body(("plain",)).get_content()


def test_send_test_mail_errors(root, monkeypatch):
    from cmk_addons.plugins.parameter_report.lib import mailer

    monkeypatch.setattr(mailer, "sendmail_available", lambda root: True)
    with pytest.raises(SettingsError, match="No recipients"):
        gui_backend.send_test_mail(root, None)  # base without a schedule
    _prof(root, "k", SCHED_K.format(enabled="false"))

    def fail(message, to, sender):
        raise RuntimeError("exit 75")

    monkeypatch.setattr(mailer, "_send", fail)
    monkeypatch.setattr(mailer, "default_sender", lambda: "site@host.example")
    with pytest.raises(SettingsError, match="Test mail failed: exit 75"):
        gui_backend.send_test_mail(root, "k")
    monkeypatch.setattr(mailer, "sendmail_available", lambda root: False)
    with pytest.raises(SettingsError, match="sendmail is missing"):
        gui_backend.send_test_mail(root, "k")
