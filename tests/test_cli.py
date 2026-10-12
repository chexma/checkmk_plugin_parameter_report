import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cmk_addons.plugins.parameter_report.lib import cli, config_file, runstate

FAKE_VARS = [
    "PR_FAKE_HOSTS",
    "PR_FAKE_ERRORS",
    "PR_FAKE_FAIL",
    "PR_FAKE_UNKNOWN",
    "PR_FAKE_UNTESTED",
    "PR_FAKE_SLEEP",
]


@pytest.fixture
def site(monkeypatch, tmp_path):
    root = tmp_path / "site"
    (root / "etc").mkdir(parents=True)
    monkeypatch.setenv("OMD_SITE", "cmk")
    monkeypatch.setenv("OMD_ROOT", str(root))
    monkeypatch.setattr(cli, "COLLECT_TARGET", "fake_collector:collect")
    monkeypatch.setenv("PR_FAKE_RECORD", str(tmp_path / "record.json"))
    for var in FAKE_VARS:
        monkeypatch.delenv(var, raising=False)
    return root


def _run(tmp_path, **values):
    """Run the CLI with a config file holding these values (inline collect, 1 worker)."""
    values = {"collect_mode": "inline", "workers": 1, **values}
    cfg = tmp_path / "run.toml"
    cfg.write_text(config_file.dump(values))
    return cli.main(["--config", str(cfg)])


def _status(root):
    return json.loads((root / "var/parameter_report/status.json").read_text())


def _meta(pdf):
    return json.loads(pdf.with_suffix(".json").read_text())


def _record(tmp_path):
    return json.loads((tmp_path / "record.json").read_text())


def test_requires_site_user(monkeypatch, capsys):
    monkeypatch.delenv("OMD_SITE", raising=False)
    assert cli.main([]) == 1
    assert "site user" in capsys.readouterr().err


def test_success_writes_pdf_metadata_and_status(site, tmp_path, capsys):
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out)) == 0
    assert out.read_bytes().startswith(b"%PDF")
    meta = _meta(out)
    assert meta["exit_code"] == 0 and meta["hosts"] == 2 and meta["services"] == 4
    assert set(meta["peak_rss_mb"]) == {"main", "collect", "render_worker"}
    status = _status(site)
    assert status["state"] == "done" and status["exit_code"] == 0
    assert status["output"] == str(out) and status["phase_progress"] == [2, 2]
    assert str(out) in capsys.readouterr().out


def test_collect_in_subprocess_is_default(site, tmp_path):
    cfg = tmp_path / "run.toml"
    cfg.write_text(config_file.dump({"output": str(tmp_path / "r.pdf"), "workers": 1}))
    assert cli.main(["--config", str(cfg)]) == 0
    assert _record(tmp_path)["pid"] != os.getpid()


def test_failed_host_gives_exit_code_2(site, tmp_path, monkeypatch):
    monkeypatch.setenv("PR_FAKE_ERRORS", "b")
    assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 2
    status = _status(site)
    assert status["exit_code"] == 2 and "1 host" in status["message"]


def test_warnings(site, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PR_FAKE_UNKNOWN", "zz")
    monkeypatch.setenv("PR_FAKE_UNTESTED", "1")
    assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 0
    err = capsys.readouterr().err
    assert "unknown host: zz" in err
    assert "not tested" in err


def test_no_hosts(site, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PR_FAKE_HOSTS", "")
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out)) == 1
    assert "No hosts" in capsys.readouterr().err
    assert _status(site)["state"] == "failed"
    assert not out.exists()


