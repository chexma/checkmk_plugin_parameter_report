import pytest
from cmk_addons.plugins.parameter_report.lib import toc
from cmk_addons.plugins.parameter_report.lib.settings import Settings
from pypdf import PdfReader


@pytest.mark.parametrize(
    "settings, columns",
    [
        (Settings(), 3),
        (Settings(portrait=True), 2),
        (Settings(page_size="A3"), 4),
    ],
)
def test_layout_columns(settings, columns):
    layout = toc.layout(settings)
    assert layout.columns == columns and layout.rows > 30


@pytest.mark.parametrize("hosts, pages", [(0, 0), (1, 1), (100, 1), (1000, 8)])
def test_page_count(hosts, pages):
    assert toc.page_count(hosts, Settings()) == pages


def _entries(count, failed=()):
    return [toc.Entry(f"host{i:04d}", 10 + i, f"host{i:04d}" in failed) for i in range(count)]


def test_render_one_link_per_entry(tmp_path):
    out = tmp_path / "toc.pdf"
    entries = _entries(300)
    links = toc.render(entries, out, Settings())
    reader = PdfReader(out)
    assert len(reader.pages) == len(links) == toc.page_count(300, Settings())
    assert [target for page in links for _rect, target in page] == [e.page - 1 for e in entries]
    first = reader.pages[0].extract_text()
    assert "Contents" in first and "host0000" in first and "10" in first
    assert "host0299" in reader.pages[-1].extract_text()


def test_render_link_rects_inside_page(tmp_path):
    settings = Settings(portrait=True)
    links = toc.render(_entries(150), tmp_path / "toc.pdf", settings)
    width, height = settings.page
    for page in links:
        for (x1, y1, x2, y2), _target in page:
            assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def test_long_names_are_shortened(tmp_path):
    out = tmp_path / "toc.pdf"
    toc.render([toc.Entry("x" * 300, 5, False)], out, Settings())
    text = PdfReader(out).pages[0].extract_text()
    assert "…" in text and "x" * 300 not in text


def test_failed_hosts_in_red_and_classification_in_footer(tmp_path):
    out = tmp_path / "toc.pdf"
    toc.render(_entries(3, failed=("host0001",)), out, Settings(classification="Internal"))
    page = PdfReader(out).pages[0]
    assert toc.ERROR_RGB in page.get_contents().get_data()
    assert "Internal" in page.extract_text()


def test_heading_in_german(tmp_path):
    out = tmp_path / "toc.pdf"
    toc.render(_entries(3), out, Settings(language="de"))
    assert "Inhaltsverzeichnis" in PdfReader(out).pages[0].extract_text()


def test_page_count_with_appendix():
    rows = toc.layout(Settings()).per_page
    assert toc.page_count(rows, Settings()) == 1
    assert toc.page_count(rows, Settings(), appendix=1) == 2  # blank slot + entry spill over
    assert toc.page_count(rows - 2, Settings(), appendix=1) == 1


def test_appendix_entry_after_a_blank_slot(tmp_path):
    out = tmp_path / "toc.pdf"
    entries = _entries(3) + [toc.Entry("Time periods", 99, appendix=True)]
    links = toc.render(entries, out, Settings())
    assert [target for _rect, target in links[0]] == [9, 10, 11, 98]  # 0-based pages
    y_hosts = [rect[1] for rect, _t in links[0][:3]]
    y_appendix = links[0][3][0][1]
    step = y_hosts[0] - y_hosts[1]
    assert y_hosts[2] - y_appendix == pytest.approx(2 * step)  # one empty row between
    assert "Time periods" in PdfReader(out).pages[0].extract_text()
