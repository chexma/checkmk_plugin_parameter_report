import logging
import runpy
import sys
import types
from pathlib import Path

LOADER = Path(__file__).parents[1] / "lib/gui/plugins/wato/parameter_report.py"
PAGES = "cmk_addons.plugins.parameter_report.gui.pages"


def test_loader_swallows_import_errors(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, PAGES, None)  # import raises ImportError
    with caplog.at_level(logging.ERROR, logger="cmk.web"):
        runpy.run_path(str(LOADER))
    assert "parameter_report: web page not loaded" in caplog.text


def test_loader_swallows_registration_errors(monkeypatch, caplog):
    fake = types.ModuleType(PAGES)

    def register():
        raise RuntimeError("registry changed")

    fake.register = register
    monkeypatch.setitem(sys.modules, PAGES, fake)
    import cmk_addons.plugins.parameter_report.gui as package

    monkeypatch.setattr(package, "pages", fake, raising=False)
    with caplog.at_level(logging.ERROR, logger="cmk.web"):
        runpy.run_path(str(LOADER))
    assert "registry changed" in caplog.text
