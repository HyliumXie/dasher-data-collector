package com.liamxie.ddprobe

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
    private var pendingWindowContentChanged: EventSnapshot? = null
    private var previousContentHash: String? = null

    private val savePendingWindowContentChanged = Runnable {
        pendingWindowContentChanged?.let(::processDoorDashEvent)
        pendingWindowContentChanged = null
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.packageName?.toString() != DOORDASH_PACKAGE_NAME) return

        val eventSnapshot = EventSnapshot(
            eventTime = event.eventTime,
            packageName = event.packageName?.toString().orEmpty(),
            eventType = event.eventType,
            className = event.className?.toString().orEmpty()
        )

        Log.d(
            TAG,
            "DoorDash event received: timestamp=${eventSnapshot.eventTime}, " +
                "packageName=${eventSnapshot.packageName}, " +
                "eventType=${eventSnapshot.eventType}, " +
                "className=${eventSnapshot.className}"
        )

        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            pendingWindowContentChanged = eventSnapshot
            handler.removeCallbacks(savePendingWindowContentChanged)
            handler.postDelayed(savePendingWindowContentChanged, DEBOUNCE_MS)
        } else {
            processDoorDashEvent(eventSnapshot)
        }
    }

    override fun onInterrupt() {
        Log.d(TAG, "Accessibility service interrupted")
    }

    override fun onDestroy() {
        handler.removeCallbacks(savePendingWindowContentChanged)
        pendingWindowContentChanged = null
        super.onDestroy()
    }

    private fun processDoorDashEvent(event: EventSnapshot) {
        val root = rootInActiveWindow
        if (root == null) {
            Log.d(TAG, "Snapshot skipped: rootInActiveWindow is null")
            return
        }

        val visibleText = mutableListOf<String>()
        val accessibilityValues = mutableListOf<String>()
        val nodeCount = IntCounter()
        val treeJson = serializeNode(root, visibleText, accessibilityValues, nodeCount)
        val visibleTextContent = visibleText.joinToString(separator = "\n")
        val treeJsonContent = treeJson.toString(2)
        val contentHash = sha256(treeJsonContent + "\n" + visibleTextContent)
        val assignmentId = extractAssignmentId(accessibilityValues)
        val screenClassification = classifyScreenForRawCollection(visibleText, assignmentId)

        Log.d(
            TAG,
            "Classified state=$screenClassification, assignmentId=${assignmentId ?: "none"}, " +
                "nodeCount=${nodeCount.value}, contentHash=$contentHash"
        )

        if (contentHash == previousContentHash) {
            Log.d(TAG, "Duplicate DoorDash screen skipped: contentHash=$contentHash")
            return
        }

        saveRawSnapshot(
            event = event,
            treeJsonContent = treeJsonContent,
            visibleTextContent = visibleTextContent,
            nodeCount = nodeCount.value,
            contentHash = contentHash,
            assignmentId = assignmentId,
            screenClassification = screenClassification
        )
        previousContentHash = contentHash
    }

    private fun saveRawSnapshot(
        event: EventSnapshot,
        treeJsonContent: String,
        visibleTextContent: String,
        nodeCount: Int,
        contentHash: String,
        assignmentId: String?,
        screenClassification: ScreenClassification
    ) {
        val snapshotDate = Date()
        val snapshotDirectory = File(
            File(File(File(filesDir, PROBE_DIRECTORY), RAW_DIRECTORY), DATE_DIRECTORY_FORMAT.format(snapshotDate)),
            SNAPSHOT_DIRECTORY_FORMAT.format(snapshotDate)
        )
        snapshotDirectory.mkdirs()

        File(snapshotDirectory, ACCESSIBILITY_TREE_FILE).writeText(treeJsonContent)
        File(snapshotDirectory, VISIBLE_TEXT_FILE).writeText(visibleTextContent)

        val metaJson = JSONObject()
            .put("timestamp", ISO_8601_FORMAT.format(snapshotDate))
            .put("packageName", event.packageName)
            .put("eventType", event.eventType)
            .put("className", event.className)
            .put("nodeCount", nodeCount)
            .put("contentHash", contentHash)
            .put("screenClassification", screenClassification.name)
            .put("assignmentId", assignmentId ?: JSONObject.NULL)
            .put(
                "screenshotStatus",
                if (screenClassification == ScreenClassification.NAVIGATION) {
                    "skipped_navigation"
                } else {
                    "pending"
                }
            )
            .put("screenshotError", JSONObject.NULL)
        writeMeta(snapshotDirectory, metaJson)

        Log.d(
            TAG,
            "Raw DoorDash snapshot saved: classification=$screenClassification, " +
                "assignmentId=${assignmentId ?: "none"}, " +
                "path=${snapshotDirectory.absolutePath}, contentHash=$contentHash"
        )

        if (screenClassification == ScreenClassification.NAVIGATION) {
            Log.d(TAG, "Screenshot skipped for NAVIGATION")
        } else {
            captureScreenshot(snapshotDirectory, metaJson)
        }
    }

    @SuppressLint("NewApi")
    private fun captureScreenshot(snapshotDirectory: File, metaJson: JSONObject) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            updateScreenshotMeta(
                snapshotDirectory = snapshotDirectory,
                metaJson = metaJson,
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
                            updateScreenshotMeta(
                                snapshotDirectory = snapshotDirectory,
                                metaJson = metaJson,
                                status = "failed",
                                error = "Bitmap.wrapHardwareBuffer returned null."
                            )
                            return
                        }

                        try {
                            File(snapshotDirectory, SCREENSHOT_FILE).outputStream().use { output ->
                                bitmap.compress(Bitmap.CompressFormat.PNG, PNG_QUALITY, output)
                            }
                            updateScreenshotMeta(
                                snapshotDirectory = snapshotDirectory,
                                metaJson = metaJson,
                                status = "saved",
                                error = null
                            )
                            Log.d(TAG, "Screenshot saved: path=${File(snapshotDirectory, SCREENSHOT_FILE).absolutePath}")
                        } catch (exception: Exception) {
                            updateScreenshotMeta(
                                snapshotDirectory = snapshotDirectory,
                                metaJson = metaJson,
                                status = "failed",
                                error = exception.message ?: exception::class.java.simpleName
                            )
                        } finally {
                            bitmap.recycle()
                            hardwareBuffer.close()
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        updateScreenshotMeta(
                            snapshotDirectory = snapshotDirectory,
                            metaJson = metaJson,
                            status = "failed",
                            error = screenshotErrorMessage(errorCode)
                        )
                    }
                }
            )
        } catch (exception: Exception) {
            updateScreenshotMeta(
                snapshotDirectory = snapshotDirectory,
                metaJson = metaJson,
                status = "failed",
                error = exception.message ?: exception::class.java.simpleName
            )
        }
    }

    private fun updateScreenshotMeta(
        snapshotDirectory: File,
        metaJson: JSONObject,
        status: String,
        error: String?
    ) {
        metaJson
            .put("screenshotStatus", status)
            .put("screenshotError", error ?: JSONObject.NULL)
        writeMeta(snapshotDirectory, metaJson)

        if (error != null) {
            Log.d(TAG, "Screenshot $status: $error")
        }
    }

    private fun writeMeta(snapshotDirectory: File, metaJson: JSONObject) {
        File(snapshotDirectory, META_FILE).writeText(metaJson.toString(2))
    }

    private fun serializeNode(
        node: AccessibilityNodeInfo,
        visibleText: MutableList<String>,
        accessibilityValues: MutableList<String>,
        nodeCount: IntCounter
    ): JSONObject {
        nodeCount.value += 1

        val text = node.text?.toString()
        val contentDescription = node.contentDescription?.toString()
        val viewIdResourceName = node.viewIdResourceName
        text?.takeIf { it.isNotBlank() }?.let(visibleText::add)
        contentDescription?.takeIf { it.isNotBlank() }?.let(visibleText::add)
        text?.takeIf { it.isNotBlank() }?.let(accessibilityValues::add)
        contentDescription?.takeIf { it.isNotBlank() }?.let(accessibilityValues::add)
        viewIdResourceName?.takeIf { it.isNotBlank() }?.let(accessibilityValues::add)

        val bounds = Rect()
        node.getBoundsInScreen(bounds)

        val children = JSONArray()

        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            children.put(serializeNode(child, visibleText, accessibilityValues, nodeCount))
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

    private fun classifyScreenForRawCollection(
        visibleText: List<String>,
        assignmentId: String?
    ): ScreenClassification {
        val normalizedVisibleText = normalizeForMatching(visibleText.joinToString(separator = "\n"))
        val hasOfferCue = OFFER_CUE_PATTERNS.any { it.containsMatchIn(normalizedVisibleText) }
        val hasDeliverBy = normalizedVisibleText.contains("deliver by")
        val hasAccept = ACCEPT_PATTERN.containsMatchIn(normalizedVisibleText)
        val hasDecline = DECLINE_PATTERN.containsMatchIn(normalizedVisibleText)
        val hasNewOfferCues = hasOfferCue || hasDeliverBy || hasAccept || hasDecline
        val hasDeclineConfirm = DECLINE_SURE_PATTERN.containsMatchIn(normalizedVisibleText) ||
            (DECLINE_OFFER_PATTERN.containsMatchIn(normalizedVisibleText) &&
                VIEW_OFFER_DETAILS_PATTERN.containsMatchIn(normalizedVisibleText))

        if (hasDeclineConfirm) {
            return ScreenClassification.DECLINE_CONFIRM
        }

        if (assignmentId != null && hasOfferCue && hasDeliverBy && hasAccept && hasDecline) {
            return ScreenClassification.NEW_OFFER_CANDIDATE
        }

        val hasNavigationCue = NAVIGATION_PATTERNS.any { it.containsMatchIn(normalizedVisibleText) }
        if (assignmentId == null && !hasNewOfferCues && hasNavigationCue) {
            return ScreenClassification.NAVIGATION
        }

        return ScreenClassification.OTHER
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

    private fun normalizeForMatching(value: String): String =
        value
            .lowercase(Locale.US)
            .replace(Regex("\\s+"), " ")

    private fun sha256(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8))
        return digest.joinToString(separator = "") { byte -> "%02x".format(byte) }
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

    private enum class ScreenClassification {
        NEW_OFFER_CANDIDATE,
        DECLINE_CONFIRM,
        NAVIGATION,
        OTHER
    }

    private class IntCounter {
        var value: Int = 0
    }

    private companion object {
        const val TAG = "DDProbe"
        const val DOORDASH_PACKAGE_NAME = "com.doordash.driverapp"
        const val DEBOUNCE_MS = 500L
        const val PROBE_DIRECTORY = "door_dash_probe"
        const val RAW_DIRECTORY = "raw"
        const val ACCESSIBILITY_TREE_FILE = "accessibility_tree.json"
        const val VISIBLE_TEXT_FILE = "visible_text.txt"
        const val META_FILE = "meta.json"
        const val SCREENSHOT_FILE = "screenshot.png"
        const val PNG_QUALITY = 100

        val OFFER_CUE_PATTERNS = listOf(
            Regex("\\bhigh paying offer\\b!?"),
            Regex("\\bnew offer\\b"),
            Regex("\\boffer card\\b"),
            Regex("\\btotal will be higher\\b")
        )
        val ACCEPT_PATTERN = Regex("\\baccept\\b")
        val DECLINE_PATTERN = Regex("\\bdecline\\b")
        val DECLINE_SURE_PATTERN = Regex("\\bare you sure you want to decline this offer\\b\\??")
        val DECLINE_OFFER_PATTERN = Regex("\\bdecline offer\\b")
        val VIEW_OFFER_DETAILS_PATTERN = Regex("\\bview offer details\\b")
        val NAVIGATION_PATTERNS = listOf(
            Regex("\\bturn left\\b"),
            Regex("\\bturn right\\b"),
            Regex("\\bmake a left u-turn\\b"),
            Regex("\\bmake a right u-turn\\b"),
            Regex("\\bavoid tolls\\b"),
            Regex("\\b\\d+(?:\\.\\d+)?\\s?(?:ft|feet)\\b")
        )
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
