# dasher-data-collector

Android Accessibility collector for DoorDash Dasher delivery research.

The app records raw, event-driven Accessibility snapshots from `com.doordash.driverapp`. Phone-side collection intentionally stays simple: it preserves original screen evidence, while order reconstruction, stage attribution, batch/add-to-route handling, and model-label generation happen offline after export.

## What It Collects

The collector watches DoorDash Accessibility events and saves the current raw node tree for each event. It does not classify screens into delivery stages on the phone.

Screenshots are captured only when a new `assignmentId` appears on a new-offer screen. This gives one visual audit image for the offer decision while keeping the rest of collection Accessibility-first.

## Phone-Side Rules

- Collection is event-driven only.
- Every saved event contains the raw `accessibility_tree.json`.
- Every saved event contains a small `event.json` index with timestamp, Android event type, node count, content hash, and detected `assignmentId` when visible.
- No `meta.json` or `visible_text.txt` files are produced by the current collector.
- No stage attribution, completion detection, lateness analysis, or rating analysis happens on the phone.
- New-offer detection is used only to decide whether to capture a one-time offer screenshot for a newly seen `assignmentId`.

## Data Output

Snapshots are saved in app-private storage:

```text
files/dasher_data_collector/raw/<yyyyMMdd>/<HHmmss_SSS>/
```

Each saved transition may contain:

- `accessibility_tree.json`: serialized accessibility node tree.
- `event.json`: timestamp, Android event type, node count, content hash, detected assignment id, and screenshot status.
- `screenshot.png`: only for the first new-offer screen for a newly seen assignment id.

## Dashboard

A local candidate-session review dashboard lives in:

```text
dashboard/
```

It reads the local analyzer output from `dasher_exports/dasher_20260902_analysis_v1` and keeps a three-column review layout: session list, rule/count context, and a vertical timeline-style session detail view. The dashboard intentionally reviews candidate sessions only; it does not attribute sessions to assignments or orders.

## Local Analyzer

The first local analyzer lives in:

```text
analyzer/accessibility_analyzer.py
```

Run it against a phone export tarball or an extracted export directory:

```sh
python3 analyzer/accessibility_analyzer.py dasher_exports/dasher_20260902_all_phone_data.tar \
  --output-dir dasher_exports/dasher_20260902_analysis_v1
```

The analyzer uses conservative screen attribution:

- Strong screen types: `OFFER`, `ADD_TO_ROUTE_OFFER`.
- Lifecycle signals such as pickup, arrived store, confirm pickup, dropoff, complete delivery, payout, unassign, photo, and navigation are emitted only as candidate labels.
- `PICKUP_CANDIDATE` uses combination signals such as `Current dash + Pick up by`, `Pickup from + Directions`, `Order for + Waiting for your order`, `Heading to + Pick up by`, `Start pickup`, `Continue with pickup`, and `Scan receipt`.
- `DROPOFF_CANDIDATE` uses delivery-context combinations such as `Current dash + Delivery to`, `Complete delivery`, `Take photo + order`, `Leave at door`, `Handed order to customer`, and customer-location warnings.
- `CONFIRM_PICKUP_CANDIDATE` requires pickup confirmation context such as `Pickup steps + Confirm pickup`, `Scan receipt + Confirm pickup`, `Take receipt photo + Confirm pickup`, or `Verify items + Confirm pickup`.
- `PAYOUT_CANDIDATE` requires payout combinations such as `DoorDash pay + Customer tips`, `Base pay + Customer tips`, or `This dash so far + This offer + Continue dashing`.
- `NAVIGATION_CANDIDATE` is treated only as an auxiliary signal. It requires route-screen context such as `Heading to + Exit`, `Heading to + Avoid tolls`, `Deliver to + Exit`, or `Deliver to + Avoid tolls`.

Analyzer outputs:

- `screen_review.csv`: one row per raw Accessibility record for rule review.
- `offer_records.csv`: every record classified as `OFFER` or `ADD_TO_ROUTE_OFFER`.
- `assignment_offers.csv`: one offer row per unique assignment id, preferring the row with the offer screenshot.
- `pickup_sessions.csv`: session-level review table for `PICKUP_CANDIDATE`, grouped by day and 30-second candidate gaps.
- `records.jsonl`: structured per-record output for later timeline building.
- `summary.json`: counts by day, screen type, candidate label, assignment id, and screenshot availability.

## Build

```sh
./gradlew assembleDebug
```

Install on a connected Android device:

```sh
./gradlew installDebug
```

After installing, enable the accessibility service in Android Settings, then open DoorDash Dasher.

## Repository Hygiene

Local collector exports and replay artifacts are ignored by git because they can contain sensitive delivery, customer, merchant, location, and earnings data:

- `dasher_*/`
- `dasher_*.tar`
- `dasher_exports/`
- `analysis/`
- `tools/`

Keep raw data local unless it has been intentionally redacted.

## Privacy

This app can capture DoorDash screen text, accessibility trees, earnings, merchant names, customer names, addresses, and screenshots. Use it only on devices/accounts where collection is permitted. Treat all exported data as confidential and redact sensitive data before sharing.
