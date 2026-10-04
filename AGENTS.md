# Agent Dashboard

Read /home/alexey/git/cloudflare-agent-git/AGENTS.md, coordination/OPERATING-MODEL.md, and coordination/RESOURCE-POLICY.md, and ~/git/.agents/skills/a2a-communication/SKILL.md plus external-model-agents/SKILL.md.

User authorizes this standalone private project, private GitHub backup, Agent Branches as primary development platform where verified, ordinary Git recovery, useful external execution, and independent review. No purchases, Copilot, Rust builds/installs, secrets in Git, deleting worktrees, changing dirty quse/aplexer, or busy-pane injection. Claude/Codex sparse.

Fresh quse immediately before every external launch. Host MemAvailable must retain >=10GiB after reservation; disk target and scratch mount must retain >=50GiB plus 512MiB estimated spike; /tmp fails so TMPDIR under .local/tmp on root. aplexer --memory 1500M maximum per new worker, bounded timeout, genuine whoami, durable tasks, first tool action and incremental artifacts.

## Core Mission & Architecture
The Agent Dashboard provides internal operational visibility across the three authorized projects (AgentBranches, Agent Dashboard, Quota-aware Launcher):
1. **Hourly 24h Utilization**: Exact `[as_of-24h, as_of)` interval across 24 UTC hourly buckets. Unique identity deduplication, clipped agent-hours, attribution, coverage, and unknown tracking.
2. **Usage Accounting**: Consume private existing usage stores (`.local/metrics/usage-events.jsonl`, OpenCode adapter); normalize provider semantics (cache/noncache/reasoning nullability, quota deltas separate from cost/tokens).
3. **Completed Features**: Join accepted task evidence to project feature completions without counting repeated reviews/commits as features.
4. **Localhost Preview & Private Storage**: Standalone private project and localhost preview using AgentBranches where verified plus Git fallback.

## Ownership & Workflow
- `agent-dashboard-head`: Native interactive implementation and integration head.
- Task executors: Independently owned backend, metrics, and UI executors.
- Independent reviewer: Owns `reviews/` only; independent challenge of accounting logic and negative cases.
- Serialize commits using `flock .local/git.lock` and explicit paths; preserve dirty changes.
