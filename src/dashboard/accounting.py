"""
Usage Accounting Engine.
Consumes private usage stores (.local/metrics/usage-events.jsonl and OpenCode adapters).
Enforces provider/conversation/response deduplication, strict nullability,
reasoning token subset rules, and separation of quota deltas from consumption.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from dashboard import canonical_project_id
from dashboard.hourly import parse_iso_timestamp, _as_utc, _iso

_TOKEN_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens", "inputTokens", "promptTokens"),
    "output_tokens": ("output_tokens", "completion_tokens", "outputTokens", "completionTokens"),
    "cache_read_tokens": (
        "cache_read_tokens",
        "cached_tokens",
        "cached_input_tokens",
        "cachedInputTokens",
        "cacheReadTokens",
    ),
    "cache_creation_tokens": (
        "cache_creation_tokens",
        "cache_write_tokens",
        "cacheWriteTokens",
        "cacheCreationTokens",
    ),
    "reasoning_tokens": (
        "reasoning_tokens",
        "reasoning_output_tokens",
        "reasoningTokens",
        "reasoningOutputTokens",
    ),
    "noncache_input_tokens": (
        "noncache_input_tokens",
        "uncached_input_tokens",
        "noncacheInputTokens",
        "uncachedInputTokens",
    ),
}

_COST_KEYS = ("cost", "cost_usd", "reported_cost", "costUsd", "reportedCost")
_NEST_KEYS = ("usage", "tokens", "token_usage")


class InvalidCountError(ValueError):
    """Boolean or negative token count — the whole record is invalid."""


def _flatten_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Copy nested usage/token objects into a working dict without inventing values."""
    out = dict(raw)
    for nest in _NEST_KEYS:
        inner = raw.get(nest)
        if not isinstance(inner, dict):
            continue
        for key, value in inner.items():
            if key not in out or out[key] is None or out[key] == "":
                out[key] = value
    return out


def _first_present(raw: Dict[str, Any], keys: Tuple[str, ...]) -> Tuple[bool, Any]:
    """Return (found, value) for the first matching key that is present."""
    for key in keys:
        if key in raw:
            return True, raw[key]
    return False, None


def _as_token_int(value: Any) -> Optional[int]:
    """Parse a token count. Bool and negative values raise InvalidCountError."""
    if isinstance(value, bool):
        raise InvalidCountError("boolean token count")
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (ValueError, TypeError):
        return None
    if number < 0:
        raise InvalidCountError("negative token count")
    return number


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _token_field(raw: Dict[str, Any], field: str) -> Optional[int]:
    found, value = _first_present(raw, _TOKEN_ALIASES[field])
    if not found:
        return None
    return _as_token_int(value)


