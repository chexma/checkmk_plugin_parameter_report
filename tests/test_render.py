from dataclasses import replace
from datetime import datetime

import pytest
from cmk_addons.plugins.parameter_report.lib import render, render_canvas
from cmk_addons.plugins.parameter_report.lib.model import (
    HostReport,
    Origin,
    ParamSet,
    ParamValue,
    ReportSummary,
    ServiceReport,
    TimePeriod,
)
from cmk_addons.plugins.parameter_report.lib.render import format_value, render_cover
from cmk_addons.plugins.parameter_report.lib.settings import Settings
from pypdf import PdfReader

NOW = datetime(2026, 10, 9, 12, 0)


@pytest.fixture
def render_hosts():
    return render_canvas.render_hosts


def _pdf_text(path):
    reader = PdfReader(path)
    return " ".join(" ".join(page.extract_text().split()) for page in reader.pages)


def _has(text, expected):
    # pypdf splits and joins text runs unpredictably; compare without whitespace
    return "".join(expected.split()) in "".join(text.split())


def _service(description="Filesystem /", values=(), timeperiods=(), **kwargs):
    sets = (ParamSet(None, tuple(values)),) + tuple(
        ParamSet(name, tuple(vals)) for name, vals in timeperiods
    )
    return ServiceReport(description, kwargs.pop("check_plugin", "df"), "/", sets, **kwargs)


def _summary(**kwargs):
    values = dict(
        site="cmk",
        version="2.5.0p15",
        edition="ultimate",
        generated_at=NOW,
        host_count=1000,
        service_count=50000,
    )
    values.update(kwargs)
    return ReportSummary(**values)


# --- value formatting -------------------------------------------------------


def test_format_value_scalars():
    assert format_value(None) == "–"
    assert format_value(True) == "yes"
    assert format_value(False) == "no"
    assert format_value(20) == "20"
    assert format_value("onmagic") == "onmagic"


def test_format_value_sequences_and_dicts():
    assert format_value((80.0, 90.0)) == "80.0 / 90.0"
    assert format_value(("fixed", (5.0, 10.0))) == "fixed / (5.0 / 10.0)"
    assert format_value([]) == "–"
    assert format_value({"both": ("perc", (0.01, 0.1))}) == "{both: perc / (0.01 / 0.1)}"


def test_format_value_unknown_object_uses_str():
    class Odd:
        def __str__(self):
            return "odd-thing"

    assert format_value(Odd()) == "odd-thing"


def test_explicit_password_is_redacted():
    value = {"auth": ("user", ("cmk_postprocessed", "explicit_password", ("uuid1", "s3cret")))}
    text = format_value(value)
    assert "s3cret" not in text
    assert "***" in text


def test_stored_password_shows_store_id_only():
    text = format_value(("cmk_postprocessed", "stored_password", ("my_pw_id", "")))
    assert text == "Password store: my_pw_id"


# --- host pages ------------------------------------------------------------


def test_render_hosts_bookmarks_and_content(tmp_path, render_hosts):
    hosts = [
        HostReport(
            "web01",
            addresses=("IPv4: 10.0.0.1",),
            tags={"agent": "cmk-agent"},
            labels={"cmk/os_family": "linux"},
            label_sources={"cmk/os_family": "discovered"},
            hostgroups=("linux",),
            contactgroups=("all",),
            agent_mode="Checkmk-Agent",
            services=(
                _service(
                    values=[
                        ParamValue("levels", (90.0, 95.0), Origin.RULE),
                        ParamValue("trend_range", 24, Origin.DEFAULT),
                    ]
                ),
            ),
        ),
        HostReport("web02", services=(_service(),)),
    ]
    out = tmp_path / "hosts.pdf"
    render_hosts(hosts, out, NOW)

    reader = PdfReader(out)
    assert [o.title for o in reader.outline] == ["web01", "web02"]
    text = _pdf_text(out)
    for expected in [
        "web01",
        "IPv4: 10.0.0.1",
        "agent:cmk-agent",
        "cmk/os_family:linux (discovered)",
        "Checkmk-Agent",
        "Filesystem /",
        "90.0 / 95.0",
        "trend_range: 24",
        "page 1 of host",
        "2026-10-09 12:00",
    ]:
        assert _has(text, expected), expected


def test_every_footer_names_its_own_host(tmp_path, render_hosts):
    many = tuple(_service(f"Filesystem /data/{i:03d}") for i in range(120))
    hosts = [HostReport("alpha"), HostReport("big", services=many), HostReport("gamma")]
    out = tmp_path / "hosts.pdf"
    render_hosts(hosts, out, NOW)
    footers = []
    for page in PdfReader(out).pages:
        lines = page.extract_text().splitlines()
        name = next(line.split("·")[0].strip() for line in lines if "created" in line)
        number = next(line for line in lines if line.endswith("of host"))
        footers.append((name, int(number.split()[1])))
    big = [number for name, number in footers if name == "big"]
    assert footers[0] == ("alpha", 1) and footers[-1] == ("gamma", 1)
    assert len(big) > 1 and big == list(range(1, len(big) + 1))
    assert len(footers) == len(big) + 2


