#!/usr/bin/env bash
# Create 1000 test hosts (spike-0000..spike-0999) in folder /spike with canned
# agent output, ~50 services each and 40 parameter rules. Idempotent.
# Run as site user in the devcontainer: tests/fixtures/create_test_hosts.sh
set -Eeuo pipefail
cd "$(dirname "$0")"

API="http://localhost:5000/cmk/check_mk/api/1.0"
AUTH="Authorization: Bearer cmkadmin cmkadmin"
api() {
    curl -sS -f -H "$AUTH" -H "Accept: application/json" \
        -H "Content-Type: application/json" "$@"
}

data_dir="$OMD_ROOT/var/parameter_report_testdata"
mkdir -p "$data_dir"
bash "$OMD_ROOT/share/check_mk/agents/check_mk_agent.linux" >"$data_dir/agent.txt" 2>/dev/null || true

if curl -sS -o /dev/null -w '%{http_code}' -H "$AUTH" \
    "$API/objects/host_config/spike-0000" | grep -q '^200$'; then
    echo "test hosts exist, skipping creation"
else
    api -X POST "$API/domain-types/folder_config/collections/all" \
        -d '{"name": "spike", "title": "spike", "parent": "/"}' >/dev/null
    for batch in $(seq 0 9); do
        jq -n --argjson b "$batch" '{entries: [range(0; 100) as $i | ($b * 100 + $i) as $n |
            {folder: "/spike",
             host_name: ("spike-\($n | tostring | ("0000" + .)[-4:])"),
             attributes: {ipaddress: "127.0.\(($n / 250) | floor).\(($n % 250) + 1)"}}]}' |
            api -X POST "$API/domain-types/host_config/actions/bulk-create/invoke" -d @- >/dev/null
    done
    echo "created 1000 hosts in /spike"
fi

marker="parameter_report test data"
if ! api "$API/domain-types/rule/collections/all?ruleset_name=datasource_programs" |
    jq -e --arg m "$marker" '.value[] | select(.extensions.properties.description == $m)' >/dev/null; then
    api -X POST "$API/domain-types/rule/collections/all" -d "$(jq -n \
        --arg cmd "cat '$data_dir/agent.txt'" --arg m "$marker" '{
        ruleset: "datasource_programs", folder: "/spike",
        properties: {disabled: false, description: $m},
        value_raw: ($cmd | tojson), conditions: {}}')" >/dev/null
    echo "created datasource rule"
fi

# No metrics for the test hosts: their RRDs would take ~67 MB per host (~65 GB).
if ! api "$API/domain-types/rule/collections/all?ruleset_name=extra_service_conf:process_perf_data" |
    jq -e --arg m "$marker" '.value[] | select(.extensions.properties.description == $m)' >/dev/null; then
    api -X POST "$API/domain-types/rule/collections/all" -d "$(jq -n --arg m "$marker" '{
        ruleset: "extra_service_conf:process_perf_data", folder: "/spike",
        properties: {disabled: false, description: $m},
        value_raw: ("0" | tojson), conditions: {}}')" >/dev/null
    echo "created rule disabling metrics"
fi

python3 gen_test_data.py
cmk -U >/dev/null
echo "config compiled"