@pytest.mark.parametrize("mode", ["inline", "subprocess"])
def test_collector_failure(site, tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setenv("PR_FAKE_FAIL", "1")
    assert _run(tmp_path, collect_mode=mode, output=str(tmp_path / "r.pdf")) == 1
    err = capsys.readouterr().err
    assert "Could not load the configuration: kaputt" in err
    assert "Traceback" not in err
    assert _status(site)["state"] == "failed"


def test_default_output_name(site, tmp_path):
    assert _run(tmp_path) == 0
    files = list((site / "var/parameter_report").glob("parameter_report_*.pdf"))
    assert len(files) == 1
    assert re.fullmatch(r"parameter_report_\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{6}\.pdf", files[0].name)


def test_unwritable_output_dir_is_checked_before_collecting(site, tmp_path, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert _run(tmp_path, output=str(locked / "r.pdf")) == 1
    finally:
        locked.chmod(0o700)
    assert "not usable" in capsys.readouterr().err
    assert not (tmp_path / "record.json").exists()


@pytest.mark.parametrize(
    "content, message",
    [
        ("chunk_size = 0\n", "chunk_size"),
        ('workers = "abc"\n', "workers"),
        ("column_widths = [90, 90, 90, 90]\n", "parameter column"),
        ('highlight_color = "gelb"\n', "highlight_color"),
        ('host_tags = ["nocolon"]\n', "host_tags"),
    ],
)
def test_invalid_settings(site, tmp_path, capsys, content, message):
    cfg = tmp_path / "bad.toml"
    cfg.write_text(content)
    assert cli.main(["--config", str(cfg)]) == 1
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err
    assert not (tmp_path / "record.json").exists()


def test_config_with_schedule_and_mail_tables_still_runs(site, tmp_path):
    out = tmp_path / "r.pdf"
    cfg = tmp_path / "with_tables.toml"
    cfg.write_text(
        f'collect_mode = "inline"\nworkers = 1\noutput = "{out}"\n\n'
        '[schedule]\nenabled = true\nperiod = "day"\nday = 0\ntime = "06:00"\n'
        'recipients = ["a@b.de"]\n\n[mail]\nmax_attachment_mb = 5\n'
    )
    assert cli.main(["--config", str(cfg)]) == 0
    assert out.exists()


def test_central_config_is_the_default(site, tmp_path):
    out = tmp_path / "r.pdf"
    (site / "etc/parameter_report.toml").write_text(
        f'collect_mode = "inline"\nworkers = 1\nmin_split_lines = 3\noutput = "{out}"\n'
    )
    assert cli.main([]) == 0
    assert _meta(out)["settings"]["min_split_lines"] == 3


def test_explicit_config_replaces_central_config(site, tmp_path):
    (site / "etc/parameter_report.toml").write_text("min_split_lines = 3\n")
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out), chunk_size=9) == 0
    settings = _meta(out)["settings"]
    assert settings["chunk_size"] == 9 and settings["min_split_lines"] == 2  # central file not read


def test_without_any_config_builtin_defaults(site):
    assert cli.main([]) == 0  # built-in defaults; the site fixture patches the collector
    assert len(list((site / "var/parameter_report").glob("parameter_report_*.pdf"))) == 1


def test_missing_explicit_config(site, tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "missing.toml")]) == 1
    assert "not readable" in capsys.readouterr().err


def test_help_lists_only_config_and_verbose():
    text = cli._parser().format_help()
    assert "--config" in text and "--verbose" in text and "--hosts" not in text


def test_explicit_config_with_unknown_key(site, tmp_path, capsys):
    cfg = tmp_path / "x.toml"
    cfg.write_text("chunksize = 7\n")
    assert cli.main(["--config", str(cfg)]) == 1
    assert "chunksize" in capsys.readouterr().err


def test_selection_is_passed_to_collector(site, tmp_path):
    values = dict(
        hosts=["a"],
        folders=["linux/web", "/db/"],
        host_tags=["criticality:prod"],
        host_labels=["os:linux"],
    )
    assert _run(tmp_path, output=str(tmp_path / "r.pdf"), **values) == 0
    record = _record(tmp_path)
    assert record["host_names"] == ["a"]
    assert record["folders"] == ["linux/web", "db"]
    assert record["host_tags"] == [["criticality", "prod"]]
    assert record["host_labels"] == [["os", "linux"]]


