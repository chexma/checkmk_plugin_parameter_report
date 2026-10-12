"""The MKP manifest must ship exactly the report's code, texts, command and GUI loader."""

import ast
import pprint
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _manifest():
    return ast.literal_eval((ROOT / "package").read_text())


def test_manifest_is_in_mkp_formatting():
    text = (ROOT / "package").read_text()
    assert pprint.pformat(ast.literal_eval(text)) + "\n" == text


def test_manifest_lists_all_modules():
    plugin = ROOT / "plugins" / "parameter_report"
    expected = {
        f"parameter_report/{p.relative_to(plugin).as_posix()}"
        for p in plugin.rglob("*")
        if p.suffix in (".py", ".toml") and "__pycache__" not in p.parts
    }
    assert set(_manifest()["files"]["cmk_addons_plugins"]) == expected


def test_manifest_ships_command_and_gui_loader():
    files = _manifest()["files"]
    assert files["bin"] == ["cmk-parameter-report"]
    assert files["gui"] == ["wato/parameter_report.py"]
    assert set(files) == {"cmk_addons_plugins", "bin", "gui"}


def test_manifest_metadata():
    manifest = _manifest()
    assert manifest["name"] == "parameter_report"
    assert manifest["version"] == "0.1.0"
    assert manifest["version.min_required"] == "2.5.0"
    assert manifest["version.usable_until"] == "2.6.0"
