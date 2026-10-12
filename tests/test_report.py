import ctypes
import multiprocessing
import signal
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import pytest
from cmk_addons.plugins.parameter_report.lib import report, runstate, toc
from cmk_addons.plugins.parameter_report.lib.model import (
    HostReport,
    ParamSet,
    ReportSummary,
    ServiceReport,
    TimePeriod,
)
from cmk_addons.plugins.parameter_report.lib.report import ReportError, build_report, chunked
from cmk_addons.plugins.parameter_report.lib.settings import Settings
from pypdf import PdfReader

SUMMARY = ReportSummary("cmk", "2.5.0p15", "ultimate", datetime(2026, 10, 9, 12, 0), 5, 0)


def _service(index):
    return ServiceReport(f"Filesystem /data/{index:03d}", "df", "/", (ParamSet(None, ()),))


def _hosts(*names):
    return [HostReport(name) for name in names]


def _parent_death_signal():
    value = ctypes.c_int()
    ctypes.CDLL("libc.so.6").prctl(2, ctypes.byref(value))  # PR_GET_PDEATHSIG
    return value.value


def test_chunked():
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunked([], 3) == []
    with pytest.raises(ValueError):
        chunked([1], 0)


@pytest.mark.parametrize("workers", [1, 2])
def test_merge_sorts_hosts_and_puts_cover_first(tmp_path, workers):
    out = tmp_path / "report.pdf"
    calls = []
    settings = Settings(workers=workers, chunk_size=2, toc=False)
    result = build_report(
        _hosts("c", "a", "e", "b", "d"),
        SUMMARY,
        out,
        settings,
        progress=lambda done, total: calls.append((done, total)),
    )
    reader = PdfReader(out)
    assert [o.title for o in reader.outline] == ["a", "b", "c", "d", "e"]
    assert "Check Parameter Report" in reader.pages[0].extract_text()
    assert len(reader.pages) == 6  # cover + one page per host
    assert set(result.timings) == {"render", "merge"}
    assert 20 < result.worker_peak_mb < 1000
    assert sorted(calls)[-1] == (5, 5) and len(calls) == 3
    assert list(tmp_path.iterdir()) == [out]  # temp dir removed


def test_cover_title_in_merged_report(tmp_path):
    out = tmp_path / "report.pdf"
    settings = Settings(workers=1, cover_title="Customer AG review", classification="Internal")
    build_report(_hosts("b", "a"), SUMMARY, out, settings)
    reader = PdfReader(out)
    assert len(reader.pages) == 4  # cover, contents, one page per host
    assert "Customer AG review" in reader.pages[0].extract_text()
    assert [o.title for o in reader.outline] == ["Contents", "a", "b"]
    assert reader.metadata.title == "Customer AG review"


