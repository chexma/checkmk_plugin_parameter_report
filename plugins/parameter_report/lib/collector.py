"""Collect hosts and their effective parameters from the Checkmk configuration.

This is the only module that imports Checkmk internals. They change between
releases, so a Checkmk update breaks this module first; it is tested against
the versions in TESTED_VERSIONS. Unlike `cmk -D` it builds no data sources
(fetchers, special agent command lines) and does no DNS lookups.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence

import cmk.ccc.version as cmk_version
import cmk.utils.paths
from cmk.base.app import make_app
from cmk.base.modes.check_mk import (
    BundledHostRulesetMatcher,
    config,
    load_checks,
    load_config,
    make_final_service_name_config,
)
from cmk.utils.timeperiod import get_all_timeperiods, is_builtin_timeperiod

from .model import (
    CollectResult,
    HostReport,
    ParamSet,
    ServiceReport,
    TimePeriod,
    classify_params,
    host_matches,
    time_period_from_spec,
    undefined_time_period,
)

TESTED_VERSIONS = frozenset({"2.5.0p15"})


def collect(
    host_names: Sequence[str] | None = None,
    folders: Sequence[str] = (),
    host_tags: Sequence[tuple[str, str]] = (),
    host_labels: Sequence[tuple[str, str]] = (),
) -> CollectResult:
    start = time.perf_counter()
    edition = cmk_version.edition(cmk.utils.paths.omd_root)
    app = make_app(edition)
    plugins = load_checks()
    loaded = load_config(plugins, app.get_builtin_host_labels)
    cache, loaded_config = loaded.config_cache, loaded.loaded_config
    periods = {
        str(name): time_period_from_spec(str(name), spec, is_builtin_timeperiod(name))
        for name, spec in get_all_timeperiods(loaded_config.timeperiods).items()
    }
    referenced: set[str] = set()
    hosts_config = cache.hosts_config

    known = {
        str(h): h
        for h in (*hosts_config.hosts, *hosts_config.clusters)
        if cache.is_active(h) and cache.is_online(h)
    }
    if folders or host_tags or host_labels:
        labels_of = cache.label_manager.labels_of_host
        known = {
            name: h
            for name, h in known.items()
            if host_matches(
                cache.host_path(h),
                {str(k): str(v) for k, v in cache.host_tags.tags(h).items()},
                {str(k): str(v) for k, v in labels_of(h).items()},
                folders,
                host_tags,
                host_labels,
            )
        }
    wanted = list(dict.fromkeys(host_names)) if host_names else sorted(known)
    selected = [known[name] for name in wanted if name in known]
    unknown = tuple(name for name in wanted if name not in known)

    # Same preparation as `cmk -D` (cmk.base.modes.check_mk.mode_dump_hosts)
    cache.ruleset_matcher.ruleset_optimizer.set_all_processed_hosts(set(selected))
    service_names = cache.make_passive_service_name_config(
        make_final_service_name_config(loaded_config, cache.ruleset_matcher)
    )
    enforced = config.EnforcedServicesTable(
        BundledHostRulesetMatcher(
            loaded_config.static_checks,
            cache.ruleset_matcher,
            cache.label_manager.labels_of_host,
        ),
        service_names,
        plugins.check_plugins,
        cache.label_manager.labels_of_service,
    )
    configurer = cache.make_service_configurer(plugins.check_plugins, service_names)
    loaded_at = time.perf_counter()

    reports = []
    for host in selected:
        try:
            reports.append(
                _host_report(
                    host,
                    cache,
                    loaded_config,
                    plugins,
                    service_names,
                    enforced,
                    configurer,
                    periods,
                    referenced,
                )
            )
        except Exception as exc:  # one broken host must not stop the report
            reports.append(HostReport(name=str(host), error=f"{type(exc).__name__}: {exc}"))
    done = time.perf_counter()

    version = cmk_version.__version__
    return CollectResult(
        hosts=tuple(reports),
        unknown_hosts=unknown,
        site=os.environ.get("OMD_SITE", ""),
        version=version,
        edition=edition.name.lower(),
        untested_version=version not in TESTED_VERSIONS,
        timeperiods=tuple(
            sorted(
                [*periods.values(), *(undefined_time_period(n) for n in referenced - set(periods))],
                key=lambda p: p.name,
            )
        ),
        timings={"load_config": loaded_at - start, "collect": done - loaded_at},
    )


def _host_report(
    host, cache, loaded_config, plugins, service_names, enforced, configurer, periods, referenced
):
    labels = cache.label_manager
    is_cluster = host in cache.hosts_config.clusters
    table = cache.check_table(host, plugins.check_plugins, configurer, service_names, enforced)
    services = []
    for svc in sorted(table.values(), key=lambda s: s.description):
        plugin = plugins.check_plugins.get(svc.check_plugin_name)
        defaults = (plugin.check_default_parameters if plugin else None) or {}
        services.append(
            ServiceReport(
                description=svc.description,
                check_plugin=str(svc.check_plugin_name),
                item=svc.item,
                param_sets=_param_sets(
                    svc.parameters, defaults, svc.discovered_parameters, periods, referenced
                ),
                servicegroups=tuple(
                    cache.servicegroups_of_service(host, svc.description, svc.labels)
                ),
                labels=dict(svc.labels),
                enforced=svc.is_enforced,
            )
        )
    return HostReport(
        name=str(host),
        is_cluster=is_cluster,
        addresses=_addresses(host, loaded_config),
        parents=tuple(str(h) for h in (cache.nodes(host) if is_cluster else cache.parents(host))),
        tags={str(k): str(v) for k, v in cache.host_tags.tags(host).items()},
        labels={str(k): str(v) for k, v in labels.labels_of_host(host).items()},
        label_sources={str(k): str(v) for k, v in labels.label_sources_of_host(host).items()},
        hostgroups=tuple(cache.hostgroups(host)),
        contactgroups=tuple(cache.contactgroups(host)),
        agent_mode=_agent_mode(cache.computed_datasources(host)),
        datasources=_datasources(host, cache),
        services=tuple(services),
    )


def _param_sets(
    params,
    defaults: Mapping[str, object],
    discovered: Mapping[str, object],
    periods: Mapping[str, TimePeriod],
    referenced: set[str],
):
    """Main set (always active periods switched on), then a set for each other period.

    params.evaluate() takes "is this period active?" and applies Checkmk's own
    rule: of the active entries the first one wins, key by key.
    """
    names = {str(name) for entry in params.entries for name, _values in entry.timeperiod_values}
    referenced.update(names)
    always = sorted(name for name in names if name in periods and periods[name].always)
    standard = params.evaluate(lambda tp: tp in always)
    sets = [
        ParamSet(
            None,
            classify_params(standard, defaults, discovered),
            always=tuple((name, periods[name].alias) for name in always),
        )
    ]
    for name in sorted(names - set(always)):
        effective = params.evaluate(lambda tp, current=name: tp == current or tp in always)
        alias = periods[name].alias if name in periods else name
        sets.append(ParamSet(name, classify_params(effective, defaults, discovered), alias=alias))
    return tuple(sets)


def _addresses(host, loaded_config) -> tuple[str, ...]:
    # Configured addresses only: resolving names would mean one DNS query per host
    addresses = []
    if ipv4 := loaded_config.ipaddresses.get(host):
        addresses.append(f"IPv4: {ipv4}")
    if ipv6 := loaded_config.ipv6addresses.get(host):
        addresses.append(f"IPv6: {ipv6}")
    return tuple(addresses)


def _agent_mode(sources) -> str:
    if sources.is_all_agents_host:
        return "Checkmk agent and all special agents"
    if sources.is_all_special_agents_host:
        return "Special agents only"
    parts = (["Checkmk agent"] if sources.is_tcp else []) + (["SNMP"] if sources.is_snmp else [])
    return " + ".join(parts) or "No Checkmk agent"


def _datasources(host, cache) -> tuple[str, ...]:
    # Names only: command lines may contain passwords
    found = [f"Special agent: {name}" for name, _configs in cache.special_agents(host)]
    if cache.datasource_programs(host):
        found.append("Datasource program")
    if cache.is_ping_host(host):
        found.append("Ping only")
    return tuple(found)
