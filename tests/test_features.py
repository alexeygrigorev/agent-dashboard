"""
Unit tests for completed features tracking.
"""

import datetime
import json
import os
import unittest

from dashboard.features import load_accepted_features

UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
SCRATCH = os.environ.get("TMPDIR", "/home/alexey/git/agent-dashboard/.local/tmp")


def _write_tasks(payload):
    os.makedirs(SCRATCH, exist_ok=True)
    path = os.path.join(SCRATCH, "features-tasks-test.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


class TestFeaturesTracking(unittest.TestCase):
    def test_feature_extraction_and_deduplication(self):
        sample_tasks = {
            "tasks": [
                {
                    "id": "task-feat-1",
                    "feature_id": "feat-001",
                    "project_id": "agent-branches",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-04T10:00:00Z",
                    "commit": "abc1234",
                    "tests": ["tests/test_hourly.py"],
                },
                {
                    "id": "task-feat-1-review",
                    "feature_id": "feat-001",
                    "project_id": "agent-branches",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-04T10:05:00Z",
                    "commit": "def5678",
                    "tests": ["tests/test_hourly.py"],
                },
                {
                    "id": "task-feat-2",
                    "feature_id": "feat-002",
                    "project_id": "agent-branches",
                    "status": "in_progress",
                },
                {
                    "id": "task-dash-1",
                    "feature_id": "feat-dash-001",
                    "project_id": "agent-dashboard",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-04T11:00:00Z",
                    "commit": "dashc0de",
                    "tests": {"path": "tests/test_server.py"},
                },
            ]
        }
        path = _write_tasks(sample_tasks)
        feats = load_accepted_features(path, as_of=AS_OF)
        ab_feats = feats["projects"]["agent-branches"]
        self.assertEqual(len(ab_feats), 1)
        self.assertEqual(ab_feats[0]["feature_id"], "feat-001")
        d_feats = feats["projects"]["agent-dashboard"]
        self.assertEqual(len(d_feats), 1)
        self.assertEqual(d_feats[0]["feature_id"], "feat-dash-001")

    def test_unaccepted_substring_not_counted(self):
        path = _write_tasks({
            "tasks": [
                {
                    "id": "t-un",
                    "feature_id": "feat-un",
                    "project_id": "agent-branches",
                    "status": "UNACCEPTED",
                    "acceptance_status": "NOT_ACCEPTED",
                    "accepted_at": "2026-10-04T10:00:00Z",
                    "commit": "abc",
                    "tests": ["t"],
                },
                {
                    "id": "t-done",
                    "feature_id": "feat-done",
                    "project_id": "agent-branches",
                    "status": "done",
                    "acceptance_status": "ACCEPT: verified live",
                    "accepted_at": "2026-10-04T10:00:00Z",
                    "commit": "abc",
                    "tests": ["t"],
                },
            ]
        })
        feats = load_accepted_features(path, as_of=AS_OF)
        self.assertEqual(feats["projects"], {})

    def test_updated_at_is_not_accepted_at(self):
        path = _write_tasks({
            "tasks": [
                {
                    "id": "t-upd",
                    "feature_id": "feat-upd",
                    "project_id": "agent-dashboard",
                    "status": "ACCEPTED",
                    "updated_at": "2026-10-04T10:00:00Z",
                    "commit": "abc",
                    "tests": ["t"],
                }
            ]
        })
        feats = load_accepted_features(path, as_of=AS_OF)
        self.assertEqual(feats["projects"], {})

    def test_missing_commit_or_tests_rejected(self):
        path = _write_tasks({
            "tasks": [
                {
                    "id": "t-nocommit",
                    "feature_id": "feat-nc",
                    "project_id": "quota-launcher",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-04T10:00:00Z",
                    "tests": ["t"],
                },
                {
                    "id": "t-notests",
                    "feature_id": "feat-nt",
                    "project_id": "quota-launcher",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-04T10:00:00Z",
                    "commit": "abc",
                    "tests": [],
                },
            ]
        })
        feats = load_accepted_features(path, as_of=AS_OF)
        self.assertEqual(feats["projects"], {})

    def test_24h_completed_feature_filter(self):
        path = _write_tasks({
            "tasks": [
                {
                    "id": "t-old",
                    "feature_id": "feat-old",
                    "project_id": "agent-branches",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-02T10:00:00Z",
                    "commit": "old",
                    "tests": ["t"],
                },
                {
                    "id": "t-new",
                    "feature_id": "feat-new",
                    "project_id": "agent-branches",
                    "status": "ACCEPTED",
                    "accepted_at": "2026-10-04T11:00:00Z",
                    "pr_url": "https://example.invalid/pr/1",
                    "tests": "tests/test_features.py",
                },
            ]
        })
        feats = load_accepted_features(path, as_of=AS_OF)
        items = feats["projects"]["agent-branches"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["feature_id"], "feat-new")

    def test_missing_tasks_file_unknown(self):
        feats = load_accepted_features(os.path.join(SCRATCH, "no-such-tasks.json"), as_of=AS_OF)
        self.assertTrue(feats["unknown"])
        self.assertEqual(feats["projects"], {})
        self.assertTrue(feats["coverage_gaps"])


if __name__ == "__main__":
    unittest.main()
