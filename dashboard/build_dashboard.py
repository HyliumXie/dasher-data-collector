#!/usr/bin/env python3
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "dasher_exports" / "dasher_20260831_raw" / "20260831"
OUT = Path(__file__).resolve().parent / "index.html"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
SAMPLE_ASSIGNMENT_ID = "9f7b7843-9664-4f4e-b55e-9f11aaa34657"

STAGE_ORDER = [
    "NEW_OFFER",
    "DECLINE_CONFIRMATION",
    "ACCEPTED",
    "ARRIVED",
    "PICKED_UP",
    "COMPLETED",
    "UNASSIGNED",
]

MAIN_FLOW = ["NEW_OFFER", "ACCEPTED", "ARRIVED", "PICKED_UP", "COMPLETED"]


def walk(node):
    yield node
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from walk(child)


def extract_accessibility(tree):
    texts = []
    buttons = []
    clickable = []
    for node in walk(tree):
        text = (node.get("text") or node.get("contentDescription") or "").strip()
        if not text:
            continue
        texts.append(text)
        class_name = node.get("className") or ""
        if "Button" in class_name or node.get("clickable"):
            buttons.append(text)
        if node.get("clickable"):
            clickable.append(text)
    unique_texts = list(dict.fromkeys(texts))
    unique_buttons = list(dict.fromkeys(buttons))
    unique_clickable = list(dict.fromkeys(clickable))
    return {
        "texts": unique_texts,
        "buttons": unique_buttons,
        "clickable": unique_clickable,
        "textPreview": unique_texts[:18],
    }


def money_number(value):
    if not value:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(value).replace(",", ""))
    return float(match.group(1)) if match else None


def duration_minutes(start, end):
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 60, 1)


def status_for(stages):
    has_active = any(stage in stages for stage in ["ACCEPTED", "ARRIVED", "PICKED_UP", "COMPLETED", "UNASSIGNED"])
    if "COMPLETED" in stages:
        return "COMPLETED"
    if "UNASSIGNED" in stages:
        return "UNASSIGNED"
    if "DECLINE_CONFIRMATION" in stages and not has_active:
        return "DECLINED"
    if "NEW_OFFER" in stages and not has_active:
        return "TIMEOUT_OR_DECLINED"
    return "ACCEPTED_INCOMPLETE"


def short_id(assignment_id):
    return assignment_id[:8]


