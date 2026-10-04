"""
Unit tests for 24-hour hourly utilization engine.
"""

import unittest
import datetime
from dashboard.hourly import (
    parse_iso_timestamp,
    generate_hourly_buckets,
    compute_hourly_utilization,
)


class TestHourlyUtilization(unittest.TestCase):
    def test_bucket_generation(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
        buckets = generate_hourly_buckets(as_of)
        self.assertEqual(len(buckets), 24)
        # Check first and last boundaries
        self.assertEqual(buckets[0][0], datetime.datetime(2026, 10, 3, 12, 0, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(buckets[-1][1], datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=datetime.timezone.utc))
        for b_start, b_end in buckets:
            self.assertEqual((b_end - b_start).total_seconds(), 3600)

    def test_single_agent_clipping(self):
        # Agent running for 3 hours across 3 buckets
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
        spans = [
            {
                "project_id": "agent-branches",
                "agent_id": "worker-1",
                "started_at": "2026-10-04T09:00:00Z",
                "ended_at": "2026-10-04T12:00:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        ab_data = res["projects"]["agent-branches"]
        self.assertEqual(ab_data["unique_agents"], 1)
        self.assertAlmostEqual(ab_data["total_agent_hours"], 3.0, places=3)

    def test_partial_hour_calculation(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # 30-minute span in a single bucket
        spans = [
            {
                "project_id": "agent-dashboard",
                "agent_id": "dash-worker",
                "started_at": "2026-10-04T10:15:00Z",
                "ended_at": "2026-10-04T10:45:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        d_data = res["projects"]["agent-dashboard"]
        self.assertEqual(d_data["unique_agents"], 1)
        self.assertAlmostEqual(d_data["total_agent_hours"], 0.5, places=3)

    def test_identity_deduplication(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # Same agent with two distinct task intervals
        spans = [
            {
                "project_id": "quota-launcher",
                "agent_id": "launcher-1",
                "started_at": "2026-10-04T08:00:00Z",
                "ended_at": "2026-10-04T09:00:00Z",
            },
            {
                "project_id": "quota-launcher",
                "agent_id": "launcher-1",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            },
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        ql_data = res["projects"]["quota-launcher"]
        self.assertEqual(ql_data["unique_agents"], 1)
        self.assertAlmostEqual(ql_data["total_agent_hours"], 2.0, places=3)


if __name__ == "__main__":
    unittest.main()
