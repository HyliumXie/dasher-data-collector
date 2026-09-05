# Collector

Collector is Gig Pilot's Android data-capture component. It observes DoorDash Dasher pages through an Accessibility Service and preserves the raw trees, event metadata, and screenshot evidence needed for offline analysis.

Collector records evidence only. It does not assign delivery lifecycle stages or decide whether an offer is worth accepting on the phone.

## Capture Behavior

- Save a page only when rootInActiveWindow belongs to com.doordash.driverapp.
- Wait approximately 750 ms after an Accessibility event for the page to stabilize.
- Deduplicate identical contentHash values.
- Use an annotationSignature that normalizes countdown and clock changes.
- Capture screenshots for new offers and semantic page changes.
- Throttle screenshots to a minimum interval of approximately 1.75 seconds.
- Store annotation screenshots as JPEG quality 80.
- Persist seen assignment IDs so service restarts do not repeat new-offer handling.

## Private Storage

Records are stored in the app's private directory:

    files/collector/raw/<yyyyMMdd>/<HHmmss_SSS>/

A record can contain:

    accessibility_tree.json
    event.json
    screenshot.jpg

event.json includes the Collector version, capture type, root package, hashes, assignment evidence, tree and screenshot timestamps, screenshot delay, and screenshot status.

## In-App File Manager

Opening Collector displays its private data in a conventional Material-style file manager.

- Browse date and record folders.
- Tap a file to open it with an installed Android viewer.
- Long-press to enter selection mode.
- Select multiple items, select all, and delete with confirmation.
- Refresh the current folder.
- Open Android Accessibility Settings from the app.

Deleted records cannot be recovered. Export important data before removing it.

## Build and Install

    cd collector
    .\gradlew.bat assembleDebug
    adb install -r .\app\build\outputs\apk\debug\app-debug.apk

The APK is generated at:

    collector/app/build/outputs/apk/debug/app-debug.apk

After installation, enable Collector under Android Accessibility Settings. The application ID is com.liamxie.collector.

## Privacy

Captured data can include customer names, addresses, merchants, routes, earnings, and screenshots. Files remain in app-private storage and are managed from Collector. Use the app only with data you are authorized to access, and treat every export as confidential.
