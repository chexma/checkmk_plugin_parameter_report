"""Product code and package description must be English (docs stay German)."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GERMAN = re.compile(
    r"[äöüÄÖÜß]|\b(keine|nicht|und|oder|Fehler|Farbe|Seite|Datei|bereits|erwartet|Standard)\b"
)


def _sources():
    yield from (ROOT / "plugins").rglob("*.py")
    yield ROOT / "bin" / "cmk-parameter-report"
    yield ROOT / "lib" / "gui" / "plugins" / "wato" / "parameter_report.py"


def test_no_german_in_product_code():
    hits = []
    for path in _sources():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if GERMAN.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert hits == []


def test_package_description_is_english():
    description = ast.literal_eval((ROOT / "package").read_text())["description"]
    assert not GERMAN.search(description), description