def normalize_usage_record(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize raw telemetry or usage event record into strict schema.
    Unknown cache/noncache/reasoning stay null unless the source proves them.
    quota_delta is never converted into cost or tokens.
    reasoning_tokens are a subset and are never added to output_tokens.
    Boolean or negative token counts invalidate the whole record (returns None).
    A proven 0 is preserved (not treated as missing).
    """
    if not isinstance(raw, dict):
        return None

    flat = _flatten_raw(raw)

    project_id = canonical_project_id(
        flat.get("project_id") if "project_id" in flat else None
    )
    provider = flat.get("provider") or flat.get("engine") or "unknown"
    conversation_id = flat.get("conversation_id") or flat.get("harness_conversation_id")
    response_id = flat.get("response_id") or flat.get("message_id")
    timestamp = (
        flat.get("timestamp")
        or flat.get("observed_at")
        or flat.get("created_at")
        or flat.get("at")
    )
    mode = flat.get("mode")

    try:
        input_tokens = _token_field(flat, "input_tokens")
        output_tokens = _token_field(flat, "output_tokens")
        cache_read = _token_field(flat, "cache_read_tokens")
        cache_creation = _token_field(flat, "cache_creation_tokens")
        reasoning = _token_field(flat, "reasoning_tokens")
        noncache_input = _token_field(flat, "noncache_input_tokens")
    except InvalidCountError:
        return None

    quota_found, quota_raw = _first_present(flat, ("quota_delta", "quotaDelta"))
    quota_delta = _as_float(quota_raw) if quota_found else None

    cost_found, cost_raw = _first_present(flat, _COST_KEYS)
    cost = _as_float(cost_raw) if cost_found else None

    return {
        "project_id": project_id,
        "provider": provider,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "timestamp": timestamp,
        "mode": mode,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "reasoning_tokens": reasoning,
        "noncache_input_tokens": noncache_input,
        "quota_delta": quota_delta,
        "cost": cost,
    }


def _parse_record_ts(value: Any) -> Optional[datetime.datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return _as_utc(value)
    if not isinstance(value, str):
        return None
    try:
        return parse_iso_timestamp(value)
    except Exception:
        return None


def _empty_agg() -> Dict[str, Any]:
    return {
        "total_input_tokens": None,
        "total_output_tokens": None,
        "total_cache_read_tokens": None,
        "total_cache_creation_tokens": None,
        "total_reasoning_tokens": None,
        "total_noncache_input_tokens": None,
        "has_unknown_tokens": False,
        "has_unknown_cache": False,
        "has_unknown_reasoning": False,
        "cache_coverage": None,
        "reasoning_coverage": None,
        "total_quota_delta_percent": None,
        "total_cost": None,
        "event_count": 0,
        "unknown": False,
    }


def _sum_fail_closed(values: List[Optional[int | float]]) -> Tuple[Optional[int | float], int, int]:
    """Sum known values only if every value is proven; else total is None.
    Returns (total_or_none, known_count, n).
    """
    n = len(values)
    known = [v for v in values if v is not None]
    if n == 0:
        return None, 0, 0
    if len(known) != n:
        return None, len(known), n
    return sum(known), len(known), n


def aggregate_project_usage(
    records: List[Dict[str, Any]],
    as_of: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """
    Aggregate usage across projects with response-identity deduplication
    and a half-open [as_of-24h, as_of) window.

    Records lacking response_id are not deduplicated by timestamp.
    Cumulative snapshots (mode=cumulative) keep the latest per (provider, conversation_id).
    Reasoning is never folded into output totals. Quota deltas stay out of cost/tokens.
    """
    if as_of is None:
        as_of = datetime.datetime.now(datetime.timezone.utc)
    else:
        as_of = _as_utc(as_of)
    window_start = as_of - datetime.timedelta(hours=24)
    window_end = as_of

    coverage_gaps: List[Dict[str, str]] = []
    if not records:
        coverage_gaps.append({"source": "records", "reason": "empty or missing usage input"})
        return {
            "as_of": _iso(as_of),
            "window_start": _iso(window_start),
            "window_end": _iso(window_end),
            "unknown": True,
            "coverage_gaps": coverage_gaps,
            "invalid_records": 0,
            "records_missing_response_id": 0,
            "records_missing_timestamp": 0,
            "projects": {},
        }

    normalized: List[Dict[str, Any]] = []
    invalid_records = 0
    for raw in records:
        if not isinstance(raw, dict):
            invalid_records += 1
            continue
        try:
            norm = normalize_usage_record(raw)
        except InvalidCountError:
            invalid_records += 1
            continue
        if norm is None:
            invalid_records += 1
            continue
        normalized.append(norm)

    if not normalized:
        coverage_gaps.append({"source": "records", "reason": "no valid usage records"})
        return {
            "as_of": _iso(as_of),
            "window_start": _iso(window_start),
            "window_end": _iso(window_end),
            "unknown": True,
            "coverage_gaps": coverage_gaps,
            "invalid_records": invalid_records,
            "records_missing_response_id": 0,
            "records_missing_timestamp": 0,
            "projects": {},
        }

    windowed: List[Dict[str, Any]] = []
    missing_ts = 0
    for norm in normalized:
        ts = _parse_record_ts(norm.get("timestamp"))
        if ts is None:
            missing_ts += 1
            continue
        if ts < window_start or ts >= window_end:
            continue
        windowed.append(norm)

    if missing_ts:
        coverage_gaps.append({
            "source": "timestamp",
            "reason": f"{missing_ts} record(s) missing or unparseable timestamp; excluded from window",
        })

    latest_cumulative: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    seen_response: set = set()
    kept: List[Dict[str, Any]] = []
    missing_response_id = 0

    for norm in windowed:
        prov = norm["provider"]
        cid = norm["conversation_id"]
        rid = norm["response_id"]
        ts = norm["timestamp"]
        if (norm.get("mode") or "").lower() == "cumulative" and cid:
            key = (prov, cid)
            prev = latest_cumulative.get(key)
            prev_ts = "" if prev is None else str(prev.get("timestamp") or "")
            if prev is None or str(ts or "") >= prev_ts:
                latest_cumulative[key] = norm
            continue
        if rid:
            dedup_key = (prov, cid, rid)
            if dedup_key in seen_response:
                continue
            seen_response.add(dedup_key)
            kept.append(norm)
            continue
        missing_response_id += 1
        kept.append(norm)

    to_sum = list(latest_cumulative.values()) + kept
    if missing_response_id:
        coverage_gaps.append({
            "source": "response_id",
            "reason": f"{missing_response_id} windowed record(s) lack response identity; counted without timestamp dedup",
        })

    projects: Dict[str, Dict[str, Any]] = {}
    buckets: Dict[str, Dict[str, List[Optional[int | float]]]] = {}

    def _bucket(proj: str) -> Dict[str, List[Optional[int | float]]]:
        if proj not in buckets:
            buckets[proj] = {
                "input_tokens": [],
                "output_tokens": [],
                "cache_read_tokens": [],
                "cache_creation_tokens": [],
                "reasoning_tokens": [],
                "noncache_input_tokens": [],
                "quota_delta": [],
                "cost": [],
            }
            projects[proj] = _empty_agg()
        return buckets[proj]

    for norm in to_sum:
        proj = norm["project_id"]
        slot = _bucket(proj)
        projects[proj]["event_count"] += 1
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "reasoning_tokens",
            "noncache_input_tokens",
            "quota_delta",
            "cost",
        ):
            slot[field].append(norm[field])

    for proj, slot in buckets.items():
        p_agg = projects[proj]
        in_total, in_known, in_n = _sum_fail_closed(slot["input_tokens"])
        out_total, out_known, out_n = _sum_fail_closed(slot["output_tokens"])
        p_agg["total_input_tokens"] = in_total
        p_agg["total_output_tokens"] = out_total
        p_agg["has_unknown_tokens"] = (in_known != in_n) or (out_known != out_n)

        cr_total, cr_known, cr_n = _sum_fail_closed(slot["cache_read_tokens"])
        cc_total, cc_known, cc_n = _sum_fail_closed(slot["cache_creation_tokens"])
        p_agg["total_cache_read_tokens"] = cr_total
        p_agg["total_cache_creation_tokens"] = cc_total
        p_agg["cache_coverage"] = (cr_known / cr_n) if cr_n else None
        p_agg["has_unknown_cache"] = cr_known != cr_n or cc_known != cc_n

        rs_total, rs_known, rs_n = _sum_fail_closed(slot["reasoning_tokens"])
        p_agg["total_reasoning_tokens"] = rs_total
        p_agg["reasoning_coverage"] = (rs_known / rs_n) if rs_n else None
        p_agg["has_unknown_reasoning"] = rs_known != rs_n

        nc_values = [v for v in slot["noncache_input_tokens"] if v is not None]
        p_agg["total_noncache_input_tokens"] = sum(nc_values) if nc_values else None

        qd_total, qd_known, qd_n = _sum_fail_closed(slot["quota_delta"])
        p_agg["total_quota_delta_percent"] = qd_total

        cost_total, cost_known, cost_n = _sum_fail_closed(slot["cost"])
        p_agg["total_cost"] = cost_total

    unknown = len(to_sum) == 0
    if unknown:
        coverage_gaps.append({"source": "window", "reason": "no usage records inside [as_of-24h, as_of)"})

    return {
        "as_of": _iso(as_of),
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "unknown": unknown,
        "coverage_gaps": coverage_gaps,
        "invalid_records": invalid_records,
        "records_missing_response_id": missing_response_id,
        "records_missing_timestamp": missing_ts,
        "projects": projects,
    }


def load_usage_events_jsonl(path: str) -> List[Dict[str, Any]]:
    """Safely load JSONL usage events file. Missing file yields empty list."""
    if not path or not os.path.exists(path):
        return []
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def _known_standup_value(stats: Dict[str, Any], field: str, aliases: Tuple[str, ...]) -> Any:
    missing = stats.get("missing_fields") or {}
    missing_count = missing.get(field, 0) or 0
    if missing_count:
        return None
    for alias in aliases:
        if alias in stats:
            return stats[alias]
    return None


def _flatten_opencode_sessions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    sessions = data.get("sessions") or []
    if not isinstance(sessions, list):
        return records
    observed_at = data.get("observed_at") or data.get("at")
    for session in sessions:
        if not isinstance(session, dict):
            continue
        conversation_id = session.get("conversation_id")
        owners = session.get("owners") or []
        team_id = None
        if isinstance(owners, list) and owners and isinstance(owners[0], dict):
            team_id = owners[0].get("team_id")
        models = session.get("models") or []
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            cumulative = model.get("cumulative") if isinstance(model.get("cumulative"), dict) else model
            known = cumulative.get("known_fields") if isinstance(cumulative.get("known_fields"), dict) else {}
            missing = cumulative.get("missing_fields") if isinstance(cumulative.get("missing_fields"), dict) else {}

            def pick(field: str, *alts: str) -> Any:
                if missing.get(field):
                    return None
                if known and not known.get(field):
                    return None
                for name in (field,) + alts:
                    if name in cumulative:
                        return cumulative[name]
                return None

            records.append({
                "provider": model.get("provider") or "opencode",
                "project_id": session.get("project_id"),
                "team_id": team_id,
                "conversation_id": conversation_id,
                "response_id": None,
                "timestamp": observed_at,
                "mode": "cumulative",
                "input_tokens": pick("input_tokens"),
                "output_tokens": pick("output_tokens"),
                "reasoning_tokens": pick("reasoning_output_tokens", "reasoning_tokens"),
                "cached_input_tokens": pick("cached_input_tokens"),
                "cache_write_tokens": pick("cache_write_tokens"),
                "cost": pick("reported_cost", "cost"),
            })
    return records


def load_opencode_usage(path: str) -> List[Dict[str, Any]]:
    """
    Load OpenCode adapter usage: JSONL events, a JSON list, a single event,
    a standup object with by_team aggregates, or session-level adapter output.
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read().strip()
    if not text:
        return []

    if path.endswith(".jsonl"):
        return load_usage_events_jsonl(path)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return load_usage_events_jsonl(path)

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("sessions"), list) and data.get("sessions") and isinstance(data["sessions"][0], dict) and "models" in data["sessions"][0]:
        return _flatten_opencode_sessions(data)
    if "by_team" in data and isinstance(data["by_team"], dict):
        records: List[Dict[str, Any]] = []
        observed = data.get("observed_at") or data.get("at")
        for team_id, stats in data["by_team"].items():
            if not isinstance(stats, dict):
                continue
            records.append({
                "provider": "opencode",
                "team_id": team_id,
                "timestamp": observed,
                "mode": "cumulative",
                "input_tokens": _known_standup_value(stats, "input_tokens", ("input_tokens",)),
                "output_tokens": _known_standup_value(stats, "output_tokens", ("output_tokens",)),
                "reasoning_tokens": _known_standup_value(
                    stats, "reasoning_output_tokens", ("reasoning_output_tokens", "reasoning_tokens")
                ),
                "cached_input_tokens": _known_standup_value(
                    stats, "cached_input_tokens", ("cached_input_tokens",)
                ),
                "cache_write_tokens": _known_standup_value(
                    stats, "cache_write_tokens", ("cache_write_tokens",)
                ),
                "cost": _known_standup_value(stats, "reported_cost", ("reported_cost", "cost")),
            })
        return records
    if "opencode_usage" in data and isinstance(data["opencode_usage"], dict):
        return load_opencode_usage_from_obj(data["opencode_usage"])
    return [data]


