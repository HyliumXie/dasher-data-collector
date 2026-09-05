# Gig Pilot

Delivery platforms use algorithms to assign orders, shape incentives, create time pressure, and decide how much information workers can see. Drivers are expected to react to those systems without being able to inspect how they affect earnings.

Gig Pilot puts data and decision-making tools back in the hands of delivery workers. If large platforms can use algorithms to optimize profit, workers can use their own data to build acceptance strategies that maximize net income and net hourly earnings. When every driver has an independent strategy tool, workers can reduce information asymmetry, challenge platforms such as DoorDash from a stronger position, and reclaim control over their labor, time, data, and income.

## Goal

Gig Pilot ultimately aims to answer one practical question: **Is this offer worth accepting?**

The answer requires more than the displayed payout. A useful strategy must account for mileage, estimated time, merchant wait time, return distance, market conditions, stacked-order risk, vehicle expenses, and opportunity cost. Gig Pilot is intended to turn those factors into an explainable recommendation, not another invisible algorithm that makes decisions for workers.

## Current Status

The project is currently building its data and labeling foundation. It does not yet provide a production-ready acceptance model.

1. Collector records DoorDash page evidence on an Android device.
2. Exported records are stored locally under data/.
3. Dashboard is used to label delivery lifecycle pages.
4. Analyzer applies conservative rules and produces structured review data.
5. Once labels are reliable, the project can train and evaluate offer-value models.

## Repository Layout

    gig-pilot/
    ├── collector/      Android Accessibility data collector
    ├── analyzer/       Offline page analysis and rule validation
    ├── dashboard/      Local screenshot-labeling service
    ├── data/           Raw collection data, excluded from Git
    └── annotations/    Human labels, excluded from Git

## Quick Start

Build Collector:

    cd collector
    .\gradlew.bat assembleDebug

Start Dashboard:

    cd P:\gig-pilot
    python dashboard\build_dashboard.py

Then open http://127.0.0.1:8765.

Run Analyzer:

    python analyzer\accessibility_analyzer.py <exported-tar-or-data-directory>

## Principles

- Preserve raw evidence: Collector records trees, events, and screenshots without assigning lifecycle stages on the phone.
- Worker-controlled data: raw records and human labels stay local by default.
- Explainable recommendations: future models should expose the earnings, time, distance, and risk factors behind a recommendation.
- Privacy first: addresses, customer names, screenshots, and earnings are sensitive data.
- Do not create another black box: Gig Pilot should strengthen worker judgment rather than replace one opaque platform algorithm with another.

## Privacy and Responsibility

Collected records may contain customer names, addresses, merchants, routes, earnings, and screenshots. Only analyze data you are authorized to access, and follow applicable laws, platform terms, and privacy requirements. Never upload unredacted data/ or annotations/ to a public repository.