def build_data():
    ASSETS_DIR.mkdir(exist_ok=True)
    records = []
    for meta_path in sorted(DATA_ROOT.glob("*/meta.json")):
        folder = meta_path.parent.name
        meta = json.loads(meta_path.read_text())
        tree_path = meta_path.parent / "accessibility_tree.json"
        tree = json.loads(tree_path.read_text())
        accessibility = extract_accessibility(tree)
        timestamp = datetime.fromisoformat(meta["timestamp"])
        assignment_id = meta.get("assignmentId") or meta.get("assignedAssignmentId")
        offer = meta.get("offer") or {}
        records.append(
            {
                "folder": folder,
                "timestamp": meta["timestamp"],
                "time": timestamp.strftime("%H:%M:%S"),
                "dt": timestamp,
                "assignmentId": assignment_id,
                "stage": meta.get("stage") or meta.get("screenClassification") or "UNKNOWN",
                "outcome": meta.get("outcome"),
                "outcomeReason": meta.get("outcomeReason"),
                "confidence": meta.get("confidence"),
                "timestampSource": meta.get("timestampSource"),
                "buttonTexts": meta.get("buttonTexts") or [],
                "persistReason": meta.get("persistReason"),
                "expectedPay": offer.get("expectedPay"),
                "offerPayNumber": money_number(offer.get("expectedPay")),
                "miles": offer.get("miles"),
                "deliverBy": offer.get("deliverBy"),
                "restaurants": offer.get("restaurants") or [],
                "pickupCount": offer.get("pickupCount"),
                "dropoffCount": offer.get("dropoffCount"),
                "estimatedOrderCount": offer.get("estimatedOrderCount"),
                "offerType": offer.get("offerType"),
                "hasTotalWillBeHigher": offer.get("hasTotalWillBeHigher"),
                "dashTotal": meta.get("dashTotal"),
                "accessibility": accessibility,
            }
        )
    records.sort(key=lambda item: item["dt"])

    grouped = defaultdict(list)
    for record in records:
        if record["assignmentId"]:
            grouped[record["assignmentId"]].append(record)

    orders = []
    for index, (assignment_id, items) in enumerate(sorted(grouped.items(), key=lambda kv: kv[1][0]["dt"]), 1):
        counts = Counter(item["stage"] for item in items)
        first_event_by_stage = {}
        all_events = []
        for item in items:
            event = {
                key: item[key]
                for key in [
                    "folder",
                    "timestamp",
                    "time",
                    "stage",
                    "outcome",
                    "outcomeReason",
                    "confidence",
                    "timestampSource",
                    "buttonTexts",
                    "persistReason",
                    "dashTotal",
                    "accessibility",
                ]
            }
            all_events.append(event)
            if item["stage"] not in first_event_by_stage:
                first_event_by_stage[item["stage"]] = event

        new_offer_record = next((item for item in items if item["stage"] == "NEW_OFFER"), None)
        offer_screenshot_folder = None
        if new_offer_record:
            source_screenshot = DATA_ROOT / new_offer_record["folder"] / "screenshot.png"
            if source_screenshot.exists():
                offer_screenshot_folder = new_offer_record["folder"]
        stages = set(first_event_by_stage)
        missing = [stage for stage in MAIN_FLOW if stage not in stages]
        status = status_for(stages)
        notes = []
        if missing:
            notes.append("missing_stage=" + ",".join(missing))
        if any(item["confidence"] == "LOW" for item in items):
            notes.append("has_low_confidence")
        if counts["NEW_OFFER"] > 1:
            notes.append("duplicate_new_offer_capture")
        if counts["ARRIVED"] > 3 or counts["PICKED_UP"] > 3 or counts["ACCEPTED"] > 2:
            notes.append("duplicate_stage_frames")
        if "DECLINE_CONFIRMATION" in stages and status not in ["DECLINED", "TIMEOUT_OR_DECLINED"]:
            notes.append("decline_confirmation_seen_but_later_active")
        if new_offer_record and new_offer_record.get("hasTotalWillBeHigher"):
            notes.append("hidden_tip_possible")
        flow_times = [first_event_by_stage[stage]["timestamp"] for stage in MAIN_FLOW if stage in first_event_by_stage]
        if flow_times != sorted(flow_times):
            notes.append("stage_time_order_anomaly")

        timeline = []
        for stage, event in first_event_by_stage.items():
            timeline.append(
                {
                    "stage": stage,
                    "time": event["time"],
                    "timestamp": event["timestamp"],
                    "folder": event["folder"],
                    "confidence": event["confidence"],
                    "timestampSource": event["timestampSource"],
                    "buttons": event["buttonTexts"],
                    "accessibilityButtons": event["accessibility"]["buttons"],
                    "textPreview": event["accessibility"]["textPreview"],
                    "outcome": event["outcome"],
                    "outcomeReason": event["outcomeReason"],
                    "dashTotal": event["dashTotal"],
                }
            )
        timeline.sort(key=lambda event: event["timestamp"])

        orders.append(
            {
                "index": index,
                "assignmentId": assignment_id,
                "shortId": short_id(assignment_id),
                "status": status,
                "firstTime": items[0]["time"],
                "lastTime": items[-1]["time"],
                "durationMinutes": duration_minutes(items[0]["dt"], items[-1]["dt"]),
                "expectedPay": new_offer_record.get("expectedPay") if new_offer_record else None,
                "miles": new_offer_record.get("miles") if new_offer_record else None,
                "deliverBy": new_offer_record.get("deliverBy") if new_offer_record else None,
                "restaurants": new_offer_record.get("restaurants") if new_offer_record else [],
                "pickupCount": new_offer_record.get("pickupCount") if new_offer_record else None,
                "dropoffCount": new_offer_record.get("dropoffCount") if new_offer_record else None,
                "estimatedOrderCount": new_offer_record.get("estimatedOrderCount") if new_offer_record else None,
                "offerType": new_offer_record.get("offerType") if new_offer_record else None,
                "hasTotalWillBeHigher": new_offer_record.get("hasTotalWillBeHigher") if new_offer_record else False,
                "offerScreenshotFolder": offer_screenshot_folder,
                "offerScreenshot": None,
                "stageCounts": dict(counts),
                "missingStages": missing,
                "notes": notes,
                "timeline": timeline,
                "events": all_events,
            }
        )

    if SAMPLE_ASSIGNMENT_ID:
        orders = [order for order in orders if order["assignmentId"] == SAMPLE_ASSIGNMENT_ID]

    for order in orders:
        if order["offerScreenshotFolder"]:
            image_name = f"offer_{order['shortId']}.png"
            source_screenshot = DATA_ROOT / order["offerScreenshotFolder"] / "screenshot.png"
            shutil.copyfile(source_screenshot, ASSETS_DIR / image_name)
            order["offerScreenshot"] = f"assets/{image_name}"

    visible_events = [event for order in orders for event in order["events"]]
    summary = {
        "date": "2026-08-31",
        "source": str(DATA_ROOT.relative_to(ROOT)),
        "orders": len(orders),
        "events": len(visible_events),
        "statusCounts": dict(Counter(order["status"] for order in orders)),
        "stageCounts": dict(Counter(event["stage"] for event in visible_events)),
    }
    return {"summary": summary, "orders": orders}


