import pytest
from cmk_addons.plugins.parameter_report.lib.model import (
    ALWAYS,
    HostReport,
    Origin,
    ParamSet,
    ParamValue,
    ServiceReport,
    TimePeriod,
    classify_params,
    covers_whole_day,
    day_groups,
    display_name,
    host_matches,
    is_always_active,
    only_differences,
    only_rule_values,
    time_period_from_spec,
    undefined_time_period,
    used_timeperiods,
)


def test_value_equal_to_default_is_default():
    result = classify_params({"levels": (80.0, 90.0)}, {"levels": (80.0, 90.0)}, {})
    assert result == (ParamValue("levels", (80.0, 90.0), Origin.DEFAULT),)


def test_value_from_discovery_is_discovered():
    result = classify_params({"speed": 1000}, {}, {"speed": 1000})
    assert result == (ParamValue("speed", 1000, Origin.DISCOVERED),)


def test_discovered_wins_over_default_when_equal_to_both():
    result = classify_params({"x": 1}, {"x": 1}, {"x": 1})
    assert result[0].origin is Origin.DISCOVERED


def test_value_differing_from_default_is_rule():
    result = classify_params({"levels": (90.0, 95.0)}, {"levels": (80.0, 90.0)}, {})
    assert result[0].origin is Origin.RULE


def test_discovered_key_changed_by_rule_is_rule():
    result = classify_params({"speed": 10}, {}, {"speed": 1000})
    assert result[0].origin is Origin.RULE


def test_key_without_default_is_rule():
    result = classify_params({"magic": 0.8}, {"levels": (80.0, 90.0)}, {})
    assert result[0].origin is Origin.RULE


def test_order_of_effective_params_is_kept():
    result = classify_params({"b": 1, "a": 2}, {}, {})
    assert [v.key for v in result] == ["b", "a"]


def test_timespecific_flag():
    plain = ServiceReport("CPU load", "cpu_loads", None, (ParamSet(None, ()),))
    timed = ServiceReport(
        "CPU load", "cpu_loads", None, (ParamSet(None, ()), ParamSet("workhours", ()))
    )
    assert not plain.timespecific
    assert timed.timespecific


def _svc(name, *sets):
    return ServiceReport(name, "df", None, tuple(sets))


def test_only_rule_values_filters_values_services_and_keeps_hosts():
    rule = ParamValue("levels", (90.0, 95.0), Origin.RULE)
    default = ParamValue("trend", 24, Origin.DEFAULT)
    discovered = ParamValue("speed", 1000, Origin.DISCOVERED)
    host = HostReport(
        "h",
        services=(
            _svc("mixed", ParamSet(None, (rule, default))),
            _svc("defaults", ParamSet(None, (default, discovered))),
            _svc(
                "timed",
                ParamSet(None, (default,)),
                ParamSet("night", (rule,)),
                ParamSet("day", (default,)),
            ),
        ),
    )
    (result,) = only_rule_values([host, HostReport("empty")])[:1]
    assert [s.description for s in result.services] == ["mixed", "timed"]
    assert result.services[0].param_sets == (ParamSet(None, (rule,)),)
    # time period sets hold differences from the standard set: all caused by rules, kept as is
    assert result.services[1].param_sets == (
        ParamSet(None, ()),
        ParamSet("night", (rule,)),
        ParamSet("day", (default,)),
    )
    assert only_rule_values([HostReport("empty")]) == (HostReport("empty"),)


def test_only_rule_values_keeps_the_always_prefix():
    rule = ParamValue("levels", (2.0, 2.0), Origin.RULE)
    svc = _svc("fs", ParamSet(None, (rule,), always=(("24X7", "Always"),)))
    (host,) = only_rule_values([HostReport("h", services=(svc,))])
    assert host.services[0].param_sets[0].always == (("24X7", "Always"),)


def test_only_differences():
    levels = ParamValue("levels", (80.0, 90.0), Origin.DEFAULT)
    trend = ParamValue("trend", 24, Origin.DEFAULT)
    night_levels = ParamValue("levels", (95.0, 98.0), Origin.RULE)
    extra = ParamValue("magic", 0.8, Origin.RULE)
    host = HostReport(
        "h",
        services=(
            _svc(
                "fs",
                ParamSet(None, (levels, trend)),
                ParamSet("night", (night_levels, trend), alias="Night"),
                ParamSet("same", (levels, trend)),
                ParamSet("more", (levels, trend, extra)),
            ),
            _svc("plain", ParamSet(None, (levels,))),
        ),
    )
    (result,) = only_differences([host])
    assert result.services[0].param_sets == (
        ParamSet(None, (levels, trend)),
        ParamSet("night", (night_levels,), alias="Night"),
        ParamSet("more", (extra,)),
    )
    assert result.services[1] == host.services[1]
    assert not result.services[0].param_sets[0].always


def test_only_differences_leaves_errors_and_empty_services_alone():
    broken = HostReport("broken", error="KeyError: x")
    empty = HostReport("e", services=(_svc("none", ParamSet(None, ())),))
    assert only_differences([broken, empty]) == (broken, empty)


def test_changed_only_keeps_a_difference_equal_to_the_default():
    rule = ParamValue("levels", (90.0, 95.0), Origin.RULE)
    back_to_default = ParamValue("levels", (80.0, 90.0), Origin.DEFAULT)
    svc = _svc("fs", ParamSet(None, (rule,)), ParamSet("night", (back_to_default,)))
    hosts = only_rule_values(only_differences([HostReport("h", services=(svc,))]))
    assert hosts[0].services[0].param_sets[1] == ParamSet("night", (back_to_default,))


