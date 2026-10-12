import os
import tomllib

import pytest
from cmk_addons.plugins.parameter_report.lib import config_file
from cmk_addons.plugins.parameter_report.lib.settings import (
    FIELD_NAMES,
    SettingsError,
    build_settings,
)

ALL_KINDS = {
    "hosts": ["a", "b"],
    "folders": ["linux/web"],
    "host_tags": ["criticality:prod"],
    "host_labels": ["os:linux"],
    "changed_only": True,
    "collect_mode": "inline",
    "workers": 1,
    "min_split_lines": 5,
    "margin_mm": 15.0,
    "font_size": 7,
    "column_widths": [30, 20, 12, 14],
    "highlight_color": "#ff0000",
    "cover_title": "Review",
    "output": "/tmp/r.pdf",
    "started_by": "cron",
}


def test_sections_cover_every_setting_once():
    names = [name for _title, fields in config_file.SECTIONS for name in fields]
    assert sorted(names) == sorted(FIELD_NAMES)


def test_dump_round_trip_all_kinds():
    text = config_file.dump(ALL_KINDS)
    assert build_settings(tomllib.loads(text)) == build_settings(None, ALL_KINDS)


def test_dump_round_trip_special_characters():
    note = 'He said "hi" \\ path C:\\x\nsecond line\twith tab \x01 ctrl · Übersicht ß'
    text = config_file.dump({"cover_note": note, "cover_title": 'a "b" \\ c'})
    loaded = build_settings(tomllib.loads(text))
    assert loaded.cover_note == note and loaded.cover_title == 'a "b" \\ c'


def test_dump_header_order_and_comments():
    text = config_file.dump({"cover_title": "T", "hosts": ["a"]}, header="Saved by x")
    lines = text.splitlines()
    assert lines[0] == "# Saved by x"
    assert text.index("# Hosts to include") < text.index("hosts = ") < text.index("# Cover page")
    assert "# Layout" not in text  # sections without values are left out


def test_to_values_only_changed_and_complete():
    s = build_settings(None, {"min_split_lines": 3, "workers": 1})
    changed = config_file.to_values(s, only_changed=True)
    assert changed["min_split_lines"] == 3 and "collect_mode" not in changed
    complete = config_file.to_values(s, only_changed=False)
    assert complete["collect_mode"] == "subprocess" and "output" not in complete  # None: left out
    assert build_settings(tomllib.loads(config_file.dump(complete))) == s


def test_dump_rejects_values_that_do_not_load_back(monkeypatch):
    monkeypatch.setattr(
        config_file, "_value", lambda value: "1"
    )  # min_split_lines = 1 instead of 3
    with pytest.raises(SettingsError, match="not written"):
        config_file.dump({"min_split_lines": 3})


def test_dump_reports_invalid_toml(monkeypatch):
    monkeypatch.setattr(config_file, "_value", lambda value: "[")
    with pytest.raises(SettingsError, match="Configuration not written"):
        config_file.dump({"min_split_lines": 3})


def test_to_values_pairs_round_trip():
    s = build_settings(None, {"host_tags": ["a:b", "c:d"], "host_labels": ["os:linux"]})
    values = config_file.to_values(s, only_changed=True)
    assert values["host_tags"] == ["a:b", "c:d"] and values["host_labels"] == ["os:linux"]
    assert build_settings(tomllib.loads(config_file.dump(values))) == s


def test_write_atomic_with_backup(tmp_path):
    path = tmp_path / "pr.toml"
    path.write_text("min_split_lines = 1\n")
    config_file.write_atomic(path, "min_split_lines = 2\n", backup=True)
    assert path.read_text() == "min_split_lines = 2\n"
    assert (tmp_path / "pr.toml.bak").read_text() == "min_split_lines = 1\n"


def test_write_atomic_new_file_without_backup(tmp_path):
    path = tmp_path / "pr.toml"
    config_file.write_atomic(path, "min_split_lines = 2\n", backup=True)
    assert path.read_text() == "min_split_lines = 2\n" and not (tmp_path / "pr.toml.bak").exists()


def test_write_atomic_failure_keeps_old_file(tmp_path, monkeypatch):
    path = tmp_path / "pr.toml"
    path.write_text("min_split_lines = 1\n")

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        config_file.write_atomic(path, "min_split_lines = 2\n", backup=False)
    assert path.read_text() == "min_split_lines = 1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pr.toml"]


def test_load_config_file(tmp_path):
    cfg = tmp_path / "pr.toml"
    cfg.write_text('chunk_size = 25\ncollect_mode = "inline"\nfolders = ["linux"]\n')
    assert config_file.load(cfg) == {
        "chunk_size": 25,
        "collect_mode": "inline",
        "folders": ["linux"],
    }


def test_load_config_file_errors(tmp_path):
    with pytest.raises(SettingsError, match="not readable"):
        config_file.load(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("chunk_size = = 3")
    with pytest.raises(SettingsError, match="not valid TOML"):
        config_file.load(bad)
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("chunksize = 3")
    with pytest.raises(SettingsError, match="chunksize"):
        config_file.load(unknown)


def test_dump_with_description():
    text = config_file.dump({"min_split_lines": 2}, "Profile k", description='Kunde "K"')
    assert text.splitlines()[:3] == ["# Profile k", "", "# Profile"]
    assert 'description = "Kunde \\"K\\""' in text


def test_load_extra_keys(tmp_path):
    path = tmp_path / "p.toml"
    path.write_text('description = "x"\nmin_split_lines = 1\n')
    with pytest.raises(SettingsError, match="description"):
        config_file.load(path)
    assert config_file.load(path, extra=("description",)) == {
        "description": "x",
        "min_split_lines": 1,
    }


def test_tables_are_not_settings(tmp_path):
    path = tmp_path / "base.toml"
    path.write_text(
        'min_split_lines = 2\n\n[schedule]\nenabled = true\nperiod = "day"\nday = 0\n'
        'time = "06:00"\nrecipients = []\n'
    )
    values = config_file.load(path)
    assert config_file.settings_part(values) == {"min_split_lines": 2}
    assert values["schedule"]["period"] == "day"


def test_dump_with_tables():
    tables = {
        "schedule": {
            "enabled": True,
            "period": "day",
            "day": 0,
            "time": "06:00",
            "recipients": ["a@b.de"],
        },
        "mail": {"max_attachment_mb": 5},
    }
    text = config_file.dump({"min_split_lines": 2}, "h", tables=tables)
    loaded = tomllib.loads(text)
    assert loaded["schedule"] == tables["schedule"] and loaded["mail"] == tables["mail"]
    assert loaded["min_split_lines"] == 2 and text.index("min_split_lines") < text.index(
        "[schedule]"
    )
