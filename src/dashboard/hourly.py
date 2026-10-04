"""
24-Hour Hourly Utilization Engine.
Computes 24 half-open UTC hourly buckets [as_of - 24h, as_of) per project.
Deduplicates identities and clips agent-hours to bucket boundaries.
"""

from __future__ import annotations

import datetime
from typing import Dict, List, Any, Optional


def parse_iso_timestamp(ts_str: str) -> datetime.datetime:
    """Parse ISO timestamp string into UTC datetime."""
    # Replace Z with +00:00 for fromisoformat compatibility
    clean = ts_str.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def generate_hourly_buckets(as_of: datetime.datetime) -> List[tuple[datetime.datetime, datetime.datetime]]:
    """
    Generate 24 half-open hourly buckets in UTC: [start, end)
    spanning [as_of - 24h, as_of).
    """
    # Truncate as_of to current hour boundary
    end_of_window = as_of.replace(minute=0, second=0, microsecond=0)
    if as_of > end_of_window:
        end_of_window += datetime.timedelta(hours=1)
    
    start_of_window = end_of_window - datetime.timedelta(hours=24)
    buckets = []
    for h in range(24):
        b_start = start_of_window + datetime.timedelta(hours=h)
        b_end = b_start + datetime.timedelta(hours=1)
        buckets.append((b_start, b_end))
    return buckets


def compute_hourly_utilization(
    agent_spans: List[Dict[str, Any]],
    as_of: Optional[datetime.datetime] = None
) -> Dict[str, Any]:
    """
    Compute 24-hour utilization across projects.
    
    agent_spans: list of dicts:
      - project_id: str ("agent-branches", "agent-dashboard", "quota-launcher")
      - agent_id: str (unique agent or session UUID)
      - started_at: str (ISO)
      - ended_at: Optional[str] (ISO, None if running)
      
    Returns dictionary with:
      - as_of: ISO str
      - window_start: ISO str
      - window_end: ISO str
      - projects: Dict[project_id, {
            "unique_agents": int,
            "total_agent_hours": float,
            "hourly_buckets": List[{
                "bucket_start": ISO str,
                "bucket_end": ISO str,
                "active_agents": int,
                "agent_hours": float
            }]
        }]
    """
    if as_of is None:
        as_of = datetime.datetime.now(datetime.timezone.utc)
    else:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=datetime.timezone.utc)
        else:
            as_of = as_of.astimezone(datetime.timezone.utc)

    buckets = generate_hourly_buckets(as_of)
    window_start = buckets[0][0]
    window_end = buckets[-1][1]

    # Pre-structure project maps
    projects: Dict[str, Dict[str, Any]] = {}

    for span in agent_spans:
        proj = span.get("project_id", "unknown")
        if proj not in projects:
            projects[proj] = {
                "agents": set(),
                "total_agent_hours": 0.0,
                "hourly": [
                    {
                        "bucket_start": b[0].isoformat(),
                        "bucket_end": b[1].isoformat(),
                        "active_agents": set(),
                        "agent_hours": 0.0,
                    }
                    for b in buckets
                ]
            }

        agent_id = span.get("agent_id") or span.get("session_id")
        if not agent_id:
            continue

        try:
            start_dt = parse_iso_timestamp(span["started_at"])
        except Exception:
            continue

        end_str = span.get("ended_at") or span.get("completed_at")
        if end_str:
            try:
                end_dt = parse_iso_timestamp(end_str)
            except Exception:
                end_dt = as_of
        else:
            end_dt = as_of

        # Guard against backwards timestamps
        if end_dt < start_dt:
            continue

        # Overlap with overall 24h window
        if end_dt <= window_start or start_dt >= window_end:
            continue

        projects[proj]["agents"].add(agent_id)

        # Distribute into the 24 hourly buckets
        for idx, (b_start, b_end) in enumerate(buckets):
            overlap_start = max(start_dt, b_start)
            overlap_end = min(end_dt, b_end)
            if overlap_end > overlap_start:
                duration_seconds = (overlap_end - overlap_start).total_seconds()
                hours = duration_seconds / 3600.0
                # Clip max per single agent in 1 hour bucket to 1.0
                clipped_hours = min(hours, 1.0)
                
                b_dict = projects[proj]["hourly"][idx]
                b_dict["active_agents"].add(agent_id)
                b_dict["agent_hours"] += clipped_hours
                projects[proj]["total_agent_hours"] += clipped_hours

    # Serialize sets to counts
    output_projects: Dict[str, Any] = {}
    for proj_id, p_data in projects.items():
        serialized_buckets = []
        for b_entry in p_data["hourly"]:
            serialized_buckets.append({
                "bucket_start": b_entry["bucket_start"],
                "bucket_end": b_entry["bucket_end"],
                "active_agents": len(b_entry["active_agents"]),
                "agent_hours": round(b_entry["agent_hours"], 4),
            })
        output_projects[proj_id] = {
            "unique_agents": len(p_data["agents"]),
            "total_agent_hours": round(p_data["total_agent_hours"], 4),
            "hourly_buckets": serialized_buckets,
        }

    return {
        "as_of": as_of.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "projects": output_projects,
    }
