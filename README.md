# Agent Dashboard

Standalone private operational dashboard for autonomous agent fleet monitoring across the three authorized projects:
1. `agent-branches` (Cloudflare Agent Git runtime protocol)
2. `agent-dashboard` (Fleet operations and accounting engine)
3. `quota-launcher` (Quota-aware multi-provider launcher)

## Key Subsystems
- `dashboard.hourly`: 24 UTC hourly bucket aggregator `[as_of-24h, as_of)` with agent-hour clipping and identity deduplication.
- `dashboard.accounting`: Usage accounting engine consuming `.local/metrics/usage-events.jsonl` and OpenCode adapters, enforcing strict nullability and quota/usage separation.
- `dashboard.features`: Completed feature tracking joined from accepted task evidence in `coordination/TASKS.json`.
- `dashboard.server`: Lightweight localhost preview server.

## Development & Testing
```bash
python3 -m unittest discover -s tests/
```
