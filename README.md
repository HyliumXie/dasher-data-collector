# dasher-data-collector

dasher-data-collector is an Android data collection utility for observing DoorDash Dasher app screens through Android Accessibility APIs. It records raw accessibility snapshots, visible text, metadata, and screenshots to help inspect offer, decline confirmation, navigation, and other Dasher UI states during debugging or research.

## Features

- Listens for accessibility events from the DoorDash Dasher package (`com.doordash.driverapp`).
- Serializes the active accessibility node tree into structured JSON.
- Extracts visible text and accessibility values from screen content.
- Classifies captured screens as new offer candidates, decline confirmations, navigation screens, or other states.
- Skips duplicate captures by hashing screen content.
- Debounces frequent window content changes to reduce noisy snapshots.
- Saves screenshots for non-navigation screens on Android 11 / API 30 or newer.
- Stores each capture as a timestamped raw snapshot in app-private storage.

## Requirements

- Android Studio with Android Gradle Plugin support.
- JDK 11 or newer.
- Android SDK 37.
- Android device or emulator running Android 7.0 / API 24 or newer.
- Android 11 / API 30 or newer is required for screenshot capture.
- DoorDash Dasher app installed on the test device.

## Installation

Clone the repository and open it in Android Studio:

```sh
git clone <repository-url>
cd dasher-data-collector
```

Build the debug APK with Gradle:

```sh
./gradlew assembleDebug
```

Install it on a connected device:

```sh
./gradlew installDebug
```

## Quick Start

1. Install dasher-data-collector on the Android device.
2. Open Android system settings.
3. Go to Accessibility settings.
4. Enable the dasher-data-collector accessibility service.
5. Open the DoorDash Dasher app.
6. Navigate through offer, decline, or delivery screens.
7. Inspect generated snapshot files from dasher-data-collector app-private storage.

## Data Output

Snapshots are saved under the app's private files directory:

```text
files/dasher_data_collector/raw/<yyyyMMdd>/<HHmmss_SSS>/
```

Each snapshot directory may contain:

- `accessibility_tree.json`: Serialized accessibility node tree.
- `visible_text.txt`: Text and content descriptions visible through accessibility APIs.
- `meta.json`: Capture metadata, classification, event details, node count, content hash, assignment ID, and screenshot status.
- `screenshot.png`: Screenshot captured by the accessibility service when supported and applicable.

Screenshot status values include:

- `pending`: Screenshot capture was requested but has not finished yet.
- `saved`: Screenshot was successfully saved.
- `failed`: Screenshot capture failed.
- `unsupported`: Device API level does not support accessibility screenshots.
- `skipped_navigation`: Screenshot was intentionally skipped for navigation screens.

## Screen Classification

dasher-data-collector currently classifies raw captures into:

- `NEW_OFFER_CANDIDATE`: A possible new offer screen with offer cues, delivery timing, accept/decline actions, and an assignment ID.
- `DECLINE_CONFIRM`: A decline confirmation screen.
- `NAVIGATION`: A navigation-like screen without offer cues.
- `OTHER`: Any screen that does not match the above patterns.

Classification is heuristic and based on visible accessibility text, content descriptions, view IDs, and assignment ID patterns.

## Project Structure

```text
.
├── app/
│   ├── build.gradle.kts
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml
│       │   ├── java/com/liamxie/dasherdatacollector/
│       │   │   ├── MainActivity.kt
│       │   │   └── DoorDashAccessibilityService.kt
│       │   └── res/
│       │       └── xml/dasher_data_collector_accessibility_service.xml
│       ├── test/
│       └── androidTest/
├── build.gradle.kts
├── settings.gradle.kts
└── gradle/libs.versions.toml
```

## Development

Run a local build:

```sh
./gradlew build
```

Run unit tests:

```sh
./gradlew test
```

Run Android instrumented tests on a connected device or emulator:

```sh
./gradlew connectedAndroidTest
```

Useful implementation files:

- `app/src/main/java/com/liamxie/dasherdatacollector/DoorDashAccessibilityService.kt`: Accessibility event handling, node serialization, classification, and screenshot capture.
- `app/src/main/java/com/liamxie/dasherdatacollector/MainActivity.kt`: Minimal Compose activity.
- `app/src/main/res/xml/dasher_data_collector_accessibility_service.xml`: Accessibility service capabilities and event configuration.
- `gradle/libs.versions.toml`: Dependency and plugin versions.

## Privacy And Safety

dasher-data-collector can capture screen text, view structure, and screenshots from the DoorDash Dasher app. Use it only on devices and accounts where you have permission to collect this data. Do not collect, share, or publish personal, customer, merchant, location, payment, or account data without proper authorization and redaction.

Generated files are stored in app-private storage, but they may still contain sensitive information. Treat all captured output as confidential.

## Known Limitations

- The app UI is currently minimal and primarily serves as a host for the accessibility service.
- Classification rules are heuristic and may need updates when DoorDash changes its UI text or structure.
- Screenshots require Android 11 / API 30 or newer.
- Secure windows, permission issues, or Android screenshot throttling can prevent screenshot capture.
- The service only processes events from `com.doordash.driverapp`.

## License

No license has been specified yet.
