"""
Unit tests for usage accounting engine.
"""

import datetime
import json
import os
import unittest

from dashboard import canonical_project_id
from dashboard.accounting import (
    aggregate_project_usage,
    load_opencode_usage,
    normalize_usage_record,
)

UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
IN_WINDOW = "2026-10-04T11:00:00Z"
SCRATCH = os.environ.get("TMPDIR", "/home/alexey/git/agent-dashboard/.local/tmp")


def _rec(**kwargs):
    base = {
        "project_id": "agent-branches",
        "provider": "gemini",
        "conversation_id": "conv-1",
        "timestamp": IN_WINDOW,
    }
    base.update(kwargs)
    return base


class TestUsageAccounting(unittest.TestCase):
    def test_nullability_and_types(self):
        raw = {
            "project_id": "agent-branches",
            "provider": "gemini",
            "conversation_id": "conv-1",
            "input_tokens": 100,
            "reasoning_tokens": 25,
            "quota_delta": "-1.5%",
            "timestamp": IN_WINDOW,
        }
        norm = normalize_usage_record(raw)
        self.assertEqual(norm["input_tokens"], 100)
        self.assertIsNone(norm["output_tokens"])
        self.assertEqual(norm["reasoning_tokens"], 25)
        self.assertIsNone(norm["quota_delta"])

        raw_float = {
            "project_id": "agent-branches",
            "provider": "gemini",
            "conversation_id": "conv-1",
            "quota_delta": -1.5,
            "timestamp": IN_WINDOW,
        }
        norm2 = normalize_usage_record(raw_float)
        self.assertEqual(norm2["quota_delta"], -1.5)

    def test_known_zero_preserved(self):
        norm = normalize_usage_record(_rec(input_tokens=0, output_tokens=0, cached_input_tokens=0, prompt_tokens=99))
        self.assertEqual(norm["input_tokens"], 0)
        self.assertEqual(norm["output_tokens"], 0)
        self.assertEqual(norm["cache_read_tokens"], 0)
        agg = aggregate_project_usage(
            [_rec(input_tokens=0, output_tokens=0, cached_input_tokens=0, response_id="r0")],
            as_of=AS_OF,
        )
        p = agg["projects"]["agent-branches"]
        self.assertEqual(p["total_input_tokens"], 0)
        self.assertEqual(p["total_output_tokens"], 0)
        self.assertEqual(p["total_cache_read_tokens"], 0)

    def test_boolean_and_negative_counts_invalid(self):
        self.assertIsNone(normalize_usage_record(_rec(input_tokens=True)))
        self.assertIsNone(normalize_usage_record(_rec(output_tokens=False)))
        self.assertIsNone(normalize_usage_record(_rec(input_tokens=-5)))
        agg = aggregate_project_usage(
            [_rec(input_tokens=True, response_id="bad")],
            as_of=AS_OF,
        )
        self.assertEqual(agg["invalid_records"], 1)
        self.assertEqual(agg["projects"], {})

    def test_unknown_cache_and_reasoning_stay_null(self):
        raw = _rec(input_tokens=10, output_tokens=4, response_id="r1")
        norm = normalize_usage_record(raw)
        self.assertIsNone(norm["cache_read_tokens"])
        self.assertIsNone(norm["reasoning_tokens"])
        agg = aggregate_project_usage([raw], as_of=AS_OF)
        p = agg["projects"]["agent-branches"]
        self.assertIsNone(p["total_cache_read_tokens"])
        self.assertIsNone(p["total_reasoning_tokens"])
        self.assertEqual(p["cache_coverage"], 0.0)
        self.assertEqual(p["reasoning_coverage"], 0.0)
        self.assertTrue(p["has_unknown_cache"])
        self.assertTrue(p["has_unknown_reasoning"])

    def test_reasoning_token_subset_isolation(self):
        raw = {
            "project_id": "agent-dashboard",
            "provider": "codex",
            "conversation_id": "c-1",
            "response_id": "r-1",
            "output_tokens": 50,
            "reasoning_tokens": 30,
            "timestamp": IN_WINDOW,
        }
        agg = aggregate_project_usage([raw], as_of=AS_OF)
        d_agg = agg["projects"]["agent-dashboard"]
        self.assertEqual(d_agg["total_output_tokens"], 50)
        self.assertEqual(d_agg["total_reasoning_tokens"], 30)

    def test_deduplication_by_response_id(self):
        raw1 = {
            "project_id": "quota-launcher",
            "provider": "zai",
            "conversation_id": "c-1",
            "response_id": "resp-1",
            "input_tokens": 200,
            "output_tokens": 100,
            "timestamp": IN_WINDOW,
        }
        raw2 = dict(raw1)
        agg = aggregate_project_usage([raw1, raw2], as_of=AS_OF)
        ql_agg = agg["projects"]["quota-launcher"]
        self.assertEqual(ql_agg["event_count"], 1)
        self.assertEqual(ql_agg["total_input_tokens"], 200)
        self.assertEqual(ql_agg["total_output_tokens"], 100)

    def test_missing_response_id_does_not_dedup_on_timestamp(self):
        a = _rec(input_tokens=10, output_tokens=1, timestamp=IN_WINDOW)
        b = _rec(input_tokens=10, output_tokens=1, timestamp=IN_WINDOW)
        agg = aggregate_project_usage([a, b], as_of=AS_OF)
        p = agg["projects"]["agent-branches"]
        self.assertEqual(p["event_count"], 2)
        self.assertEqual(p["total_input_tokens"], 20)
        self.assertGreaterEqual(agg["records_missing_response_id"], 2)

    def test_24h_usage_filter(self):
        inside = _rec(input_tokens=7, output_tokens=1, response_id="in", timestamp=IN_WINDOW)
        outside = _rec(
            input_tokens=100,
            output_tokens=50,
            response_id="out",
            timestamp="2026-10-02T11:00:00Z",
        )
        agg = aggregate_project_usage([inside, outside], as_of=AS_OF)
        p = agg["projects"]["agent-branches"]
        self.assertEqual(p["event_count"], 1)
        self.assertEqual(p["total_input_tokens"], 7)

    def test_quota_not_converted_to_cost_or_tokens(self):
        raw = _rec(input_tokens=3, output_tokens=1, quota_delta=-1.5, response_id="q1")
        agg = aggregate_project_usage([raw], as_of=AS_OF)
        p = agg["projects"]["agent-branches"]
        self.assertEqual(p["total_input_tokens"], 3)
        self.assertEqual(p["total_quota_delta_percent"], -1.5)
        self.assertIsNone(p["total_cost"])

    def test_noncanonical_project_unattributed(self):
        raw = _rec(project_id="oversight", input_tokens=1, output_tokens=1, response_id="n1")
        del raw["project_id"]
        raw["team_id"] = "oversight"
        # project_id missing -> unattributed (team_id is not a canonical project id)
        raw2 = {
            "team_id": "oversight",
            "provider": "zcode",
            "conversation_id": "c",
            "response_id": "n1",
            "input_tokens": 1,
            "output_tokens": 1,
            "timestamp": IN_WINDOW,
        }
        agg = aggregate_project_usage([raw2], as_of=AS_OF)
        self.assertIn("unattributed", agg["projects"])
        self.assertNotIn("oversight", agg["projects"])

    def test_opencode_adapter_reasoning_not_folded(self):
        os.makedirs(SCRATCH, exist_ok=True)
        path = os.path.join(SCRATCH, "opencode-adapter-test.json")
        payload = {
            "observed_at": IN_WINDOW,
            "by_team": {
                "a16-runtime-protocol": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "reasoning_output_tokens": 12,
                    "cached_input_tokens": 5,
                    "cache_write_tokens": 0,
                    "reported_cost": 0.0,
                    "missing_fields": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_output_tokens": 0,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "reported_cost": 0,
                    },
                }
            },
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        records = load_opencode_usage(path)
        self.assertEqual(len(records), 1)
        agg = aggregate_project_usage(records, as_of=AS_OF)
        p = agg["projects"]["unattributed"]
        self.assertEqual(p["total_output_tokens"], 40)
        self.assertEqual(p["total_reasoning_tokens"], 12)
        self.assertEqual(p["total_cache_read_tokens"], 5)
        self.assertEqual(p["total_cost"], 0.0)

    def test_canonical_project_id_aliases_and_fourth_product(self):
        self.assertEqual(canonical_project_id("agent-quota-launcher"), "quota-launcher")
        self.assertEqual(canonical_project_id("agent_quota_launcher"), "quota-launcher")
        self.assertEqual(canonical_project_id("agent-coordination"), "agent-coordination")
        self.assertEqual(canonical_project_id("agent_coordination"), "agent-coordination")
        self.assertEqual(canonical_project_id("agent-branches"), "agent-branches")
        self.assertEqual(canonical_project_id("agent-dashboard"), "agent-dashboard")
        self.assertEqual(canonical_project_id("quota-launcher"), "quota-launcher")
        self.assertEqual(canonical_project_id("unknown-xyz"), "unattributed")
        self.assertEqual(canonical_project_id(None), "unattributed")
        self.assertEqual(canonical_project_id(""), "unattributed")

    def test_usage_accounting_alias_and_coordination_routing(self):
        rec_ql = _rec(project_id="agent-quota-launcher", input_tokens=10, output_tokens=5, response_id="r-ql")
        rec_coord = _rec(project_id="agent-coordination", input_tokens=20, output_tokens=8, response_id="r-coord")
        rec_unknown = _rec(project_id="mysterious-lane", input_tokens=7, output_tokens=3, response_id="r-unk")
        agg = aggregate_project_usage([rec_ql, rec_coord, rec_unknown], as_of=AS_OF)
        self.assertIn("quota-launcher", agg["projects"])
        self.assertNotIn("agent-quota-launcher", agg["projects"])
        self.assertEqual(agg["projects"]["quota-launcher"]["total_input_tokens"], 10)
        self.assertEqual(agg["projects"]["quota-launcher"]["total_output_tokens"], 5)
        self.assertIn("agent-coordination", agg["projects"])
        self.assertEqual(agg["projects"]["agent-coordination"]["total_input_tokens"], 20)
        self.assertEqual(agg["projects"]["agent-coordination"]["total_output_tokens"], 8)
        self.assertIn("unattributed", agg["projects"])
        self.assertNotIn("mysterious-lane", agg["projects"])
        self.assertEqual(agg["projects"]["unattributed"]["total_input_tokens"], 7)
        self.assertEqual(agg["projects"]["unattributed"]["total_output_tokens"], 3)


if __name__ == "__main__":
    unittest.main()
