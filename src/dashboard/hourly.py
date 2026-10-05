"""
24-Hour Hourly Utilization Engine.
Computes 24 half-open UTC hourly buckets [as_of - 24h, as_of) per project.
Unique-identity dedup unions clipped intervals per (project, bucket, agent).
"""

from __future__ import annotations

import datetime
import json
import os
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dashboard import CANONICAL_PROJECT_IDS, UNATTRIBUTED_PROJECT_ID, canonical_project_id

Interval = Tuple[datetime.datetime, datetime.datetime]


def parse_iso_timestamp(ts_str: str) -> datetime.datetime:
    """Parse ISO timestamp string into UTC datetime."""
    clean = ts_str.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return _as_utc(dt).isoformat()


def generate_hourly_buckets(as_of: datetime.datetime) -> List[Interval]:
    """
    Generate 24 half-open hourly buckets tiling [as_of - 24h, as_of).

    bucket i = [as_of-24h + i*1h, as_of-24h + (i+1)*1h)
    window_start = as_of - 24h, window_end = as_of exactly.
    Non-hour as_of is kept exact: no rounding and no future seconds.
    """
    as_of = _as_utc(as_of)
    window_start = as_of - datetime.timedelta(hours=24)
    buckets: List[Interval] = []
    for i in range(24):
        b_start = window_start + datetime.timedelta(hours=i)
        b_end = window_start + datetime.timedelta(hours=i + 1)
        buckets.append((b_start, b_end))
    return buckets


