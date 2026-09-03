#!/usr/bin/env python3
import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "dasher_exports" / "dasher_20260902_analysis_v1"
OUT = Path(__file__).resolve().parent / "index.html"


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def load_data():
    sessions = read_csv(ANALYSIS_DIR / "candidate_sessions.csv")
    summary = json.loads((ANALYSIS_DIR / "summary.json").read_text())
    label_counts = Counter(row["candidate_label"] for row in sessions)
    day_counts = Counter(row["day"] for row in sessions)
    sessions.sort(key=lambda row: (row["day"], row["start_time"], row["candidate_label"]))
    return {
        "summary": {
            "source": str(ANALYSIS_DIR.relative_to(ROOT)),
            "records": summary["records"],
            "assignments": summary["unique_assignment_ids"],
            "sessions": len(sessions),
            "labelCounts": dict(label_counts),
            "dayCounts": dict(day_counts),
            "candidateLabels": summary["candidate_labels"],
            "gapSeconds": summary["candidate_session_analysis"]["gap_seconds"],
        },
        "sessions": sessions,
    }


def render_html(payload):
    data_json = json.dumps(payload, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DoorDash Candidate Sessions</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101112;
      --panel: #18191b;
      --panel-2: #202124;
      --line: #424448;
      --muted: #a8aaad;
      --text: #f5f5f2;
      --accent: #ff5b4f;
      --good: #63d289;
      --warn: #f2bd62;
      --blue: #78a7ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    button, input, select {{ font: inherit; }}
    .app {{
      display: grid;
      grid-template-columns: 360px 360px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .sidebar, .context {{
      background: #141517;
      border-right: 1px solid #2b2d31;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    .head {{
      padding: 22px 20px 16px;
      border-bottom: 1px solid #2b2d31;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 800;
    }}
    h1 {{
      margin: 6px 0 14px;
      font-size: 24px;
      line-height: 1.15;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }}
    .stat {{
      background: #202124;
      border: 1px solid #303237;
      border-radius: 6px;
      padding: 9px;
    }}
    .stat strong {{ display: block; font-size: 17px; }}
    .stat span {{ color: var(--muted); font-size: 11px; }}
    .filters {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin-top: 14px;
    }}
    .filters input, .filters select {{
      width: 100%;
      border: 1px solid #35373c;
      background: #111214;
      color: var(--text);
      border-radius: 6px;
      padding: 10px 11px;
      outline: none;
    }}
    .list {{
      overflow: auto;
      padding: 10px;
      display: grid;
      gap: 8px;
      align-content: start;
    }}
    .session-button {{
      appearance: none;
      border: 1px solid #2d2f34;
      background: #1a1b1e;
      color: var(--text);
      border-radius: 6px;
      padding: 12px;
      text-align: left;
      cursor: pointer;
    }}
    .session-button:hover {{ border-color: #494c52; background: #202226; }}
    .session-button.active {{
      border-color: var(--accent);
      box-shadow: inset 3px 0 0 var(--accent);
      background: #24201f;
    }}
    .row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }}
    .label {{
      font-weight: 800;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 230px;
    }}
    .count {{ font-weight: 850; }}
    .meta {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      background: #2b2d31;
      color: #d4d4d1;
      font-size: 11px;
      font-weight: 800;
      padding: 3px 8px;
      text-transform: uppercase;
    }}
    .pill.pickup {{ color: var(--blue); }}
    .pill.dropoff {{ color: var(--good); }}
    .pill.payout {{ color: var(--warn); }}
    .context-body {{ overflow: auto; padding: 20px; }}
    .context h2, .main h2 {{ margin: 0; font-size: 19px; line-height: 1.25; }}
    .muted {{ color: var(--muted); }}
    .section {{ margin-top: 22px; }}
    .section-title {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 850;
      margin-bottom: 8px;
    }}
    .rule-list {{ display: grid; gap: 8px; }}
    .rule-item {{
      background: #202124;
      border: 1px solid #303237;
      border-radius: 6px;
      padding: 10px;
    }}
    .rule-item strong {{ display: block; margin-bottom: 4px; }}
    .main {{ min-width: 0; padding: 34px 36px; }}
    .detail {{ max-width: 980px; margin: 0 auto; }}
    .back {{ color: var(--muted); font-size: 34px; line-height: 1; margin-bottom: 22px; }}
    .title {{
      margin: 0;
      font-size: clamp(30px, 4vw, 50px);
      line-height: 1.04;
      letter-spacing: 0;
    }}
    .subtitle {{
      color: var(--muted);
      margin-top: 10px;
      font-size: 18px;
      font-weight: 750;
    }}
    .summary-grid {{
      background: var(--panel-2);
      border: 1px solid #303237;
      border-radius: 8px;
      padding: 18px 20px;
      margin: 28px 0 36px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 18px;
    }}
    .summary-grid label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 850;
      margin-bottom: 4px;
    }}
    .summary-grid strong {{ font-size: 20px; }}
    .timeline {{
      position: relative;
      margin: 0 0 34px 8px;
      padding-left: 40px;
    }}
    .timeline:before {{
      content: "";
      position: absolute;
      left: 8px;
      top: 14px;
      bottom: 14px;
      width: 4px;
      border-radius: 2px;
      background: var(--line);
    }}
    .event {{
      position: relative;
      padding: 0 0 30px 0;
    }}
    .event:last-child {{ padding-bottom: 0; }}
    .event:before {{
      content: "";
      position: absolute;
      left: -39px;
      top: 7px;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #5b5d62;
      border: 3px solid var(--bg);
      z-index: 1;
    }}
    .event.start:before {{ background: var(--accent); }}
    .event.end:before {{ background: var(--good); }}
    .event.sample:before {{ background: var(--blue); }}
    .event-head {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
      align-items: baseline;
    }}
    .stage {{ color: #d8d8d5; font-size: 21px; font-weight: 760; }}
    .time {{ font-size: 28px; font-weight: 900; }}
    .text-preview {{
      margin-top: 10px;
      color: #d5d5d2;
      line-height: 1.48;
      overflow-wrap: anywhere;
    }}
    .chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }}
    .chip {{
      border: 1px solid #3b3d42;
      color: #d4d4d1;
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
    }}
    .empty {{ color: var(--muted); padding: 28px; text-align: center; }}
    @media (max-width: 1040px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar, .context {{ min-height: auto; max-height: 48vh; border-right: 0; border-bottom: 1px solid #2b2d31; }}
      .main {{ padding: 24px 18px 42px; }}
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .title {{ font-size: 34px; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="head">
        <div class="eyebrow">Candidate Session Review</div>
        <h1>DoorDash Sessions</h1>
        <div class="stats">
          <div class="stat"><strong id="statSessions">0</strong><span>sessions</span></div>
          <div class="stat"><strong id="statRecords">0</strong><span>records</span></div>
          <div class="stat"><strong id="statAssignments">0</strong><span>assignments</span></div>
        </div>
        <div class="filters">
          <input id="search" placeholder="Search session text">
          <select id="labelFilter"></select>
          <select id="dayFilter"></select>
        </div>
      </div>
      <div class="list" id="sessionList"></div>
    </aside>
    <section class="context">
      <div class="head">
        <div class="eyebrow">Rules</div>
        <h1>Stage Signals</h1>
      </div>
      <div class="context-body" id="contextBody"></div>
    </section>
    <main class="main">
      <section class="detail" id="detail"></section>
    </main>
  </div>
  <script>
    const DATA = {data_json};
    let selectedId = DATA.sessions[0]?.session_id || "";

    const labelNames = {{
      PICKUP_CANDIDATE: "Pickup",
      DROPOFF_CANDIDATE: "Dropoff",
      ARRIVED_STORE_CANDIDATE: "Arrived store",
      CONFIRM_PICKUP_CANDIDATE: "Confirm pickup",
      COMPLETE_DELIVERY_CANDIDATE: "Complete delivery",
      PAYOUT_CANDIDATE: "Payout",
      UNASSIGN_CANDIDATE: "Unassign",
      PHOTO_CANDIDATE: "Photo",
      NAVIGATION_CANDIDATE: "Navigation"
    }};
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({{
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\\"": "&quot;", "'": "&#39;"
    }}[ch]));
    const shortTime = (iso) => iso ? new Date(iso).toLocaleTimeString([], {{ hour: "2-digit", minute: "2-digit", second: "2-digit" }}) : "--";
    const labelClass = (label) => label.replace("_CANDIDATE", "").toLowerCase();

    function initFilters() {{
      const labelFilter = document.getElementById("labelFilter");
      const labels = Object.keys(DATA.summary.labelCounts).sort();
      labelFilter.innerHTML = `<option value="ALL">All candidate labels</option>` + labels.map((label) =>
        `<option value="${{esc(label)}}">${{esc(labelNames[label] || label)}} (${{DATA.summary.labelCounts[label]}})</option>`
      ).join("");
      const dayFilter = document.getElementById("dayFilter");
      const days = Object.keys(DATA.summary.dayCounts).sort();
      dayFilter.innerHTML = `<option value="ALL">All days</option>` + days.map((day) =>
        `<option value="${{esc(day)}}">${{esc(day)}} (${{DATA.summary.dayCounts[day]}})</option>`
      ).join("");
    }}

    function filteredSessions() {{
      const query = document.getElementById("search").value.trim().toLowerCase();
      const label = document.getElementById("labelFilter").value;
      const day = document.getElementById("dayFilter").value;
      return DATA.sessions.filter((session) => {{
        const text = [
          session.session_id,
          session.candidate_label,
          session.merchant_candidates,
          session.customer_candidates,
          session.trigger_rules,
          session.sample_visible_text
        ].join(" ").toLowerCase();
        return (label === "ALL" || session.candidate_label === label) &&
          (day === "ALL" || session.day === day) &&
          (!query || text.includes(query));
      }});
    }}

    function renderStats() {{
      document.getElementById("statSessions").textContent = DATA.summary.sessions;
      document.getElementById("statRecords").textContent = DATA.summary.records;
      document.getElementById("statAssignments").textContent = DATA.summary.assignments;
    }}

    function renderList() {{
      const list = document.getElementById("sessionList");
      const sessions = filteredSessions();
      if (!sessions.some((session) => session.session_id === selectedId)) {{
        selectedId = sessions[0]?.session_id || "";
      }}
      list.innerHTML = sessions.map((session) => `
        <button class="session-button ${{session.session_id === selectedId ? "active" : ""}}" data-id="${{esc(session.session_id)}}">
          <div class="row">
            <div class="label">${{esc(labelNames[session.candidate_label] || session.candidate_label)}}</div>
            <div class="count">${{esc(session.record_count)}}</div>
          </div>
          <div class="meta">
            <span>${{esc(session.day)}} · ${{shortTime(session.start_time)}}</span>
            <span class="pill ${{labelClass(session.candidate_label)}}">${{esc(session.session_id)}}</span>
          </div>
        </button>
      `).join("") || `<div class="empty">No matching sessions</div>`;
      list.querySelectorAll("button[data-id]").forEach((button) => {{
        button.addEventListener("click", () => {{
          selectedId = button.dataset.id;
          renderList();
          renderDetail();
        }});
      }});
    }}

    function renderContext() {{
      const body = document.getElementById("contextBody");
      const labels = Object.entries(DATA.summary.labelCounts).sort((a, b) => b[1] - a[1]);
      body.innerHTML = `
        <h2>${{esc(DATA.summary.source)}}</h2>
        <div class="muted">Session gap: ${{esc(DATA.summary.gapSeconds)}} seconds</div>
        <div class="section">
          <div class="section-title">Counts</div>
          <div class="rule-list">
            ${{labels.map(([label, count]) => `<div class="rule-item"><strong>${{esc(labelNames[label] || label)}}</strong><span class="muted">${{count}} sessions · ${{DATA.summary.candidateLabels[label] || 0}} records</span></div>`).join("")}}
          </div>
        </div>
        <div class="section">
          <div class="section-title">Boundary</div>
          <div class="rule-item">
            <strong>No order attribution here</strong>
            <span class="muted">This view only reviews candidate screen sessions. Assignment/order lifecycle attribution comes later.</span>
          </div>
        </div>
      `;
    }}

    function booleanChips(session) {{
      const pairs = [
        ["Pickup", session.has_pickup_candidate],
        ["Arrived", session.has_arrived_store_candidate],
        ["Confirm pickup", session.has_confirm_pickup_candidate],
        ["Navigation", session.has_navigation_candidate],
        ["Dropoff", session.has_dropoff_candidate],
        ["Complete", session.has_complete_delivery_candidate],
        ["Payout", session.has_payout_candidate],
        ["Unassign", session.has_unassign_candidate],
        ["Photo", session.has_photo_candidate]
      ];
      return pairs.filter(([, value]) => value === "True" || value === true).map(([label]) => `<span class="chip">${{esc(label)}}</span>`).join("");
    }}

    function renderDetail() {{
      const session = DATA.sessions.find((item) => item.session_id === selectedId);
      const detail = document.getElementById("detail");
      if (!session) {{
        detail.innerHTML = `<div class="empty">Select a session</div>`;
        return;
      }}
      detail.innerHTML = `
        <div class="back">←</div>
        <h2 class="title">${{esc(labelNames[session.candidate_label] || session.candidate_label)}}</h2>
        <div class="subtitle">${{esc(session.day)}} · ${{esc(session.session_id)}}</div>
        <div class="summary-grid">
          <div><label>Records</label><strong>${{esc(session.record_count)}}</strong></div>
          <div><label>Duration</label><strong>${{Number(session.duration_seconds).toFixed(1)}}s</strong></div>
          <div><label>Start</label><strong>${{shortTime(session.start_time)}}</strong></div>
          <div><label>End</label><strong>${{shortTime(session.end_time)}}</strong></div>
        </div>
        <div class="timeline">
          <div class="event start">
            <div class="event-head"><span class="stage">Session started</span><span class="time">${{shortTime(session.start_time)}}</span></div>
            <div class="text-preview">${{esc(session.first_folder)}}</div>
          </div>
          <div class="event sample">
            <div class="event-head"><span class="stage">Representative screen</span><span class="time">${{esc(session.record_count)}} records</span></div>
            <div class="chips">${{booleanChips(session)}}</div>
            <div class="text-preview">${{esc(session.sample_visible_text || "No visible text")}}</div>
          </div>
          <div class="event end">
            <div class="event-head"><span class="stage">Session ended</span><span class="time">${{shortTime(session.end_time)}}</span></div>
            <div class="text-preview">${{esc(session.last_folder)}}</div>
          </div>
        </div>
        <div class="section">
          <div class="section-title">Candidates</div>
          <div class="rule-list">
            <div class="rule-item"><strong>Merchants</strong><span class="muted">${{esc(session.merchant_candidates || "--")}}</span></div>
            <div class="rule-item"><strong>Customers</strong><span class="muted">${{esc(session.customer_candidates || "--")}}</span></div>
            <div class="rule-item"><strong>Trigger rules</strong><span class="muted">${{esc(session.trigger_rules || "--")}}</span></div>
          </div>
        </div>
      `;
    }}

    document.getElementById("search").addEventListener("input", renderList);
    document.getElementById("labelFilter").addEventListener("change", renderList);
    document.getElementById("dayFilter").addEventListener("change", renderList);
    initFilters();
    renderStats();
    renderContext();
    renderList();
    renderDetail();
  </script>
</body>
</html>"""


def main():
    OUT.write_text(render_html(load_data()))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
