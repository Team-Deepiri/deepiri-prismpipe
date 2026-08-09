# PrismPipe Go / No-Go for Deepiri Wiring

## Decision: **GO** (2026-07-27)

Wiring landed:

- API Gateway mounts `/api/prism` → `PRISMPIPE_URL` when `PRISMPIPE_ENABLED=true`
- PrismPipe Deepiri pipeline: `POST /pipelines/deepiri/health` (auth → LIS → cyrex → aggregate)
- Compose + configmap env wired; system regression gate added

## Run

```bash
# Unit + bench + host regression (PrismPipe must be up for usefulness)
make prism-gate

# Bring up stack then full gate (requires Docker)
make prism-gate-full

# Direct
./scripts/dev/prismpipe/system_regression_gate.sh
cd platform-services/shared/deepiri-prismpipe && poetry run pytest tests/ -v && ./scripts/bench/run_bench_gate.sh
```

## Checklist

| Gate | Result | Notes |
|------|--------|-------|
| Unit + organic tests green | GO | includes `test_deepiri_nodes` |
| API contract tests green | GO | |
| Regression goldens green | GO | |
| Bench gate passed | GO | |
| Dedup hit_ratio ≥ 0.5 | GO | system gate + bench |
| Swarm error_rate == 0 | GO | |
| `/metrics` schema | GO | |
| Compose healthcheck | GO | |
| Gateway `/api/prism` | GO | rebuild api-gateway after pull |
| Deepiri pipeline useful=true | measure on live stack | auth+LIS must be up |

## Usefulness bar

PrismPipe is **useful** when:

1. Multi-hop `deepiri.auth.health → lis → cyrex → aggregate` returns `useful: true` (auth+LIS reachable)
2. Identical repeats raise `hit_ratio` (fewer downstream HTTP calls)
3. Direct auth/LIS health still works (platform not broken by wiring)

## Explicitly still deferred

- Replacing all Gateway REST with organisms
- Bedd skills on organism events
- LLM IntentPlanner production rollout
