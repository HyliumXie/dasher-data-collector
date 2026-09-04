package com.liamxie.dasherdatacollector

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.*
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.*

class DoorDashAccessibilityService : AccessibilityService() {
    private val handler = Handler(Looper.getMainLooper())
    private var pending: Runnable? = null
    private var latestEvent: EventSnapshot? = null
    private var lastContentHash: String? = null
    private var lastScreenshotSignature: String? = null
    private var lastScreenshotElapsed = 0L
    private val seenAssignments by lazy { preferences.getStringSet(SEEN_KEY, emptySet())?.toMutableSet() ?: mutableSetOf() }
    private val preferences by lazy { getSharedPreferences(PREFS, MODE_PRIVATE) }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.packageName?.toString() != DOORDASH_PACKAGE) return
        latestEvent = EventSnapshot(event.eventTime, System.currentTimeMillis(), event.packageName.toString(), event.eventType, event.className?.toString().orEmpty())
        pending?.let(handler::removeCallbacks)
        pending = Runnable { latestEvent?.let(::captureStablePage); latestEvent = null; pending = null }
            .also { handler.postDelayed(it, STABILITY_DELAY_MS) }
    }

    override fun onInterrupt() { pending?.let(handler::removeCallbacks) }

    private fun captureStablePage(event: EventSnapshot) {
        val root = rootInActiveWindow ?: return
        val rootPackage = root.packageName?.toString().orEmpty()
        if (rootPackage != DOORDASH_PACKAGE) return
        val values = mutableListOf<String>()
        val count = Counter()
        val capturedAt = Date()
        val treeText = serialize(root, values, count).toString(2)
        val contentHash = sha256(treeText)
        if (contentHash == lastContentHash) return
        lastContentHash = contentHash
        val signature = annotationSignature(values)
        val assignmentId = extractAssignmentId(values)
        val offer = assignmentId != null && isOffer(values)
        val newOffer = offer && rememberAssignment(assignmentId)
        val semanticChange = signature != lastScreenshotSignature
        val captureType = when { newOffer -> "new_offer"; semanticChange -> "semantic_change"; else -> "tree_only" }
        val directory = snapshotDirectory(capturedAt)
        File(directory, TREE_FILE).writeText(treeText)
        val json = JSONObject()
            .put("collectorVersion", VERSION).put("captureType", captureType)
            .put("timestamp", ISO_FORMAT.format(capturedAt)).put("accessibilityEventTime", event.eventTime)
            .put("packageName", event.packageName).put("rootPackageName", rootPackage)
            .put("eventType", event.eventType).put("eventTypeName", AccessibilityEvent.eventTypeToString(event.eventType))
            .put("className", event.className).put("nodeCount", count.value)
            .put("contentHash", contentHash).put("annotationSignature", signature)
            .put("assignmentId", assignmentId ?: JSONObject.NULL).put("isNewOffer", offer).put("isNewAssignmentOffer", newOffer)
            .put("treeCapturedAt", ISO_FORMAT.format(capturedAt)).put("screenshotCapturedAt", JSONObject.NULL)
            .put("screenshotDelayMs", JSONObject.NULL).put("screenshotStatus", if (semanticChange || newOffer) "pending" else "not_requested")
            .put("screenshotError", JSONObject.NULL)
        writeEvent(directory, json)
        if (semanticChange || newOffer) {
            val delay = (SCREENSHOT_INTERVAL_MS - (SystemClock.elapsedRealtime() - lastScreenshotElapsed)).coerceAtLeast(0)
            handler.postDelayed({
                if (rootInActiveWindow?.packageName?.toString() == DOORDASH_PACKAGE) screenshot(directory, json, event.receivedAt, signature)
                else updateScreenshot(directory, json, "skipped", "Active root is no longer DoorDash", event.receivedAt)
            }, delay)
        }
    }

    private fun snapshotDirectory(date: Date): File {
        val day = File(File(File(filesDir, "dasher_data_collector"), "raw"), DAY_FORMAT.format(date)).apply { mkdirs() }
        val base = SNAPSHOT_FORMAT.format(date)
        var result = File(day, base); var suffix = 1
        while (result.exists()) result = File(day, "${base}_${suffix++}")
        return result.apply { mkdirs() }
    }

    private fun rememberAssignment(id: String): Boolean {
        if (!seenAssignments.add(id)) return false
        preferences.edit().putStringSet(SEEN_KEY, HashSet(seenAssignments)).apply()
        return true
    }

    @SuppressLint("NewApi")
    private fun screenshot(directory: File, json: JSONObject, receivedAt: Long, signature: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            updateScreenshot(directory, json, "unsupported", "Requires Android 11 / API 30", receivedAt); return
        }
        lastScreenshotElapsed = SystemClock.elapsedRealtime()
        takeScreenshot(Display.DEFAULT_DISPLAY, { handler.post(it) }, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val buffer = result.hardwareBuffer
                val bitmap = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)
                if (bitmap == null) { buffer.close(); updateScreenshot(directory, json, "failed", "Bitmap conversion failed", receivedAt); return }
                try {
                    File(directory, SCREENSHOT_FILE).outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 80, it) }
                    lastScreenshotSignature = signature
                    updateScreenshot(directory, json, "saved", null, receivedAt)
                } catch (error: Exception) { updateScreenshot(directory, json, "failed", error.message, receivedAt) }
                finally { bitmap.recycle(); buffer.close() }
            }
            override fun onFailure(code: Int) = updateScreenshot(directory, json, "failed", "Screenshot error $code", receivedAt)
        })
    }

    private fun updateScreenshot(directory: File, json: JSONObject, status: String, error: String?, receivedAt: Long) {
        val now = Date()
        json.put("screenshotStatus", status).put("screenshotError", error ?: JSONObject.NULL)
            .put("screenshotCapturedAt", if (status == "saved") ISO_FORMAT.format(now) else JSONObject.NULL)
            .put("screenshotDelayMs", now.time - receivedAt)
        writeEvent(directory, json)
    }

    private fun writeEvent(directory: File, json: JSONObject) = File(directory, EVENT_FILE).writeText(json.toString(2))

    private fun serialize(node: AccessibilityNodeInfo, values: MutableList<String>, count: Counter): JSONObject {
        count.value++
        val text = node.text?.toString(); val description = node.contentDescription?.toString(); val viewId = node.viewIdResourceName
        listOf(text, description, viewId).filterNotNull().filter(String::isNotBlank).forEach(values::add)
        val bounds = Rect().also(node::getBoundsInScreen); val children = JSONArray()
        for (i in 0 until node.childCount) node.getChild(i)?.let { children.put(serialize(it, values, count)) }
        return JSONObject().put("text", text ?: JSONObject.NULL).put("contentDescription", description ?: JSONObject.NULL)
            .put("viewIdResourceName", viewId ?: JSONObject.NULL).put("className", node.className?.toString() ?: JSONObject.NULL)
            .put("packageName", node.packageName?.toString() ?: JSONObject.NULL)
            .put("boundsInScreen", JSONObject().put("left", bounds.left).put("top", bounds.top).put("right", bounds.right).put("bottom", bounds.bottom))
            .put("clickable", node.isClickable).put("enabled", node.isEnabled).put("focusable", node.isFocusable)
            .put("focused", node.isFocused).put("selected", node.isSelected).put("scrollable", node.isScrollable)
            .put("childCount", node.childCount).put("children", children)
    }

    private fun annotationSignature(values: List<String>) = sha256(values.asSequence().map { it.trim().lowercase(Locale.US) }
        .map { COUNTDOWN.replace(it, "<countdown>") }.map { CLOCK.replace(it, "<clock>") }.filter(String::isNotBlank).joinToString("\n"))
    private fun isOffer(values: List<String>): Boolean {
        val lines = values.map(String::trim); val lower = lines.map { it.lowercase(Locale.US) }.toSet(); val joined = lines.joinToString("\n")
        return "decline" in lower && ("accept" in lower || "add to route" in lower) && MONEY.containsMatchIn(joined) && MILES.containsMatchIn(joined)
    }
    private fun extractAssignmentId(values: List<String>): String? {
        val joined = values.joinToString("\n")
        ID_PATTERNS.forEach { pattern -> pattern.find(joined)?.groups?.get(1)?.value?.let { return it.trim('.', ',', ';', ':', '#') } }
        return UUID.find(joined)?.value
    }
    private fun sha256(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

    private data class EventSnapshot(val eventTime: Long, val receivedAt: Long, val packageName: String, val eventType: Int, val className: String)
    private class Counter { var value = 0 }
    companion object {
        private const val DOORDASH_PACKAGE = "com.doordash.driverapp"
        private const val VERSION = "2.0.0"
        private const val PREFS = "collector_state"
        private const val SEEN_KEY = "seen_offer_assignment_ids"
        private const val TREE_FILE = "accessibility_tree.json"
        private const val EVENT_FILE = "event.json"
        private const val SCREENSHOT_FILE = "screenshot.jpg"
        private const val STABILITY_DELAY_MS = 750L
        private const val SCREENSHOT_INTERVAL_MS = 1750L
        private val DAY_FORMAT = SimpleDateFormat("yyyyMMdd", Locale.US)
        private val SNAPSHOT_FORMAT = SimpleDateFormat("HHmmss_SSS", Locale.US)
        private val ISO_FORMAT = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US)
        private val MONEY = Regex("[+]?\\$\\s*\\d+(?:\\.\\d{2})?\\+?")
        private val MILES = Regex("(?:additional\\s+)?\\b\\d+(?:\\.\\d+)?\\s*mi\\b", RegexOption.IGNORE_CASE)
        private val COUNTDOWN = Regex("\\b(?:\\d{1,2}:)?\\d{1,2}:\\d{2}\\b|\\b\\d+\\s*(?:sec(?:ond)?s?|s)\\b")
        private val CLOCK = Regex("\\b\\d{1,2}:\\d{2}\\s*(?:am|pm)\\b")
        private val ID_PATTERNS = listOf(
            Regex("\\b(?:assignment|delivery|offer)\\s*(?:id|identifier)\\b\\s*[:#=\\-]?\\s*([a-zA-Z0-9][a-zA-Z0-9_-]{7,})", RegexOption.IGNORE_CASE),
            Regex("\\b(?:assignmentId|assignment_id|deliveryId|delivery_id|offerId|offer_id)\\b\\s*[:#=\\-]?\\s*([a-zA-Z0-9][a-zA-Z0-9_-]{7,})", RegexOption.IGNORE_CASE))
        private val UUID = Regex("\\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\b")
    }
}
