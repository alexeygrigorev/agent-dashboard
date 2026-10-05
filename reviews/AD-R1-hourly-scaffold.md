# Independent Exact-Pin Review: Hourly Utilization Scaffold (AD-R1)

- **Review Target (Pinned Commit)**: `9c2244c0c9ec9a98b1e96513b67358e2e2826ae6` (Restore dashboard head scaffold after bootstrap ownership race)
- **Active Working Commit (HEAD)**: `efed70d5b1dd5d67f9a28e1d4225d3f7fa1bfdee` (Bit-for-bit identical to `9c2244c` across `src/` and `tests/`; only `scripts/restore_test.sh` was touched in `efed70d`)
- **Reviewer**: `ad-independent-reviewer` (`session_id: 96a4693f-2fa6-4a5f-a67f-9c251d847645`)
- **Scope**: `reviews/` only; read-only verification of `src/dashboard/hourly.py`, `tests/test_hourly.py`, and secondary engines (`src/dashboard/accounting.py`, `src/dashboard/features.py`, `src/dashboard/server.py`)
- **Stage**: Scaffold preliminary review (AD-R1). A follow-up verification of repair commits will be conducted under AD-R2.

---

## 1. Prior Defect Verification (D1 – D3)

### Defect D1: Window Generation Ceil-Shifting and Dropped Earliest Partial Hour
- **Claim**: `generate_hourly_buckets` ceil-shifts the window into the future and drops the earliest partial hour (contract: exact `[as_of-24h, as_of)`, 24 UTC hourly buckets).
- **Verdict**: **CONFIRMED**
- **Analysis**:
  In `src/dashboard/hourly.py` lines 30–35:
  ```python
  end_of_window = as_of.replace(minute=0, second=0, microsecond=0)
  if as_of > end_of_window:
      end_of_window += datetime.timedelta(hours=1)
  start_of_window = end_of_window - datetime.timedelta(hours=24)
  ```
  When `as_of` contains non-zero minutes or seconds (e.g. `2026-10-04T12:30:00Z`), `end_of_window` is ceil-advanced to `13:00:00Z`, projecting the window 30 minutes into the future. Because exactly 24 hourly buckets are generated backwards from `end_of_window`, `start_of_window` becomes `2026-10-03T13:00:00Z`. Consequently:
  1. The interval `[2026-10-03T12:30:00Z, 2026-10-03T13:00:00Z)` is completely dropped from the 24-hour accounting window. Any agent running exclusively within that 30-minute interval is omitted (`unique_agents = 0`, `total_agent_hours = 0.0`).
  2. The interval `[2026-10-04T12:30:00Z, 2026-10-04T13:00:00Z)` is in the future relative to `as_of`, yet is treated as valid historical observation space.
- **Minimal Reproduction (One-Line)**:
  `python3 -c 'import datetime; from dashboard.hourly import generate_hourly_buckets; b = generate_hourly_buckets(datetime.datetime(2026,10,4,12,30,0,tzinfo=datetime.timezone.utc)); assert b[0][0] == datetime.datetime(2026,10,3,13,0,0,tzinfo=datetime.timezone.utc) and b[-1][1] == datetime.datetime(2026,10,4,13,0,0,tzinfo=datetime.timezone.utc)'`

---