def _printed_numbers(text):
    """Host name -> page number as printed in the contents (name, number, leader dots)."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and set(line) - set(". ")]  # drop leader dots
    return {
        name: int(number)
        for name, number in zip(lines, lines[1:])
        if number.isdigit() and not name.isdigit()
    }


def _link_targets(reader, page):
    numbers = {p.indirect_reference.idnum: i for i, p in enumerate(reader.pages)}
    annots = reader.pages[page].get("/Annots") or []
    return [numbers[a.get_object()["/Dest"][0].idnum] for a in annots]


def test_contents_links_point_to_the_hosts(tmp_path):
    out = tmp_path / "report.pdf"
    many = [HostReport("big", services=tuple(_service(i) for i in range(150)))]
    hosts = many + _hosts("c", "a", "e", "b", "d")
    settings = Settings(workers=2, chunk_size=2)
    build_report(hosts, SUMMARY, out, settings)
    reader = PdfReader(out)
    outline = {o.title: reader.get_destination_page_number(o) for o in reader.outline}
    assert outline["Contents"] == 1  # right after the cover
    host_pages = [outline[name] for name in ("a", "b", "big", "c", "d", "e")]
    assert _link_targets(reader, 1) == host_pages
    printed = _printed_numbers(reader.pages[1].extract_text())
    names = ("a", "b", "big", "c", "d", "e")
    assert [printed[name] for name in names] == [page + 1 for page in host_pages]  # 1-based
    assert host_pages[-1] - host_pages[2] > 3  # "big" spans several pages


def test_contents_after_a_multi_page_cover(tmp_path):
    out = tmp_path / "report.pdf"
    failed = tuple((f"h{i:03d}", "KeyError: 'x'") for i in range(120))
    summary = ReportSummary("cmk", "2.5.0p15", "ultimate", SUMMARY.generated_at, 2, 0, failed)
    build_report(_hosts("b", "a"), summary, out, Settings(workers=1))
    reader = PdfReader(out)
    outline = {o.title: reader.get_destination_page_number(o) for o in reader.outline}
    assert outline["Contents"] > 1  # the cover has more than one page
    assert _link_targets(reader, outline["Contents"]) == [outline["a"], outline["b"]]


def test_no_contents_when_switched_off(tmp_path):
    out = tmp_path / "report.pdf"
    build_report(_hosts("b", "a"), SUMMARY, out, Settings(workers=1, toc=False))
    reader = PdfReader(out)
    assert len(reader.pages) == 3 and [o.title for o in reader.outline] == ["a", "b"]


def test_render_failure_leaves_no_output(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(report, "_render_chunk", boom)
    out = tmp_path / "report.pdf"
    with pytest.raises(ReportError, match="kaputt"):
        build_report(_hosts("a"), SUMMARY, out, Settings(workers=1))
    assert list(tmp_path.iterdir()) == []


def test_cancel_passes_through_and_cleans_up(tmp_path, monkeypatch):
    def cancel(*args, **kwargs):
        raise runstate.Cancelled("SIGTERM")

    monkeypatch.setattr(report, "_render_chunk", cancel)
    with pytest.raises(runstate.Cancelled):
        build_report(_hosts("a"), SUMMARY, tmp_path / "r.pdf", Settings(workers=1))
    assert list(tmp_path.iterdir()) == []


def test_existing_output_is_replaced(tmp_path):
    out = tmp_path / "report.pdf"
    out.write_text("old")
    build_report(_hosts("a"), SUMMARY, out, Settings(workers=1))
    assert out.read_bytes().startswith(b"%PDF")


def test_no_hosts_is_an_error(tmp_path):
    with pytest.raises(ReportError, match="No hosts"):
        build_report([], SUMMARY, tmp_path / "r.pdf", Settings(workers=1))


def test_worker_init_sets_parent_death_signal():
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(1, mp_context=context, initializer=report.worker_init) as pool:
        assert pool.submit(_parent_death_signal).result() == signal.SIGKILL


def test_temp_dir_carries_site_marker(tmp_path, monkeypatch):
    seen = []

    def record(hosts, path, generated_at, settings):
        seen.append((path.parent / runstate.SITE_MARKER).read_text())
        return 50

    monkeypatch.setattr(report, "_render_chunk", record)
    with pytest.raises(ReportError):  # nothing rendered, merge fails
        build_report(_hosts("a"), SUMMARY, tmp_path / "r.pdf", Settings(workers=1))
    assert seen == ["cmk"]


@pytest.mark.parametrize("contents", [True, False])
def test_every_page_carries_its_page_number(tmp_path, contents):
    out = tmp_path / "report.pdf"
    hosts = [HostReport("big", services=tuple(_service(i) for i in range(150)))] + _hosts("a")
    build_report(hosts, SUMMARY, out, Settings(workers=1, toc=contents))
    reader = PdfReader(out)
    total = len(reader.pages)
    for index, page in enumerate(reader.pages):
        text = " ".join(page.extract_text().split())
        assert f"page {index + 1} of {total}" in text, index
    texts = [" ".join(page.extract_text().split()) for page in reader.pages]
    assert any("page 2 of host" in text for text in texts)  # host-relative number kept


def test_links_on_the_second_contents_page(tmp_path):
    out = tmp_path / "report.pdf"
    names = [f"h{i:03d}" for i in range(140)]  # more than one contents page holds
    build_report(_hosts(*names), SUMMARY, out, Settings(workers=1, chunk_size=50))
    reader = PdfReader(out)
    outline = {o.title: reader.get_destination_page_number(o) for o in reader.outline}
    per_page = toc.layout(Settings()).per_page
    assert outline["h000"] == 3  # cover, two contents pages
    assert _link_targets(reader, 2) == [outline[name] for name in names[per_page:]]


def test_merged_report_in_german(tmp_path):
    out = tmp_path / "report.pdf"
    build_report(_hosts("b", "a"), SUMMARY, out, Settings(workers=1, language="de"))
    reader = PdfReader(out)
    assert [o.title for o in reader.outline] == ["Inhaltsverzeichnis", "a", "b"]
    assert reader.metadata.title == "Check Parameter Report"
    assert "Seite 1 von 4" in reader.pages[0].extract_text()


def test_appendix_after_the_hosts(tmp_path):
    out = tmp_path / "report.pdf"
    summary = ReportSummary(
        "cmk",
        "2.5.0p15",
        "ultimate",
        datetime(2026, 10, 9, 12, 0),
        2,
        0,
        timeperiods=(TimePeriod("24X7", "Always", builtin=True, always=True),),
    )
    build_report(_hosts("b", "a"), summary, out, Settings(workers=1, language="de"))
    reader = PdfReader(out)
    assert [o.title for o in reader.outline] == ["Inhaltsverzeichnis", "a", "b", "Zeiträume"]
    assert len(reader.pages) == 5  # cover, contents, a, b, appendix
    assert "Zeiträume" in reader.pages[-1].extract_text()
    contents = reader.pages[1]
    targets = [
        reader.get_page_number(a.get_object()["/Dest"][0].get_object()) for a in contents["/Annots"]
    ]
    assert targets == [2, 3, 4]
    assert "Zeiträume" in contents.extract_text()


def test_no_appendix_without_timeperiods(tmp_path):
    out = tmp_path / "report.pdf"
    build_report(_hosts("a"), SUMMARY, out, Settings(workers=1))
    assert [o.title for o in PdfReader(out).outline] == ["Contents", "a"]