def test_changed_only(site, tmp_path):
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out), changed_only=True) == 0
    assert _meta(out)["services"] == 2


def test_lock_held(site, tmp_path, capsys):
    with runstate.RunLock(runstate.lock_path(site)):
        assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 1
    assert "already running" in capsys.readouterr().err
    assert not (site / "var/parameter_report/status.json").exists()


def test_stale_temp_dirs_are_removed(site, tmp_path):
    stale = tmp_path / ".parameter_report-old"
    stale.mkdir()
    (stale / "chunk.pdf").write_text("x")
    (stale / runstate.SITE_MARKER).write_text("cmk")
    assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 0
    assert not stale.exists()


def test_verbose_prints_timings_and_peaks(site, tmp_path, capsys):
    cfg = tmp_path / "run.toml"
    values = {"collect_mode": "inline", "workers": 1, "output": str(tmp_path / "r.pdf")}
    cfg.write_text(config_file.dump(values))
    assert cli.main(["--config", str(cfg), "-v"]) == 0
    err = capsys.readouterr().err
    for word in [
        "load_config",
        "collect",
        "render",
        "merge",
        "peak main",
        "peak collect",
        "peak render_worker",
    ]:
        assert word in err, word


def test_sigterm_cancels_run(site, tmp_path, monkeypatch):
    monkeypatch.setenv("PR_FAKE_SLEEP", "60")
    code = (
        "import sys\n"
        "from cmk_addons.plugins.parameter_report.lib import cli\n"
        "cli.COLLECT_TARGET = 'fake_collector:collect'\n"
        "sys.exit(cli.main(sys.argv[1:]))\n"
    )
    tests_dir = str(Path(__file__).parent)
    # sitecustomize adds local/lib/python3 from OMD_ROOT, which points to tmp here
    package_root = str(Path(cli.__file__).parents[4])
    paths = [tests_dir, package_root, os.environ.get("PYTHONPATH")]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, paths)))
    cfg = tmp_path / "run.toml"
    cfg.write_text(config_file.dump({"output": str(tmp_path / "r.pdf"), "workers": 1}))
    proc = subprocess.Popen([sys.executable, "-c", code, "--config", str(cfg)], env=env)
    record = tmp_path / "record.json"
    for _ in range(600):
        if record.exists():
            break
        time.sleep(0.1)
    collector_pid = json.loads(record.read_text())["pid"]
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=60) == 1
    assert _status(site)["state"] == "cancelled"
    assert not list(tmp_path.glob(".parameter_report-*"))
    time.sleep(1)
    with pytest.raises(ProcessLookupError):
        os.kill(collector_pid, 0)


def test_bin_script_is_executable():
    path = os.path.join(os.path.dirname(__file__), "..", "bin", "cmk-parameter-report")
    assert os.access(path, os.X_OK)


def test_unexpected_error_after_rendering_sets_failed(site, tmp_path, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise OSError(28, "Kein Platz auf dem Gerät")

    monkeypatch.setattr(runstate, "write_metadata", broken)
    assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 1
    err = capsys.readouterr().err
    assert "Kein Platz" in err and "Traceback" not in err
    status = _status(site)
    assert status["state"] == "failed" and "Kein Platz" in status["message"]


def test_unusable_status_file_is_checked_before_collecting(site, tmp_path, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert (
            _run(tmp_path, output=str(tmp_path / "r.pdf"), status_file=str(locked / "s.json")) == 1
        )
    finally:
        locked.chmod(0o700)
    err = capsys.readouterr().err
    assert "Status file" in err and "Traceback" not in err
    assert not (tmp_path / "record.json").exists()


def test_cancel_handlers_are_active_before_status_and_cleanup(site, tmp_path, monkeypatch):
    seen = []
    real_status, real_cleanup = runstate.StatusFile, runstate.cleanup_stale_temp

    class RecordingStatus(real_status):
        def __init__(self, *args, **kwargs):
            seen.append(signal.getsignal(signal.SIGTERM))
            super().__init__(*args, **kwargs)

    def recording_cleanup(*args):
        seen.append(signal.getsignal(signal.SIGTERM))
        return real_cleanup(*args)

    monkeypatch.setattr(runstate, "StatusFile", RecordingStatus)
    monkeypatch.setattr(runstate, "cleanup_stale_temp", recording_cleanup)
    assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 0
    assert len(seen) == 2
    assert all(handler not in (signal.SIG_DFL, signal.default_int_handler) for handler in seen)


def test_metadata_records_site(site, tmp_path):
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out)) == 0
    assert _meta(out)["site"] == "cmk"


