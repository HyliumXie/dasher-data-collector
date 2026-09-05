# Dashboard

Dashboard is Gig Pilot's local screenshot-labeling tool. It creates reliable delivery-lifecycle training labels from Collector records. It reads data/<date>/<timestamp>/ directly and never modifies raw collection files.

## Start the Server

From the repository root:

    python dashboard\build_dashboard.py

Open http://127.0.0.1:8765. To use a different address or port:

    python dashboard\build_dashboard.py --host 127.0.0.1 --port 9000

The service uses only the Python standard library. It listens on localhost by default; do not expose pages containing sensitive delivery data to a network or the internet.

## Labeling Workflow

- Filter pending, labeled, or screenshot-backed pages in the left panel.
- Review the screenshot, nearby timeline records, and Accessibility text in the center.
- Select a Stage and confidence level, then add notes or an order-association hint.
- Optionally reuse a label for records with the same contentHash.
- Use Left/Right Arrow to navigate and Ctrl+S or Command+S to save.

Confidence values: CERTAIN, LIKELY, and UNSURE.

Supported stages:

    OFFER, ADD_TO_ROUTE_OFFER, DECLINED, HEADING_TO_PICKUP,
    ARRIVED_AT_STORE, WAITING_FOR_ORDER, CONFIRM_PICKUP, PICKED_UP,
    HEADING_TO_DROPOFF, ARRIVED_AT_CUSTOMER, DROP_OFF, PHOTO,
    COMPLETE_DELIVERY, COMPLETED, PAYOUT, UNASSIGN, DASH_HOME,
    OTHER, UNKNOWN

## Label Storage

Labels are appended to:

    annotations/page_labels.jsonl

Both data/ and annotations/ are excluded by the repository's .gitignore. Labels may still contain customer, merchant, or order information and must not be shared without redaction.
