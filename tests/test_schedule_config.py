import pytest
from cmk_addons.plugins.parameter_report.lib import schedule_config as sc
from cmk_addons.plugins.parameter_report.lib.settings import SettingsError, check_address

GOOD = {"enabled": True, "period": "week", "day": 0, "time": "06:00", "recipients": ["a@b.de"]}


@pytest.mark.parametrize("address", ["a@b.de", "first.last+tag@sub.example.com"])
def test_address_accepts(address):
    assert check_address(address, "recipients") == address


@pytest.mark.parametrize(
    "address",
    [
        "",
        "a",
        "a@b",
        "a b@c.de",
        "a@b.de\nBcc: x@y.z",
        "a@b.de, c@d.de",
        "<a@b.de>",
        "a@b.de\r",
        "x" * 250 + "@b.de",
        "-oX@b.de",
        "a@x..y",
        "a@.b.de",
        "a@b.de.",
    ],
)
def test_address_rejects(address):
    with pytest.raises(SettingsError, match="is not a valid mail address"):
        check_address(address, "recipients")


def test_schedule_round_trip_and_description():
    s = sc.schedule_from_toml(GOOD, "base")
    assert s == sc.Schedule(True, "week", 0, "06:00", ("a@b.de",))
    assert sc.schedule_to_toml(s) == GOOD
    assert s.cmk_period() == ("week", 0) and s.timeofday() == (6, 0)
    assert s.describe() == "weekly, Mon 06:00"
    assert sc.Schedule(True, "day", 0, "23:59", ()).describe() == "daily, 23:59"
    assert sc.Schedule(True, "month_begin", 15, "07:30", ()).describe() == "monthly, day 15, 07:30"
    assert sc.Schedule(True, "month_end", 1, "07:30", ()).describe() == "monthly, last day, 07:30"
    assert (
        sc.Schedule(True, "month_end", 3, "07:30", ()).describe() == "monthly, 3rd last day, 07:30"
    )
    assert sc.Schedule(True, "day", 0, "06:00", ()).cmk_period() == "day"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"period": "hourly"}, "period"),
        ({"day": 7}, "day"),
        ({"period": "month_begin", "day": 29}, "day"),
        ({"period": "month_end", "day": 0}, "day"),
        ({"time": "24:00"}, "time"),
        ({"time": "6:00"}, "time"),
        ({"enabled": "yes"}, "enabled"),
        ({"recipients": ["bad"]}, "not a valid mail address"),
        ({"recipients": [f"a{i}@b.de" for i in range(21)]}, "at most 20"),
        ({"unknown": 1}, "unknown"),
    ],
)
def test_schedule_errors(change, message):
    with pytest.raises(SettingsError, match=message):
        sc.schedule_from_toml({**GOOD, **change}, "profile k")


def test_schedule_form_round_trip():
    s = sc.Schedule(True, "month_end", 2, "05:15", ("a@b.de", "c@d.de"))
    form = sc.schedule_to_form(s)
    assert form == {
        "enabled": "on",
        "period": "month_end",
        "day": "2",
        "time": "05:15",
        "recipients": "a@b.de, c@d.de",
    }
    assert sc.schedule_from_form(form) == s
    assert sc.schedule_from_form({"period": "day", "day": "", "time": "", "recipients": ""}) is None
    assert sc.schedule_to_form(None)["enabled"] == ""


def test_mail_settings():
    assert sc.mail_from_toml({}) == sc.MailSettings()
    m = sc.mail_from_toml({"sender": "cmk@x.de", "max_attachment_mb": 5})
    assert m == sc.MailSettings("cmk@x.de", 5)
    assert sc.mail_to_toml(sc.MailSettings()) == {}
    assert sc.mail_from_form(sc.mail_to_form(m)) == m
    for bad in ({"max_attachment_mb": 0}, {"max_attachment_mb": 101}, {"sender": "x"}):
        with pytest.raises(SettingsError):
            sc.mail_from_toml(bad)


def test_mail_default_size_is_10_mb():
    assert sc.MailSettings().max_attachment_mb == 10
    assert sc.mail_from_toml({}).max_attachment_mb == 10
    assert sc.mail_to_toml(sc.MailSettings(max_attachment_mb=10)) == {}
    assert sc.mail_to_toml(sc.MailSettings(max_attachment_mb=20)) == {"max_attachment_mb": 20}


def test_form_ignores_day_for_a_daily_schedule():
    form = {"enabled": "on", "period": "day", "day": "3", "time": "06:00", "recipients": "a@b.de"}
    assert sc.schedule_from_form(form).day == 0