def _site_profile(site, name, text):
    d = site / "etc/parameter_report.d"
    d.mkdir(exist_ok=True)
    (d / f"{name}.toml").write_text(text)


def test_profile_on_top_of_base(site, tmp_path):
    out = tmp_path / "r.pdf"
    (site / "etc/parameter_report.toml").write_text(
        f'collect_mode = "inline"\nworkers = 1\nmin_split_lines = 3\noutput = "{out}"\n'
    )
    _site_profile(site, "kunde_a", 'min_split_lines = 5\ncover_customer = "A AG"\n')
    assert cli.main(["--profile", "kunde_a"]) == 0
    settings = _meta(out)["settings"]
    assert settings["min_split_lines"] == 5 and settings["cover_customer"] == "A AG"
    assert settings["profile"] == "kunde_a"


def test_unknown_profile(site, capsys):
    _site_profile(site, "a", "")
    assert cli.main(["--profile", "x"]) == 1
    assert "Profile x not found. Available: a" in capsys.readouterr().err


def test_invalid_profile_name(site, capsys):
    assert cli.main(["--profile", "../x"]) == 1
    assert "Profile names" in capsys.readouterr().err


def test_empty_profile_name_is_no_fallback_to_base(site, capsys):
    assert cli.main(["--profile", ""]) == 1
    assert "Profile names" in capsys.readouterr().err


def test_profile_and_config_exclusive(site, tmp_path, capsys):
    assert cli.main(["--profile", "a", "--config", str(tmp_path / "x.toml")]) == 1
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.fixture
def sent(monkeypatch):
    from cmk_addons.plugins.parameter_report.lib import mailer

    calls = []
    monkeypatch.setattr(mailer, "_send", lambda m, to, sender: calls.append(to))
    monkeypatch.setattr(mailer, "default_sender", lambda: "site@host.example")
    return calls


def test_mail_to_in_run_file_sends_mail(site, tmp_path, sent):
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out), mail_to=["a@b.de"]) == 0
    assert sent == [["a@b.de"]]
    assert _meta(out)["mail"] == {"to": ["a@b.de"], "sent": True, "attached": True, "error": None}


def test_mail_failure_keeps_report(site, tmp_path, monkeypatch):
    from cmk_addons.plugins.parameter_report.lib import mailer

    def fail(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(mailer, "_send", fail)
    monkeypatch.setattr(mailer, "default_sender", lambda: "s@h.de")
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out), mail_to=["a@b.de"]) == 0
    assert out.exists() and _meta(out)["mail"]["error"].startswith("boom")


def test_no_mail_without_mail_to(site, tmp_path, sent):
    out = tmp_path / "r.pdf"
    assert _run(tmp_path, output=str(out)) == 0
    assert sent == [] and "mail" not in _meta(out)


def test_mail_switch_uses_profile_recipients(site, tmp_path, sent):
    out = tmp_path / "r.pdf"
    (site / "etc/parameter_report.toml").write_text(
        f'collect_mode = "inline"\nworkers = 1\noutput = "{out}"\n'
    )
    d = site / "etc/parameter_report.d"
    d.mkdir()
    (d / "k.toml").write_text(
        '\n[schedule]\nenabled = false\nperiod = "day"\nday = 0\ntime = "06:00"\n'
        'recipients = ["k@b.de"]\n'
    )
    assert cli.main(["--profile", "k", "--mail"]) == 0
    assert sent == [["k@b.de"]]