def load_opencode_usage_from_obj(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize an already-loaded OpenCode adapter object."""
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("sessions"), list) and data["sessions"] and isinstance(data["sessions"][0], dict) and "models" in (data["sessions"][0] or {}):
        return _flatten_opencode_sessions(data)
    if "by_team" in data and isinstance(data["by_team"], dict):
        # Reuse file loader shape via a temporary dict dump path-free.
        records: List[Dict[str, Any]] = []
        observed = data.get("observed_at") or data.get("at")
        for team_id, stats in data["by_team"].items():
            if not isinstance(stats, dict):
                continue
            records.append({
                "provider": "opencode",
                "team_id": team_id,
                "timestamp": observed,
                "mode": "cumulative",
                "input_tokens": _known_standup_value(stats, "input_tokens", ("input_tokens",)),
                "output_tokens": _known_standup_value(stats, "output_tokens", ("output_tokens",)),
                "reasoning_tokens": _known_standup_value(
                    stats, "reasoning_output_tokens", ("reasoning_output_tokens", "reasoning_tokens")
                ),
                "cached_input_tokens": _known_standup_value(
                    stats, "cached_input_tokens", ("cached_input_tokens",)
                ),
                "cache_write_tokens": _known_standup_value(
                    stats, "cache_write_tokens", ("cache_write_tokens",)
                ),
                "cost": _known_standup_value(stats, "reported_cost", ("reported_cost", "cost")),
            })
        return records
    return [data]


def load_usage_records(
    events_path: Optional[str] = None,
    opencode_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if events_path:
        records.extend(load_usage_events_jsonl(events_path))
    if opencode_path:
        records.extend(load_opencode_usage(opencode_path))
    return records
