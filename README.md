# dasher-data-collector

Android Accessibility collector for DoorDash Dasher delivery research.

The app records reduced, stage-focused snapshots from `com.doordash.driverapp` so the exported data can be replayed into offer timelines and later used to train an income-optimization model. The model input should come only from the first offer screen; later stages are collected as outcome labels and timing evidence.

## What It Collects

The collector watches DoorDash accessibility events and persists only useful state transitions:

- `NEW_OFFER`
- `DECLINE_CONFIRMATION`
- `ACCEPTED`
- `ARRIVED`
- `PICKED_UP`
- `COMPLETED`
- `UNASSIGNED`

It skips repeated navigation/map updates and duplicate content hashes. Screenshots are saved only for high-value review cases such as new offers, completion/pay pages, unassignment pages, and low-confidence attribution.

## Attribution Rules

Current production-candidate rules are deterministic and conservative:

- `assignmentId` is used as the offer identity when present on `NEW_OFFER`.
- Later stages do not depend on `assignmentId`, because today's dataset showed later-stage assignmentId coverage at `0%`.
- `NEW_OFFER` is detected from `Decline` + `Accept` or `Add to route` + pay + mileage + `Deliver by`.
- Seeing `Accept` or `Add to route` does not by itself mean the order was accepted.
- The collector keeps pending and active assignment state in memory, then attributes later stages by restaurant/task context when safe.
- `Add to route` creates an independent pending assignment and must not overwrite the original active order.
- `Directions` and `Continue` are supporting signals only, never standalone `PICKED_UP` classifiers.
- `This dash so far` alone is not completion evidence, but dash-total delta can be used later to recover pay when settlement pages are missing.
- Missing or ambiguous attribution should stay unknown instead of being forced onto the wrong order.

## Data Output

Snapshots are saved in app-private storage:

```text
files/dasher_data_collector/raw/<yyyyMMdd>/<HHmmss_SSS>/
```

Each saved transition may contain:

- `meta.json`: timestamp, event type, stage, outcome, confidence, timestamp source, assignment id, extracted offer fields, dash total, screenshot status.
- `visible_text.txt`: visible accessibility text for the saved transition.
- `accessibility_tree.json`: serialized accessibility node tree.
- `screenshot.png`: optional screenshot for review/debug cases.

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