def test_mail_switch_errors(site, tmp_path, capsys):
    assert cli.main(["--mail"]) == 1
    assert "no recipients configured" in capsys.readouterr().err
    assert cli.main(["--config", str(tmp_path / "x.toml"), "--mail"]) == 1
    assert "--mail" in capsys.readouterr().err


def test_metadata_exists_before_mailing(site, tmp_path, monkeypatch):
    from cmk_addons.plugins.parameter_report.lib import mailer

    out = tmp_path / "r.pdf"
    seen = []

    def check(message, to, sender):
        seen.append(json.loads(out.with_suffix(".json").read_text()))

    monkeypatch.setattr(mailer, "_send", check)
    monkeypatch.setattr(mailer, "default_sender", lambda: "s@h.de")
    assert _run(tmp_path, output=str(out), mail_to=["a@b.de"]) == 0
    assert len(seen) == 1 and "mail" not in seen[0] and seen[0]["exit_code"] == 0
    assert _meta(out)["mail"]["sent"] is True


def _old_report(site, name, profile):
    reports = site / "var/parameter_report"
    reports.mkdir(parents=True, exist_ok=True)
    pdf = reports / name
    pdf.write_bytes(b"%PDF")
    pdf.with_suffix(".json").write_text(
        json.dumps({"site": "cmk", "settings": {"profile": profile}})
    )
    os.utime(pdf, (time.time() - 3 * 86400,) * 2)
    return pdf


def test_config_run_uses_the_base_retention(site, tmp_path):
    """A --config file must not delete the reports of every profile with its own value."""
    old = _old_report(site, "parameter_report_old.pdf", "kunde_a")
    out = site / "var/parameter_report/parameter_report_new.pdf"
    assert _run(tmp_path, output=str(out), retention_days=1) == 0
    assert old.exists()  # the base keeps reports forever
    (site / "etc/parameter_report.toml").write_text("retention_days = 2\n")
    assert _run(tmp_path, output=str(out), retention_days=30) == 0
    assert not old.exists()  # the base deletes after 2 days


def test_run_applies_differences_and_passes_used_timeperiods(site, tmp_path, monkeypatch):
    from cmk_addons.plugins.parameter_report.lib.model import (
        CollectResult,
        HostReport,
        Origin,
        ParamSet,
        ParamValue,
        ServiceReport,
        TimePeriod,
    )

    levels = ParamValue("levels", (80.0, 90.0), Origin.DEFAULT)
    night = ParamValue("levels", (95.0, 98.0), Origin.RULE)
    sets = (
        ParamSet(None, (levels,)),
        ParamSet("night", (night,), alias="Night"),
        ParamSet("same", (levels,)),
    )
    svc = ServiceReport("Filesystem /", "df", "/", sets)
    periods = (TimePeriod("night", "Night"), TimePeriod("same", "Same"), TimePeriod("x", "X"))
    result = CollectResult(
        (HostReport("h", services=(svc,)),),
        (),
        "cmk",
        "2.5.0p15",
        "ultimate",
        False,
        {},
        timeperiods=periods,
    )
    monkeypatch.setattr(cli, "_collect", lambda settings: (result, 1))
    captured = {}

    def fake_build(hosts, summary, output, settings, progress=None):
        captured["hosts"], captured["summary"] = hosts, summary
        raise cli.ReportError("stop here")

    monkeypatch.setattr(cli, "build_report", fake_build)
    assert _run(tmp_path, output=str(tmp_path / "r.pdf")) == 1  # ReportError: exit 1
    sets = captured["hosts"][0].services[0].param_sets
    assert [ps.timeperiod for ps in sets] == [None, "night"]
    assert [p.name for p in captured["summary"].timeperiods] == ["night"]
