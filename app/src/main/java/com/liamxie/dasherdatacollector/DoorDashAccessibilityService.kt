package com.liamxie.dasherdatacollector

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class DoorDashAccessibilityService : AccessibilityService() {
    private val handler = Handler(Looper.getMainLooper())
    private val seenOfferAssignmentIds = linkedSetOf<String>()

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.packageName?.toString() != DOORDASH_PACKAGE_NAME) return

        processDoorDashEvent(
            EventSnapshot(
                eventTime = event.eventTime,
                packageName = event.packageName?.toString().orEmpty(),
                eventType = event.eventType,
                className = event.className?.toString().orEmpty()
            )
        )
    }

    override fun onInterrupt() {
        Log.d(TAG, "Accessibility service interrupted")
    }

    private fun processDoorDashEvent(event: EventSnapshot) {
        val root = rootInActiveWindow
        if (root == null) {
            Log.d(TAG, "Raw event skipped: rootInActiveWindow is null")
            return
        }

        val accessibilityValues = mutableListOf<String>()
        val nodeCount = IntCounter()
        val treeJson = serializeNode(root, accessibilityValues, nodeCount)
        val treeJsonContent = treeJson.toString(2)
        val contentHash = sha256(treeJsonContent)
        val assignmentId = extractAssignmentId(accessibilityValues)
        val isNewOffer = assignmentId != null && isNewOffer(accessibilityValues)
        val isNewAssignmentOffer = isNewOffer && seenOfferAssignmentIds.add(assignmentId)

        saveEventSnapshot(
            event = event,
            treeJsonContent = treeJsonContent,
            nodeCount = nodeCount.value,
            contentHash = contentHash,
            assignmentId = assignmentId,
            isNewOffer = isNewOffer,
            isNewAssignmentOffer = isNewAssignmentOffer
        )
    }

    private fun saveEventSnapshot(
        event: EventSnapshot,
        treeJsonContent: String,
        nodeCount: Int,
        contentHash: String,
        assignmentId: String?,
        isNewOffer: Boolean,
        isNewAssignmentOffer: Boolean
    ) {
        val snapshotDate = Date()
        val snapshotDirectory = File(
            File(File(File(filesDir, PROBE_DIRECTORY), RAW_DIRECTORY), DATE_DIRECTORY_FORMAT.format(snapshotDate)),
            SNAPSHOT_DIRECTORY_FORMAT.format(snapshotDate)
        )
        snapshotDirectory.mkdirs()

        File(snapshotDirectory, ACCESSIBILITY_TREE_FILE).writeText(treeJsonContent)

        val eventJson = JSONObject()
            .put("timestamp", ISO_8601_FORMAT.format(snapshotDate))
            .put("accessibilityEventTime", event.eventTime)
            .put("packageName", event.packageName)
            .put("eventType", event.eventType)
            .put("eventTypeName", eventTypeName(event.eventType))
            .put("className", event.className)
            .put("nodeCount", nodeCount)
            .put("contentHash", contentHash)
            .put("assignmentId", assignmentId ?: JSONObject.NULL)
            .put("isNewOffer", isNewOffer)
            .put("isNewAssignmentOffer", isNewAssignmentOffer)
            .put("screenshotStatus", if (isNewAssignmentOffer) "pending" else "not_requested")
            .put("screenshotError", JSONObject.NULL)
        writeEvent(snapshotDirectory, eventJson)

        Log.d(
            TAG,
            "Raw DoorDash accessibility event saved: path=${snapshotDirectory.absolutePath}, " +
                "eventType=${event.eventType}, assignmentId=${assignmentId ?: "none"}, " +
                "isNewAssignmentOffer=$isNewAssignmentOffer"
        )

        if (isNewAssignmentOffer) {
            captureScreenshot(snapshotDirectory, eventJson)
        }
    }

    @SuppressLint("NewApi")
    private fun captureScreenshot(snapshotDirectory: File, eventJson: JSONObject) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            updateScreenshotEvent(
                snapshotDirectory = snapshotDirectory,
                eventJson = eventJson,
                status = "unsupported",
                error = "AccessibilityService.takeScreenshot requires Android 11 / API 30 or newer."
            )
            return
        }

        try {
            takeScreenshot(
                Display.DEFAULT_DISPLAY,
                { command -> handler.post(command) },
                object : TakeScreenshotCallback {
                    override fun onSuccess(screenshot: ScreenshotResult) {
                        val hardwareBuffer = screenshot.hardwareBuffer
                        val bitmap = Bitmap.wrapHardwareBuffer(hardwareBuffer, screenshot.colorSpace)

                        if (bitmap == null) {
                            hardwareBuffer.close()
                            updateScreenshotEvent(
                                snapshotDirectory = snapshotDirectory,
                                eventJson = eventJson,
                                status = "failed",
                                error = "Bitmap.wrapHardwareBuffer returned null."
                            )
                            return
                        }

                        try {
                            File(snapshotDirectory, SCREENSHOT_FILE).outputStream().use { output ->
                                bitmap.compress(Bitmap.CompressFormat.PNG, PNG_QUALITY, output)
                            }
                            updateScreenshotEvent(
                                snapshotDirectory = snapshotDirectory,
                                eventJson = eventJson,
                                status = "saved",
                                error = null
                            )
                            Log.d(TAG, "Offer screenshot saved: path=${File(snapshotDirectory, SCREENSHOT_FILE).absolutePath}")
                        } catch (exception: Exception) {
                            updateScreenshotEvent(
                                snapshotDirectory = snapshotDirectory,
                                eventJson = eventJson,
                                status = "failed",
                                error = exception.message ?: exception::class.java.simpleName
                            )
                        } finally {
                            bitmap.recycle()
                            hardwareBuffer.close()
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        updateScreenshotEvent(
                            snapshotDirectory = snapshotDirectory,
                            eventJson = eventJson,
                            status = "failed",
                            error = screenshotErrorMessage(errorCode)
                        )
                    }
                }
            )
        } catch (exception: Exception) {
            updateScreenshotEvent(
                snapshotDirectory = snapshotDirectory,
                eventJson = eventJson,
                status = "failed",
                error = exception.message ?: exception::class.java.simpleName
            )
        }
    }

    private fun updateScreenshotEvent(
        snapshotDirectory: File,
        eventJson: JSONObject,
        status: String,
        error: String?
    ) {
        eventJson
            .put("screenshotStatus", status)
            .put("screenshotError", error ?: JSONObject.NULL)
        writeEvent(snapshotDirectory, eventJson)

        if (error != null) {
            Log.d(TAG, "Screenshot $status: $error")
        }
    }

    private fun writeEvent(snapshotDirectory: File, eventJson: JSONObject) {
        File(snapshotDirectory, EVENT_FILE).writeText(eventJson.toString(2))
    }

    private fun serializeNode(
        node: AccessibilityNodeInfo,
        accessibilityValues: MutableList<String>,
        nodeCount: IntCounter
    ): JSONObject {
        nodeCount.value += 1

        val text = node.text?.toString()
        val contentDescription = node.contentDescription?.toString()
        val viewIdResourceName = node.viewIdResourceName
        text?.takeIf { it.isNotBlank() }?.let(accessibilityValues::add)
        contentDescription?.takeIf { it.isNotBlank() }?.let(accessibilityValues::add)
        viewIdResourceName?.takeIf { it.isNotBlank() }?.let(accessibilityValues::add)

        val bounds = Rect()
        node.getBoundsInScreen(bounds)

        val children = JSONArray()
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            children.put(serializeNode(child, accessibilityValues, nodeCount))
        }

        return JSONObject()
            .put("text", text ?: JSONObject.NULL)
            .put("contentDescription", contentDescription ?: JSONObject.NULL)
            .put("viewIdResourceName", viewIdResourceName ?: JSONObject.NULL)
            .put("className", node.className?.toString() ?: JSONObject.NULL)
            .put(
                "boundsInScreen",
                JSONObject()
                    .put("left", bounds.left)
                    .put("top", bounds.top)
                    .put("right", bounds.right)
                    .put("bottom", bounds.bottom)
            )
            .put("clickable", node.isClickable)
            .put("enabled", node.isEnabled)
            .put("focusable", node.isFocusable)
            .put("focused", node.isFocused)
            .put("selected", node.isSelected)
            .put("scrollable", node.isScrollable)
            .put("childCount", node.childCount)
            .put("children", children)
    }

    private fun isNewOffer(accessibilityValues: List<String>): Boolean {
        val lines = accessibilityValues.map { it.trim() }.filter { it.isNotBlank() }
        val lowerLines = lines.map { it.lowercase(Locale.US) }.toSet()
        val text = lines.joinToString(separator = "\n")
        return "decline" in lowerLines &&
            ("accept" in lowerLines || "add to route" in lowerLines) &&
            MONEY_PATTERN.containsMatchIn(text) &&
            MILES_PATTERN.containsMatchIn(text) &&
            DELIVER_BY_PATTERN.containsMatchIn(text)
    }

    private fun extractAssignmentId(accessibilityValues: List<String>): String? {
        val joinedValues = accessibilityValues.joinToString(separator = "\n")
        ASSIGNMENT_ID_LABELED_PATTERNS.forEach { pattern ->
            pattern.find(joinedValues)?.groups?.get(1)?.value?.let { return normalizeAssignmentId(it) }
        }

        return UUID_PATTERN.find(joinedValues)?.value?.let(::normalizeAssignmentId)
    }

    private fun normalizeAssignmentId(value: String): String =
        value.trim().trim('.', ',', ';', ':', '#')

    private fun sha256(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8))
        return digest.joinToString(separator = "") { byte -> "%02x".format(byte) }
    }

    private fun eventTypeName(eventType: Int): String =
        when (eventType) {
            AccessibilityEvent.TYPE_VIEW_CLICKED -> "TYPE_VIEW_CLICKED"
            AccessibilityEvent.TYPE_VIEW_LONG_CLICKED -> "TYPE_VIEW_LONG_CLICKED"
            AccessibilityEvent.TYPE_VIEW_SELECTED -> "TYPE_VIEW_SELECTED"
            AccessibilityEvent.TYPE_VIEW_FOCUSED -> "TYPE_VIEW_FOCUSED"
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> "TYPE_VIEW_TEXT_CHANGED"
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> "TYPE_WINDOW_STATE_CHANGED"
            AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED -> "TYPE_NOTIFICATION_STATE_CHANGED"
            AccessibilityEvent.TYPE_VIEW_HOVER_ENTER -> "TYPE_VIEW_HOVER_ENTER"
            AccessibilityEvent.TYPE_VIEW_HOVER_EXIT -> "TYPE_VIEW_HOVER_EXIT"
            AccessibilityEvent.TYPE_TOUCH_EXPLORATION_GESTURE_START -> "TYPE_TOUCH_EXPLORATION_GESTURE_START"
            AccessibilityEvent.TYPE_TOUCH_EXPLORATION_GESTURE_END -> "TYPE_TOUCH_EXPLORATION_GESTURE_END"
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> "TYPE_WINDOW_CONTENT_CHANGED"
            AccessibilityEvent.TYPE_VIEW_SCROLLED -> "TYPE_VIEW_SCROLLED"
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED -> "TYPE_VIEW_TEXT_SELECTION_CHANGED"
            AccessibilityEvent.TYPE_ANNOUNCEMENT -> "TYPE_ANNOUNCEMENT"
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED -> "TYPE_VIEW_ACCESSIBILITY_FOCUSED"
            AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED -> "TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED"
            AccessibilityEvent.TYPE_VIEW_TEXT_TRAVERSED_AT_MOVEMENT_GRANULARITY ->
                "TYPE_VIEW_TEXT_TRAVERSED_AT_MOVEMENT_GRANULARITY"
            AccessibilityEvent.TYPE_GESTURE_DETECTION_START -> "TYPE_GESTURE_DETECTION_START"
            AccessibilityEvent.TYPE_GESTURE_DETECTION_END -> "TYPE_GESTURE_DETECTION_END"
            AccessibilityEvent.TYPE_TOUCH_INTERACTION_START -> "TYPE_TOUCH_INTERACTION_START"
            AccessibilityEvent.TYPE_TOUCH_INTERACTION_END -> "TYPE_TOUCH_INTERACTION_END"
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> "TYPE_WINDOWS_CHANGED"
            AccessibilityEvent.TYPE_VIEW_CONTEXT_CLICKED -> "TYPE_VIEW_CONTEXT_CLICKED"
            else -> "UNKNOWN"
        }

    private fun screenshotErrorMessage(errorCode: Int): String =
        when (errorCode) {
            ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR -> "ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR"
            ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT"
            ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY -> "ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY"
            ERROR_TAKE_SCREENSHOT_INVALID_WINDOW -> "ERROR_TAKE_SCREENSHOT_INVALID_WINDOW"
            ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS"
            ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "ERROR_TAKE_SCREENSHOT_SECURE_WINDOW"
            else -> "Unknown screenshot error code: $errorCode"
        }

    private data class EventSnapshot(
        val eventTime: Long,
        val packageName: String,
        val eventType: Int,
        val className: String
    )

    private class IntCounter {
        var value: Int = 0
    }

    private companion object {
        const val TAG = "DasherDataCollector"
        const val DOORDASH_PACKAGE_NAME = "com.doordash.driverapp"
        const val PROBE_DIRECTORY = "dasher_data_collector"
        const val RAW_DIRECTORY = "raw"
        const val ACCESSIBILITY_TREE_FILE = "accessibility_tree.json"
        const val EVENT_FILE = "event.json"
        const val SCREENSHOT_FILE = "screenshot.png"
        const val PNG_QUALITY = 100

        val MONEY_PATTERN = Regex("[+]?\\$\\s*\\d+(?:\\.\\d{2})?\\+?", RegexOption.IGNORE_CASE)
        val MILES_PATTERN = Regex("(?:additional\\s+)?\\b\\d+(?:\\.\\d+)?\\s*mi\\b", RegexOption.IGNORE_CASE)
        val DELIVER_BY_PATTERN = Regex("\\bdeliver by\\s+[0-9:]+\\s*[ap]m\\b", RegexOption.IGNORE_CASE)
        val ASSIGNMENT_ID_LABELED_PATTERNS = listOf(
            Regex(
                "\\b(?:assignment|delivery|offer)\\s*(?:id|identifier)\\b\\s*[:#=\\-]?\\s*([a-zA-Z0-9][a-zA-Z0-9_-]{7,})",
                RegexOption.IGNORE_CASE
            ),
            Regex(
                "\\b(?:assignmentId|assignment_id|deliveryId|delivery_id|offerId|offer_id)\\b\\s*[:#=\\-]?\\s*([a-zA-Z0-9][a-zA-Z0-9_-]{7,})",
                RegexOption.IGNORE_CASE
            )
        )
        val UUID_PATTERN = Regex(
            "\\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\b"
        )

        val DATE_DIRECTORY_FORMAT = SimpleDateFormat("yyyyMMdd", Locale.US)
        val SNAPSHOT_DIRECTORY_FORMAT = SimpleDateFormat("HHmmss_SSS", Locale.US)
        val ISO_8601_FORMAT = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US)
    }
}
