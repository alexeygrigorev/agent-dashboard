"""
Unit tests for dashboard preview server endpoints.
"""

import datetime
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request

from dashboard.server import run_server

SCRATCH = os.environ.get("TMPDIR", "/home/alexey/git/agent-dashboard/.local/tmp")
UTC = datetime.timezone.utc


class TestDashboardServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SCRATCH, exist_ok=True)
        cls.spans_path = os.path.join(SCRATCH, "server-spans.json")
        cls.usage_path = os.path.join(SCRATCH, "server-usage.jsonl")
        cls.tasks_path = os.path.join(SCRATCH, "server-tasks.json")
        cls.registry_path = os.path.join(SCRATCH, "server-registry.json")
        cls.metrics_dir = os.path.join(SCRATCH, "server-metrics")
        os.makedirs(cls.metrics_dir, exist_ok=True)
        with open(cls.spans_path, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {
                        "project_id": "agent-dashboard",
                        "agent_id": "srv-1",
                        "started_at": "2026-10-04T10:00:00Z",
                        "ended_at": "2026-10-04T11:00:00Z",
                    }
                ],
                handle,
            )
        with open(cls.usage_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "project_id": "agent-dashboard",
                "provider": "zcode",
                "conversation_id": "c1",
                "response_id": "r1",
                "input_tokens": 4,
                "output_tokens": 2,
                "timestamp": "2026-10-04T11:00:00Z",
            }) + "\n")
        with open(cls.tasks_path, "w", encoding="utf-8") as handle:
            json.dump({"tasks": []}, handle)
        with open(cls.registry_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "teams": [{"id": "t", "agents": []}], "agents": []}, handle)

        cls._env = {
            "AGENT_SPANS_PATH": cls.spans_path,
            "USAGE_EVENTS_PATH": cls.usage_path,
            "TASKS_JSON_PATH": cls.tasks_path,
            "TEAM_REGISTRY_PATH": cls.registry_path,
            "DASHBOARD_METRICS_DIR": cls.metrics_dir,
            "OPENCODE_USAGE_PATH": os.path.join(cls.metrics_dir, "missing-opencode.json"),
        }
        cls._old_env = {}
        for key, value in cls._env.items():
            cls._old_env[key] = os.environ.get(key)
            os.environ[key] = value

        cls.server = run_server(port=0, host="127.0.0.1")
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for key, value in cls._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _get(self, path):
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.headers, resp.read()

    def test_health_endpoint(self):
        status, _headers, data = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn(b"agent-dashboard", data)

    def test_html_root_endpoint(self):
        status, headers, data = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn(b"Autonomous Agent Fleet Operational Dashboard", data)
        self.assertNotIn(b"Status: Operational", data)

    def test_hourly_endpoint(self):
        status, _headers, data = self._get("/api/hourly")
        self.assertEqual(status, 200)
        self.assertIn(b"projects", data)
        payload = json.loads(data.decode("utf-8"))
        self.assertIn("coverage_gaps", payload)
        self.assertEqual(payload["sources"]["hourly"]["kind"], "observed-history")
        self.assertEqual(payload["sources"]["registry"]["kind"], "static-registration")
        self.assertFalse(payload["sources"]["registry"]["used_for_hours"])

    def test_hourly_honors_as_of(self):
        status, _headers, data = self._get("/api/hourly?as_of=2026-10-04T13:17:43Z")
        self.assertEqual(status, 200)
        payload = json.loads(data.decode("utf-8"))
        self.assertTrue(payload["window_end"].startswith("2026-10-04T13:17:43"))
        self.assertTrue(payload["window_start"].startswith("2026-10-03T13:17:43"))
        self.assertEqual(len(payload["projects"]["agent-dashboard"]["hourly_buckets"]), 24)

    def test_hourly_invalid_as_of(self):
        req = urllib.request.Request(self.base + "/api/hourly?as_of=not-a-date")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_usage_endpoint(self):
        status, _headers, data = self._get("/api/usage?as_of=2026-10-04T12:00:00Z")
        self.assertEqual(status, 200)
        payload = json.loads(data.decode("utf-8"))
        self.assertIn("projects", payload)
        self.assertIn("coverage_gaps", payload)
        self.assertEqual(payload["projects"]["agent-dashboard"]["total_input_tokens"], 4)

    def test_features_endpoint(self):
        status, _headers, data = self._get("/api/features")
        self.assertEqual(status, 200)
        payload = json.loads(data.decode("utf-8"))
        self.assertIn("projects", payload)
        self.assertIn("coverage_gaps", payload)

    def test_missing_source_coverage_gap(self):
        old = os.environ.get("AGENT_SPANS_PATH")
        os.environ["AGENT_SPANS_PATH"] = os.path.join(SCRATCH, "missing-spans.json")
        try:
            status, _headers, data = self._get("/api/hourly?as_of=2026-10-04T12:00:00Z")
            self.assertEqual(status, 200)
            payload = json.loads(data.decode("utf-8"))
            self.assertTrue(payload["unknown"])
            reasons = " ".join(g.get("reason", "") for g in payload["coverage_gaps"]).lower()
            self.assertTrue("unreadable" in reasons or "missing" in reasons)
        finally:
            if old is None:
                os.environ.pop("AGENT_SPANS_PATH", None)
            else:
                os.environ["AGENT_SPANS_PATH"] = old

    def test_static_css_if_present(self):
        css_path = os.path.join(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static")),
            "dashboard.css",
        )
        if not os.path.isfile(css_path):
            self.skipTest("static/dashboard.css not present")
        status, headers, _data = self._get("/dashboard.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main()