def test_page_counter_restarts_per_host(tmp_path, render_hosts):
    many = tuple(_service(f"Filesystem /data/{i:03d}") for i in range(120))
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("big", services=many), HostReport("small")], out, NOW)
    reader = PdfReader(out)
    assert len(reader.pages) > 2
    last_page = reader.pages[-1].extract_text()
    assert _has(last_page, "small") and _has(last_page, "page 1 of host")
    assert _has(reader.pages[1].extract_text(), "page 2 of host")


def test_timespecific_and_enforced_marks(tmp_path, render_hosts):
    svc = _service(
        "CPU load",
        check_plugin="cpu_loads",
        values=[ParamValue("levels15", (5.0, 10.0), Origin.RULE)],
        timeperiods=[("workhours", [ParamValue("levels15", (2.0, 4.0), Origin.RULE)])],
        enforced=True,
    )
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("db01", services=(svc,))], out, NOW)
    text = _pdf_text(out)
    for expected in ["(enforced)", "(time specific)", "Time period workhours", "2.0 / 4.0"]:
        assert _has(text, expected), expected


def test_error_host_gets_error_page(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("broken", error="KeyError: 'x'")], out, NOW)
    text = _pdf_text(out)
    assert _has(text, "Error while collecting data")
    assert _has(text, "KeyError: 'x'")


def test_very_long_error_runs_over_pages(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("broken", error="boom " * 8000)], out, NOW)
    assert len(PdfReader(out).pages) > 1


def test_oversized_parameter_row_is_split_over_pages(tmp_path, render_hosts):
    huge = ParamValue("ignored", [f"service number {i}" for i in range(1500)], Origin.RULE)
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("h", services=(_service(values=[huge]),))], out, NOW)
    assert len(PdfReader(out).pages) > 1


def test_markup_characters_are_escaped(tmp_path, render_hosts):
    svc = _service(
        "Log <app> & co",
        values=[ParamValue("pattern", "a<b && c>d", Origin.RULE)],
        servicegroups=("<grp>",),
    )
    host = HostReport("h1", labels={"team": "ops&dev"}, services=(svc,))
    out = tmp_path / "hosts.pdf"
    render_hosts([host], out, NOW)
    text = _pdf_text(out)
    for expected in ["Log <app> & co", "a<b && c>d", "team:ops&dev", "<grp>"]:
        assert _has(text, expected), expected


def test_host_without_services(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("ping-only", agent_mode="Kein Checkmk-Agent")], out, NOW)
    assert _has(_pdf_text(out), "No services configured.")


def test_changed_only_message(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("quiet")], out, NOW, Settings(changed_only=True))
    assert _has(_pdf_text(out), "No parameters changed by rules.")


def test_cluster_shows_nodes(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("cl01", is_cluster=True, parents=("node1", "node2"))], out, NOW)
    text = _pdf_text(out)
    assert _has(text, "Cluster nodes") and _has(text, "node1, node2")
    assert not _has(text, "via DNS")


def test_password_is_not_in_pdf(tmp_path, render_hosts):
    secret = ("cmk_postprocessed", "explicit_password", ("uuid1", "s3cret"))
    svc = _service(values=[ParamValue("password", secret, Origin.RULE)])
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("h", services=(svc,))], out, NOW)
    assert not _has(_pdf_text(out), "s3cret")


def test_portrait_setting(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("h")], out, NOW, Settings(portrait=True))
    box = PdfReader(out).pages[0].mediabox
    assert box.width < box.height


# --- cover -----------------------------------------------------------------


def test_cover_page_content(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(), out)
    text = _pdf_text(out)
    for expected in [
        "Check Parameter Report",
        "cmk",
        "2.5.0p15",
        "ultimate",
        "2026-10-09 12:00",
        "1000",
        "50000",
        "Highlighted",
    ]:
        assert expected in text, expected
    assert "Hosts with errors" not in text
    assert "Mode" not in text


def test_cover_lists_failed_hosts(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(failed_hosts=(("broken", "KeyError: 'x'"),)), out)
    text = _pdf_text(out)
    assert "Hosts with errors (1)" in text
    assert "broken" in text and "KeyError: 'x'" in text


def test_cover_with_very_long_error_still_renders(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(failed_hosts=(("broken", "x" * 30_000),)), out)
    text = _pdf_text(out)
    assert "broken" in text and "[truncated]" in text


