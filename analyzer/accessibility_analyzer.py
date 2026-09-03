#!/usr/bin/env python3
"""Analyze DoorDash Accessibility collector exports.

This first local analyzer intentionally keeps classification conservative:
only OFFER and ADD_TO_ROUTE_OFFER are strong screen types. Other lifecycle
signals are emitted as candidate labels for review.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Iterator


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
MONEY_RE = re.compile(r"[+]?\$\s*\d+(?:\.\d{2})?\+?")
MILEAGE_RE = re.compile(r"(?:additional\s+)?\b\d+(?:\.\d+)?\s*mi\b", re.IGNORECASE)
DELIVER_BY_RE = re.compile(r"\bdeliver by\s+[0-9:]+\s*[ap]m\b", re.IGNORECASE)
DOORDASH_VIEW_ID_PREFIX = "com.doordash.driverapp:id/"
CANDIDATE_SESSION_GAP_SECONDS = 30
CANDIDATE_SESSION_LABELS = [
    "PICKUP_CANDIDATE",
    "DROPOFF_CANDIDATE",
    "ARRIVED_STORE_CANDIDATE",
    "CONFIRM_PICKUP_CANDIDATE",
    "COMPLETE_DELIVERY_CANDIDATE",
    "PAYOUT_CANDIDATE",
    "UNASSIGN_CANDIDATE",
    "PHOTO_CANDIDATE",
    "NAVIGATION_CANDIDATE",
]


@dataclass(frozen=True)
class ExportEntry:
    folder: str
    day: str
    event: dict[str, Any]
    tree: dict[str, Any]
    has_screenshot: bool


@dataclass(frozen=True)
class ExtractedScreen:
    folder: str
    day: str
    timestamp: str
    event_type_name: str
    assignment_id: str
    proposed_screen_type: str
    confidence: str
    candidate_labels: list[str]
    matched_rules: list[str]
    buttons: list[str]
    money_texts: list[str]
    mileage_texts: list[str]
    deliver_by_texts: list[str]
    pickup_names: list[str]
    top_visible_texts: list[str]
    has_screenshot: bool
    screenshot_status: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze DoorDash Accessibility raw exports.")
    parser.add_argument("input", help="Path to exported .tar file or extracted collector directory.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for analyzer outputs. Defaults to <input stem>_analysis next to input.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Analyze only the first N records.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    screens = []
    for index, entry in enumerate(iter_export_entries(input_path)):
        if args.limit is not None and index >= args.limit:
            break
        screens.append(analyze_entry(entry))

    write_records_jsonl(output_dir / "records.jsonl", screens)
    write_screen_review_csv(output_dir / "screen_review.csv", screens)
    write_offer_records_csv(output_dir / "offer_records.csv", screens)
    write_assignment_offers_csv(output_dir / "assignment_offers.csv", screens)
    session_summary = write_candidate_sessions(output_dir, screens)
    summary = build_summary(screens)
    summary["candidate_session_analysis"] = session_summary
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print(f"Analyzed {len(screens)} records")
    print(f"Wrote {output_dir}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def default_output_dir(input_path: Path) -> Path:
    stem = input_path.stem if input_path.suffix == ".tar" else input_path.name
    return input_path.parent / f"{stem}_analysis"


def iter_export_entries(input_path: Path) -> Iterator[ExportEntry]:
    if input_path.is_file() and input_path.suffix == ".tar":
        yield from iter_tar_entries(input_path)
        return
    if input_path.is_dir():
        yield from iter_directory_entries(input_path)
        return
    raise FileNotFoundError(f"Input is not a .tar file or directory: {input_path}")


def iter_tar_entries(tar_path: Path) -> Iterator[ExportEntry]:
    events: dict[str, dict[str, Any]] = {}
    screenshots: set[str] = set()

    with tarfile.open(tar_path, "r") as archive:
        for member in archive:
            if member.isdir():
                continue
            folder = parent_folder(member.name)
            if member.name.endswith("/event.json"):
                file_obj = archive.extractfile(member)
                if file_obj is not None:
                    events[folder] = json.loads(file_obj.read().decode("utf-8"))
            elif member.name.endswith("/screenshot.png"):
                screenshots.add(folder)

    with tarfile.open(tar_path, "r") as archive:
        for member in archive:
            if not member.name.endswith("/accessibility_tree.json"):
                continue
            folder = parent_folder(member.name)
            file_obj = archive.extractfile(member)
            if file_obj is None:
                continue
            tree = json.loads(file_obj.read().decode("utf-8"))
            yield ExportEntry(
                folder=folder,
                day=day_from_folder(folder),
                event=events.get(folder, {}),
                tree=tree,
                has_screenshot=folder in screenshots,
            )


def iter_directory_entries(root: Path) -> Iterator[ExportEntry]:
    for tree_path in sorted(root.rglob("accessibility_tree.json")):
        folder_path = tree_path.parent
        folder = str(folder_path.relative_to(root))
        event_path = folder_path / "event.json"
        event = json.loads(event_path.read_text()) if event_path.exists() else {}
        yield ExportEntry(
            folder=folder,
            day=day_from_folder(folder),
            event=event,
            tree=json.loads(tree_path.read_text()),
            has_screenshot=(folder_path / "screenshot.png").exists(),
        )


def analyze_entry(entry: ExportEntry) -> ExtractedScreen:
    nodes = flatten_nodes(entry.tree)
    visible_texts = unique_preserve_order(
        value
        for node in nodes
        for value in (node.get("text"), node.get("contentDescription"), node.get("viewIdResourceName"))
        if isinstance(value, str) and value.strip()
    )
    human_texts = [text for text in visible_texts if not text.startswith(DOORDASH_VIEW_ID_PREFIX)]
    lower_set = {text.lower() for text in human_texts}
    joined_text = "\n".join(human_texts)
    lower_joined = joined_text.lower()

    assignment_id = entry.event.get("assignmentId")
    if not isinstance(assignment_id, str) or not assignment_id:
        assignment_id = find_first(UUID_RE, joined_text)

    buttons = extract_buttons(nodes)
    money_texts = unique_preserve_order(
        match.group(0) for text in human_texts for match in MONEY_RE.finditer(text)
    )
    mileage_texts = unique_preserve_order(
        match.group(0) for text in human_texts for match in MILEAGE_RE.finditer(text)
    )
    deliver_by_texts = unique_preserve_order(match.group(0) for match in DELIVER_BY_RE.finditer(joined_text))
    pickup_names = extract_pickup_names(human_texts)

    proposed_type, confidence, strong_rules = classify_strong(
        assignment_id=assignment_id,
        lower_set=lower_set,
        lower_joined=lower_joined,
        money_texts=money_texts,
        mileage_texts=mileage_texts,
    )
    candidate_labels, candidate_rules = classify_candidates(lower_joined)

    return ExtractedScreen(
        folder=entry.folder,
        day=entry.day,
        timestamp=str(entry.event.get("timestamp", "")),
        event_type_name=str(entry.event.get("eventTypeName", "")),
        assignment_id=assignment_id or "",
        proposed_screen_type=proposed_type,
        confidence=confidence,
        candidate_labels=candidate_labels,
        matched_rules=strong_rules + candidate_rules,
        buttons=buttons,
        money_texts=money_texts,
        mileage_texts=mileage_texts,
        deliver_by_texts=deliver_by_texts,
        pickup_names=pickup_names,
        top_visible_texts=human_texts[:40],
        has_screenshot=entry.has_screenshot,
        screenshot_status=str(entry.event.get("screenshotStatus", "")),
    )


def classify_strong(
    assignment_id: str,
    lower_set: set[str],
    lower_joined: str,
    money_texts: list[str],
    mileage_texts: list[str],
) -> tuple[str, str, list[str]]:
    has_assignment = bool(assignment_id)
    has_decline = "decline" in lower_set
    has_accept = "accept" in lower_set
    has_add_to_route = "add to route" in lower_set
    has_money = bool(money_texts)
    has_mileage = bool(mileage_texts)
    has_additional_mileage = any("additional" in text.lower() for text in mileage_texts)

    if has_assignment and has_add_to_route and has_decline and has_money and has_additional_mileage:
        return (
            "ADD_TO_ROUTE_OFFER",
            "strong",
            ["assignmentId + Add to route + Decline + money + Additional mi"],
        )
    if has_assignment and has_accept and has_decline and has_money and has_mileage:
        return ("OFFER", "strong", ["assignmentId + Accept + Decline + money + mi"])
    if "decline" in lower_joined and ("accept" in lower_joined or "add to route" in lower_joined):
        return ("OTHER", "low", ["offer-like text without strong rule"])
    return ("OTHER", "low", [])


def classify_candidates(lower_joined: str) -> tuple[list[str], list[str]]:
    rules = [
        ("ARRIVED_STORE_CANDIDATE", ["arrived at store", "arrive at store"]),
        ("COMPLETE_DELIVERY_CANDIDATE", ["complete delivery", "complete dropoff"]),
        ("UNASSIGN_CANDIDATE", ["unassign", "unassigned"]),
    ]
    labels: list[str] = []
    matched: list[str] = []
    pickup_rule = pickup_candidate_rule(lower_joined)
    if pickup_rule:
        labels.append("PICKUP_CANDIDATE")
        matched.append(f"PICKUP_CANDIDATE: {pickup_rule}")

    dropoff_rule = dropoff_candidate_rule(lower_joined)
    if dropoff_rule:
        labels.append("DROPOFF_CANDIDATE")
        matched.append(f"DROPOFF_CANDIDATE: {dropoff_rule}")

    confirm_pickup_rule = confirm_pickup_candidate_rule(lower_joined)
    if confirm_pickup_rule:
        labels.append("CONFIRM_PICKUP_CANDIDATE")
        matched.append(f"CONFIRM_PICKUP_CANDIDATE: {confirm_pickup_rule}")

    photo_rule = photo_candidate_rule(lower_joined)
    if photo_rule:
        labels.append("PHOTO_CANDIDATE")
        matched.append(f"PHOTO_CANDIDATE: {photo_rule}")

    navigation_rule = navigation_candidate_rule(lower_joined)
    if navigation_rule:
        labels.append("NAVIGATION_CANDIDATE")
        matched.append(f"NAVIGATION_CANDIDATE: {navigation_rule}")

    for label, keywords in rules:
        hits = [keyword for keyword in keywords if keyword in lower_joined]
        if hits:
            labels.append(label)
            matched.append(f"{label}: {', '.join(hits)}")

    payout_rule = payout_candidate_rule(lower_joined)
    if payout_rule:
        labels.append("PAYOUT_CANDIDATE")
        matched.append(f"PAYOUT_CANDIDATE: {payout_rule}")
    return labels, matched


def pickup_candidate_rule(lower_joined: str) -> str:
    combinations = [
        ("Current dash + Pick up by", ("current dash", "pick up by")),
        ("Pickup from + Directions", ("pickup from", "directions")),
        ("Order for + Waiting for your order", ("order for", "waiting for your order")),
        ("Heading to + Pick up by", ("heading to", "pick up by")),
        ("Start pickup", ("start pickup",)),
        ("Continue with pickup", ("continue with pickup",)),
        ("Scan receipt", ("scan receipt",)),
    ]
    matches = [
        label
        for label, keywords in combinations
        if all(keyword in lower_joined for keyword in keywords)
    ]
    return ", ".join(matches)


def dropoff_candidate_rule(lower_joined: str) -> str:
    combinations = [
        ("Current dash + Delivery to", ("current dash", "delivery to")),
        ("Current dash + Drop off", ("current dash", "drop off")),
        ("Complete delivery", ("complete delivery",)),
        ("Complete dropoff", ("complete dropoff",)),
        ("Take photo + order", ("take photo", "order")),
        ("Leave at door", ("leave at door",)),
        ("Handed order to customer", ("handed order", "customer")),
        ("Navigate to customer address + drop off", ("navigate to the customer's address", "drop off")),
        ("Customer location warning", ("right location", "customer", "drop off")),
    ]
    matches = [
        label
        for label, keywords in combinations
        if all(keyword in lower_joined for keyword in keywords)
    ]
    return ", ".join(matches)


def confirm_pickup_candidate_rule(lower_joined: str) -> str:
    combinations = [
        ("Pickup steps + Confirm pickup", ("pickup steps", "confirm pickup")),
        ("Scan receipt + Confirm pickup", ("scan receipt", "confirm pickup")),
        ("Take receipt photo + Confirm pickup", ("take receipt photo", "confirm pickup")),
        ("Verify items + Confirm pickup", ("verify items", "confirm pickup")),
    ]
    matches = [
        label
        for label, keywords in combinations
        if all(keyword in lower_joined for keyword in keywords)
    ]
    return ", ".join(matches)


def photo_candidate_rule(lower_joined: str) -> str:
    combinations = [
        ("Take photo + Capture image", ("take photo", "capture image")),
        ("Drop-off location photo", ("take photo of drop-off location",)),
        ("No-contact delivery photo", ("no-contact delivery", "take a photo")),
        ("Uploading photo", ("uploading your photo",)),
        ("Retake + Done", ("retake", "done")),
        ("Capture order photo", ("capture the order", "capture image")),
        ("Capture receipt photo", ("capture receipt", "capture image")),
        ("Take receipt photo", ("take receipt photo",)),
    ]
    matches = [
        label
        for label, keywords in combinations
        if all(keyword in lower_joined for keyword in keywords)
    ]
    return ", ".join(matches)


def navigation_candidate_rule(lower_joined: str) -> str:
    combinations = [
        ("Heading to + Exit", ("heading to", "exit")),
        ("Heading to + Avoid tolls", ("heading to", "avoid tolls")),
        ("Deliver to + Exit", ("deliver to", "exit")),
        ("Deliver to + Avoid tolls", ("deliver to", "avoid tolls")),
    ]
    matches = [
        label
        for label, keywords in combinations
        if all(keyword in lower_joined for keyword in keywords)
    ]
    return ", ".join(matches)


def payout_candidate_rule(lower_joined: str) -> str:
    if all(keyword in lower_joined for keyword in ("doordash pay", "customer tips")):
        return "DoorDash pay + Customer tips"
    if all(keyword in lower_joined for keyword in ("base pay", "customer tips")):
        return "Base pay + Customer tips"
    if all(keyword in lower_joined for keyword in ("this dash so far", "this offer", "continue dashing")):
        return "This dash so far + This offer + Continue dashing"
    if all(keyword in lower_joined for keyword in ("this dash", "this offer", "continue dashing")):
        return "This dash + This offer + Continue dashing"
    return ""


def flatten_nodes(root: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(node)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                visit(child)

    visit(root)
    return nodes


def extract_buttons(nodes: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for node in nodes:
        if not node.get("clickable"):
            continue
        for key in ("text", "contentDescription"):
            value = node.get(key)
            if isinstance(value, str) and value.strip() and not value.startswith(DOORDASH_VIEW_ID_PREFIX):
                labels.append(value.strip())
    return unique_preserve_order(labels)


def extract_pickup_names(human_texts: list[str]) -> list[str]:
    names: list[str] = []
    for index, text in enumerate(human_texts[:-1]):
        if text == "Pickup":
            candidate = human_texts[index + 1]
            if candidate not in {"Customer dropoff", "Accept", "Decline"}:
                names.append(candidate)
    return unique_preserve_order(names)


def write_records_jsonl(path: Path, screens: list[ExtractedScreen]) -> None:
    with path.open("w") as output:
        for screen in screens:
            output.write(json.dumps(screen.__dict__, ensure_ascii=False) + "\n")


def write_screen_review_csv(path: Path, screens: list[ExtractedScreen]) -> None:
    fieldnames = [
        "timestamp",
        "day",
        "folder",
        "assignment_id",
        "proposed_screen_type",
        "confidence",
        "candidate_labels",
        "matched_rules",
        "buttons",
        "money_texts",
        "mileage_texts",
        "deliver_by_texts",
        "pickup_names",
        "has_screenshot",
        "screenshot_status",
        "top_visible_texts",
    ]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for screen in screens:
            writer.writerow(csv_row(screen, fieldnames))


def write_offer_records_csv(path: Path, screens: list[ExtractedScreen]) -> None:
    fieldnames = [
        "timestamp",
        "day",
        "folder",
        "assignment_id",
        "offer_type",
        "amount",
        "mileage",
        "deliver_by",
        "pickup_names",
        "has_screenshot",
        "screenshot_status",
    ]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for screen in screens:
            if screen.proposed_screen_type not in {"OFFER", "ADD_TO_ROUTE_OFFER"}:
                continue
            writer.writerow(
                {
                    "timestamp": screen.timestamp,
                    "day": screen.day,
                    "folder": screen.folder,
                    "assignment_id": screen.assignment_id,
                    "offer_type": screen.proposed_screen_type,
                    "amount": first_or_empty(screen.money_texts),
                    "mileage": first_or_empty(screen.mileage_texts),
                    "deliver_by": first_or_empty(screen.deliver_by_texts),
                    "pickup_names": " / ".join(screen.pickup_names),
                    "has_screenshot": screen.has_screenshot,
                    "screenshot_status": screen.screenshot_status,
                }
            )


def write_assignment_offers_csv(path: Path, screens: list[ExtractedScreen]) -> None:
    first_by_assignment: dict[str, ExtractedScreen] = {}
    for screen in screens:
        if screen.proposed_screen_type not in {"OFFER", "ADD_TO_ROUTE_OFFER"}:
            continue
        if not screen.assignment_id:
            continue
        existing = first_by_assignment.get(screen.assignment_id)
        if existing is None or (screen.has_screenshot and not existing.has_screenshot):
            first_by_assignment[screen.assignment_id] = screen

    fieldnames = [
        "timestamp",
        "day",
        "folder",
        "assignment_id",
        "offer_type",
        "amount",
        "mileage",
        "deliver_by",
        "pickup_names",
        "has_screenshot",
        "screenshot_status",
    ]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for screen in sorted(first_by_assignment.values(), key=lambda item: item.timestamp):
            writer.writerow(
                {
                    "timestamp": screen.timestamp,
                    "day": screen.day,
                    "folder": screen.folder,
                    "assignment_id": screen.assignment_id,
                    "offer_type": screen.proposed_screen_type,
                    "amount": first_or_empty(screen.money_texts),
                    "mileage": first_or_empty(screen.mileage_texts),
                    "deliver_by": first_or_empty(screen.deliver_by_texts),
                    "pickup_names": " / ".join(screen.pickup_names),
                    "has_screenshot": screen.has_screenshot,
                    "screenshot_status": screen.screenshot_status,
                }
            )


def write_candidate_sessions(output_dir: Path, screens: list[ExtractedScreen]) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}

    for label in CANDIDATE_SESSION_LABELS:
        label_screens = [
            screen for screen in screens if label in screen.candidate_labels and screen.timestamp
        ]
        label_screens.sort(key=lambda screen: (screen.day, screen.timestamp))
        sessions = build_candidate_sessions(label_screens)
        rows = [
            candidate_session_row(label, index + 1, session)
            for index, session in enumerate(sessions)
        ]
        all_rows.extend(rows)
        summaries[label] = candidate_session_summary(label, label_screens, sessions, rows)

        filename = f"{label.lower().replace('_candidate', '')}_sessions.csv"
        write_session_rows(output_dir / filename, rows)

        if label == "PICKUP_CANDIDATE":
            write_session_rows(output_dir / "pickup_sessions.csv", rows)

    all_rows.sort(key=lambda row: (row["day"], row["start_time"], row["candidate_label"]))
    write_session_rows(output_dir / "candidate_sessions.csv", all_rows)

    return {
        "gap_seconds": CANDIDATE_SESSION_GAP_SECONDS,
        "total_sessions": len(all_rows),
        "by_label": summaries,
    }


def build_candidate_sessions(screens: list[ExtractedScreen]) -> list[list[ExtractedScreen]]:
    sessions: list[list[ExtractedScreen]] = []
    current_session: list[ExtractedScreen] = []
    for screen in screens:
        if not current_session:
            current_session = [screen]
            continue

        previous = current_session[-1]
        same_day = screen.day == previous.day
        gap_seconds = timestamp_gap_seconds(previous.timestamp, screen.timestamp) if same_day else None
        if same_day and gap_seconds is not None and gap_seconds <= CANDIDATE_SESSION_GAP_SECONDS:
            current_session.append(screen)
        else:
            sessions.append(current_session)
            current_session = [screen]

    if current_session:
        sessions.append(current_session)
    return sessions


def candidate_session_row(
    candidate_label: str,
    session_number: int,
    session: list[ExtractedScreen],
) -> dict[str, Any]:
    labels = {label for screen in session for label in screen.candidate_labels}
    merchant_candidates = session_merchant_candidates(session)
    customer_candidates = session_customer_candidates(session)
    sample_screen = representative_candidate_sample(candidate_label, session)
    start_time = session[0].timestamp
    end_time = session[-1].timestamp

    return {
        "session_id": f"{session_prefix(candidate_label)}_s{session_number:04d}",
        "candidate_label": candidate_label,
        "day": session[0].day,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": f"{timestamp_gap_seconds(start_time, end_time):.3f}",
        "record_count": len(session),
        "first_folder": session[0].folder,
        "last_folder": session[-1].folder,
        "merchant_candidates": " | ".join(merchant_candidates[:20]),
        "customer_candidates": " | ".join(customer_candidates[:20]),
        "has_pickup_candidate": "PICKUP_CANDIDATE" in labels,
        "has_arrived_store_candidate": "ARRIVED_STORE_CANDIDATE" in labels,
        "has_confirm_pickup_candidate": "CONFIRM_PICKUP_CANDIDATE" in labels,
        "has_navigation_candidate": "NAVIGATION_CANDIDATE" in labels,
        "has_dropoff_candidate": "DROPOFF_CANDIDATE" in labels,
        "has_complete_delivery_candidate": "COMPLETE_DELIVERY_CANDIDATE" in labels,
        "has_payout_candidate": "PAYOUT_CANDIDATE" in labels,
        "has_unassign_candidate": "UNASSIGN_CANDIDATE" in labels,
        "has_photo_candidate": "PHOTO_CANDIDATE" in labels,
        "sample_visible_text": " | ".join(sample_screen.top_visible_texts[:60]),
        "trigger_rules": " | ".join(session_trigger_rules(candidate_label, session)),
    }


def write_session_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "session_id",
        "candidate_label",
        "day",
        "start_time",
        "end_time",
        "duration_seconds",
        "record_count",
        "first_folder",
        "last_folder",
        "merchant_candidates",
        "customer_candidates",
        "has_pickup_candidate",
        "has_arrived_store_candidate",
        "has_confirm_pickup_candidate",
        "has_navigation_candidate",
        "has_dropoff_candidate",
        "has_complete_delivery_candidate",
        "has_payout_candidate",
        "has_unassign_candidate",
        "has_photo_candidate",
        "trigger_rules",
        "sample_visible_text",
    ]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def candidate_session_summary(
    label: str,
    screens: list[ExtractedScreen],
    sessions: list[list[ExtractedScreen]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    record_counts = [len(session) for session in sessions]
    durations = [float(row["duration_seconds"]) for row in rows]
    trigger_counter = Counter(
        rule
        for screen in screens
        for rule in screen.matched_rules
        if rule.startswith(f"{label}:")
    )

    return {
        "records": len(screens),
        "sessions": len(sessions),
        "sessions_by_day": dict(Counter(session[0].day for session in sessions if session)),
        "records_per_session": numeric_summary(record_counts),
        "duration_per_session_seconds": numeric_summary(durations),
        "overlap_session_counts": {
            "pickup": sum(row["has_pickup_candidate"] for row in rows),
            "arrived_store": sum(row["has_arrived_store_candidate"] for row in rows),
            "confirm_pickup": sum(row["has_confirm_pickup_candidate"] for row in rows),
            "navigation": sum(row["has_navigation_candidate"] for row in rows),
            "dropoff": sum(row["has_dropoff_candidate"] for row in rows),
            "complete_delivery": sum(row["has_complete_delivery_candidate"] for row in rows),
            "payout": sum(row["has_payout_candidate"] for row in rows),
            "unassign": sum(row["has_unassign_candidate"] for row in rows),
            "photo": sum(row["has_photo_candidate"] for row in rows),
        },
        "top_trigger_rules": trigger_counter.most_common(20),
    }


def representative_candidate_sample(
    candidate_label: str,
    session: list[ExtractedScreen],
) -> ExtractedScreen:
    phrase_map = {
        "PICKUP_CANDIDATE": ("pickup from", "current dash", "order for", "heading to", "start pickup"),
        "DROPOFF_CANDIDATE": ("delivery to", "deliver to", "complete delivery", "take photo", "leave at door"),
        "ARRIVED_STORE_CANDIDATE": ("arrived at store", "pickup from"),
        "CONFIRM_PICKUP_CANDIDATE": ("confirm pickup", "pickup steps", "verify items", "scan receipt"),
        "COMPLETE_DELIVERY_CANDIDATE": ("complete delivery", "complete delivery steps"),
        "PAYOUT_CANDIDATE": ("doordash pay", "customer tips", "this offer", "continue dashing"),
        "UNASSIGN_CANDIDATE": ("unassign", "confirm your unassign", "select an issue"),
        "PHOTO_CANDIDATE": ("take photo", "capture image", "drop-off location", "receipt photo"),
        "NAVIGATION_CANDIDATE": ("heading to", "deliver to", "avoid tolls", "exit"),
    }
    preferred_phrases = phrase_map.get(candidate_label, ())
    for screen in session:
        text = "\n".join(screen.top_visible_texts).lower()
        if any(phrase in text for phrase in preferred_phrases):
            return screen
    return session[len(session) // 2]


def session_trigger_rules(candidate_label: str, session: list[ExtractedScreen]) -> list[str]:
    return unique_preserve_order(
        rule
        for screen in session
        for rule in screen.matched_rules
        if rule.startswith(f"{candidate_label}:")
    )


def session_prefix(candidate_label: str) -> str:
    return candidate_label.lower().replace("_candidate", "")


def write_pickup_sessions_csv(path: Path, screens: list[ExtractedScreen]) -> dict[str, Any]:
    pickup_screens = [
        screen for screen in screens if "PICKUP_CANDIDATE" in screen.candidate_labels and screen.timestamp
    ]
    pickup_screens.sort(key=lambda screen: (screen.day, screen.timestamp))

    sessions: list[list[ExtractedScreen]] = []
    current_session: list[ExtractedScreen] = []
    for screen in pickup_screens:
        if not current_session:
            current_session = [screen]
            continue

        previous = current_session[-1]
        same_day = screen.day == previous.day
        gap_seconds = timestamp_gap_seconds(previous.timestamp, screen.timestamp) if same_day else None
        if same_day and gap_seconds is not None and gap_seconds <= PICKUP_SESSION_GAP_SECONDS:
            current_session.append(screen)
        else:
            sessions.append(current_session)
            current_session = [screen]

    if current_session:
        sessions.append(current_session)

    fieldnames = [
        "session_id",
        "day",
        "start_time",
        "end_time",
        "duration_seconds",
        "record_count",
        "first_folder",
        "last_folder",
        "merchant_candidates",
        "customer_candidates",
        "has_arrived_store_candidate",
        "has_confirm_pickup_candidate",
        "has_navigation_candidate",
        "has_dropoff_candidate",
        "sample_visible_text",
    ]

    rows = [pickup_session_row(index + 1, session) for index, session in enumerate(sessions)]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return pickup_session_summary(pickup_screens, sessions, rows)


def pickup_session_row(session_number: int, session: list[ExtractedScreen]) -> dict[str, Any]:
    labels = {label for screen in session for label in screen.candidate_labels}
    merchant_candidates = session_merchant_candidates(session)
    customer_candidates = session_customer_candidates(session)
    sample_screen = representative_pickup_sample(session)
    start_time = session[0].timestamp
    end_time = session[-1].timestamp

    return {
        "session_id": f"pickup_s{session_number:04d}",
        "day": session[0].day,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": f"{timestamp_gap_seconds(start_time, end_time):.3f}",
        "record_count": len(session),
        "first_folder": session[0].folder,
        "last_folder": session[-1].folder,
        "merchant_candidates": " | ".join(merchant_candidates[:20]),
        "customer_candidates": " | ".join(customer_candidates[:20]),
        "has_arrived_store_candidate": "ARRIVED_STORE_CANDIDATE" in labels,
        "has_confirm_pickup_candidate": "CONFIRM_PICKUP_CANDIDATE" in labels,
        "has_navigation_candidate": "NAVIGATION_CANDIDATE" in labels,
        "has_dropoff_candidate": "DROPOFF_CANDIDATE" in labels,
        "sample_visible_text": " | ".join(sample_screen.top_visible_texts[:60]),
    }


def pickup_session_summary(
    pickup_screens: list[ExtractedScreen],
    sessions: list[list[ExtractedScreen]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    record_counts = [len(session) for session in sessions]
    durations = [float(row["duration_seconds"]) for row in rows]
    merchant_counter = Counter(
        merchant
        for row in rows
        for merchant in split_pipe_values(str(row["merchant_candidates"]))
    )
    trigger_counter = Counter(
        rule
        for screen in pickup_screens
        for rule in screen.matched_rules
        if rule.startswith("PICKUP_CANDIDATE:")
    )

    return {
        "pickup_candidate_records": len(pickup_screens),
        "pickup_sessions": len(sessions),
        "sessions_by_day": dict(Counter(session[0].day for session in sessions if session)),
        "records_per_session": numeric_summary(record_counts),
        "duration_per_session_seconds": numeric_summary(durations),
        "overlap_session_counts": {
            "arrived_store": sum(row["has_arrived_store_candidate"] for row in rows),
            "confirm_pickup": sum(row["has_confirm_pickup_candidate"] for row in rows),
            "navigation": sum(row["has_navigation_candidate"] for row in rows),
            "dropoff": sum(row["has_dropoff_candidate"] for row in rows),
        },
        "top_merchant_candidates": merchant_counter.most_common(30),
        "pickup_trigger_combos": trigger_counter.most_common(),
    }


def representative_pickup_sample(session: list[ExtractedScreen]) -> ExtractedScreen:
    preferred_phrases = (
        "pickup from",
        "current dash",
        "order for",
        "heading to",
        "start pickup",
        "continue with pickup",
    )
    for screen in session:
        text = "\n".join(screen.top_visible_texts).lower()
        if screen.proposed_screen_type == "OTHER" and any(phrase in text for phrase in preferred_phrases):
            return screen
    return session[len(session) // 2]


def session_merchant_candidates(session: list[ExtractedScreen]) -> list[str]:
    names: list[str] = []
    for screen in session:
        names.extend(screen.pickup_names)
        texts = screen.top_visible_texts
        for index, text in enumerate(texts[:-1]):
            if text.lower() in {"pickup", "pickup from"}:
                names.append(texts[index + 1])
        for text in texts:
            if text.lower().startswith("heading to "):
                names.append(text[11:])
    return unique_preserve_order(normalize_candidate_name(name) for name in names)


def session_customer_candidates(session: list[ExtractedScreen]) -> list[str]:
    names: list[str] = []
    for screen in session:
        texts = screen.top_visible_texts
        for index, text in enumerate(texts[:-1]):
            if text.lower() in {"order for", "delivery for", "delivery to"}:
                names.append(texts[index + 1])
    return unique_preserve_order(normalize_candidate_name(name) for name in names)


def build_summary(screens: list[ExtractedScreen]) -> dict[str, Any]:
    by_day: dict[str, dict[str, Any]] = {}
    for day, day_screens in group_by_day(screens).items():
        by_day[day] = {
            "records": len(day_screens),
            "first_timestamp": min((screen.timestamp for screen in day_screens if screen.timestamp), default=""),
            "last_timestamp": max((screen.timestamp for screen in day_screens if screen.timestamp), default=""),
            "screen_types": dict(Counter(screen.proposed_screen_type for screen in day_screens)),
            "candidate_labels": dict(
                Counter(label for screen in day_screens for label in screen.candidate_labels)
            ),
            "unique_assignment_ids": len({screen.assignment_id for screen in day_screens if screen.assignment_id}),
            "screenshots": sum(1 for screen in day_screens if screen.has_screenshot),
        }

    return {
        "records": len(screens),
        "screen_types": dict(Counter(screen.proposed_screen_type for screen in screens)),
        "candidate_labels": dict(Counter(label for screen in screens for label in screen.candidate_labels)),
        "unique_assignment_ids": len({screen.assignment_id for screen in screens if screen.assignment_id}),
        "screenshots": sum(1 for screen in screens if screen.has_screenshot),
        "days": by_day,
    }


def csv_row(screen: ExtractedScreen, fieldnames: list[str]) -> dict[str, str]:
    raw = screen.__dict__
    row: dict[str, str] = {}
    for field in fieldnames:
        value = raw[field]
        if isinstance(value, list):
            row[field] = " | ".join(str(item) for item in value)
        else:
            row[field] = str(value)
    return row


def group_by_day(screens: list[ExtractedScreen]) -> dict[str, list[ExtractedScreen]]:
    grouped: dict[str, list[ExtractedScreen]] = defaultdict(list)
    for screen in screens:
        grouped[screen.day].append(screen)
    return dict(grouped)


def parent_folder(path: str) -> str:
    return path.rsplit("/", 1)[0]


def day_from_folder(folder: str) -> str:
    if "/raw/" not in folder:
        return ""
    return folder.split("/raw/", 1)[1].split("/", 1)[0]


def find_first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0) if match else ""


def first_or_empty(values: list[str]) -> str:
    return values[0] if values else ""


def timestamp_gap_seconds(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def numeric_summary(values: list[int] | list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0, "median": 0, "mean": 0, "max": 0}
    return {
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
    }


def split_pipe_values(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


def normalize_candidate_name(value: str) -> str:
    normalized = " ".join(value.split()).strip(" .,:;")
    if normalized.lower() in {
        "pickup",
        "pickup from",
        "pick up by",
        "customer dropoff",
        "accept",
        "decline",
        "directions",
        "arrived at store",
        "confirm pickup",
        "current dash",
        "safety",
        "help",
        "customer",
    }:
        return ""
    return normalized


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


if __name__ == "__main__":
    main()
