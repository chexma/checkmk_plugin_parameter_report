from datetime import datetime

import pytest
from cmk_addons.plugins.parameter_report.lib import gui_backend, runstate, schedules
from cmk_addons.plugins.parameter_report.lib.schedule_config import Schedule


@pytest.fixture
def root(tmp_path):
    (tmp_path / "etc").mkdir()
    return tmp_path


def _sched(root, name, text):
    if name is None:
        path = root / "etc/parameter_report.toml"
    else:
        (root / "etc/parameter_report.d").mkdir(exist_ok=True)
        path = root / f"etc/parameter_report.d/{name}.toml"
    path.write_text(path.read_text() + text if path.exists() else text)


DAILY_6 = (
    '\n[schedule]\nenabled = true\nperiod = "day"\nday = 0\ntime = "06:00"\n'
    'recipients = ["a@b.de"]\n'
)
MON = datetime(2026, 10, 12, 6, 10)  # a Monday, 10 minutes after 06:00


def test_due():
    s = Schedule(True, "day", 0, "06:00", ())
    six = datetime(2026, 10, 12, 6, 0).timestamp()
    assert schedules.due(s, 0, MON)
    assert not schedules.due(s, six + 1, MON)  # already ran
    assert not schedules.due(s, 0, datetime(2026, 10, 12, 7, 5))  # more than an hour late
    assert not schedules.due(Schedule(False, "day", 0, "06:00", ()), 0, MON)


def test_run_due_starts_one_and_records(root):
    _sched(root, None, DAILY_6)
    _sched(root, "k", DAILY_6)
    started = []
    name = schedules.run_due(root, MON, start=lambda r, p, rec: started.append((p, rec)))
    assert started == [(None, ("a@b.de",))] and name == ""
    assert schedules.load_state(root)[""]["last_run"] == MON.timestamp()
    name = schedules.run_due(root, MON, start=lambda r, p, rec: started.append((p, rec)))
    assert name == "k" and started[-1] == ("k", ("a@b.de",))  # next minute: the profile


def test_waits_while_locked(root):
    _sched(root, None, DAILY_6)
    with runstate.RunLock(runstate.lock_path(root)):
        assert schedules.run_due(root, MON, start=lambda *a: pytest.fail("started")) is None
    assert schedules.load_state(root) == {}


def test_skips_after_an_hour(root):
    _sched(root, None, DAILY_6)
    late = datetime(2026, 10, 12, 7, 30)
    assert schedules.run_due(root, late, start=lambda *a: pytest.fail("started")) is None


def test_invalid_profile_records_error(root):
    _sched(root, "bad", DAILY_6 + "keep = = 1\n")
    assert schedules.run_due(root, MON, start=lambda *a: pytest.fail("started")) is None
    # an unparsable file has no readable schedule: nothing due, nothing recorded
    _sched(root, "k", 'collect_mode = "nope"\n' + DAILY_6)  # top-level key before the table
    assert schedules.run_due(root, MON, start=lambda *a: pytest.fail("started")) is None
    state = schedules.load_state(root)["k"]
    assert state["last_run"] == MON.timestamp() and "collect_mode" in state["last_error"]


def test_state_follows_rename(root):
    schedules.save_state(root, {"a": {"last_run": 5.0, "last_error": None}})
    schedules.rename_state(root, "a", "b")
    assert schedules.load_state(root) == {"b": {"last_run": 5.0, "last_error": None}}


def test_state_of_deleted_profile_removed(root):
    schedules.save_state(root, {"gone": {"last_run": 5.0, "last_error": None}})
    schedules.run_due(root, MON, start=lambda *a: None)
    assert "gone" not in schedules.load_state(root)


def test_broken_state_file(root):
    path = root / schedules.STATE_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    assert schedules.load_state(root) == {}


def test_state_entries_are_sanitized(root):
    path = root / schedules.STATE_FILE
    path.parent.mkdir(parents=True)
    path.write_text('{"": "x", "k": {"last_run": "y"}, "t": {"last_run": true}}')
    assert schedules.load_state(root) == {}


def test_lock_race_keeps_state(root):
    _sched(root, None, DAILY_6)

    def locked(*a):
        raise gui_backend.StartError("busy", locked=True)

    assert schedules.run_due(root, MON, start=locked) is None
    assert schedules.load_state(root) == {}


def test_failed_start_records_error(root):
    _sched(root, None, DAILY_6)

    def broken(*a):
        raise RuntimeError("boom")

    assert schedules.run_due(root, MON, start=broken) is None
    assert schedules.load_state(root) == {"": {"last_run": MON.timestamp(), "last_error": "boom"}}


def test_not_due_when_scheduled_time_is_in_the_future(monkeypatch):
    # DST spring forward: 02:xx is normalised to 03:xx, so "scheduled" lies after now
    s = Schedule(True, "day", 0, "02:30", ())
    now = datetime(2026, 3, 29, 3, 10)
    future = datetime(2026, 3, 29, 3, 30).timestamp()
    monkeypatch.setattr("cmk.utils.schedule.last_scheduled_time", lambda *a: future)
    assert not schedules.due(s, 0, now)
    assert schedules.due(s, 0, datetime(2026, 3, 29, 3, 40))  # once the time has come


def test_skip_is_recorded_once_after_the_window(root):
    _sched(root, None, DAILY_6)
    with runstate.RunLock(runstate.lock_path(root)):
        assert schedules.run_due(root, MON, start=lambda *a: pytest.fail("started")) is None
        assert schedules.load_state(root) == {}  # still inside the window
        late = datetime(2026, 10, 12, 7, 5)
        assert schedules.run_due(root, late, start=lambda *a: pytest.fail("started")) is None
    entry = schedules.load_state(root)[""]
    assert entry["last_run"] == late.timestamp()
    assert entry["last_error"].startswith("skipped: another report was running")
    later = datetime(2026, 10, 12, 7, 6)
    schedules.run_due(root, later, start=lambda *a: pytest.fail("started"))
    assert schedules.load_state(root)[""]["last_run"] == late.timestamp()  # not again


def test_old_appointment_of_a_new_schedule_is_not_a_skip(root):
    _sched(root, None, DAILY_6)
    # last 06:00 was days ago for a weekly schedule: no state entry, older than 24 h
    _sched(root, "w", '\n[schedule]\nenabled = true\nperiod = "week"\nday = 0\ntime = "06:00"\n')
    (root / "etc/parameter_report.toml").unlink()
    wed = datetime(2026, 10, 14, 12, 0)  # Monday 06:00 is 2 days ago
    assert schedules.run_due(root, wed, start=lambda *a: pytest.fail("started")) is None
    assert schedules.load_state(root) == {}


def test_skip_of_an_entry_with_state_older_than_a_day(root):
    _sched(root, "w", '\n[schedule]\nenabled = true\nperiod = "week"\nday = 0\ntime = "06:00"\n')
    schedules.save_state(root, {"w": {"last_run": 5.0, "last_error": None}})
    wed = datetime(2026, 10, 14, 12, 0)
    assert schedules.run_due(root, wed, start=lambda *a: pytest.fail("started")) is None
    assert "skipped" in schedules.load_state(root)["w"]["last_error"]
