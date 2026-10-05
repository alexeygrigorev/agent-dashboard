/* Agent Dashboard frontend — vanilla JS, fetch-based, no external assets.
 * Defensive rendering: null/missing fields render as "n/a" or "unknown",
 * never as 0. API failures leave the static fallback layout in place. */
"use strict";

var PROJECT_IDS = ["agent-branches", "agent-dashboard", "quota-launcher", "agent-coordination"];

function isNullish(v) {
  return v === null || v === undefined;
}

function fmtInt(v) {
  if (isNullish(v) || v === "") return { text: "n/a", cls: "na" };
  var n = Number(v);
  if (!isFinite(n)) return { text: "n/a", cls: "na" };
  return { text: String(Math.round(n)), cls: "" };
}

function fmtHours(v) {
  if (isNullish(v) || v === "") return { text: "n/a", cls: "na" };
  var n = Number(v);
  if (!isFinite(n)) return { text: "n/a", cls: "na" };
  return { text: n.toFixed(2) + " h", cls: "" };
}

function fmtCoverage(v) {
  // Coverage may be 0..1 fraction or 0..100 percent; null -> "unknown".
  if (isNullish(v) || v === "") return { text: "unknown", cls: "na" };
  var n = Number(v);
  if (!isFinite(n)) return { text: "unknown", cls: "na" };
  var pct = n <= 1 ? n * 100 : n;
  return { text: pct.toFixed(1) + "%", cls: "" };
}

function fmtTokens(v) {
  if (isNullish(v) || v === "") return { text: "n/a", cls: "na" };
  var n = Number(v);
  if (!isFinite(n)) return { text: "n/a", cls: "na" };
  return { text: String(Math.round(n)), cls: "" };
}

function fmtQuota(v) {
  if (isNullish(v) || v === "") return { text: "n/a", cls: "na" };
  var n = Number(v);
  if (!isFinite(n)) return { text: "n/a", cls: "na" };
  return { text: n.toFixed(2) + "%", cls: "" };
}

function fmtTime(v) {
  if (isNullish(v) || v === "") return "n/a";
  var d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  return d.toISOString();
}

function shortHourLabel(iso) {
  var d = new Date(iso);
  if (isNaN(d.getTime())) return "??:??";
  var h = d.getUTCHours();
  var m = d.getUTCMinutes();
  return (h < 10 ? "0" + h : "" + h) + ":" + (m < 10 ? "0" + m : "" + m);
}

function setCell(id, fmt) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = fmt.text;
  el.className = fmt.cls || "";
}

function showError(id, msg) {
  var el = document.getElementById(id);
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
  } else {
    el.hidden = false;
    el.textContent = msg;
  }
}

function setHealth(state, text) {
  var el = document.getElementById("health-status");
  if (!el) return;
  el.textContent = text;
  el.className = "badge " + state;
}

function getJSON(url) {
  return fetch(url, { headers: { Accept: "application/json" } }).then(function (resp) {
    if (!resp.ok) throw new Error("HTTP " + resp.status + " for " + url);
    return resp.json();
  });
}

function renderHourly(data) {
  var info = document.getElementById("window-info");
  if (data && (data.window_start || data.window_end || data.as_of)) {
    info.textContent =
      "Window [" + fmtTime(data.window_start) + ", " + fmtTime(data.window_end) +
      ") — as_of " + fmtTime(data.as_of) + " (UTC, half-open buckets)";
  } else {
    info.textContent = "Window: n/a (hourly payload missing window fields)";
  }

  var projects = (data && data.projects) || {};

  // Top-level or nested unattributed rollup, if the backend provides one.
  var topUnattrib = null;
  if (data && !isNullish(data.unattributed_agent_hours)) topUnattrib = data.unattributed_agent_hours;
  if (projects.unattributed && !isNullish(projects.unattributed.total_agent_hours)) {
    topUnattrib = projects.unattributed.total_agent_hours;
  }

  PROJECT_IDS.forEach(function (pid) {
    var p = projects[pid];
    var badge = document.getElementById("unknown-" + pid);
    if (!p) {
      if (badge) badge.hidden = false;
      setCell("unique-" + pid, { text: "n/a", cls: "na" });
      setCell("hours-" + pid, { text: "n/a", cls: "na" });
      setCell("coverage-" + pid, { text: "unknown", cls: "na" });
      setCell("unattrib-" + pid, topUnattrib === null ? { text: "n/a", cls: "na" } : fmtHours(topUnattrib));
      renderChart(pid, []);
      return;
    }
    if (badge) badge.hidden = !(p.unknown === true);
    setCell("unique-" + pid, fmtInt(p.unique_agents));
    setCell("hours-" + pid, fmtHours(p.total_agent_hours));
    setCell("coverage-" + pid, fmtCoverage(p.coverage));
    var un = !isNullish(p.unattributed_agent_hours) ? p.unattributed_agent_hours : topUnattrib;
    setCell("unattrib-" + pid, isNullish(un) ? { text: "n/a", cls: "na" } : fmtHours(un));
    renderChart(pid, p.hourly_buckets || []);
  });

  // Shared identities note — counts are non-additive across projects.
  var note = document.getElementById("shared-note");
  var shared = data && data.shared_agent_ids;
  if (Array.isArray(shared)) {
    if (shared.length === 0) {
      note.textContent = "Shared-agent accounting: no agent identities span multiple projects in this window; per-project counts sum normally.";
    } else {
      note.textContent =
        "Shared-agent accounting: " + shared.length + " agent identity/identities appear in multiple projects; " +
        "unique-agent counts are non-additive across projects.";
    }
  } else {
    note.textContent = "Shared-agent accounting: shared_agent_ids n/a — per-project unique-agent counts may be non-additive across projects.";
  }
}