def test_cover_shows_changed_only_mode(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(), out, Settings(changed_only=True))
    assert "only changed parameters" in _pdf_text(out)


def test_cover_error_limit_follows_settings(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(
        _summary(failed_hosts=(("h", "y" * 200),)), out, Settings(max_cover_error_chars=50)
    )
    assert "y" * 51 not in _pdf_text(out).replace(" ", "")


def test_cover_with_maximum_error_length_fits_in_portrait(tmp_path):
    out = tmp_path / "cover.pdf"
    settings = Settings(portrait=True, max_cover_error_chars=5000)
    render_cover(_summary(failed_hosts=(("broken", "word " * 3000),)), out, settings)
    assert "[truncated]" in _pdf_text(out)


# --- cover texts ------------------------------------------------------------

FRONT = Settings(
    cover_title="Parameter review R&D <lab>",
    cover_company="ACME GmbH",
    cover_customer="Customer AG",
    cover_author="A. Admin",
    cover_note="First line of the note\nSecond line",
    classification="Confidential",
)


def test_cover_is_one_page_without_cover_settings(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(), out)
    assert len(PdfReader(out).pages) == 1


@pytest.mark.parametrize("portrait", [False, True])
def test_cover_settings_and_summary_on_one_page(tmp_path, portrait):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(), out, replace(FRONT, portrait=portrait))
    reader = PdfReader(out)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for expected in [
        "Parameter review R&D <lab>",
        "ACME GmbH",
        "Customer AG",
        "A. Admin",
        "First line of the note",
        "Second line",
        "Confidential",
        "2026-10-09 12:00",
        "cmk",
        "2.5.0p15",
        "1000",
        "50000",
        "Highlighted",
    ]:
        assert _has(text, expected), expected
    assert text.count("2026-10-09 12:00") == 1  # created only once
    assert "Check Parameter Report" not in text  # own title replaces the default
    assert reader.metadata.title == "Parameter review R&D <lab>"


def test_cover_with_many_failed_hosts_continues_on_next_pages(tmp_path):
    out = tmp_path / "cover.pdf"
    failed = tuple((f"host{i:03d}", "KeyError: 'x'") for i in range(80))
    render_cover(_summary(failed_hosts=failed), out, FRONT)
    reader = PdfReader(out)
    assert len(reader.pages) > 1
    assert _has(reader.pages[0].extract_text(), "Parameter review R&D <lab>")
    assert _has(_pdf_text(out), "host079")


def test_cover_without_customer_and_note(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(), out, Settings(cover_company="ACME GmbH"))
    text = PdfReader(out).pages[0].extract_text()
    assert _has(text, "Check Parameter Report") and _has(text, "ACME GmbH")
    assert "Customer" not in text and "Author" not in text


def test_cover_default_title_with_customer(tmp_path):
    out = tmp_path / "cover.pdf"
    render_cover(_summary(), out, Settings(cover_customer="Customer AG"))
    front = PdfReader(out).pages[0].extract_text()
    assert _has(front, "Check Parameter Report") and _has(front, "Customer AG")
    assert "Confidential" not in front


def test_classification_in_every_host_footer(tmp_path, render_hosts):
    many = tuple(_service(f"Filesystem /data/{i:03d}") for i in range(120))
    out = tmp_path / "hosts.pdf"
    render_hosts([HostReport("big", services=many)], out, NOW, Settings(classification="Internal"))
    pages = PdfReader(out).pages
    assert len(pages) > 1
    assert all(_has(page.extract_text(), "Internal") for page in pages)


def test_host_page_in_german_with_date_format(tmp_path, render_hosts):
    out = tmp_path / "hosts.pdf"
    settings = Settings(language="de", date_format="DD.MM.YYYY")
    host = HostReport("web01", services=(_service(timeperiods=(("night", ()),), enforced=True),))
    render_hosts([host, HostReport("empty")], out, NOW, settings)
    text = _pdf_text(out)
    for expected in [
        "Adressen",
        "Hostgruppen",
        "Servicegruppen",
        "Zeitraum",
        "(erzwungen)",
        "keine",
        "Seite 1 des Hosts",
        "erstellt 09.10.2026 12:00",
        "Keine Services konfiguriert.",
    ]:
        assert _has(text, expected), expected
    assert not _has(text, "Service groups") and not _has(text, "2026-10-09")


def test_cover_page_in_german_with_date_format(tmp_path):
    out = tmp_path / "cover.pdf"
    settings = Settings(
        language="de", date_format="DD.MM.YYYY", classification="Intern", changed_only=True
    )
    render_cover(_summary(failed_hosts=(("broken", "x"),)), out, settings)
    text = _pdf_text(out)
    for expected in [
        "Check Parameter Report",
        "Klassifizierung",
        "Erstellt",
        "09.10.2026 12:00",
        "Hervorgehoben",
        "nur geänderte Parameter",
        "Hosts mit Fehlern (1)",
    ]:
        assert expected in text, expected
    assert "Classification" not in text


