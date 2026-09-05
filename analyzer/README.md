# Analyzer

Analyzer is Gig Pilot's offline analysis component. It reads Accessibility trees and event records exported by Collector, extracts page-level signals, and creates structured outputs for rule iteration, human review, and future delivery-lifecycle reconstruction.

## Current Capabilities

- Read an exported .tar archive or an extracted directory.
- Process accessibility_tree.json, event.json, and screenshot status.
- Extract assignment IDs, payouts, mileage, delivery times, buttons, and merchant candidates.
- Apply strict rules for OFFER and ADD_TO_ROUTE_OFFER.
- Emit pickup, drop-off, payout, photo, unassign, and navigation candidate signals.
- Group nearby candidate pages into short review sessions.

Analyzer is currently a page-rule scanner, not a complete order-lifecycle engine. It does not determine whether an offer is worth accepting, and candidate labels should not be treated as ground truth.

## Usage

Analyze a TAR archive:

    python analyzer\accessibility_analyzer.py .\dasher_export.tar

Analyze an extracted directory and select an output directory:

    python analyzer\accessibility_analyzer.py .\data --output-dir .\analysis

Analyze only the first 100 records:

    python analyzer\accessibility_analyzer.py .\data --limit 100

Without --output-dir, results are written beside the input under <input-name>_analysis.

## Outputs

- records.jsonl: structured page-level records.
- screen_review.csv: the main table for reviewing page rules.
- offer_records.csv: every page identified as an offer.
- assignment_offers.csv: one representative offer per assignment.
- candidate_sessions.csv: all grouped candidate sessions.
- pickup_sessions.csv and related files: stage-specific candidate sessions.
- summary.json: counts by date, page type, candidate signal, screenshot, and assignment.

## Next Steps

Once enough human labels are available, Analyzer can add order attribution, lifecycle transitions, stacked-order handling, time and mileage features, earnings outcome attribution, and rule/model accuracy evaluation.
