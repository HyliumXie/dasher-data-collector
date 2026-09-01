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

A local first-order sample dashboard lives in:

```text
dashboard/
```

It currently focuses on the `2026-08-31` Nothing Bundt Cakes / Starbucks sample order and keeps the three-column review layout: order list, offer screenshot, and lifecycle timeline.

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
- `analysis/`
- `tools/`

Keep raw data local unless it has been intentionally redacted.

## Privacy

This app can capture DoorDash screen text, accessibility trees, earnings, merchant names, customer names, addresses, and screenshots. Use it only on devices/accounts where collection is permitted. Treat all exported data as confidential and redact sensitive data before sharing.
