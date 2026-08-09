# PrismPipe Benchmarking

## Quick start

```bash
cd platform-services/shared/deepiri-prismpipe
poetry install --with dev,server --extras storage
poetry run python scripts/bench/run_prismpipe_bench.py
./scripts/bench/run_bench_gate.sh
```

## Scenarios

| Scenario | Claim |
|----------|-------|
| `dedup_identical_100` | Shared computation restores outputs; hit_ratio / dedup_ratio |
| `lineage_spawn_50` | Child knowledge inheritance latency |
| `time_split_3branch` | Speculative winner vs slow branch |
| `swarm_mapreduce_1k` | Swarm workers produce correct reduce |
| `persist_hibernate_wake` | Hibernate/wake latency |
| `http_organism_execute_p95` | Concurrent spawn+execute p95 |

Thresholds live in `scripts/bench/scenarios.yaml`. Baselines in `scripts/bench/baselines/baseline.json`.

## Compose

```bash
# from platform root
docker compose -f docker-compose.dev.yml up -d redis deepiri-prismpipe
curl -s localhost:5011/health
curl -s localhost:5011/metrics
```

Env:

- `REDIS_URL` — hot organism persistence (preferred)
- `DATABASE_URL` — Postgres cold store when Redis unset
