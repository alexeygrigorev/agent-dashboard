"""
Unit tests for 24-hour hourly utilization engine.
"""

import datetime
import os
import unittest

from dashboard import canonical_project_id
from dashboard.hourly import (
    compute_hourly_utilization,
    generate_hourly_buckets,
    parse_iso_timestamp,
    union_seconds,
    validate_registry_shape,
)

UTC = datetime.timezone.utc


class TestHourlyUtilization(unittest.TestCase):
    def test_bucket_generation(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        buckets = generate_hourly_buckets(as_of)
        self.assertEqual(len(buckets), 24)
        self.assertEqual(buckets[0][0], datetime.datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC))
        self.assertEqual(buckets[-1][1], datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC))
        for b_start, b_end in buckets:
            self.assertEqual((b_end - b_start).total_seconds(), 3600)

    def test_non_hour_as_of_exact_window(self):
        as_of = datetime.datetime(2026, 10, 4, 13, 17, 43, tzinfo=UTC)
        buckets = generate_hourly_buckets(as_of)
        self.assertEqual(len(buckets), 24)
        window_start = as_of - datetime.timedelta(hours=24)
        self.assertEqual(buckets[0][0], window_start)
        self.assertEqual(buckets[-1][1], as_of)
        for _start, end in buckets:
            self.assertLessEqual(end, as_of)
        res = compute_hourly_utilization([], as_of=as_of)
        self.assertEqual(parse_iso_timestamp(res["window_start"]), window_start)
        self.assertEqual(parse_iso_timestamp(res["window_end"]), as_of)
        self.assertTrue(res["unknown"])
        self.assertIsNone(res["projects"]["agent-branches"]["coverage"])
        self.assertIsNone(res["projects"]["agent-branches"]["total_agent_hours"])

    def test_single_agent_clipping(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
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
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
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
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
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

    def test_duplicate_identical_spans_do_not_change_hours(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        span = {
            "project_id": "agent-branches",
            "agent_id": "dup-1",
            "started_at": "2026-10-04T10:00:00Z",
            "ended_at": "2026-10-04T11:30:00Z",
        }
        once = compute_hourly_utilization([span], as_of=as_of)["projects"]["agent-branches"]
        twice = compute_hourly_utilization([span, dict(span)], as_of=as_of)["projects"]["agent-branches"]
        self.assertEqual(once["unique_agents"], twice["unique_agents"])
        self.assertAlmostEqual(once["total_agent_hours"], twice["total_agent_hours"], places=4)
        self.assertAlmostEqual(once["total_agent_hours"], 1.5, places=3)

    def test_overlapping_spans_union_once(self):
        as_of = datetime.datetime(2026, 10, 4, 14, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-dashboard",
                "agent_id": "overlap-1",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T12:00:00Z",
            },
            {
                "project_id": "agent-dashboard",
                "agent_id": "overlap-1",
                "started_at": "2026-10-04T11:00:00Z",
                "ended_at": "2026-10-04T13:00:00Z",
            },
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)["projects"]["agent-dashboard"]
        self.assertEqual(res["unique_agents"], 1)
        self.assertAlmostEqual(res["total_agent_hours"], 3.0, places=3)

    def test_future_ended_at_clamped_to_as_of(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "quota-launcher",
                "agent_id": "future-1",
                "started_at": "2026-10-04T11:00:00Z",
                "ended_at": "2026-10-04T15:00:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)["projects"]["quota-launcher"]
        self.assertAlmostEqual(res["total_agent_hours"], 1.0, places=3)

    def test_invalid_ended_at_unknown_ended_not_alive(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-branches",
                "agent_id": "bad-end",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "not-a-timestamp",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)["projects"]["agent-branches"]
        self.assertEqual(res["unknown_ended"], 1)
        self.assertEqual(res["total_agent_hours"], 0.0)
        self.assertEqual(res["unique_agents"], 0)

    def test_ended_before_started_invalid_spans(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-dashboard",
                "agent_id": "inverted",
                "started_at": "2026-10-04T11:00:00Z",
                "ended_at": "2026-10-04T10:00:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)["projects"]["agent-dashboard"]
        self.assertEqual(res["invalid_spans"], 1)
        self.assertEqual(res["total_agent_hours"], 0.0)

    def test_span_entirely_outside_window(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "quota-launcher",
                "agent_id": "old",
                "started_at": "2026-10-02T08:00:00Z",
                "ended_at": "2026-10-02T10:00:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)["projects"]["quota-launcher"]
        self.assertEqual(res["total_agent_hours"], 0.0)
        self.assertEqual(res["unique_agents"], 0)
        self.assertFalse(res["unknown"])

    def test_agent_across_three_adjacent_buckets(self):
        as_of = datetime.datetime(2026, 10, 4, 14, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-branches",
                "agent_id": "cross-3",
                "started_at": "2026-10-04T10:30:00Z",
                "ended_at": "2026-10-04T12:45:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        ab = res["projects"]["agent-branches"]
        self.assertAlmostEqual(ab["total_agent_hours"], 2.25, places=3)
        buckets = ab["hourly_buckets"]
        by_start = {b["bucket_start"]: b for b in buckets}
        self.assertAlmostEqual(by_start["2026-10-04T10:00:00+00:00"]["agent_hours"], 0.5, places=3)
        self.assertAlmostEqual(by_start["2026-10-04T11:00:00+00:00"]["agent_hours"], 1.0, places=3)
        self.assertAlmostEqual(by_start["2026-10-04T12:00:00+00:00"]["agent_hours"], 0.75, places=3)
        self.assertEqual(by_start["2026-10-04T10:00:00+00:00"]["active_agents"], 1)
        self.assertEqual(by_start["2026-10-04T11:00:00+00:00"]["active_agents"], 1)
        self.assertEqual(by_start["2026-10-04T12:00:00+00:00"]["active_agents"], 1)

    def test_missing_spans_unknown_not_zeros(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        res = compute_hourly_utilization(None, as_of=as_of)
        self.assertTrue(res["unknown"])
        for proj in ("agent-branches", "agent-dashboard", "quota-launcher", "agent-coordination"):
            self.assertIsNone(res["projects"][proj]["coverage"])
            self.assertIsNone(res["projects"][proj]["total_agent_hours"])
            self.assertTrue(res["projects"][proj]["unknown"])

    def test_noncanonical_project_goes_to_unattributed(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "some-other-team",
                "agent_id": "x",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        self.assertNotIn("some-other-team", res["projects"])
        self.assertIn("unattributed", res["projects"])
        self.assertAlmostEqual(res["projects"]["unattributed"]["total_agent_hours"], 1.0, places=3)

    def test_shared_agent_ids_non_additive(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-branches",
                "agent_id": "shared-1",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            },
            {
                "project_id": "agent-dashboard",
                "agent_id": "shared-1",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            },
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        self.assertEqual(res["shared_agent_ids"], ["shared-1"])
        self.assertEqual(res["projects"]["agent-branches"]["unique_agents"], 1)
        self.assertEqual(res["projects"]["agent-dashboard"]["unique_agents"], 1)

    def test_missing_agent_id_unattributed_hours(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-branches",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            }
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)["projects"]["agent-branches"]
        self.assertAlmostEqual(res["unattributed_agent_hours"], 1.0, places=3)
        self.assertEqual(res["unique_agents"], 0)
        self.assertEqual(res["total_agent_hours"], 0.0)

    def test_union_seconds_helper(self):
        a = datetime.datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
        b = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        c = datetime.datetime(2026, 10, 4, 11, 0, tzinfo=UTC)
        d = datetime.datetime(2026, 10, 4, 13, 0, tzinfo=UTC)
        self.assertEqual(union_seconds([(a, b), (c, d)]), 3 * 3600)

    def test_registry_shape_rejects_members(self):
        ok, reason = validate_registry_shape({"teams": [{"id": "t", "members": []}]})
        self.assertFalse(ok)
        self.assertIn("members", reason)
        ok, reason = validate_registry_shape({"teams": [{"id": "t", "agents": []}], "agents": []})
        self.assertTrue(ok)

    def test_canonical_project_id_aliases_and_fourth_product(self):
        self.assertEqual(canonical_project_id("agent-quota-launcher"), "quota-launcher")
        self.assertEqual(canonical_project_id("agent_quota_launcher"), "quota-launcher")
        self.assertEqual(canonical_project_id("agent-coordination"), "agent-coordination")
        self.assertEqual(canonical_project_id("agent_coordination"), "agent-coordination")
        self.assertEqual(canonical_project_id("agent-branches"), "agent-branches")
        self.assertEqual(canonical_project_id("agent_branches"), "agent-branches")
        self.assertEqual(canonical_project_id("agent-dashboard"), "agent-dashboard")
        self.assertEqual(canonical_project_id("agent_dashboard"), "agent-dashboard")
        self.assertEqual(canonical_project_id("quota-launcher"), "quota-launcher")
        self.assertEqual(canonical_project_id("unknown-lane"), "unattributed")
        self.assertEqual(canonical_project_id(None), "unattributed")
        self.assertEqual(canonical_project_id(""), "unattributed")

    def test_hourly_utilization_aliases_and_fourth_product(self):
        as_of = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        spans = [
            {
                "project_id": "agent-quota-launcher",
                "agent_id": "ql-agent-1",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            },
            {
                "project_id": "agent-coordination",
                "agent_id": "coord-agent-1",
                "started_at": "2026-10-04T10:00:00Z",
                "ended_at": "2026-10-04T11:00:00Z",
            },
        ]
        res = compute_hourly_utilization(spans, as_of=as_of)
        self.assertIn("quota-launcher", res["projects"])
        self.assertNotIn("agent-quota-launcher", res["projects"])
        self.assertEqual(res["projects"]["quota-launcher"]["unique_agents"], 1)
        self.assertAlmostEqual(res["projects"]["quota-launcher"]["total_agent_hours"], 1.0, places=3)
        self.assertIn("agent-coordination", res["projects"])
        self.assertEqual(res["projects"]["agent-coordination"]["unique_agents"], 1)
        self.assertAlmostEqual(res["projects"]["agent-coordination"]["total_agent_hours"], 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
