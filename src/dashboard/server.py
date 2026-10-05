"""
Agent Dashboard Localhost Preview Server.
Lightweight standard library HTTP server providing HTML preview and REST JSON APIs.
"""

from __future__ import annotations

import datetime
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from dashboard.accounting import (
    aggregate_project_usage,
    load_opencode_usage,
    load_opencode_usage_from_obj,
    load_usage_events_jsonl,
)
from dashboard.features import load_accepted_features
from dashboard.hourly import (
    compute_hourly_utilization,
    load_agent_spans,
    load_spans_from_observation,
    parse_iso_timestamp,
    validate_registry_shape,
)

DEFAULT_METRICS_DIR = "/home/alexey/git/cloudflare-agent-git/.local/metrics"
DEFAULT_TASKS_PATH = "/home/alexey/git/cloudflare-agent-git/coordination/TASKS.json"
DEFAULT_REGISTRY_PATH = "/home/alexey/git/cloudflare-agent-git/coordination/TEAM-REGISTRY.json"


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _static_dir() -> str:
    env = os.environ.get("DASHBOARD_STATIC_DIR")
    if env:
        return env
    return os.path.join(_repo_root(), "static")


def _metrics_dir() -> str:
    return os.environ.get("DASHBOARD_METRICS_DIR", DEFAULT_METRICS_DIR)


def _source_entry(kind: str, path: Optional[str], readable: bool, **extra: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"kind": kind, "path": path, "readable": readable}
    entry.update(extra)
    return entry


def _file_readable(path: Optional[str]) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.R_OK)


def _dir_readable(path: Optional[str]) -> bool:
    return bool(path) and os.path.isdir(path) and os.access(path, os.R_OK)


def _parse_as_of(query: Dict[str, List[str]]) -> Tuple[Optional[datetime.datetime], Optional[str]]:
    values = query.get("as_of") or []
    if not values or not values[0]:
        return datetime.datetime.now(datetime.timezone.utc), None
    try:
        return parse_iso_timestamp(values[0]), None
    except Exception:
        return None, "invalid as_of"


def _load_hourly_payload(as_of: datetime.datetime) -> Dict[str, Any]:
    coverage_gaps: List[Dict[str, str]] = []
    sources: Dict[str, Any] = {}

    spans_path = os.environ.get("AGENT_SPANS_PATH")
    metrics_dir = _metrics_dir()
    registry_path = os.environ.get("TEAM_REGISTRY_PATH", DEFAULT_REGISTRY_PATH)

    spans: Optional[List[Dict[str, Any]]] = None
    hourly_kind = "missing"

    if spans_path:
        readable = _file_readable(spans_path)
        sources["hourly"] = _source_entry("observed-history", spans_path, readable, used_for_hours=readable)
        if readable:
            loaded = load_agent_spans(spans_path)
            if loaded is None:
                coverage_gaps.append({"source": spans_path, "reason": "spans file missing after open"})
            else:
                spans = loaded
                hourly_kind = "observed-history"
        else:
            coverage_gaps.append({"source": spans_path, "reason": "AGENT_SPANS_PATH missing or unreadable"})
    else:
        sources["metrics"] = _source_entry(
            "observed-history",
            metrics_dir,
            _dir_readable(metrics_dir),
            used_for_hours=False,
        )
        obs_spans, obs_gaps = load_spans_from_observation(metrics_dir)
        coverage_gaps.extend(obs_gaps)
        if obs_spans is None:
            sources["hourly"] = _source_entry("missing", metrics_dir, False, used_for_hours=False)
        else:
            spans = obs_spans
            hourly_kind = "observed-history"
            sources["hourly"] = _source_entry(
                "observed-history",
                os.path.join(metrics_dir, "observation-state.json"),
                True,
                used_for_hours=True,
                companion=os.path.join(metrics_dir, "latest.json"),
            )

    registry_readable = _file_readable(registry_path)
    registry_note = "static-registration; not observed live history; not used for hours"
    if registry_readable:
        try:
            with open(registry_path, "r", encoding="utf-8") as handle:
                reg_data = json.load(handle)
            ok, reason = validate_registry_shape(reg_data)
            sources["registry"] = _source_entry(
                "static-registration",
                registry_path,
                ok,
                used_for_hours=False,
                note=registry_note if ok else reason,
            )
            if not ok:
                coverage_gaps.append({"source": registry_path, "reason": reason})
        except Exception:
            sources["registry"] = _source_entry(
                "static-registration",
                registry_path,
                False,
                used_for_hours=False,
                note="unreadable",
            )
            coverage_gaps.append({"source": registry_path, "reason": "unreadable"})
    else:
        sources["registry"] = _source_entry(
            "static-registration",
            registry_path,
            False,
            used_for_hours=False,
            note=registry_note,
        )
        coverage_gaps.append({"source": registry_path, "reason": "missing or unreadable"})

    result = compute_hourly_utilization(spans, as_of=as_of)
    result["sources"] = sources
    existing_gaps = result.get("coverage_gaps") or []
    if not isinstance(existing_gaps, list):
        existing_gaps = []
    result["coverage_gaps"] = existing_gaps + coverage_gaps
    result["hourly_source_kind"] = hourly_kind
    return result