### Defect D2: Overlapping / Duplicate Spans Double-Add Agent Hours
- **Claim**: Overlapping or duplicate spans of one agent double-add `agent_hours` though unique-agent count dedupes.
- **Verdict**: **CONFIRMED**
- **Analysis**:
  In `src/dashboard/hourly.py` lines 133–146:
  ```python
  for idx, (b_start, b_end) in enumerate(buckets):
      overlap_start = max(start_dt, b_start)
      overlap_end = min(end_dt, b_end)
      if overlap_end > overlap_start:
          duration_seconds = (overlap_end - overlap_start).total_seconds()
          hours = duration_seconds / 3600.0
          clipped_hours = min(hours, 1.0)
          
          b_dict = projects[proj]["hourly"][idx]
          b_dict["active_agents"].add(agent_id)
          b_dict["agent_hours"] += clipped_hours
          projects[proj]["total_agent_hours"] += clipped_hours
  ```
  While `b_dict["active_agents"]` is a `set` that correctly dedupes duplicate agent identifiers, `b_dict["agent_hours"] += clipped_hours` is purely additive across all span records for that agent. The clipping `min(hours, 1.0)` is evaluated per-span rather than per-agent within the bucket. For two duplicate spans of 1.0 hour in the same bucket, `agent_hours` increments to `2.0` hours (and `total_agent_hours` increments to `2.0`), while `unique_agents` and `active_agents` report `1`. A single agent cannot physically execute >1.0 agent-hours within a single 1.0-hour window.
- **Minimal Reproduction (One-Line)**:
  `python3 -c 'import datetime; from dashboard.hourly import compute_hourly_utilization; spans = [{"project_id":"p","agent_id":"a","started_at":"2026-10-04T10:00:00Z","ended_at":"2026-10-04T11:00:00Z"}]*2; res = compute_hourly_utilization(spans, as_of=datetime.datetime(2026,10,4,12,0,0,tzinfo=datetime.timezone.utc)); assert res["projects"]["p"]["unique_agents"] == 1 and res["projects"]["p"]["total_agent_hours"] == 2.0'`

---

### Defect D3: Invalid `ended_at` Silently Assumed Alive Until `as_of`
- **Claim**: Invalid `ended_at` is silently treated as alive-until-as_of instead of unknown.
- **Verdict**: **CONFIRMED**
- **Analysis**:
  In `src/dashboard/hourly.py` lines 113–120:
  ```python
  end_str = span.get("ended_at") or span.get("completed_at")
  if end_str:
      try:
          end_dt = parse_iso_timestamp(end_str)
      except Exception:
          end_dt = as_of
  else:
      end_dt = as_of
  ```
  When `end_str` contains an unparseable, malformed, or corrupt string (e.g. `"INVALID_TIMESTAMP"`), the `except Exception:` clause unconditionally falls back to `end_dt = as_of`. This treats a corrupt terminal record identically to an actively running agent. If an agent started 12 hours ago and logged a corrupt terminal timestamp, it is assigned 12.0 hours of active execution up to `as_of`. The system fails to isolate or report this corrupt data in an unknown tracking counter.
- **Minimal Reproduction (One-Line)**:
  `python3 -c 'import datetime; from dashboard.hourly import compute_hourly_utilization; spans = [{"project_id":"p","agent_id":"a","started_at":"2026-10-04T02:00:00Z","ended_at":"CORRUPT"}]; res = compute_hourly_utilization(spans, as_of=datetime.datetime(2026,10,4,12,0,0,tzinfo=datetime.timezone.utc)); assert res["projects"]["p"]["total_agent_hours"] == 10.0'`

---

## 2. Independent Defect Probe: Newly Discovered Defects

### Defect N1: Null/Empty `project_id` Creates Literal `None` Dictionary Key
- **Location**: `src/dashboard/hourly.py:88`
- **Issue**: `proj = span.get("project_id", "unknown")`. When a span contains `{"project_id": None}` or `{"project_id": ""}`, `span.get()` returns `None` (or `""`) rather than the default `"unknown"`. As a result, `projects[None]` is instantiated, outputting `{None: ...}`. In JSON serialization (`server.py`), this serializes to `{"null": ...}` instead of attributing to `"unknown"` or `"unattributed"`.
- **Failing Case / Reproduction**:
  `python3 -c 'from dashboard.hourly import compute_hourly_utilization; import datetime; res = compute_hourly_utilization([{"project_id": None, "agent_id": "a1", "started_at": "2026-10-04T10:00:00Z", "ended_at": "2026-10-04T11:00:00Z"}], as_of=datetime.datetime(2026,10,4,12,0,0,tzinfo=datetime.timezone.utc)); assert None in res["projects"]'`

