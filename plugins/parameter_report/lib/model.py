"""Data model of the parameter report.

Plain data only, no Checkmk imports: render.py and report.py work on these
classes, so they run in worker processes and tests without a site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum


class Origin(StrEnum):
    DEFAULT = "default"
    DISCOVERED = "discovered"
    RULE = "rule"


@dataclass(frozen=True, slots=True)
class ParamValue:
    key: str
    value: object
    origin: Origin


@dataclass(frozen=True, slots=True)
class ParamSet:
    """Effective parameters; timeperiod None means the standard values.

    alias: alias of the time period (time period sets). always: (name, alias) of
    the always active time periods counted into the standard set.
    """

    timeperiod: str | None
    values: tuple[ParamValue, ...]
    alias: str = ""
    always: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ServiceReport:
    description: str
    check_plugin: str
    item: str | None
    param_sets: tuple[ParamSet, ...]
    servicegroups: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)
    enforced: bool = False

    @property
    def timespecific(self) -> bool:
        return any(ps.timeperiod is not None for ps in self.param_sets)


@dataclass(frozen=True, slots=True)
class HostReport:
    name: str
    is_cluster: bool = False
    addresses: tuple[str, ...] = ()
    # Cluster nodes for clusters, parents for ordinary hosts
    parents: tuple[str, ...] = ()
    tags: Mapping[str, str] = field(default_factory=dict)
    labels: Mapping[str, str] = field(default_factory=dict)
    label_sources: Mapping[str, str] = field(default_factory=dict)
    hostgroups: tuple[str, ...] = ()
    contactgroups: tuple[str, ...] = ()
    agent_mode: str = ""
    datasources: tuple[str, ...] = ()
    services: tuple[ServiceReport, ...] = ()
    # Set when collecting the data of this host failed
    error: str | None = None


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
ALWAYS = "24X7"  # built into Checkmk, always present and active
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")  # exception days in a time period definition
Range = tuple[str, str]


def display_name(name: str, alias: str) -> str:
    return name if not alias or alias == name else f"{alias} ({name})"


@dataclass(frozen=True, slots=True)
class TimePeriod:
    """Definition of a time period as shown in the appendix."""

    name: str
    alias: str
    builtin: bool = False
    always: bool = False
    defined: bool = True  # False: named in a rule, but not configured (never active)
    days: tuple[tuple[str, tuple[Range, ...]], ...] = ()  # all WEEKDAYS, in order
    exceptions: tuple[tuple[str, tuple[Range, ...]], ...] = ()  # (YYYY-MM-DD, ranges)
    exclude: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return display_name(self.name, self.alias)


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def covers_whole_day(ranges: Iterable[Range]) -> bool:
    reached = 0
    for start, end in sorted(ranges, key=lambda r: _minutes(r[0])):
        if _minutes(start) > reached:
            return False
        reached = max(reached, _minutes(end))
    return reached >= 24 * 60


def _ranges(value: object) -> tuple[Range, ...]:
    return tuple((str(start), str(end)) for start, end in value or ())  # type: ignore[attr-defined]


def is_always_active(name: str, spec: Mapping[str, object]) -> bool:
    if name == ALWAYS:
        return True
    if spec.get("exclude"):
        return False
    if not all(covers_whole_day(_ranges(spec.get(day))) for day in WEEKDAYS):
        return False
    return all(covers_whole_day(_ranges(v)) for k, v in spec.items() if DATE.fullmatch(k))


def time_period_from_spec(
    name: str, spec: Mapping[str, object], builtin: bool = False
) -> TimePeriod:
    """TimePeriod from a Checkmk time period definition (weekdays, dates, exclude, alias)."""
    return TimePeriod(
        name=name,
        alias=str(spec.get("alias") or name),
        builtin=builtin,
        always=is_always_active(name, spec),
        days=tuple((day, _ranges(spec.get(day))) for day in WEEKDAYS),
        exceptions=tuple(sorted((k, _ranges(v)) for k, v in spec.items() if DATE.fullmatch(k))),
        exclude=tuple(str(e) for e in spec.get("exclude") or ()),  # type: ignore[attr-defined]
    )


def undefined_time_period(name: str) -> TimePeriod:
    return TimePeriod(name, name, defined=False)


def day_groups(
    days: Sequence[tuple[str, tuple[Range, ...]]],
) -> tuple[tuple[str, str, tuple[Range, ...]], ...]:
    """Consecutive weekdays with the same ranges as (first, last, ranges); empty days left out."""
    groups: list[tuple[str, str, tuple[Range, ...]]] = []
    for day, ranges in days:
        if not ranges:
            continue
        if (
            groups
            and groups[-1][2] == ranges
            and WEEKDAYS.index(day) == WEEKDAYS.index(groups[-1][1]) + 1
        ):
            groups[-1] = (groups[-1][0], day, ranges)
        else:
            groups.append((day, day, ranges))
    return tuple(groups)


@dataclass(frozen=True, slots=True)
class ReportSummary:
    site: str
    version: str
    edition: str
    generated_at: datetime
    host_count: int
    service_count: int
    failed_hosts: tuple[tuple[str, str], ...] = ()
    timeperiods: tuple[TimePeriod, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectResult:
    hosts: tuple[HostReport, ...]
    unknown_hosts: tuple[str, ...]
    site: str
    version: str
    edition: str
    untested_version: bool
    timings: Mapping[str, float]
    timeperiods: tuple[TimePeriod, ...] = ()


def classify_params(
    effective: Mapping[str, object],
    defaults: Mapping[str, object],
    discovered: Mapping[str, object],
) -> tuple[ParamValue, ...]:
    """Tag each effective parameter with where its value comes from.

    A rule that sets exactly the default value is reported as DEFAULT: the
    rule itself is not visible in the effective parameters.
    """
    values = []
    for key, value in effective.items():
        if key in discovered and discovered[key] == value:
            origin = Origin.DISCOVERED
        elif key in defaults and defaults[key] == value:
            origin = Origin.DEFAULT
        else:
            origin = Origin.RULE
        values.append(ParamValue(key, value, origin))
    return tuple(values)


def only_rule_values(hosts: Sequence[HostReport]) -> tuple[HostReport, ...]:
    """Keep only parameters set by rules; drop services without any (changed_only).

    Time period sets are kept as they are: after only_differences() they hold the
    values a rule changes for that period, even one equal to the plugin default.
    """
    result = []
    for host in hosts:
        services = []
        for svc in host.services:
            sets = []
            for ps in svc.param_sets:
                if ps.timeperiod is None:
                    ps = replace(ps, values=tuple(v for v in ps.values if v.origin is Origin.RULE))
                sets.append(ps)
            if any(ps.values for ps in sets):
                services.append(replace(svc, param_sets=tuple(sets)))
        result.append(replace(host, services=tuple(services)))
    return tuple(result)


def only_differences(hosts: Sequence[HostReport]) -> tuple[HostReport, ...]:
    """Reduce every time period set to the values that differ from the standard set.

    Sets without a difference are dropped. Checkmk lays period values over the
    standard values, so a key missing in a period set never means "removed".
    """
    result = []
    for host in hosts:
        services = []
        for svc in host.services:
            standard = {
                v.key: v.value for ps in svc.param_sets if ps.timeperiod is None for v in ps.values
            }
            sets = []
            for ps in svc.param_sets:
                if ps.timeperiod is not None:
                    values = tuple(
                        v for v in ps.values if v.key not in standard or standard[v.key] != v.value
                    )
                    if not values:
                        continue
                    ps = replace(ps, values=values)
                sets.append(ps)
            services.append(replace(svc, param_sets=tuple(sets)))
        result.append(replace(host, services=tuple(services)))
    return tuple(result)


def used_timeperiods(
    hosts: Sequence[HostReport], periods: Sequence[TimePeriod]
) -> tuple[TimePeriod, ...]:
    """The periods the report shows, plus the ones they exclude (transitively)."""
    by_name = {p.name: p for p in periods}
    wanted: set[str] = set()
    for host in hosts:
        for svc in host.services:
            for ps in svc.param_sets:
                if ps.timeperiod is not None:
                    wanted.add(ps.timeperiod)
                wanted.update(name for name, _alias in ps.always)
    todo = list(wanted)
    while todo:
        period = by_name.get(todo.pop())
        for name in period.exclude if period else ():
            if name not in wanted:
                wanted.add(name)
                todo.append(name)
    used = (by_name[name] for name in wanted if name in by_name)
    return tuple(sorted(used, key=lambda p: p.label.lower()))


def host_matches(
    path: str,
    tags: Mapping[str, str],
    labels: Mapping[str, str],
    folders: Sequence[str],
    host_tags: Sequence[tuple[str, str]],
    host_labels: Sequence[tuple[str, str]],
) -> bool:
    """Host selection: any of the folders (incl. subfolders), all tags, all labels.

    path is Checkmk's host path, e.g. '/wato/linux/web/'; hosts outside the
    Setup folders never match a folder filter.
    """
    if folders and not any(
        path.startswith(f"/wato/{folder}/" if folder else "/wato/") for folder in folders
    ):
        return False
    if any(tags.get(group) != tag for group, tag in host_tags):
        return False
    return all(labels.get(key) == value for key, value in host_labels)