def _load_usage_payload(as_of: datetime.datetime) -> Dict[str, Any]:
    coverage_gaps: List[Dict[str, str]] = []
    sources: Dict[str, Any] = {}
    records: List[Dict[str, Any]] = []
    metrics_dir = _metrics_dir()

    events_path = os.environ.get(
        "USAGE_EVENTS_PATH",
        os.path.join(metrics_dir, "usage-events.jsonl"),
    )
    events_readable = _file_readable(events_path)
    sources["usage_events"] = _source_entry("observed-history", events_path, events_readable)
    if events_readable:
        records.extend(load_usage_events_jsonl(events_path))
    else:
        coverage_gaps.append({"source": events_path, "reason": "usage-events.jsonl missing or unreadable"})

    opencode_path = os.environ.get("OPENCODE_USAGE_PATH")
    latest_path = os.path.join(metrics_dir, "latest.json")
    adapter_path = os.path.join(metrics_dir, "opencode-adapter-acceptance.json")

    opencode_loaded = False
    if opencode_path:
        readable = _file_readable(opencode_path)
        sources["opencode_adapter"] = _source_entry("observed-history", opencode_path, readable)
        if readable:
            records.extend(load_opencode_usage(opencode_path))
            opencode_loaded = True
        else:
            coverage_gaps.append({"source": opencode_path, "reason": "OPENCODE_USAGE_PATH unreadable"})

    if not opencode_loaded and _file_readable(latest_path):
        try:
            with open(latest_path, "r", encoding="utf-8") as handle:
                latest = json.load(handle)
            usage_obj = latest.get("opencode_usage") if isinstance(latest, dict) else None
            if isinstance(usage_obj, dict):
                records.extend(load_opencode_usage_from_obj(usage_obj))
                sources["opencode_adapter"] = _source_entry(
                    "observed-history",
                    latest_path,
                    True,
                    field="opencode_usage",
                )
                opencode_loaded = True
            else:
                coverage_gaps.append({"source": latest_path, "reason": "opencode_usage missing in latest.json"})
        except Exception:
            coverage_gaps.append({"source": latest_path, "reason": "latest.json unreadable for OpenCode usage"})

    if not opencode_loaded:
        readable = _file_readable(adapter_path)
        sources.setdefault("opencode_adapter", _source_entry("observed-history", adapter_path, readable))
        if readable:
            records.extend(load_opencode_usage(adapter_path))
            opencode_loaded = True
        else:
            coverage_gaps.append({"source": adapter_path, "reason": "OpenCode adapter missing or unreadable"})

    result = aggregate_project_usage(records, as_of=as_of)
    result["sources"] = {**sources, **(result.get("sources") or {})}
    result["coverage_gaps"] = list(result.get("coverage_gaps") or []) + coverage_gaps
    return result