---

### Defect N2: Missing-Data Omission for Authorized Projects with Zero Activity
- **Location**: `src/dashboard/hourly.py:85-103`
- **Issue**: The three authorized projects (`agent-branches`, `agent-dashboard`, `quota-launcher`) are not pre-populated in `output_projects`. If an authorized project has 0 events/spans in the 24-hour window, its key is completely missing from `res["projects"]` rather than returning a zero-filled schema (`unique_agents: 0`, `total_agent_hours: 0.0`, 24 zero buckets). Consumers querying authorized project keys directly will trigger `KeyError`.
- **Failing Case / Reproduction**:
  `python3 -c 'from dashboard.hourly import compute_hourly_utilization; res = compute_hourly_utilization([]); assert "agent-branches" not in res["projects"]'`

---

### Defect N3: Total Absence of Unknown/Invalid Record Tracking and Coverage
- **Location**: `src/dashboard/hourly.py:105-124`
- **Issue**: `AGENTS.md` explicitly specifies: *"Unique identity deduplication, clipped agent-hours, attribution, coverage, and unknown tracking."* In the current implementation:
  - Spans missing `agent_id` are silently dropped (`continue` at line 106).
  - Spans with unparseable `started_at` are silently dropped (`continue` at line 111).
  - Spans with backwards timestamps (`end_dt < start_dt`) are silently dropped (`continue` at line 124).
  None of these anomalies are recorded in an `unknown` or `dropped_records` field, and no coverage ratio is computed. Telemetry degradation is invisible to operators.
- **Failing Case / Reproduction**:
  Passing 10 malformed spans to `compute_hourly_utilization` produces an empty output without any count or indicator of rejected or anomalous spans.

---

### Defect N4: Non-Identical Overlapping Spans for the Same Agent Exceed Physical Capacity
- **Location**: `src/dashboard/hourly.py:140-145`
- **Issue**: If an agent has overlapping spans that are not exact duplicates (e.g. Span 1: 10:00–10:40 and Span 2: 10:20–11:00 in bucket 10:00–11:00), the actual wall-clock duration of agent activity is 60 minutes (1.0 hour). Because `hours = duration_seconds / 3600.0` is computed per span and `min(hours, 1.0)` is evaluated per span (where each is <= 1.0), the engine sums `40m + 40m = 80m = 1.3333h`. In-bucket agent spans must be unioned across time intervals per agent before summing agent hours.
- **Failing Case / Reproduction**:
  `python3 -c 'import datetime; from dashboard.hourly import compute_hourly_utilization; spans = [{"project_id":"p","agent_id":"a","started_at":"2026-10-04T10:00:00Z","ended_at":"2026-10-04T10:40:00Z"},{"project_id":"p","agent_id":"a","started_at":"2026-10-04T10:20:00Z","ended_at":"2026-10-04T11:00:00Z"}]; res = compute_hourly_utilization(spans, as_of=datetime.datetime(2026,10,4,12,0,0,tzinfo=datetime.timezone.utc)); assert res["projects"]["p"]["hourly_buckets"][22]["agent_hours"] == 1.3333'`

---

