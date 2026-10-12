"""Write autochecks (50 services per host) and 40 parameter rules for the test hosts.

Run as site user after create_test_hosts.sh created the hosts in folder /spike.
Autochecks are written directly: cmk -D needs no agent data for them.
"""

import os
from pathlib import Path

ROOT = Path(os.environ["OMD_ROOT"])
HOSTS = [f"spike-{n:04d}" for n in range(1000)]

SERVICES: list[tuple[str, str | None]] = [
    ("checkmk_agent", None),
    ("cpu_loads", None),
    ("kernel_util", None),
    ("mem_used", None),
    ("uptime", None),
    ("tcp_conn_stats", None),
    ("diskstat", "SUMMARY"),
    ("systemd_units_services_summary", None),
    ("postfix_mailq", "default"),
]
SERVICES += [("df", m) for m in ["/", "/boot", "/var", "/tmp", "/home", "/opt"]]
SERVICES += [("df", f"/data/vol{i:02d}") for i in range(20)]
SERVICES += [("lnx_if", f"{i:02d}") for i in range(1, 11)]
SERVICES += [("mounts", m) for m in ["/", "/var", "/data/vol00", "/data/vol01", "/boot"]]

IF_PARAMS = {
    "item_appearance": "index",
    "discovered_oper_status": ["1"],
    "discovered_speed": 10000000000,
}


def autochecks() -> str:
    lines = ["["]
    for name, item in SERVICES:
        params = IF_PARAMS if name == "lnx_if" else {}
        lines.append(
            f"  {{'check_plugin_name': {name!r}, 'item': {item!r}, "
            f"'parameters': {params!r}, 'service_labels': {{}}}},"
        )
    lines.append("]")
    return "\n".join(lines) + "\n"


def rule(index, value, host_regex, item_regex=None):
    condition = {"host_name": [{"$regex": host_regex}]}
    if item_regex:
        condition["service_description"] = [{"$regex": item_regex}]
    return {
        "id": f"spike-{index:04d}",
        "value": value,
        "condition": condition,
        "options": {"description": "parameter_report test data"},
    }


def rules() -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {
        "filesystem": [],
        "cpu_load": [],
        "interfaces": [],
        "memory_linux": [],
    }
    i = 0
    for k in range(15):
        value = {"levels": (80.0 + k, 90.0 + k / 2), "magic": 0.8}
        groups["filesystem"].append(rule(i, value, f"spike-0{k % 10}", f"/data/vol{k:02d}"))
        i += 1
    for k in range(10):
        value = {"levels15": ("fixed", (5.0 + k, 10.0 + k))}
        groups["cpu_load"].append(rule(i, value, f"spike-00{k}"))
        i += 1
    for k in range(10):
        value = {"errors": {"both": ("abs", (10 + k, 20 + k))}, "traffic": []}
        groups["interfaces"].append(rule(i, value, f"spike-.{k}", f"0{k}"))
        i += 1
    for k in range(5):
        value = {"levels_ram": ("perc_used", (80.0 + k, 90.0 + k))}
        groups["memory_linux"].append(rule(i, value, f"spike-{k}"))
        i += 1
    return groups


# Time periods and time-specific rules (Filesystem /var of spike-0020..0022)
TIMEPERIODS = {
    "pr_business": {
        "alias": "Business hours",
        **{
            d: [("08:00", "17:00")]
            for d in ("monday", "tuesday", "wednesday", "thursday", "friday")
        },
        "exclude": ["pr_holidays"],
    },
    "pr_holidays": {"alias": "Holidays", "2026-12-24": [("00:00", "24:00")]},
}


def timespecific_rules() -> list[dict]:
    def tp_rule(index, host, default, values):
        value = {"tp_default_value": default, "tp_values": values}
        return rule(100 + index, value, f"{host}$", "/var$")

    return [
        # 24X7 is always active: main row shows 2.0 / 2.0, no period row
        tp_rule(0, "spike-0020", {"levels": (70.0, 80.0)}, [("24X7", {"levels": (2.0, 2.0)})]),
        # own period: main row keeps the default, period row only levels
        tp_rule(1, "spike-0021", {}, [("pr_business", {"levels": (95.0, 98.0)})]),
        # order: pr_business listed before 24X7 wins while active
        tp_rule(
            2,
            "spike-0022",
            {},
            [("pr_business", {"levels": (60.0, 70.0)}), ("24X7", {"levels": (2.0, 2.0)})],
        ),
    ]


def main() -> None:
    text = autochecks()
    autochecks_dir = ROOT / "var/check_mk/autochecks"
    for host in HOSTS:
        (autochecks_dir / f"{host}.mk").write_text(text)
    out = ["# parameter_report test rules, written by tests/fixtures/gen_test_data.py"]
    for group, entries in rules().items():
        out.append(f"checkgroup_parameters.setdefault({group!r}, [])")
        out.append(
            f"checkgroup_parameters[{group!r}] = {entries!r} + checkgroup_parameters[{group!r}]"
        )
    out.append(f"timeperiods.update({TIMEPERIODS!r})")
    out.append(
        f"checkgroup_parameters['filesystem'] = {timespecific_rules()!r}"
        " + checkgroup_parameters['filesystem']"
    )
    # Outside conf.d/wato: WATO owns its rules.mk files and would lose or rewrite these
    (ROOT / "etc/check_mk/conf.d/parameter_report_test_rules.mk").write_text("\n".join(out) + "\n")
    print(f"{len(HOSTS)} hosts x {len(SERVICES)} services, 43 rules, 2 time periods")


if __name__ == "__main__":
    main()