def _load_features_payload(as_of: datetime.datetime) -> Dict[str, Any]:
    tasks_path = os.environ.get("TASKS_JSON_PATH", DEFAULT_TASKS_PATH)
    result = load_accepted_features(tasks_path, as_of=as_of)
    readable = _file_readable(tasks_path)
    result["sources"] = {
        "tasks": _source_entry("task-ledger", tasks_path, readable),
    }
    if not readable and not any(g.get("source") == "tasks" for g in result.get("coverage_gaps") or []):
        result.setdefault("coverage_gaps", []).append(
            {"source": tasks_path, "reason": "TASKS.json missing or unreadable"}
        )
    return result


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Request handler for Agent Dashboard."""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        query = parse_qs(parsed.query)

        if path == "/api/health":
            self._handle_health()
            return
        if path == "/api/hourly":
            self._handle_hourly(query)
            return
        if path == "/api/usage":
            self._handle_usage(query)
            return
        if path == "/api/features":
            self._handle_features(query)
            return
        self._handle_static(path)

    def _handle_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle_health(self) -> None:
        static_path = os.path.join(_static_dir(), "index.html")
        metrics_dir = _metrics_dir()
        self._handle_json({
            "status": "ok",
            "service": "agent-dashboard",
            "static_index_readable": _file_readable(static_path),
            "metrics_dir_readable": _dir_readable(metrics_dir),
        })

    def _handle_hourly(self, query: Dict[str, List[str]]) -> None:
        as_of, err = _parse_as_of(query)
        if err:
            self._handle_json({"error": err}, status=400)
            return
        assert as_of is not None
        self._handle_json(_load_hourly_payload(as_of))

    def _handle_usage(self, query: Dict[str, List[str]]) -> None:
        as_of, err = _parse_as_of(query)
        if err:
            self._handle_json({"error": err}, status=400)
            return
        assert as_of is not None
        self._handle_json(_load_usage_payload(as_of))

    def _handle_features(self, query: Dict[str, List[str]]) -> None:
        as_of, err = _parse_as_of(query)
        if err:
            self._handle_json({"error": err}, status=400)
            return
        assert as_of is not None
        self._handle_json(_load_features_payload(as_of))

    def _handle_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        static_root = os.path.realpath(_static_dir())
        candidate = os.path.realpath(os.path.join(static_root, rel))
        if candidate == static_root or candidate.startswith(static_root + os.sep):
            if os.path.isfile(candidate):
                self._send_file(candidate)
                return
        if path in ("/", "/index.html"):
            self._send_fallback_html()
            return
        self._handle_json({"error": "not found"}, status=404)

    def _send_file(self, full_path: str) -> None:
        ctype, _encoding = mimetypes.guess_type(full_path)
        if not ctype:
            if full_path.endswith(".css"):
                ctype = "text/css"
            elif full_path.endswith(".js"):
                ctype = "text/javascript"
            else:
                ctype = "application/octet-stream"
        with open(full_path, "rb") as handle:
            payload = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_fallback_html(self) -> None:
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Agent Fleet Dashboard</title>
</head>
<body>
  <h1>Autonomous Agent Fleet Operational Dashboard</h1>
  <p>static/index.html is missing. JSON APIs: <a href="/api/health">/api/health</a>,
     <a href="/api/hourly">/api/hourly</a>, <a href="/api/usage">/api/usage</a>,
     <a href="/api/features">/api/features</a>.</p>
  <p>Source coverage and unknown fields are in the JSON. This fallback does not assert live status.</p>
</body>
</html>"""
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(port: int = 8765, host: str = "127.0.0.1") -> HTTPServer:
    """Instantiate HTTP server bound to localhost."""
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
