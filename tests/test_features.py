"""
Unit tests for completed features tracking.
"""

import unittest
import tempfile
import json
import os
from dashboard.features import load_accepted_features


class TestFeaturesTracking(unittest.TestCase):
    def test_feature_extraction_and_deduplication(self):
        sample_tasks = {
            "tasks": [
                {
                    "id": "task-feat-1",
                    "feature_id": "feat-001",
                    "project_id": "agent-branches",
                    "status": "done",
                    "acceptance_status": "ACCEPT: verified live",
                    "commit": "abc1234",
                },
                # Duplicate entry for same feature (e.g. repeated review)
                {
                    "id": "task-feat-1-review",
                    "feature_id": "feat-001",
                    "project_id": "agent-branches",
                    "status": "done",
                    "acceptance_status": "ACCEPT: review passed",
                    "commit": "def5678",
                },
                # In progress task (must NOT be counted)
                {
                    "id": "task-feat-2",
                    "feature_id": "feat-002",
                    "project_id": "agent-branches",
                    "status": "in_progress",
                },
                # Distinct feature in another project
                {
                    "id": "task-dash-1",
                    "feature_id": "feat-dash-001",
                    "project_id": "agent-dashboard",
                    "status": "done",
                    "acceptance_status": "ACCEPT: dashboard live",
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(sample_tasks, f)
            temp_path = f.name

        try:
            feats = load_accepted_features(temp_path)
            ab_feats = feats["agent-branches"]
            self.assertEqual(len(ab_feats), 1)
            self.assertEqual(ab_feats[0]["feature_id"], "feat-001")

            d_feats = feats["agent-dashboard"]
            self.assertEqual(len(d_feats), 1)
            self.assertEqual(d_feats[0]["feature_id"], "feat-dash-001")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
