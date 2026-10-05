"""
Completed Features Engine.
Tracks and joins accepted task evidence to project feature completions
without counting repeated reviews or commits as duplicate features.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, List, Optional

from dashboard import canonical_project_id
from dashboard.hourly import parse_iso_timestamp, _as_utc, _iso

ACCEPTED_STATUS = "ACCEPTED"


def _exact_accepted(value: Any) -> bool:
    """Exact ACCEPTED match. Substrings such as UNACCEPTED/NOT_ACCEPTED/ACCEPT: do not count."""
    if not isinstance(value, str):
        return False
    return value.strip().upper() == ACCEPTED_STATUS


def _has_artifact(task: Dict[str, Any]) -> bool:
    commit = task.get("commit") or task.get("commit_sha") or task.get("sha")
    pr = task.get("pr_url") or task.get("pr") or task.get("pull_request")
    if isinstance(commit, str):
        commit = commit.strip()
    if isinstance(pr, str):
        pr = pr.strip()
    return bool(commit) or bool(pr)


def _has_tests(task: Dict[str, Any]) -> bool:
    tests = task.get("tests")
    if tests is None or tests is False:
        return False
    if isinstance(tests, str):
        return bool(tests.strip())
    if isinstance(tests, (list, tuple, dict)):
        return len(tests) > 0
    return bool(tests)


def _parse_accepted_at(task: Dict[str, Any]) -> Optional[datetime.datetime]:
    """accepted_at only. updated_at is not acceptance evidence."""
    raw = task.get("accepted_at")
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime.datetime):
        return _as_utc(raw)
    if not isinstance(raw, str):
        return None
    try:
        return parse_iso_timestamp(raw.strip())
    except Exception:
        return None


def load_accepted_features(
    tasks_json_path: str,
    as_of: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """
    Extract verified accepted features from coordination/TASKS.json.
    Groups by project_id. Deduplicates by feature_id or task_id.

    A feature requires:
      - task id
      - exact status or acceptance_status ACCEPTED
      - non-null parseable accepted_at (updated_at is ignored)
      - commit or PR
      - tests
    When as_of is provided, only accepted_at in [as_of-24h, as_of) is kept.
    """
    if as_of is not None:
        as_of = _as_utc(as_of)
        window_start = as_of - datetime.timedelta(hours=24)
        window_end = as_of
    else:
        window_start = None
        window_end = None

    coverage_gaps: List[Dict[str, str]] = []
    if not tasks_json_path or not os.path.exists(tasks_json_path):
        coverage_gaps.append({"source": "tasks", "reason": "TASKS.json missing or unreadable"})
        return {
            "unknown": True,
            "coverage_gaps": coverage_gaps,
            "as_of": _iso(as_of) if as_of else None,
            "window_start": _iso(window_start) if window_start else None,
            "window_end": _iso(window_end) if window_end else None,
            "projects": {},
        }

    try:
        with open(tasks_json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        coverage_gaps.append({"source": "tasks", "reason": "TASKS.json unreadable or invalid JSON"})
        return {
            "unknown": True,
            "coverage_gaps": coverage_gaps,
            "as_of": _iso(as_of) if as_of else None,
            "window_start": _iso(window_start) if window_start else None,
            "window_end": _iso(window_end) if window_end else None,
            "projects": {},
        }

    if not isinstance(data, dict):
        coverage_gaps.append({"source": "tasks", "reason": "TASKS.json root is not an object"})
        return {
            "unknown": True,
            "coverage_gaps": coverage_gaps,
            "as_of": _iso(as_of) if as_of else None,
            "window_start": _iso(window_start) if window_start else None,
            "window_end": _iso(window_end) if window_end else None,
            "projects": {},
        }

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        coverage_gaps.append({"source": "tasks", "reason": "tasks field is not a list"})
        return {
            "unknown": True,
            "coverage_gaps": coverage_gaps,
            "as_of": _iso(as_of) if as_of else None,
            "window_start": _iso(window_start) if window_start else None,
            "window_end": _iso(window_end) if window_end else None,
            "projects": {},
        }

    projects: Dict[str, List[Dict[str, Any]]] = {}
    seen_feature_ids = set()
    rejected = 0

    for task in tasks:
        if not isinstance(task, dict):
            rejected += 1
            continue
        if not (_exact_accepted(task.get("status")) or _exact_accepted(task.get("acceptance_status"))):
            continue

        task_id = task.get("id")
        if not task_id:
            rejected += 1
            continue

        accepted_at = _parse_accepted_at(task)
        if accepted_at is None:
            rejected += 1
            continue

        if window_start is not None and window_end is not None:
            if accepted_at < window_start or accepted_at >= window_end:
                continue

        if not _has_artifact(task):
            rejected += 1
            continue
        if not _has_tests(task):
            rejected += 1
            continue

        proj = canonical_project_id(task.get("project_id"))
        feature_id = task.get("feature_id") or task_id
        if feature_id in seen_feature_ids:
            continue
        seen_feature_ids.add(feature_id)

        feat_entry = {
            "feature_id": feature_id,
            "task_id": task_id,
            "project_id": proj,
            "owner_tag": task.get("owner_tag"),
            "accepted_at": _iso(accepted_at),
            "commit": task.get("commit") or task.get("commit_sha") or task.get("sha"),
            "pr_url": task.get("pr_url") or task.get("pr") or task.get("pull_request"),
            "tests": task.get("tests"),
            "reviewer": task.get("reviewer") or task.get("reviewer_tag"),
            "acceptance": task.get("acceptance"),
            "acceptance_status": task.get("acceptance_status") or task.get("status"),
        }
        projects.setdefault(proj, []).append(feat_entry)

    if rejected:
        coverage_gaps.append({
            "source": "acceptance_evidence",
            "reason": f"{rejected} ACCEPTED-status task(s) lacked accepted_at/commit-or-PR/tests/task id",
        })

    return {
        "unknown": False,
        "coverage_gaps": coverage_gaps,
        "as_of": _iso(as_of) if as_of else None,
        "window_start": _iso(window_start) if window_start else None,
        "window_end": _iso(window_end) if window_end else None,
        "projects": projects,
    }