def union_intervals(intervals: Iterable[Interval]) -> List[Interval]:
    """Merge overlapping/adjacent intervals; duration is wall-clock coverage."""
    ordered = sorted((s, e) for s, e in intervals if e > s)
    if not ordered:
        return []
    merged: List[List[datetime.datetime]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        last = merged[-1]
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def union_seconds(intervals: Iterable[Interval]) -> float:
    return sum((e - s).total_seconds() for s, e in union_intervals(intervals))


def _parse_optional_ts(value: Any) -> Tuple[Optional[datetime.datetime], bool]:
    """
    Returns (dt, invalid).
    invalid=True means the value was present but unparseable.
    """
    if value is None:
        return None, False
    if isinstance(value, datetime.datetime):
        return _as_utc(value), False
    if not isinstance(value, str):
        return None, True
    text = value.strip()
    if text == "":
        return None, False
    try:
        return parse_iso_timestamp(text), False
    except Exception:
        return None, True


def _empty_bucket(b_start: datetime.datetime, b_end: datetime.datetime, unknown: bool) -> Dict[str, Any]:
    return {
        "bucket_start": _iso(b_start),
        "bucket_end": _iso(b_end),
        "active_agents": None if unknown else 0,
        "agent_hours": None if unknown else 0.0,
    }


def _serialize_project(
    *,
    buckets: List[Interval],
    unknown: bool,
    unknown_ended: int,
    invalid_spans: int,
    agent_intervals: Dict[str, List[Interval]],
    unattributed_intervals: List[Interval],
) -> Dict[str, Any]:
    window_seconds = 24.0 * 3600.0
    if unknown:
        return {
            "unique_agents": None,
            "total_agent_hours": None,
            "coverage": None,
            "unattributed_agent_hours": None,
            "unknown_ended": unknown_ended,
            "invalid_spans": invalid_spans,
            "unknown": True,
            "hourly_buckets": [_empty_bucket(s, e, True) for s, e in buckets],
        }

    per_agent_seconds = {
        agent: union_seconds(ivals) for agent, ivals in agent_intervals.items()
    }
    attributed_agents = [a for a, sec in per_agent_seconds.items() if sec > 0]
    total_agent_hours = sum(per_agent_seconds[a] for a in attributed_agents) / 3600.0
    unattributed_hours = union_seconds(unattributed_intervals) / 3600.0

    coverage_intervals: List[Interval] = []
    for ivals in agent_intervals.values():
        coverage_intervals.extend(ivals)
    coverage_intervals.extend(unattributed_intervals)
    covered = union_seconds(coverage_intervals)
    coverage = covered / window_seconds if window_seconds else 0.0

    hourly_buckets = []
    for b_start, b_end in buckets:
        active = 0
        hours = 0.0
        for agent, ivals in agent_intervals.items():
            clipped: List[Interval] = []
            for s, e in ivals:
                overlap_start = max(s, b_start)
                overlap_end = min(e, b_end)
                if overlap_end > overlap_start:
                    clipped.append((overlap_start, overlap_end))
            bucket_seconds = union_seconds(clipped)
            if bucket_seconds > 0:
                active += 1
                hours += bucket_seconds / 3600.0
        hourly_buckets.append({
            "bucket_start": _iso(b_start),
            "bucket_end": _iso(b_end),
            "active_agents": active,
            "agent_hours": round(hours, 4),
        })

    return {
        "unique_agents": len(attributed_agents),
        "total_agent_hours": round(total_agent_hours, 4),
        "coverage": round(coverage, 6),
        "unattributed_agent_hours": round(unattributed_hours, 4),
        "unknown_ended": unknown_ended,
        "invalid_spans": invalid_spans,
        "unknown": False,
        "hourly_buckets": hourly_buckets,
    }


def compute_hourly_utilization(
    agent_spans: Optional[List[Dict[str, Any]]],
    as_of: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """
    Compute 24-hour utilization across the three canonical projects.

    Missing/empty input surfaces as coverage=null and unknown=true (not zeros).
    Non-canonical project ids fold into the visible "unattributed" state.
    """
    if as_of is None:
        as_of = datetime.datetime.now(datetime.timezone.utc)
    else:
        as_of = _as_utc(as_of)

    buckets = generate_hourly_buckets(as_of)
    window_start = as_of - datetime.timedelta(hours=24)
    window_end = as_of

    missing_input = agent_spans is None or len(agent_spans) == 0

    states: Dict[str, Dict[str, Any]] = {}
    for proj in CANONICAL_PROJECT_IDS:
        states[proj] = {
            "saw_span": False,
            "unknown_ended": 0,
            "invalid_spans": 0,
            "agent_intervals": defaultdict(list),
            "unattributed_intervals": [],
        }

    def _state(proj: str) -> Dict[str, Any]:
        if proj not in states:
            states[proj] = {
                "saw_span": False,
                "unknown_ended": 0,
                "invalid_spans": 0,
                "agent_intervals": defaultdict(list),
                "unattributed_intervals": [],
            }
        return states[proj]

    if not missing_input:
        for span in agent_spans:
            if not isinstance(span, dict):
                continue
            proj = canonical_project_id(span.get("project_id") or span.get("team_id"))
            st = _state(proj)
            st["saw_span"] = True

            started_raw = span.get("started_at")
            start_dt, start_invalid = _parse_optional_ts(started_raw)
            if start_dt is None:
                st["invalid_spans"] += 1
                continue

            end_raw = span.get("ended_at")
            if end_raw is None and "completed_at" in span:
                end_raw = span.get("completed_at")
            end_dt, end_invalid = _parse_optional_ts(end_raw)
            if end_invalid:
                st["unknown_ended"] += 1
                continue
            if end_dt is None:
                end_dt = as_of

            if end_dt < start_dt:
                st["invalid_spans"] += 1
                continue

            if end_dt > as_of:
                end_dt = as_of
            if end_dt <= start_dt:
                # Entirely in the future after clamp: outside the window.
                continue
            if end_dt <= window_start or start_dt >= window_end:
                continue

            clip_start = max(start_dt, window_start)
            clip_end = min(end_dt, window_end)
            if clip_end <= clip_start:
                continue

            agent_id = span.get("agent_id") or span.get("session_id")
            if isinstance(agent_id, str):
                agent_id = agent_id.strip() or None
            if not agent_id:
                st["unattributed_intervals"].append((clip_start, clip_end))
                continue
            st["agent_intervals"][str(agent_id)].append((clip_start, clip_end))

    projects: Dict[str, Any] = {}
    agent_to_projects: Dict[str, set] = defaultdict(set)
    emit_ids = list(CANONICAL_PROJECT_IDS)
    if UNATTRIBUTED_PROJECT_ID in states and states[UNATTRIBUTED_PROJECT_ID]["saw_span"]:
        emit_ids.append(UNATTRIBUTED_PROJECT_ID)

    for proj in emit_ids:
        st = states[proj]
        unknown = missing_input or not st["saw_span"]
        payload = _serialize_project(
            buckets=buckets,
            unknown=unknown,
            unknown_ended=st["unknown_ended"],
            invalid_spans=st["invalid_spans"],
            agent_intervals=st["agent_intervals"],
            unattributed_intervals=st["unattributed_intervals"],
        )
        projects[proj] = payload
        if not unknown:
            for agent in st["agent_intervals"]:
                agent_to_projects[agent].add(proj)

    shared_agent_ids = sorted(
        agent for agent, projs in agent_to_projects.items() if len(projs) > 1
    )

    return {
        "as_of": _iso(as_of),
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "unknown": bool(missing_input),
        "shared_agent_ids": shared_agent_ids,
        "projects": projects,
    }


def load_agent_spans(path: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """
    Load agent spans from JSON list or JSONL.
    Returns None when the file is missing (unknown input), [] when present but empty.
    """
    if not path:
        return None
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read().strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict) and isinstance(data.get("spans"), list):
            return [row for row in data["spans"] if isinstance(row, dict)]
    except json.JSONDecodeError:
        records: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append(row)
        return records
    return []


def validate_registry_shape(data: Any) -> Tuple[bool, str]:
    """TEAM-REGISTRY.json is a dict with teams/agents lists. teams[].agents, not members."""
    if not isinstance(data, dict):
        return False, "registry root is not an object"
    teams = data.get("teams")
    if teams is not None and not isinstance(teams, list):
        return False, "registry.teams is not a list"
    agents = data.get("agents")
    if agents is not None and not isinstance(agents, list):
        return False, "registry.agents is not a list"
    if isinstance(teams, list):
        for team in teams:
            if not isinstance(team, dict):
                return False, "registry.teams entry is not an object"
            if "members" in team and "agents" not in team:
                return False, "registry.teams uses members; expected agents list"
            if "agents" in team and not isinstance(team.get("agents"), list):
                return False, "registry.teams[].agents is not a list"
    return True, "ok"


def load_spans_from_registry(path: Optional[str] = None) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """
    Load static registration records from TEAM-REGISTRY.json.
    These are registration metadata, not observed live history.
    Returns (records_or_none, error_reason).
    """
    if not path:
        return None, "registry path not set"
    if not os.path.exists(path):
        return None, "registry file missing"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            reg_data = json.load(handle)
    except Exception:
        return None, "registry unreadable"
    ok, reason = validate_registry_shape(reg_data)
    if not ok:
        return None, reason
    return [], None


def load_spans_from_observation(
    metrics_dir: Optional[str],
) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, str]]]:
    """
    Build agent spans from collector observation-state + latest.json sessions.
    This is observed history, not TEAM-REGISTRY registration.
    """
    gaps: List[Dict[str, str]] = []
    if not metrics_dir:
        gaps.append({"source": "metrics", "reason": "metrics directory not set"})
        return None, gaps
    if not os.path.isdir(metrics_dir):
        gaps.append({"source": "metrics", "reason": "metrics directory missing or unreadable"})
        return None, gaps

    obs_path = os.path.join(metrics_dir, "observation-state.json")
    latest_path = os.path.join(metrics_dir, "latest.json")
    agents: Dict[str, Any] = {}
    last_unix = None
    if os.path.exists(obs_path):
        try:
            with open(obs_path, "r", encoding="utf-8") as handle:
                obs = json.load(handle)
            if isinstance(obs, dict):
                raw_agents = obs.get("agents") or {}
                if isinstance(raw_agents, dict):
                    agents = raw_agents
                else:
                    gaps.append({"source": "observation-state.json", "reason": "agents is not an object"})
                last_unix = obs.get("last_unix")
            else:
                gaps.append({"source": "observation-state.json", "reason": "root is not an object"})
        except Exception:
            gaps.append({"source": "observation-state.json", "reason": "unreadable"})
    else:
        gaps.append({"source": "observation-state.json", "reason": "missing"})

    live_ids = set()
    snapshot_at = None
    if os.path.exists(latest_path):
        try:
            with open(latest_path, "r", encoding="utf-8") as handle:
                latest = json.load(handle)
            if isinstance(latest, dict):
                snapshot_at = latest.get("at")
                sessions = latest.get("sessions") or []
                if isinstance(sessions, list):
                    for session in sessions:
                        if not isinstance(session, dict):
                            continue
                        sid = session.get("id")
                        if sid and session.get("pid_live") and session.get("counted_as_agent", True):
                            live_ids.add(sid)
                else:
                    gaps.append({"source": "latest.json", "reason": "sessions is not a list"})
            else:
                gaps.append({"source": "latest.json", "reason": "root is not an object"})
        except Exception:
            gaps.append({"source": "latest.json", "reason": "unreadable"})
    else:
        gaps.append({"source": "latest.json", "reason": "missing"})

    if not agents:
        if gaps:
            return None, gaps
        return [], gaps

    last_observed_iso = None
    if snapshot_at:
        last_observed_iso = snapshot_at
    elif isinstance(last_unix, (int, float)):
        last_observed_iso = _iso(datetime.datetime.fromtimestamp(last_unix, tz=datetime.timezone.utc))

    spans: List[Dict[str, Any]] = []
    for agent_id, rec in agents.items():
        if not isinstance(rec, dict):
            continue
        started = rec.get("first_seen_at")
        team_id = rec.get("team_id")
        ended: Optional[str]
        if agent_id in live_ids:
            ended = None
        else:
            live_seconds = rec.get("pid_live_seconds")
            if isinstance(live_seconds, (int, float)) and live_seconds > 0 and started:
                try:
                    start_dt = parse_iso_timestamp(started) if isinstance(started, str) else None
                except Exception:
                    start_dt = None
                if start_dt is not None:
                    ended = _iso(start_dt + datetime.timedelta(seconds=float(live_seconds)))
                else:
                    ended = last_observed_iso
            else:
                ended = last_observed_iso
        spans.append({
            "project_id": rec.get("project_id") or team_id,
            "agent_id": agent_id,
            "started_at": started,
            "ended_at": ended,
        })
    return spans, gaps