def render_html(payload):
    data_json = json.dumps(payload, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DoorDash Accessibility Timeline</title>
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
      --bad: #ff8f86;
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
    button, input, select {{
      font: inherit;
    }}
    .app {{
      display: grid;
      grid-template-columns: 340px minmax(320px, 430px) minmax(0, 1fr);
      min-height: 100vh;
    }}
    .sidebar {{
      border-right: 1px solid #2b2d31;
      background: #141517;
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }}
    .side-head {{
      padding: 22px 20px 16px;
      border-bottom: 1px solid #2b2d31;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 700;
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
    .stat strong {{
      display: block;
      font-size: 17px;
    }}
    .stat span {{
      color: var(--muted);
      font-size: 11px;
    }}
    .filters {{
      display: grid;
      grid-template-columns: 1fr 128px;
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
    .order-list {{
      overflow: auto;
      padding: 10px;
      display: grid;
      gap: 8px;
      align-content: start;
    }}
    .order-button {{
      appearance: none;
      border: 1px solid #2d2f34;
      background: #1a1b1e;
      color: var(--text);
      border-radius: 6px;
      padding: 12px;
      text-align: left;
      cursor: pointer;
    }}
    .order-button:hover {{
      border-color: #494c52;
      background: #202226;
    }}
    .order-button.active {{
      border-color: var(--accent);
      box-shadow: inset 3px 0 0 var(--accent);
      background: #24201f;
    }}
    .order-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }}
    .restaurant {{
      font-weight: 800;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 220px;
    }}
    .pay {{
      font-weight: 800;
    }}
    .meta {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
    }}
    .status {{
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      padding: 3px 7px;
      border-radius: 999px;
      background: #2b2d31;
      color: var(--muted);
    }}
    .status.COMPLETED {{ color: var(--good); }}
    .status.DECLINED {{ color: var(--bad); }}
    .status.UNASSIGNED {{ color: var(--warn); }}
    .status.ACCEPTED_INCOMPLETE {{ color: var(--blue); }}
    .main {{
      min-width: 0;
      padding: 34px 36px;
    }}
    .offer-pane {{
      border-right: 1px solid #2b2d31;
      background: #111214;
      min-height: 100vh;
      padding: 24px 20px;
      overflow: auto;
    }}
    .offer-pane-head {{
      margin-bottom: 14px;
    }}
    .offer-pane-head h2 {{
      margin: 2px 0 4px;
      font-size: 19px;
      line-height: 1.25;
    }}
    .offer-pane-head div {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .offer-shot-frame {{
      background: #070808;
      border: 1px solid #303237;
      border-radius: 8px;
      padding: 8px;
    }}
    .offer-shot {{
      display: block;
      width: 100%;
      height: auto;
      border-radius: 6px;
    }}
    .detail {{
      max-width: 980px;
      margin: 0 auto;
    }}
    .topline {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-start;
    }}
    .back {{
      color: var(--muted);
      font-size: 34px;
      line-height: 1;
      margin-bottom: 22px;
    }}
    .title {{
      margin: 0;
      font-size: clamp(32px, 4vw, 52px);
      line-height: 1.02;
      letter-spacing: 0;
    }}
    .subtitle {{
      color: var(--muted);
      margin-top: 10px;
      font-size: 18px;
      font-weight: 700;
    }}
    .subtitle .hot {{
      color: var(--accent);
    }}
    .order-structure {{
      margin-top: 18px;
      display: grid;
      gap: 6px;
      color: #dfdfdc;
      font-size: 18px;
      line-height: 1.35;
    }}
    .order-structure span {{
      color: var(--muted);
      font-weight: 800;
      display: inline-block;
      min-width: 150px;
    }}
    .offer-box {{
      background: var(--panel-2);
      border: 1px solid #303237;
      border-radius: 8px;
      padding: 18px 20px;
      margin: 28px 0 36px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 18px;
    }}
    .offer-box label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 800;
      margin-bottom: 4px;
    }}
    .offer-box strong {{
      font-size: 20px;
    }}
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
      padding: 0 0 34px 0;
    }}
    .event:last-child {{
      padding-bottom: 0;
    }}
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
    .event.completed:before {{ background: var(--good); }}
    .event.new_offer:before {{ background: var(--accent); }}
    .event.declined:before, .event.unassigned:before {{ background: var(--warn); }}
    .event-head {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
      align-items: baseline;
    }}
    .stage {{
      color: #d8d8d5;
      font-size: 21px;
      font-weight: 760;
    }}
    .time {{
      font-size: 28px;
      font-weight: 900;
    }}
    .detail-grid {{
      margin-top: 9px;
      display: block;
      color: var(--muted);
      font-size: 14px;
    }}
    .text-preview {{
      color: #d5d5d2;
      line-height: 1.45;
    }}
    .chips {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 12px;
    }}
    .chip {{
      border: 1px solid #3b3d42;
      color: #d4d4d1;
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
    }}
    .notes {{
      margin-top: 28px;
      color: var(--muted);
      border-top: 1px solid #303237;
      padding-top: 18px;
      font-size: 14px;
    }}
    .empty {{
      color: var(--muted);
      padding: 28px;
      text-align: center;
    }}
    @media (max-width: 900px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{ min-height: auto; max-height: 48vh; border-right: 0; border-bottom: 1px solid #2b2d31; }}
      .offer-pane {{ min-height: auto; border-right: 0; border-bottom: 1px solid #2b2d31; }}
      .main {{ padding: 24px 18px 42px; }}
      .offer-box {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .detail-grid {{ grid-template-columns: 1fr; }}
      .title {{ font-size: 34px; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="side-head">
        <div class="eyebrow">Accessibility Timeline</div>
        <h1>DoorDash Orders</h1>
        <div class="stats">
          <div class="stat"><strong id="statOrders">0</strong><span>orders</span></div>
          <div class="stat"><strong id="statEvents">0</strong><span>events</span></div>
          <div class="stat"><strong id="statDone">0</strong><span>completed</span></div>
        </div>
        <div class="filters">
          <input id="search" placeholder="Search orders">
          <select id="statusFilter">
            <option value="ALL">All status</option>
            <option value="COMPLETED">Completed</option>
            <option value="DECLINED">Declined</option>
            <option value="UNASSIGNED">Unassigned</option>
            <option value="ACCEPTED_INCOMPLETE">Incomplete</option>
          </select>
        </div>
      </div>
      <div class="order-list" id="orderList"></div>
    </aside>
    <section class="offer-pane" id="offerPane"></section>
    <main class="main">
      <section class="detail" id="detail"></section>
    </main>
  </div>
  <script>
    const DATA = {data_json};
    const stageLabels = {{
      NEW_OFFER: "New offer",
      DECLINE_CONFIRMATION: "Decline confirmation",
      ACCEPTED: "Offer accepted",
      ARRIVED: "Arrived at store",
      PICKED_UP: "Picked up",
      COMPLETED: "Completed",
      UNASSIGNED: "Unassigned"
    }};
    const stageClass = (stage) => stage.toLowerCase();
    let selectedId = DATA.orders[0]?.assignmentId;

    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({{
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\\"": "&quot;", "'": "&#39;"
    }}[ch]));

    function restaurantTitle(order) {{
      if (!order.restaurants || order.restaurants.length === 0) return "Unknown merchant";
      if (order.restaurants.length === 1) return order.restaurants[0];
      return `${{order.restaurants[0]}} +${{order.restaurants.length - 1}} other`;
    }}

    function orderStructure(order) {{
      const pickups = order.restaurants || [];
      const rows = pickups.map((name, index) => `<div><span>Pickup${{index + 1}}:</span>${{esc(name)}}</div>`);
      rows.push(`<div><span>Customer dropoff:</span>same customer</div>`);
      return rows.join("");
    }}

    function renderStats() {{
      document.getElementById("statOrders").textContent = DATA.summary.orders;
      document.getElementById("statEvents").textContent = DATA.summary.events;
      document.getElementById("statDone").textContent = DATA.summary.statusCounts.COMPLETED || 0;
    }}

    function filteredOrders() {{
      const query = document.getElementById("search").value.trim().toLowerCase();
      const status = document.getElementById("statusFilter").value;
      return DATA.orders.filter((order) => {{
        const text = [restaurantTitle(order), order.shortId, order.expectedPay, order.miles, order.status].join(" ").toLowerCase();
        return (status === "ALL" || order.status === status) && (!query || text.includes(query));
      }});
    }}

    function renderList() {{
      const list = document.getElementById("orderList");
      const orders = filteredOrders();
      if (!orders.some((order) => order.assignmentId === selectedId)) {{
        selectedId = orders[0]?.assignmentId;
      }}
      list.innerHTML = orders.map((order) => `
        <button class="order-button ${{order.assignmentId === selectedId ? "active" : ""}}" data-id="${{esc(order.assignmentId)}}">
          <div class="order-row">
            <div class="restaurant">${{esc(restaurantTitle(order))}}</div>
            <div class="pay">${{esc(order.expectedPay || "")}}</div>
          </div>
          <div class="meta">
            <span>${{esc(order.firstTime)}}-${{esc(order.lastTime)}} · ${{esc(order.miles || "")}}</span>
            <span class="status ${{esc(order.status)}}">${{esc(order.status.replaceAll("_", " "))}}</span>
          </div>
        </button>
      `).join("") || `<div class="empty">No matching orders</div>`;
      list.querySelectorAll("button[data-id]").forEach((button) => {{
        button.addEventListener("click", () => {{
          selectedId = button.dataset.id;
          renderList();
          renderOfferScreenshot();
          renderDetail();
        }});
      }});
    }}

    function renderOfferScreenshot() {{
      const order = DATA.orders.find((item) => item.assignmentId === selectedId);
      const pane = document.getElementById("offerPane");
      if (!order) {{
        pane.innerHTML = `<div class="empty">Select an order</div>`;
        return;
      }}
      pane.innerHTML = `
        <div class="offer-pane-head">
          <div>Offer screenshot</div>
          <h2>${{esc(restaurantTitle(order))}}</h2>
          <div>${{esc(order.expectedPay || "--")}} · ${{esc(order.miles || "--")}}</div>
        </div>
        <div class="offer-shot-frame">
          ${{order.offerScreenshot ? `<img class="offer-shot" src="${{esc(order.offerScreenshot)}}" alt="Offer screenshot for ${{esc(restaurantTitle(order))}}">` : `<div class="empty">No offer screenshot</div>`}}
        </div>
      `;
    }}

    function renderDetail() {{
      const order = DATA.orders.find((item) => item.assignmentId === selectedId);
      const detail = document.getElementById("detail");
      if (!order) {{
        detail.innerHTML = `<div class="empty">Select an order</div>`;
        return;
      }}
      const notes = order.notes.length ? `<div class="notes"><b>Flags</b><br>${{esc(order.notes.join(" · "))}}</div>` : "";
      detail.innerHTML = `
        <div class="back">←</div>
        <div class="topline">
          <div>
            <h2 class="title">${{esc(restaurantTitle(order))}}</h2>
            <div class="subtitle">${{esc(DATA.summary.date)}} · <span class="hot">${{esc(order.status.replaceAll("_", " "))}}</span></div>
            <div class="order-structure">${{orderStructure(order)}}</div>
          </div>
          <span class="status ${{esc(order.status)}}">${{esc(order.shortId)}}</span>
        </div>
        <div class="offer-box">
          <div><label>Offer</label><strong>${{esc(order.expectedPay || "--")}}</strong></div>
          <div><label>Miles</label><strong>${{esc(order.miles || "--")}}</strong></div>
          <div><label>Deliver by</label><strong>${{esc(order.deliverBy || "--")}}</strong></div>
          <div><label>Duration</label><strong>${{esc(order.durationMinutes ?? "--")}} min</strong></div>
        </div>
        <div class="timeline">
          ${{order.timeline.map((event) => renderEvent(event)).join("")}}
        </div>
        ${{notes}}
      `;
    }}

    function renderEvent(event) {{
      const buttons = [...new Set([...(event.buttons || []), ...(event.accessibilityButtons || [])])].slice(0, 10);
      const textPreview = (event.textPreview || []).slice(0, 12).join(" · ");
      return `
        <article class="event ${{stageClass(event.stage)}}">
          <div class="event-head">
            <div class="stage">${{esc(stageLabels[event.stage] || event.stage)}}</div>
            <div class="time">${{esc(event.time)}}</div>
          </div>
          <div class="detail-grid">
            <div class="text-preview">${{esc(textPreview)}}</div>
          </div>
          <div class="chips">
            ${{buttons.map((button) => `<span class="chip">${{esc(button)}}</span>`).join("")}}
            ${{event.dashTotal ? `<span class="chip">Dash total ${{esc(event.dashTotal)}}</span>` : ""}}
          </div>
        </article>
      `;
    }}

    document.getElementById("search").addEventListener("input", () => {{ renderList(); renderOfferScreenshot(); renderDetail(); }});
    document.getElementById("statusFilter").addEventListener("change", () => {{ renderList(); renderOfferScreenshot(); renderDetail(); }});
    renderStats();
    renderList();
    renderOfferScreenshot();
    renderDetail();
  </script>
</body>
</html>
"""


def main():
    payload = build_data()
    OUT.write_text(render_html(payload))
    print(f"Wrote {OUT}")
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
