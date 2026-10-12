import json
import os
import subprocess
from pathlib import Path

import pytest
from cmk_addons.plugins.parameter_report.lib import config_file
from pypdf import PdfReader

pytestmark = pytest.mark.site


def _run_report(tmp_path, **values):
    script = Path(os.environ["OMD_ROOT"]) / "local/bin/cmk-parameter-report"
    out = tmp_path / "report.pdf"
    values = {"output": str(out), "status_file": str(tmp_path / "status.json"), **values}
    cfg = tmp_path / "run.toml"
    cfg.write_text(config_file.dump(values))
    cmd = [str(script), "--config", str(cfg), "-v"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return proc, out


def _check(proc, out):
    assert proc.returncode == 0, proc.stderr
    titles = [o.title for o in PdfReader(out).outline]
    hosts = [t for t in titles if t.startswith("spike-")]
    assert len(hosts) == 1000
    assert hosts == sorted(hosts)
    assert titles[-1] == "Time periods"  # appendix follows the hosts
    meta = json.loads(out.with_suffix(".json").read_text())
    print(meta["timings"], meta["peak_rss_mb"])  # visible with pytest -s
    return meta


def test_full_report_defaults(site_with_test_hosts, tmp_path):
    meta = _check(*_run_report(tmp_path))
    assert meta["settings"]["collect_mode"] == "subprocess"


def test_full_report_inline_with_more_workers(site_with_test_hosts, tmp_path):
    values = dict(collect_mode="inline", workers=4, chunk_size=200)
    meta = _check(*_run_report(tmp_path, **values))
    assert meta["settings"]["collect_mode"] == "inline" and meta["settings"]["workers"] == 4


def test_time_specific_rules_in_the_pdf(site_with_test_hosts, tmp_path):
    proc, out = _run_report(tmp_path, hosts=["spike-0020", "spike-0021"], language="de")
    assert proc.returncode == 0, proc.stderr
    reader = PdfReader(out)
    text = " ".join(" ".join(p.extract_text().split()) for p in reader.pages)
    assert "[Immer aktiv: Always (24X7)]" in text
    assert "Zeitraum Business hours (pr_business):" in text
    assert reader.outline[-1].title == "Zeiträume"
    last = reader.pages[-1].extract_text()
    assert "Business hours (pr_business)" in last and "Holidays (pr_holidays)" in last
