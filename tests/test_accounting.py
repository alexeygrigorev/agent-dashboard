"""
Unit tests for usage accounting engine.
"""

import unittest
from dashboard.accounting import normalize_usage_record, aggregate_project_usage


class TestUsageAccounting(unittest.TestCase):
    def test_nullability_and_types(self):
        # Missing tokens should be None, not 0
        raw = {
            "project_id": "agent-branches",
            "provider": "gemini",
            "conversation_id": "conv-1",
            "input_tokens": 100,
            # output_tokens omitted
            "reasoning_tokens": 25,
            "quota_delta": "-1.5%",
        }
        norm = normalize_usage_record(raw)
        self.assertEqual(norm["input_tokens"], 100)
        self.assertIsNone(norm["output_tokens"])
        self.assertEqual(norm["reasoning_tokens"], 25)
        # Quota delta should fail-safe or parse
        # If string "-1.5%" fails float(), it becomes None
        self.assertIsNone(norm["quota_delta"])

        # Valid numeric float quota delta
        raw_float = {
            "project_id": "agent-branches",
            "provider": "gemini",
            "conversation_id": "conv-1",
            "quota_delta": -1.5,
        }
        norm2 = normalize_usage_record(raw_float)
        self.assertEqual(norm2["quota_delta"], -1.5)

    def test_reasoning_token_subset_isolation(self):
        # Reasoning tokens must NOT be double-added to output tokens
        raw = {
            "project_id": "agent-dashboard",
            "provider": "codex",
            "conversation_id": "c-1",
            "response_id": "r-1",
            "output_tokens": 50,
            "reasoning_tokens": 30,
        }
        agg = aggregate_project_usage([raw])
        d_agg = agg["agent-dashboard"]
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
        }
        # Duplicate record for same response_id
        raw2 = dict(raw1)
        agg = aggregate_project_usage([raw1, raw2])
        ql_agg = agg["quota-launcher"]
        self.assertEqual(ql_agg["event_count"], 1)
        self.assertEqual(ql_agg["total_input_tokens"], 200)
        self.assertEqual(ql_agg["total_output_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
