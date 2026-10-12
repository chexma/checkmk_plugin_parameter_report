import pytest
from cmk_addons.plugins.parameter_report.lib.model import Origin

pytestmark = pytest.mark.site


@pytest.fixture(scope="module")
def result(site_with_test_hosts):
    from cmk_addons.plugins.parameter_report.lib import collector

    return collector.collect(["spike-0010", "spike-0000", "no-such-host"])


def _service(result, host, description):
    host_report = next(h for h in result.hosts if h.name == host)
    return next(s for s in host_report.services if s.description == description)


def _value(service, key):
    standard = next(ps for ps in service.param_sets if ps.timeperiod is None)
    return next(v for v in standard.values if v.key == key)


def test_hosts_in_requested_order_and_unknown_reported(result):
    assert [h.name for h in result.hosts] == ["spike-0010", "spike-0000"]
    assert result.unknown_hosts == ("no-such-host",)
    assert all(h.error is None for h in result.hosts)


def test_meta_data(result):
    assert result.site == "cmk"
    assert result.version == "2.5.0p15"
    assert result.edition == "ultimate"
    assert result.untested_version is False
    assert set(result.timings) == {"load_config", "collect"}


def test_rule_value(result):
    svc = _service(result, "spike-0010", "Filesystem /data/vol10")
    levels = _value(svc, "levels")
    assert levels.value == (90.0, 95.0)
    assert levels.origin is Origin.RULE
    assert _value(svc, "magic").origin is Origin.RULE


def test_default_value(result):
    svc = _service(result, "spike-0010", "Filesystem /data/vol10")
    assert _value(svc, "trend_range").origin is Origin.DEFAULT


def test_discovered_value(result):
    svc = _service(result, "spike-0010", "Interface 01")
    speed = _value(svc, "discovered_speed")
    assert speed.value == 10000000000
    assert speed.origin is Origin.DISCOVERED


def test_host_data(result):
    host = result.hosts[0]
    assert host.addresses == ("IPv4: 127.0.0.11",)
    assert host.is_cluster is False
    assert host.agent_mode == "Checkmk agent"
    assert host.datasources == ("Datasource program",)
    assert host.hostgroups == ("check_mk",)
    assert host.contactgroups == ("all",)
    assert host.label_sources.get("cmk/site") == "discovered"
    assert len(host.services) == 50
    assert all(not s.timespecific for s in host.services)


def test_all_hosts_when_none_requested(site_with_test_hosts):
    from cmk_addons.plugins.parameter_report.lib import collector

    full = collector.collect(None)
    names = [h.name for h in full.hosts]
    assert names == sorted(names)
    assert sum(n.startswith("spike-") for n in names) == 1000


def test_folder_filter(site_with_test_hosts):
    from cmk_addons.plugins.parameter_report.lib import collector

    assert len(collector.collect(None, folders=("spike",)).hosts) == 1000
    assert collector.collect(None, folders=("does-not-exist",)).hosts == ()


def test_tag_and_label_filter_and_duplicates(site_with_test_hosts):
    from cmk_addons.plugins.parameter_report.lib import collector

    result = collector.collect(
        ["spike-0001", "spike-0002", "spike-0001"],
        host_tags=(("address_family", "ip-v4-only"),),
        host_labels=(("cmk/site", "cmk"),),
    )
    assert [h.name for h in result.hosts] == ["spike-0001", "spike-0002"]
    excluded = collector.collect(["spike-0001"], host_tags=(("address_family", "no-ip"),))
    assert excluded.hosts == ()
    assert excluded.unknown_hosts == ("spike-0001",)


@pytest.fixture(scope="module")
def timed(site_with_test_hosts):
    from cmk_addons.plugins.parameter_report.lib import collector

    return collector.collect(["spike-0020", "spike-0021", "spike-0022"])


def _sets(result, host):
    return _service(result, host, "Filesystem /var").param_sets


def test_always_active_period_is_the_main_row(timed):
    standard, *periods = _sets(timed, "spike-0020")
    assert standard.always == (("24X7", "Always"),)
    assert dict((v.key, v.value) for v in standard.values)["levels"] == (2.0, 2.0)
    assert periods == []


def test_own_period_row(timed):
    standard, business = _sets(timed, "spike-0021")
    assert standard.always == ()
    assert dict((v.key, v.value) for v in standard.values)["levels"] == (80.0, 90.0)
    assert business.timeperiod == "pr_business" and business.alias == "Business hours"
    assert dict((v.key, v.value) for v in business.values)["levels"] == (95.0, 98.0)


def test_first_entry_wins_while_active(timed):
    standard, business = _sets(timed, "spike-0022")
    assert standard.always == (("24X7", "Always"),)
    assert dict((v.key, v.value) for v in standard.values)["levels"] == (2.0, 2.0)
    assert dict((v.key, v.value) for v in business.values)["levels"] == (60.0, 70.0)


def test_timeperiods_in_result(timed):
    periods = {p.name: p for p in timed.timeperiods}
    assert periods["24X7"].builtin and periods["24X7"].always
    business = periods["pr_business"]
    assert business.alias == "Business hours" and business.exclude == ("pr_holidays",)
    assert dict(business.days)["monday"] == (("08:00", "17:00"),)
    assert periods["pr_holidays"].exceptions == (("2026-12-24", (("00:00", "24:00"),)),)
