"""
Unit tests for dashboard preview server endpoints.
"""

import unittest
import urllib.request
import threading
import time
from dashboard.server import run_server


class TestDashboardServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bind ephemeral port for testing
        cls.server = run_server(port=9876, host="127.0.0.1")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health_endpoint(self):
        req = urllib.request.Request("http://127.0.0.1:9876/api/health")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = resp.read()
            self.assertIn(b"agent-dashboard", data)

    def test_html_root_endpoint(self):
        req = urllib.request.Request("http://127.0.0.1:9876/")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = resp.read()
            self.assertIn(b"Autonomous Agent Fleet Operational Dashboard", data)

    def test_hourly_endpoint(self):
        req = urllib.request.Request("http://127.0.0.1:9876/api/hourly")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = resp.read()
            self.assertIn(b"projects", data)

    def test_usage_endpoint(self):
        req = urllib.request.Request("http://127.0.0.1:9876/api/usage")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

    def test_features_endpoint(self):
        req = urllib.request.Request("http://127.0.0.1:9876/api/features")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