### Defect N5: Future Agent Ingestion via Ceil-Shift Window
- **Location**: `src/dashboard/hourly.py:127`
- **Issue**: Due to the ceil-shifted window (D1), `window_end` is up to 59 minutes in the future when `as_of` has non-zero minutes/seconds. Line 127 checks `if end_dt <= window_start or start_dt >= window_end: continue`. If an agent span begins in the future relative to `as_of` (e.g. `as_of = 12:15:00Z`, `started_at = 12:30:00Z`, `ended_at = 13:00:00Z`), it passes the filter because `start_dt < window_end` (`12:30 < 13:00`), polluting the historical 24-hour utilization metrics with future events.
- **Failing Case / Reproduction**:
  `python3 -c 'import datetime; from dashboard.hourly import compute_hourly_utilization; as_of = datetime.datetime(2026,10,4,12,15,0,tzinfo=datetime.timezone.utc); spans = [{"project_id":"p","agent_id":"a","started_at":"2026-10-04T12:30:00Z","ended_at":"2026-10-04T13:00:00Z"}]; res = compute_hourly_utilization(spans, as_of=as_of); assert res["projects"]["p"]["total_agent_hours"] == 0.5'`

---

### Defect N6: Usage Accounting Coerces Falsy `0` Tokens to `None` and Flags False Corruption
- **Location**: `src/dashboard/accounting.py:45-49`
- **Issue**:
  ```python
  input_tokens = get_token_int("input_tokens") or get_token_int("prompt_tokens")
  output_tokens = get_token_int("output_tokens") or get_token_int("completion_tokens")
  cache_read = get_token_int("cache_read_tokens") or get_token_int("cached_tokens")
  ```
  When an event record contains an explicit `0` for `input_tokens`, Python evaluates `0` as falsy and moves to `get_token_int("prompt_tokens")`. If `prompt_tokens` is absent, `input_tokens` is assigned `None`. Later, in `aggregate_project_usage` (line 125):
  ```python
  if norm["input_tokens"] is not None:
      p_agg["total_input_tokens"] += norm["input_tokens"]
  else:
      p_agg["has_unknown_tokens"] = True
  ```
  Valid usage records with zero tokens (e.g. tool execution steps or cache-only calls) have their count set to `None` and falsely trigger `has_unknown_tokens = True`. Null and zero are conflated.
- **Failing Case / Reproduction**:
  `python3 -c 'from dashboard.accounting import normalize_usage_record; norm = normalize_usage_record({"project_id":"p","provider":"pr","conversation_id":"c","response_id":"r","input_tokens": 0, "output_tokens": 10}); assert norm["input_tokens"] is None'`

---

### Defect N7: Rejected Tasks Accepted as Completed Features
- **Location**: `src/dashboard/features.py:37`
- **Issue**:
  ```python
  # Must have accepted or done status
  if status != "done" and not (status == "accepted" or "ACCEPT" in acceptance_status):
      continue
  ```
  If a task is marked `status: "done"` by an executor but reviewer notes `acceptance_status: "REJECTED"`, `status != "done"` evaluates to `False`. The boolean expression short-circuits to `False`, bypassing the `continue`. The rejected task is incorrectly added to `projects[proj]` as a verified completed feature.
- **Failing Case / Reproduction**:
  `python3 -c 'import tempfile, json; from dashboard.features import load_accepted_features; tf = tempfile.NamedTemporaryFile("w+", delete=False); json.dump({"tasks": [{"id": "t1", "status": "done", "acceptance_status": "REJECTED"}]}, tf); tf.flush(); feats = load_accepted_features(tf.name); assert len(feats.get("unattributed", [])) == 1'`

---

### Defect N8: `server.py` Schema Mismatch When Ingesting `TEAM-REGISTRY.json`
- **Location**: `src/dashboard/server.py:56-64`
- **Issue**: `server.py` inspects `reg_data.get("teams", [])` and iterates over `team.get("members", [])` and assigns `team.get("id")` as the project identifier. In the actual `coordination/TEAM-REGISTRY.json` schema:
  - Agent lists are stored under the key `agents`, not `members`.
  - Team IDs are operational groups (e.g. `"a06-a10"`, `"a01-harness"`), whereas authorized projects are defined under top-level `projects` (`agent-branches`, `agent-dashboard`, `quota-launcher`).
  As a result, 168 out of 169 agents in `TEAM-REGISTRY.json` are dropped, and the single agent found is attributed to team `"a06-a10"` rather than an authorized project.