function renderChart(pid, buckets) {
  var host = document.getElementById("chart-" + pid);
  if (!host) return;
  host.textContent = "";
  if (!Array.isArray(buckets) || buckets.length === 0) {
    var p = document.createElement("p");
    p.className = "muted";
    p.textContent = "no bucket data (unknown)";
    host.appendChild(p);
    return;
  }
  var max = 0;
  buckets.forEach(function (b) {
    var h = Number(b && b.agent_hours);
    if (isFinite(h) && h > max) max = h;
  });
  if (max <= 0) max = 1;

  var bars = document.createElement("div");
  bars.className = "bars";
  bars.setAttribute("role", "img");
  bars.setAttribute("aria-label", buckets.length + " hourly buckets for " + pid);

  buckets.forEach(function (b) {
    var wrap = document.createElement("div");
    wrap.className = "bar-wrap";
    var bar = document.createElement("div");
    var h = b ? Number(b.agent_hours) : NaN;
    var agents = b ? b.active_agents : null;
    var start = b ? b.bucket_start : null;
    var end = b ? b.bucket_end : null;
    var pct = isFinite(h) && h > 0 ? Math.max(2, Math.round((h / max) * 100)) : 0;
    bar.className = "bar" + (pct === 0 ? " bar-zero" : "");
    bar.style.height = pct + "%";
    var hTxt = isFinite(h) ? h : "n/a";
    var aTxt = isNullish(agents) ? "n/a" : String(agents);
    bar.title =
      "[" + (start || "n/a") + ", " + (end || "n/a") + ") UTC — active: " +
      aTxt + ", hours: " + hTxt;
    wrap.appendChild(bar);
    bars.appendChild(wrap);
  });
  host.appendChild(bars);

  var labels = document.createElement("div");
  labels.className = "x-labels";
  labels.setAttribute("aria-hidden", "true");
  buckets.forEach(function (b, i) {
    var s = document.createElement("span");
    // Label every 6th bucket (plus the final edge) to avoid clutter.
    if (i % 6 === 0) {
      s.textContent = shortHourLabel(b ? b.bucket_start : null) + "Z";
    } else if (i === buckets.length - 1) {
      s.textContent = shortHourLabel(b ? b.bucket_end : null) + "Z";
    } else {
      s.textContent = "";
    }
    labels.appendChild(s);
  });
  host.appendChild(labels);
}

function renderUsage(data) {
  var body = document.getElementById("usage-body");
  if (!body) return;
  body.textContent = "";
  var projects = data && typeof data === "object" ? data : null;
  // Some backends wrap as {projects: {...}}; accept both shapes.
  if (projects && projects.projects && typeof projects.projects === "object") projects = projects.projects;
  var keys = projects ? Object.keys(projects) : [];
  if (keys.length === 0) {
    var tr = document.createElement("tr");
    var td = document.createElement("td");
    td.colSpan = 9;
    td.className = "muted";
    td.textContent = "no usage data (unknown)";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  keys.sort();
  keys.forEach(function (proj) {
    var agg = projects[proj] || {};
    var tr = document.createElement("tr");
    function cell(text, cls) {
      var td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      return td;
    }
    tr.appendChild(cell(proj, null));
    var ev = fmtInt(agg.event_count);
    tr.appendChild(cell(ev.text, ev.cls === "na" ? "na" : null));
    var fi = fmtTokens(agg.total_input_tokens);
    tr.appendChild(cell(fi.text, fi.cls === "na" ? "na" : null));
    var fo = fmtTokens(agg.total_output_tokens);
    tr.appendChild(cell(fo.text, fo.cls === "na" ? "na" : null));
    var cr = fmtTokens(agg.total_cache_read_tokens);
    tr.appendChild(cell(cr.text, cr.cls === "na" ? "na" : null));
    var cc = fmtTokens(agg.total_cache_creation_tokens);
    tr.appendChild(cell(cc.text, cc.cls === "na" ? "na" : null));
    var rs = fmtTokens(agg.total_reasoning_tokens);
    tr.appendChild(cell(rs.text, rs.cls === "na" ? "na" : null));
    var qd = fmtQuota(agg.total_quota_delta_percent);
    tr.appendChild(cell(qd.text, qd.cls === "na" ? "na" : null));
    var flags = [];
    if (agg.has_unknown_tokens) flags.push("has unknown tokens");
    if (agg.unknown === true) flags.push("unknown");
    tr.appendChild(cell(flags.length ? flags.join("; ") : "—", flags.length ? null : "na"));
    body.appendChild(tr);
  });
}

function renderFeatures(data) {
  var host = document.getElementById("features-list");
  if (!host) return;
  host.textContent = "";
  var groups = data && typeof data === "object" ? data : null;
  if (groups && groups.projects && typeof groups.projects === "object") groups = groups.projects;
  var keys = groups ? Object.keys(groups) : [];
  if (keys.length === 0) {
    var p = document.createElement("p");
    p.className = "muted";
    p.textContent = "no completed features reported (unknown or empty)";
    host.appendChild(p);
    return;
  }
  keys.sort();
  keys.forEach(function (proj) {
    var list = groups[proj];
    var div = document.createElement("div");
    div.className = "feat-group";
    var h = document.createElement("h3");
    h.textContent = proj + " (" + (Array.isArray(list) ? list.length : "n/a") + ")";
    div.appendChild(h);
    if (!Array.isArray(list) || list.length === 0) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "no features (n/a)";
      div.appendChild(empty);
    } else {
      var ul = document.createElement("ul");
      ul.className = "feat-list";
      list.forEach(function (f) {
        var li = document.createElement("li");
        if (f && typeof f === "object") {
          var fid = !isNullish(f.feature_id) ? f.feature_id : (!isNullish(f.task_id) ? f.task_id : "n/a");
          var bits = [String(fid)];
          if (!isNullish(f.accepted_at)) bits.push("accepted " + fmtTime(f.accepted_at));
          if (!isNullish(f.owner_tag)) bits.push("owner " + f.owner_tag);
          if (!isNullish(f.commit)) bits.push("commit " + f.commit);
          if (!isNullish(f.pr_url)) bits.push("pr " + f.pr_url);
          if (!isNullish(f.reviewer)) bits.push("reviewer " + f.reviewer);
          li.textContent = bits.join(" · ");
        } else {
          li.textContent = String(f);
        }
        ul.appendChild(li);
      });
      div.appendChild(ul);
    }
    host.appendChild(div);
  });
}

