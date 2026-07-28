#!/usr/bin/env bash
# PrismPipe performance gate — fails if claims regress below thresholds.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SCENARIOS="${SCENARIOS:-scripts/bench/scenarios.yaml}"
RESULTS="${RESULTS:-scripts/bench/last_results.json}"
BASELINE="${BASELINE:-scripts/bench/baselines/baseline.json}"
P95_REGRESSION_PCT="${P95_REGRESSION_PCT:-50}"

echo "==> Running PrismPipe benches"
poetry run python scripts/bench/run_prismpipe_bench.py --scenarios "$SCENARIOS" --out "$RESULTS"

echo "==> Evaluating gates"
poetry run python - <<'PY'
import json
import os
import sys
from pathlib import Path

import yaml

results = json.loads(Path(os.environ.get("RESULTS", "scripts/bench/last_results.json")).read_text())
scenarios = yaml.safe_load(Path(os.environ.get("SCENARIOS", "scripts/bench/scenarios.yaml")).read_text())["scenarios"]
baseline_path = Path(os.environ.get("BASELINE", "scripts/bench/baselines/baseline.json"))
baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
p95_reg_pct = float(os.environ.get("P95_REGRESSION_PCT", "50"))

failures = []

dedup = results.get("dedup_identical_100", {})
cfg = scenarios["dedup_identical_100"]
if dedup.get("deduplication_ratio", 0) < cfg["min_dedup_ratio"]:
    failures.append(
        f"dedup_ratio {dedup.get('deduplication_ratio')} < {cfg['min_dedup_ratio']}"
    )
if dedup.get("hit_ratio", 0) < cfg["min_hit_ratio"]:
    failures.append(f"hit_ratio {dedup.get('hit_ratio')} < {cfg['min_hit_ratio']}")

lineage = results.get("lineage_spawn_50", {})
if lineage.get("avg_ms", 9999) > scenarios["lineage_spawn_50"]["max_avg_ms"]:
    failures.append(f"lineage avg_ms {lineage.get('avg_ms')} too high")

ts = results.get("time_split_3branch", {})
if ts.get("avg_ms", 9999) > scenarios["time_split_3branch"]["max_avg_ms"]:
    failures.append(f"time_split avg_ms {ts.get('avg_ms')} too high")

swarm = results.get("swarm_mapreduce_1k", {})
if swarm.get("error_rate", 1) > scenarios["swarm_mapreduce_1k"]["max_error_rate"]:
    failures.append(f"swarm error_rate {swarm.get('error_rate')}")
if swarm.get("reduced") != swarm.get("expected"):
    failures.append(f"swarm reduced {swarm.get('reduced')} != {swarm.get('expected')}")

persist = results.get("persist_hibernate_wake", {})
if persist.get("avg_wake_ms", 9999) > scenarios["persist_hibernate_wake"]["max_avg_wake_ms"]:
    failures.append(f"wake avg_ms {persist.get('avg_wake_ms')} too high")

http = results.get("http_organism_execute_p95", {})
if http.get("p95_ms", 9999) > scenarios["http_organism_execute_p95"]["max_p95_ms"]:
    failures.append(f"http p95 {http.get('p95_ms')} too high")

base_http = baseline.get("http_organism_execute_p95", {})
if base_http.get("p95_ms") and http.get("p95_ms"):
    limit = base_http["p95_ms"] * (1 + p95_reg_pct / 100.0)
    if http["p95_ms"] > limit:
        failures.append(
            f"http p95 {http['p95_ms']} regresses vs baseline {base_http['p95_ms']} (limit {limit})"
        )

if failures:
    print("GATE FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("GATE PASSED")
print(json.dumps(results, indent=2, sort_keys=True))
PY