- **Failing Case / Reproduction**:
  Running `_handle_hourly()` against production `TEAM-REGISTRY.json` produces `unique_agents = 1` across all teams instead of ingesting the active agent fleet.

---

## 3. Required Negative Tests List for the Repair

The backend repair commit must introduce comprehensive regression and negative unit tests to validate these boundaries:

1. **`test_window_boundaries_non_hour_aligned`**:
   - Verify that when `as_of = 2026-10-04T12:30:00Z`, `window_start` is strictly `2026-10-03T12:30:00Z` and `window_end` is strictly `2026-10-04T12:30:00Z`.
   - Verify that an agent span within `[2026-10-03T12:30:00Z, 2026-10-03T13:00:00Z)` is fully counted.
   - Verify that an agent span in the future `[2026-10-04T12:30:00Z, 2026-10-04T13:00:00Z)` is rejected.

2. **`test_per_agent_span_union_and_deduplication`**:
   - Provide duplicate identical spans for the same agent in the same bucket: assert `agent_hours == 1.0` and `unique_agents == 1`.
   - Provide overlapping spans (e.g. 10:00–10:40 and 10:20–11:00) for the same agent: assert `agent_hours == 1.0` (union duration), not `1.3333`.
   - Provide disjoint spans within the same bucket (e.g. 10:00–10:15 and 10:30–10:45) for the same agent: assert `agent_hours == 0.5`.

3. **`test_invalid_ended_at_quarantined_not_alive`**:
   - Supply a span with `started_at` 5 hours ago and `"ended_at": "CORRUPT_TIMESTAMP"`.
   - Assert that `total_agent_hours` does not accumulate 5.0 hours of active execution.
   - Assert that the corrupted span is recorded in an unknown/invalid records structure.

4. **`test_backwards_and_malformed_timestamps`**:
   - Supply a span where `ended_at < started_at`: assert it is tracked in unknown/rejected metrics, not silently ignored.
   - Supply spans missing `agent_id` or with malformed `started_at`: assert they are tracked in unknown metrics.

5. **`test_null_and_empty_project_id_attribution`**:
   - Supply `{"project_id": None}` and `{"project_id": ""}`: assert attribution maps to `"unknown"` or `"unattributed"`, never `None` or `""`.

6. **`test_authorized_projects_zero_initialization`**:
   - When given empty spans `[]`, assert that `res["projects"]` contains keys for `"agent-branches"`, `"agent-dashboard"`, and `"quota-launcher"`, each with `unique_agents: 0`, `total_agent_hours: 0.0`, and 24 zero buckets.

7. **`test_zero_token_preservation_in_accounting`**:
   - In `accounting.py`, supply `input_tokens: 0`, `output_tokens: 0`, `cache_read_tokens: 0`.
   - Assert normalized fields retain `0` (not `None`), and `has_unknown_tokens` remains `False`.

8. **`test_rejected_task_rejection_in_features`**:
   - In `features.py`, supply a task with `status: "done"` and `acceptance_status: "REJECTED"`.
   - Assert that it is excluded from accepted features.

---

## 4. Residual Risk

1. **High-Frequency Overlapping Spans & Interval Union Scaling**:
   Computing exact interval unions for thousands of micro-spans per agent across 24 hourly buckets could lead to $O(N \log N)$ interval merging overhead if not implemented cleanly with standard sweep-line or sorted interval merges.
2. **Timezone Offset Parsing vs Naive Inputs**:
   Mixed offsets in ISO strings (e.g. `+02:00`, `-05:00`, `Z`) must normalize accurately to UTC without loss of precision or naive datetime ambiguity.
3. **Cross-Project Shared Agent Fleet Totals**:
   While unique agents are deduplicated within each project, summing project unique agents across the fleet will overcount agents that contributed to multiple projects. A fleet-wide rollup metric is recommended to prevent misleading aggregate utilization.
