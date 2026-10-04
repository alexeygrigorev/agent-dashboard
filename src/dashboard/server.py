"""
Agent Dashboard Localhost Preview Server.
Lightweight standard library HTTP server providing HTML preview and REST JSON APIs.
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

from dashboard.hourly import compute_hourly_utilization
from dashboard.accounting import aggregate_project_usage, load_usage_events_jsonl
from dashboard.features import load_accepted_features


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Request handler for Agent Dashboard."""

    def do_GET(self) -> None:
        """Route GET requests."""
        if self.path == "/" or self.path == "/index.html":
            self._handle_html()
        elif self.path == "/api/health":
            self._handle_json({"status": "ok", "service": "agent-dashboard"})
        elif self.path == "/api/hourly":
            # Load agent spans from registry or sample
            self._handle_hourly()
        elif self.path == "/api/usage":
            self._handle_usage()
        elif self.path == "/api/features":
            self._handle_features()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')

    def _handle_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle_hourly(self) -> None:
        # Check if live spans available from TEAM-REGISTRY.json
        registry_path = os.environ.get("TEAM_REGISTRY_PATH", "/home/alexey/git/cloudflare-agent-git/coordination/TEAM-REGISTRY.json")
        spans = []
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r", encoding="utf-8") as f:
                    reg_data = json.load(f)
                for team in reg_data.get("teams", []):
                    proj = team.get("id") or "unattributed"
                    for member in team.get("members", []):
                        spans.append({
                            "project_id": proj,
                            "agent_id": member.get("tag") or member.get("session_id"),
                            "started_at": member.get("started_at") or member.get("registered_at"),
                            "ended_at": member.get("completed_at"),
                        })
            except Exception:
                pass
        res = compute_hourly_utilization(spans)
        self._handle_json(res)

    def _handle_usage(self) -> None:
        events_path = os.environ.get("USAGE_EVENTS_PATH", "/home/alexey/git/cloudflare-agent-git/.local/metrics/usage-events.jsonl")
        records = load_usage_events_jsonl(events_path)
        res = aggregate_project_usage(records)
        self._handle_json(res)

    def _handle_features(self) -> None:
        tasks_path = os.environ.get("TASKS_JSON_PATH", "/home/alexey/git/cloudflare-agent-git/coordination/TASKS.json")
        res = load_accepted_features(tasks_path)
        self._handle_json(res)

    def _handle_html(self) -> None:
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Agent Fleet Dashboard</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 2rem; background: #0f172a; color: #f8fafc; }
    h1 { color: #38bdf8; font-size: 1.8rem; margin-bottom: 0.5rem; }
    .subtitle { color: #94a3b8; font-size: 0.95rem; margin-bottom: 2rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.5rem; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
    .card h2 { font-size: 1.2rem; color: #e2e8f0; margin-top: 0; border-bottom: 1px solid #334155; padding-bottom: 0.5rem; }
    .metric { margin: 1rem 0; font-size: 1.1rem; }
    .metric-value { font-weight: bold; color: #38bdf8; }
    pre { background: #090d16; padding: 1rem; border-radius: 4px; overflow-x: auto; font-size: 0.85rem; color: #cbd5e1; }
    a { color: #38bdf8; text-decoration: none; }
  </style>
</head>
<body>
  <h1>Autonomous Agent Fleet Operational Dashboard</h1>
  <div class="subtitle">Rolling 24-Hour Utilization, Usage Accounting, and Completed Feature Verification</div>
  
  <div class="grid">
    <div class="card">
      <h2>Project: AgentBranches</h2>
      <div class="metric">Status: <span class="metric-value">Active Development</span></div>
      <div class="metric">Runtime Protocol: <span class="metric-value">Git Smart HTTP + DO</span></div>
      <p><a href="/api/hourly">View Hourly API &rarr;</a></p>
    </div>
    <div class="card">
      <h2>Project: Agent Dashboard</h2>
      <div class="metric">Status: <span class="metric-value">Operational</span></div>
      <div class="metric">Port: <span class="metric-value">Localhost Preview</span></div>
      <p><a href="/api/usage">View Usage API &rarr;</a></p>
    </div>
    <div class="card">
      <h2>Project: Quota-Aware Launcher</h2>
      <div class="metric">Status: <span class="metric-value">Active Integration</span></div>
      <div class="metric">Provider Gating: <span class="metric-value">Heuristic Quota Engine</span></div>
      <p><a href="/api/features">View Features API &rarr;</a></p>
    </div>
  </div>
</body>
</html>"""
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(port: int = 8765, host: str = "127.0.0.1") -> HTTPServer:
    """Instantiate and start HTTP server."""
    server = HTTPServer((host, port), DashboardRequestHandler)
    return server


def main() -> None:
    """CLI entrypoint."""
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    server = run_server(port=port, host=host)
    print(f"Agent Dashboard running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