def test_used_timeperiods_follows_excludes_and_survives_cycles():
    a = TimePeriod("a", "A", exclude=("b",))
    b = TimePeriod("b", "B", exclude=("c", "a"))
    c = TimePeriod("c", "C")
    unused = TimePeriod("unused", "Unused")
    always = TimePeriod("24X7", "Always", builtin=True, always=True)
    svc = _svc(
        "fs",
        ParamSet(None, (), always=(("24X7", "Always"),)),
        ParamSet("a", (ParamValue("x", 1, Origin.RULE),)),
    )
    hosts = [HostReport("h", services=(svc,))]
    result = used_timeperiods(hosts, [unused, c, b, a, always])
    assert [p.name for p in result] == ["a", "24X7", "b", "c"]  # by label: A, Always (24X7), B, C
    assert used_timeperiods([HostReport("x")], [a, b]) == ()


@pytest.mark.parametrize(
    "path, folders, tags, labels, expected",
    [
        ("/wato/linux/web/", (), (), (), True),
        ("/wato/linux/web/", ("linux",), (), (), True),
        ("/wato/linux/web/", ("lin",), (), (), False),
        ("/wato/linux/web/", ("db", "linux/web"), (), (), True),
        ("/wato/", ("",), (), (), True),
        ("/", ("",), (), (), False),
        ("/wato/linux/", (), (("criticality", "prod"),), (), True),
        ("/wato/linux/", (), (("criticality", "test"),), (), False),
        ("/wato/linux/", (), (), (("os", "linux"),), True),
        ("/wato/linux/", (), (), (("os", "windows"),), False),
        ("/wato/linux/", ("linux",), (("criticality", "prod"),), (("os", "linux"),), True),
    ],
)
def test_host_matches(path, folders, tags, labels, expected):
    host_tags = {"criticality": "prod", "agent": "cmk-agent"}
    host_labels = {"os": "linux"}
    assert host_matches(path, host_tags, host_labels, folders, tags, labels) is expected


ALL_DAY = [("00:00", "24:00")]
WEEK = {
    day: ALL_DAY
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
}


def test_display_name():
    assert display_name("night", "Night shift") == "Night shift (night)"
    assert display_name("night", "night") == "night"
    assert display_name("night", "") == "night"


@pytest.mark.parametrize(
    "ranges, expected",
    [
        ([("00:00", "24:00")], True),
        ([("12:00", "24:00"), ("00:00", "12:00")], True),
        ([("00:00", "12:00"), ("11:00", "24:00")], True),
        ([("00:00", "12:00"), ("12:01", "24:00")], False),
        ([("00:00", "23:59")], False),
        ([], False),
    ],
)
def test_covers_whole_day(ranges, expected):
    assert covers_whole_day(ranges) is expected


@pytest.mark.parametrize(
    "name, spec, expected",
    [
        (ALWAYS, {"alias": "Always"}, True),  # by name, whatever the spec says
        ("own", {"alias": "x", **WEEK}, True),
        ("split", {"alias": "x", **WEEK, "monday": [("00:00", "12:00"), ("12:00", "24:00")]}, True),
        ("gap", {"alias": "x", **WEEK, "sunday": [("00:00", "23:00")]}, False),
        ("no_sunday", {"alias": "x", **{d: r for d, r in WEEK.items() if d != "sunday"}}, False),
        ("excl", {"alias": "x", **WEEK, "exclude": ["holidays"]}, False),
        ("short_exception", {"alias": "x", **WEEK, "2026-12-24": [("09:00", "17:00")]}, False),
        ("full_exception", {"alias": "x", **WEEK, "2026-12-24": ALL_DAY}, True),
    ],
)
def test_is_always_active(name, spec, expected):
    assert is_always_active(name, spec) is expected


def test_time_period_from_spec():
    spec = {
        "alias": "Business hours",
        "monday": [("08:00", "17:00")],
        "friday": [("08:00", "12:00")],
        "2026-12-24": [("08:00", "10:00")],
        "2026-01-01": [],
        "exclude": ["pr_holidays"],
    }
    period = time_period_from_spec("pr_business", spec)
    assert period.label == "Business hours (pr_business)"
    assert period.builtin is False and period.always is False and period.defined is True
    assert dict(period.days)["monday"] == (("08:00", "17:00"),)
    assert dict(period.days)["tuesday"] == ()
    assert [day for day, _r in period.days][0] == "monday" and len(period.days) == 7
    assert period.exceptions == (("2026-01-01", ()), ("2026-12-24", (("08:00", "10:00"),)))
    assert period.exclude == ("pr_holidays",)
    builtin = time_period_from_spec(ALWAYS, {"alias": "Always", **WEEK}, builtin=True)
    assert builtin.builtin and builtin.always and builtin.label == "Always (24X7)"


def test_undefined_time_period():
    period = undefined_time_period("gone")
    assert period == TimePeriod("gone", "gone", defined=False)
    assert period.label == "gone" and not period.always


def test_day_groups():
    days = (
        ("monday", (("08:00", "17:00"),)),
        ("tuesday", (("08:00", "17:00"),)),
        ("wednesday", (("08:00", "17:00"),)),
        ("thursday", ()),
        ("friday", (("08:00", "17:00"),)),
        ("saturday", (("00:00", "24:00"),)),
        ("sunday", (("00:00", "24:00"),)),
    )
    assert day_groups(days) == (
        ("monday", "wednesday", (("08:00", "17:00"),)),
        ("friday", "friday", (("08:00", "17:00"),)),
        ("saturday", "sunday", (("00:00", "24:00"),)),
    )
    assert day_groups(()) == ()
