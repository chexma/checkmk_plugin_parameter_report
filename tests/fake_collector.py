"""Stand-in for collector.collect in CLI tests, controlled by environment variables.

Importable in spawned processes too (pytest puts tests/ on sys.path).
"""

import json
import os
import time

from cmk_addons.plugins.parameter_report.lib.model import (
    CollectResult,
    HostReport,
    Origin,
    ParamSet,
    ParamValue,
    ServiceReport,
)


def _list(name):
    return [item for item in os.environ.get(name, "").split(",") if item]


def collect(host_names=None, folders=(), host_tags=(), host_labels=()):
    record = os.environ.get("PR_FAKE_RECORD")
    if record:
        data = {
            "host_names": host_names,
            "folders": list(folders),
            "host_tags": [list(t) for t in host_tags],
            "host_labels": [list(label) for label in host_labels],
            "pid": os.getpid(),
        }
        with open(record + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(record + ".tmp", record)
    if os.environ.get("PR_FAKE_SLEEP"):
        time.sleep(float(os.environ["PR_FAKE_SLEEP"]))
    if os.environ.get("PR_FAKE_FAIL"):
        raise RuntimeError("kaputt")
    changed = ServiceReport(
        "CPU load",
        "cpu_loads",
        None,
        (
            ParamSet(
                None,
                (
                    ParamValue("levels", (5.0, 10.0), Origin.RULE),
                    ParamValue("average", 15, Origin.DEFAULT),
                ),
            ),
        ),
    )
    plain = ServiceReport("Uptime", "uptime", None, (ParamSet(None, ()),))
    names = _list("PR_FAKE_HOSTS") if "PR_FAKE_HOSTS" in os.environ else ["a", "b"]
    errors = set(_list("PR_FAKE_ERRORS"))
    hosts = tuple(
        HostReport(n, error="Boom" if n in errors else None, services=(changed, plain))
        for n in names
    )
    return CollectResult(
        hosts=hosts,
        unknown_hosts=tuple(_list("PR_FAKE_UNKNOWN")),
        site="cmk",
        version="2.5.0p15",
        edition="ultimate",
        untested_version=bool(os.environ.get("PR_FAKE_UNTESTED")),
        timings={"load_config": 0.1, "collect": 0.2},
    )