def _timed_service():
    rule = ParamValue("levels", (2.0, 2.0), Origin.RULE)
    night = ParamValue("levels", (95.0, 98.0), Origin.RULE)
    sets = (
        ParamSet(None, (rule,), always=(("24X7", "Always"),)),
        ParamSet("night", (night,), alias="Night shift"),
    )
    return ServiceReport("Filesystem /var", "df", "/var", sets)


@pytest.mark.parametrize(
    "language, always, period",
    [
        ("en", "[Always active: Always (24X7)]", "» Time period Night shift (night):"),
        ("de", "[Immer aktiv: Always (24X7)]", "» Zeitraum Night shift (night):"),
    ],
)
def test_always_prefix_and_alias(tmp_path, render_hosts, language, always, period):
    out = tmp_path / "hosts.pdf"
    host = HostReport("web01", services=(_timed_service(),))
    render_hosts([host], out, NOW, Settings(language=language))
    text = _pdf_text(out)
    assert _has(text, always) and _has(text, period)
    assert _has(text, "levels: 2.0 / 2.0") and _has(text, "levels: 95.0 / 98.0")


def test_always_prefix_helper():
    assert render.always_prefix(ParamSet(None, ()), "en") == ""
    both = ParamSet(None, (), always=(("24X7", "Always"), ("all", "all")))
    assert render.always_prefix(both, "en") == "[Always active: Always (24X7), all] "


BUSINESS = TimePeriod(
    "pr_business",
    "Business hours",
    days=(
        ("monday", (("08:00", "17:00"),)),
        ("tuesday", (("08:00", "17:00"),)),
        ("wednesday", (("08:00", "17:00"),)),
        ("thursday", (("08:00", "17:00"),)),
        ("friday", (("08:00", "12:00"), ("13:00", "17:00"))),
        ("saturday", (("00:00", "24:00"),)),
        ("sunday", ()),
    ),
    exceptions=(("2026-12-24", (("08:00", "10:00"),)), ("2026-12-31", ())),
    exclude=("pr_holidays",),
)
HOLIDAYS = TimePeriod("pr_holidays", "Holidays")
ALWAYS_PERIOD = TimePeriod("24X7", "Always", builtin=True, always=True)
LABELS = {"pr_holidays": "Holidays (pr_holidays)"}


def test_timeperiod_rows_english():
    assert render.timeperiod_rows(BUSINESS, LABELS, "en") == [
        ("Monday–Thursday", "08:00–17:00"),
        ("Friday", "08:00–12:00, 13:00–17:00"),
        ("Saturday", "all day"),
        ("Exceptions", "2026-12-24 08:00–10:00"),
        ("", "2026-12-31 –"),
        ("Excluded", "Holidays (pr_holidays)"),
    ]
    assert render.timeperiod_rows(ALWAYS_PERIOD, {}, "en") == [("", "built in, always active")]
    gone = TimePeriod("gone", "gone", defined=False)
    assert render.timeperiod_rows(gone, {}, "en") == [("", "not defined, never active")]
    own = TimePeriod("all", "All", always=True, days=(("monday", (("00:00", "24:00"),)),))
    assert render.timeperiod_rows(own, {}, "en")[-1] == ("", "Always active")


def test_timeperiod_rows_german():
    rows = render.timeperiod_rows(BUSINESS, LABELS, "de")
    assert rows[0] == ("Montag–Donnerstag", "08:00–17:00")
    assert rows[2] == ("Samstag", "ganztägig")
    assert ("Ausnahmen", "2026-12-24 08:00–10:00") in rows
    assert rows[-1] == ("Ausgenommen", "Holidays (pr_holidays)")


def test_render_timeperiods(tmp_path):
    out = tmp_path / "tp.pdf"
    settings = Settings(language="de", classification="Intern")
    render.render_timeperiods([ALWAYS_PERIOD, BUSINESS, HOLIDAYS], out, settings)
    text = _pdf_text(out)
    for expected in [
        "Zeiträume",
        "Always (24X7)",
        "eingebaut, immer aktiv",
        "Business hours (pr_business)",
        "Montag–Donnerstag",
        "Holidays (pr_holidays)",
        "Intern",
    ]:
        assert expected in text, expected


def test_timeperiod_without_ranges_shows_dash():
    empty = TimePeriod("nothing", "Nothing", defined=True)
    assert render.timeperiod_rows(empty, {}, "en") == [("", "–")]
