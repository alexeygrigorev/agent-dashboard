"""
Usage Accounting Engine.
Consumes private usage stores (.local/metrics/usage-events.jsonl and OpenCode adapters).
Enforces provider/conversation/response deduplication, strict nullability,
reasoning token subset rules, and separation of quota deltas from consumption.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Any, Optional


def normalize_usage_record(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize raw telemetry or usage event record into strict schema.
    Enforces:
    - input_tokens: Optional[int] (null if unknown)
    - output_tokens: Optional[int] (null if unknown)
    - cache_read_tokens: Optional[int] (null if unknown)
    - cache_creation_tokens: Optional[int] (null if unknown)
    - reasoning_tokens: Optional[int] (null if unknown; must NOT be added to output_tokens)
    - quota_delta: Optional[float] (strictly tracked separately, never converted to tokens/cost)
    """
    if not isinstance(raw, dict):
        return None

    project_id = raw.get("project_id") or raw.get("team_id") or "unattributed"
    provider = raw.get("provider") or raw.get("engine") or "unknown"
    conversation_id = raw.get("conversation_id") or raw.get("harness_conversation_id")
    response_id = raw.get("response_id") or raw.get("message_id")
    timestamp = raw.get("timestamp") or raw.get("observed_at") or raw.get("created_at")

    # Safe token extraction
    def get_token_int(key: str) -> Optional[int]:
        val = raw.get(key)
        if val is None or val == "":
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    input_tokens = get_token_int("input_tokens") or get_token_int("prompt_tokens")
    output_tokens = get_token_int("output_tokens") or get_token_int("completion_tokens")
    cache_read = get_token_int("cache_read_tokens") or get_token_int("cached_tokens")
    cache_creation = get_token_int("cache_creation_tokens")
    reasoning = get_token_int("reasoning_tokens")

    # Quota delta (e.g. 5h quota drop)
    quota_delta_raw = raw.get("quota_delta")
    quota_delta = None
    if quota_delta_raw is not None and quota_delta_raw != "":
        try:
            quota_delta = float(quota_delta_raw)
        except (ValueError, TypeError):
            quota_delta = None

    return {
        "project_id": project_id,
        "provider": provider,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "timestamp": timestamp,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "reasoning_tokens": reasoning,
        "quota_delta": quota_delta,
    }


def aggregate_project_usage(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate usage across projects with deduplication.
    Deduplicates by (provider, conversation_id, response_id) when response_id is present.
    If response_id is missing, deduplicates by (provider, conversation_id, timestamp).
    """
    seen_keys = set()
    projects: Dict[str, Dict[str, Any]] = {}

    for raw in records:
        norm = normalize_usage_record(raw)
        if norm is None:
            continue

        prov = norm["provider"]
        cid = norm["conversation_id"]
        rid = norm["response_id"]
        ts = norm["timestamp"]

        # Form unique deduplication key
        if rid:
            dedup_key = (prov, cid, rid)
        elif ts and cid:
            dedup_key = (prov, cid, ts)
        else:
            dedup_key = None

        if dedup_key is not None:
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

        proj = norm["project_id"]
        if proj not in projects:
            projects[proj] = {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_creation_tokens": 0,
                "total_reasoning_tokens": 0,
                "has_unknown_tokens": False,
                "total_quota_delta_percent": 0.0,
                "event_count": 0,
            }

        p_agg = projects[proj]
        p_agg["event_count"] += 1

        if norm["input_tokens"] is not None:
            p_agg["total_input_tokens"] += norm["input_tokens"]
        else:
            p_agg["has_unknown_tokens"] = True

        if norm["output_tokens"] is not None:
            p_agg["total_output_tokens"] += norm["output_tokens"]
        else:
            p_agg["has_unknown_tokens"] = True

        if norm["cache_read_tokens"] is not None:
            p_agg["total_cache_read_tokens"] += norm["cache_read_tokens"]

        if norm["cache_creation_tokens"] is not None:
            p_agg["total_cache_creation_tokens"] += norm["cache_creation_tokens"]

        if norm["reasoning_tokens"] is not None:
            p_agg["total_reasoning_tokens"] += norm["reasoning_tokens"]

        if norm["quota_delta"] is not None:
            p_agg["total_quota_delta_percent"] += norm["quota_delta"]

    return projects


def load_usage_events_jsonl(path: str) -> List[Dict[str, Any]]:
    """Safely load JSONL usage events file."""
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    records.append(rec)
            except Exception:
                continue
    return records
