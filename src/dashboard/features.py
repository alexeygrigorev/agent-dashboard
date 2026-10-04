"""
Completed Features Engine.
Tracks and joins accepted task evidence to project feature completions
without counting repeated reviews or commits as duplicate features.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Any, Optional


def load_accepted_features(tasks_json_path: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract verified accepted features from coordination/TASKS.json.
    Groups by project_id.
    Deduplicates by feature_id or task_id.
    """
    if not os.path.exists(tasks_json_path):
        return {}

    with open(tasks_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tasks = data.get("tasks", [])
    projects: Dict[str, List[Dict[str, Any]]] = {}
    seen_feature_ids = set()

    for t in tasks:
        # Check if task is marked done or accepted
        status = t.get("status")
        acceptance_status = t.get("acceptance_status") or ""
        accepted_at = t.get("accepted_at") or t.get("updated_at")

        # Must have accepted or done status
        if status != "done" and not (status == "accepted" or "ACCEPT" in acceptance_status):
            continue

        proj = t.get("project_id") or t.get("team_id") or "unattributed"
        task_id = t.get("id")
        feature_id = t.get("feature_id") or task_id

        if not feature_id:
            continue

        if feature_id in seen_feature_ids:
            continue
        seen_feature_ids.add(feature_id)

        feat_entry = {
            "feature_id": feature_id,
            "task_id": task_id,
            "project_id": proj,
            "owner_tag": t.get("owner_tag"),
            "accepted_at": accepted_at,
            "commit": t.get("commit"),
            "pr_url": t.get("pr_url"),
            "tests": t.get("tests"),
            "reviewer": t.get("reviewer"),
            "acceptance": t.get("acceptance"),
            "acceptance_status": acceptance_status,
        }

        if proj not in projects:
            projects[proj] = []
        projects[proj].append(feat_entry)

    return projects