function setRefreshStatus(text) {
  var el = document.getElementById("refresh-status");
  if (el) el.textContent = text;
}

function loadAll(asOfISO) {
  setRefreshStatus("loading…");
  var hourlyURL = "/api/hourly" + (asOfISO ? "?as_of=" + encodeURIComponent(asOfISO) : "");
  var hourlyP = getJSON(hourlyURL).then(
    function (d) { return { ok: true, data: d }; },
    function (e) { return { ok: false, error: e }; }
  );
  var usageP = getJSON("/api/usage").then(
    function (d) { return { ok: true, data: d }; },
    function (e) { return { ok: false, error: e }; }
  );
  var featuresP = getJSON("/api/features").then(
    function (d) { return { ok: true, data: d }; },
    function (e) { return { ok: false, error: e }; }
  );
  var healthP = getJSON("/api/health").then(
    function (d) { return { ok: true, data: d }; },
    function (e) { return { ok: false, error: e }; }
  );

  return Promise.all([hourlyP, usageP, featuresP, healthP]).then(function (res) {
    var hourly = res[0], usage = res[1], features = res[2], health = res[3];

    if (health.ok) {
      setHealth("badge-ok", "health: ok");
    } else {
      setHealth("badge-err", "health: unavailable (fallback)");
    }

    if (hourly.ok) {
      showError("hourly-error", null);
      try {
        renderHourly(hourly.data);
      } catch (e) {
        showError("hourly-error", "Hourly render failed (" + e.message + "); showing fallbacks.");
      }
    } else {
      showError("hourly-error", "Hourly API unavailable (" + hourly.error.message + "); showing cached fallbacks — not a blank page.");
      // Keep static fallback text; still render unknown-state charts once.
      try { renderHourly(null); } catch (e) { /* keep fallbacks */ }
    }

    if (usage.ok) {
      showError("usage-error", null);
      try {
        renderUsage(usage.data);
      } catch (e) {
        showError("usage-error", "Usage render failed (" + e.message + ").");
      }
    } else {
      showError("usage-error", "Usage API unavailable (" + usage.error.message + "); showing fallback.");
      try { renderUsage(null); } catch (e) { /* keep fallbacks */ }
    }

    if (features.ok) {
      showError("features-error", null);
      try {
        renderFeatures(features.data);
      } catch (e) {
        showError("features-error", "Features render failed (" + e.message + ").");
      }
    } else {
      showError("features-error", "Features API unavailable (" + features.error.message + "); showing fallback.");
      try { renderFeatures(null); } catch (e) { /* keep fallbacks */ }
    }

    var stamp = new Date().toISOString();
    setRefreshStatus("updated " + stamp);
  });
}

document.addEventListener("DOMContentLoaded", function () {
  var btn = document.getElementById("refresh-btn");
  var input = document.getElementById("as-of-input");
  function currentAsOf() {
    if (input && input.value) {
      var d = new Date(input.value);
      if (!isNaN(d.getTime())) return d.toISOString();
    }
    return null;
  }
  if (btn) {
    btn.addEventListener("click", function () { loadAll(currentAsOf()); });
  }
  loadAll(null);
});
