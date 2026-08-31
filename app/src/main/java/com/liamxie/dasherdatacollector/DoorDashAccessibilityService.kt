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
    private var pendingWindowContentChanged: EventSnapshot? = null
    private var previousContentHash: String? = null
    private var previousPersistKey: String? = null
    private var pendingOfferId: String? = null
    private val activeAssignments = linkedSetOf<String>()
    private val knownAssignments = linkedMapOf<String, AssignmentState>()

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
        val analysis = analyzeDoorDashScreen(
            event = event,
            visibleText = visibleText,
            accessibilityValues = accessibilityValues,
            assignmentId = assignmentId
        )

        Log.d(
            TAG,
            "Classified state=${analysis.stage}, outcome=${analysis.outcome ?: "none"}, " +
                "assignmentId=${analysis.assignedAssignmentId ?: assignmentId ?: "none"}, " +
                "nodeCount=${nodeCount.value}, contentHash=$contentHash"
        )

        if (contentHash == previousContentHash || analysis.persistKey == previousPersistKey) {
            Log.d(TAG, "Duplicate DoorDash screen skipped: contentHash=$contentHash")
            return
        }

        if (!analysis.shouldPersist) {
            previousContentHash = contentHash
            previousPersistKey = analysis.persistKey
            Log.d(TAG, "Reduced collection skipped: stage=${analysis.stage}, persistKey=${analysis.persistKey}")
            return
        }

        saveRawSnapshot(
            event = event,
            treeJsonContent = treeJsonContent,
            visibleTextContent = visibleTextContent,
            nodeCount = nodeCount.value,
            contentHash = contentHash,
            analysis = analysis
        )
        previousContentHash = contentHash
        previousPersistKey = analysis.persistKey
    }

    private fun saveRawSnapshot(
        event: EventSnapshot,
        treeJsonContent: String,
        visibleTextContent: String,
        nodeCount: Int,
        contentHash: String,
        analysis: ScreenAnalysis
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
            .put("screenClassification", analysis.stage.name)
            .put("stage", analysis.stage.name)
            .put("outcome", analysis.outcome?.name ?: JSONObject.NULL)
            .put("outcomeReason", analysis.outcomeReason ?: JSONObject.NULL)
            .put("assignmentId", analysis.rawAssignmentId ?: JSONObject.NULL)
            .put("assignedAssignmentId", analysis.assignedAssignmentId ?: JSONObject.NULL)
            .put("timestampSource", analysis.timestampSource.name)
            .put("confidence", analysis.confidence.name)
            .put("buttonTexts", JSONArray(analysis.buttonTexts))
            .put("offer", analysis.offer?.toJson() ?: JSONObject.NULL)
            .put("dashTotal", analysis.dashTotal ?: JSONObject.NULL)
            .put("persistReason", analysis.persistReason)
            .put(
                "screenshotStatus",
                if (analysis.shouldCaptureScreenshot) {
                    "pending"
                } else {
                    "skipped_reduced_collection"
                }
            )
            .put("screenshotError", JSONObject.NULL)
        writeMeta(snapshotDirectory, metaJson)

        Log.d(
            TAG,
            "DoorDash transition snapshot saved: stage=${analysis.stage}, " +
                "assignmentId=${analysis.assignedAssignmentId ?: analysis.rawAssignmentId ?: "none"}, " +
                "path=${snapshotDirectory.absolutePath}, contentHash=$contentHash"
        )

        if (!analysis.shouldCaptureScreenshot) {
            Log.d(TAG, "Screenshot skipped: reduced collection stage=${analysis.stage}")
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

    private fun analyzeDoorDashScreen(
        event: EventSnapshot,
        visibleText: List<String>,
        accessibilityValues: List<String>,
        assignmentId: String?
    ): ScreenAnalysis {
        val text = visibleText.joinToString(separator = "\n")
        val normalizedText = normalizeForMatching(text)
        val exactLines = visibleText.map { it.trim() }.filter { it.isNotBlank() }
        val lowerLines = exactLines.map { it.lowercase(Locale.US) }.toSet()
        val buttonTexts = extractButtonTexts(accessibilityValues, exactLines)
        val dashTotal = extractDashTotal(exactLines)
        val offer = if (isNewOffer(lowerLines, normalizedText)) parseOffer(exactLines, text) else null
        val rawAssignmentId = assignmentId
        var stage = ScreenClassification.OTHER
        var outcome: Outcome? = null
        var outcomeReason: String? = null
        var assignedAssignmentId: String? = null
        var timestampSource = TimestampSource.FIRST_STAGE_SNAPSHOT
        var confidence = Confidence.MEDIUM
        var persistReason = "stage_transition"

        if (dashTotal != null) {
            knownAssignments.values.lastOrNull { it.dashTotalBefore == null }?.dashTotalBefore = dashTotal
        }

        when {
            isUnassigned(normalizedText) -> {
                stage = ScreenClassification.UNASSIGNED
                outcome = Outcome.UNASSIGNED
                outcomeReason = "explicit_unassignment"
                assignedAssignmentId = chooseAssignmentForText(normalizedText)
                assignedAssignmentId?.let {
                    knownAssignments[it]?.outcome = Outcome.UNASSIGNED
                    activeAssignments.remove(it)
                }
                confidence = if (assignedAssignmentId == null) Confidence.LOW else Confidence.MEDIUM
            }
            isCompletedFull(normalizedText) || isCompletedSummary(normalizedText) -> {
                stage = ScreenClassification.COMPLETED
                outcome = Outcome.COMPLETED
                outcomeReason = if (isCompletedFull(normalizedText)) "pay_breakdown" else "settlement_summary"
                assignedAssignmentId = chooseActiveAssignmentForCompletion(normalizedText)
                val pay = parseCompletionPay(exactLines)
                assignedAssignmentId?.let {
                    knownAssignments[it]?.apply {
                        this.outcome = Outcome.COMPLETED
                        this.finalPay = pay.finalPay
                        this.dashTotalAfter = dashTotal
                    }
                    activeAssignments.remove(it)
                }
                confidence = if (assignedAssignmentId == null) Confidence.LOW else Confidence.MEDIUM
            }
            isDeclineConfirmation(normalizedText) -> {
                stage = ScreenClassification.DECLINE_CONFIRMATION
                assignedAssignmentId = pendingOfferId
                persistReason = "decline_confirmation_visible"
                confidence = if (assignedAssignmentId == null) Confidence.LOW else Confidence.MEDIUM
            }
            offer != null -> {
                stage = ScreenClassification.NEW_OFFER
                assignedAssignmentId = rawAssignmentId
                rawAssignmentId?.let {
                    knownAssignments.putIfAbsent(
                        it,
                        AssignmentState(
                            assignmentId = it,
                            restaurants = offer.restaurants,
                            expectedPay = offer.expectedPay,
                            hasTotalWillBeHigher = offer.hasTotalWillBeHigher
                        )
                    )
                    pendingOfferId = it
                }
                confidence = Confidence.HIGH
                persistReason = "new_offer"
            }
            isArrived(normalizedText) -> {
                stage = ScreenClassification.ARRIVED
                assignedAssignmentId = chooseAssignmentForText(normalizedText) ?: promotePendingIfOnlyCandidate()
                assignedAssignmentId?.let {
                    activeAssignments.add(it)
                    pendingOfferId = null
                    knownAssignments[it]?.stage = ScreenClassification.ARRIVED
                }
                confidence = if (assignedAssignmentId == null) Confidence.LOW else Confidence.MEDIUM
            }
            isAccepted(normalizedText) -> {
                stage = ScreenClassification.ACCEPTED
                assignedAssignmentId = chooseAssignmentForText(normalizedText) ?: promotePendingIfOnlyCandidate()
                assignedAssignmentId?.let {
                    activeAssignments.add(it)
                    pendingOfferId = null
                    knownAssignments[it]?.stage = ScreenClassification.ACCEPTED
                }
                confidence = if (assignedAssignmentId == null) Confidence.LOW else Confidence.MEDIUM
            }
            isPickedUp(normalizedText) -> {
                stage = ScreenClassification.PICKED_UP
                assignedAssignmentId = chooseAssignmentForText(normalizedText) ?: activeAssignments.singleOrNull()
                assignedAssignmentId?.let {
                    knownAssignments[it]?.stage = ScreenClassification.PICKED_UP
                }
                confidence = if (assignedAssignmentId == null) Confidence.LOW else Confidence.MEDIUM
            }
            isNavigation(normalizedText) -> {
                stage = ScreenClassification.NAVIGATION
                persistReason = "navigation_skipped"
            }
        }

        if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED && stage != ScreenClassification.NEW_OFFER) {
            timestampSource = TimestampSource.CLICK_EVENT
        }

        val shouldPersist = shouldPersistStage(stage, confidence)
        val shouldCaptureScreenshot = shouldCaptureScreenshot(stage, confidence)
        val assignedState = assignedAssignmentId?.let(knownAssignments::get)
        val persistKey = listOf(
            assignedAssignmentId ?: rawAssignmentId ?: "route",
            stage.name,
            outcome?.name.orEmpty(),
            buttonTexts.joinToString(","),
            offer?.importantKey().orEmpty(),
            assignedState?.restaurantKey().orEmpty(),
            dashTotal.orEmpty()
        ).joinToString("|")

        return ScreenAnalysis(
            stage = stage,
            outcome = outcome,
            outcomeReason = outcomeReason,
            rawAssignmentId = rawAssignmentId,
            assignedAssignmentId = assignedAssignmentId,
            timestampSource = timestampSource,
            confidence = confidence,
            buttonTexts = buttonTexts,
            offer = offer,
            dashTotal = dashTotal,
            shouldPersist = shouldPersist,
            shouldCaptureScreenshot = shouldCaptureScreenshot,
            persistKey = persistKey,
            persistReason = persistReason
        )
    }

    private fun isNewOffer(lowerLines: Set<String>, normalizedText: String): Boolean =
        "decline" in lowerLines &&
            ("accept" in lowerLines || "add to route" in lowerLines) &&
            MONEY_PATTERN.containsMatchIn(normalizedText) &&
            MILES_PATTERN.containsMatchIn(normalizedText) &&
            normalizedText.contains("deliver by")

    private fun isDeclineConfirmation(normalizedText: String): Boolean =
        DECLINE_SURE_PATTERN.containsMatchIn(normalizedText) ||
            DECLINE_OFFER_PATTERN.containsMatchIn(normalizedText)

    private fun isAccepted(normalizedText: String): Boolean =
        normalizedText.contains("arrived at store") &&
            (normalizedText.contains("pickup from") ||
                normalizedText.contains("pick up by") ||
                normalizedText.contains("heading to") ||
                normalizedText.contains("current dash"))

    private fun isArrived(normalizedText: String): Boolean =
        ARRIVED_PATTERNS.any { it.containsMatchIn(normalizedText) }

    private fun isPickedUp(normalizedText: String): Boolean =
        PICKED_UP_STRONG_PATTERNS.any { it.containsMatchIn(normalizedText) }

    private fun isCompletedFull(normalizedText: String): Boolean =
        normalizedText.contains("this offer") &&
            (normalizedText.contains("doordash pay") || normalizedText.contains("base pay")) &&
            normalizedText.contains("customer tips") &&
            normalizedText.contains("continue dashing")

    private fun isCompletedSummary(normalizedText: String): Boolean =
        normalizedText.contains("this offer") &&
            normalizedText.contains("expand") &&
            normalizedText.contains("continue dashing")

    private fun isUnassigned(normalizedText: String): Boolean =
        normalizedText.contains("you've been unassigned from this order") ||
            normalizedText.contains("youve been unassigned from this order") ||
            Regex("\\bunassign order\\b").containsMatchIn(normalizedText)

    private fun isNavigation(normalizedText: String): Boolean =
        NAVIGATION_PATTERNS.any { it.containsMatchIn(normalizedText) }

    private fun shouldPersistStage(stage: ScreenClassification, confidence: Confidence): Boolean =
        when (stage) {
            ScreenClassification.NEW_OFFER,
            ScreenClassification.DECLINE_CONFIRMATION,
            ScreenClassification.ACCEPTED,
            ScreenClassification.ARRIVED,
            ScreenClassification.PICKED_UP,
            ScreenClassification.COMPLETED,
            ScreenClassification.UNASSIGNED -> true
            ScreenClassification.OTHER -> confidence == Confidence.LOW
            ScreenClassification.NAVIGATION -> false
        }

    private fun shouldCaptureScreenshot(stage: ScreenClassification, confidence: Confidence): Boolean =
        stage == ScreenClassification.NEW_OFFER ||
            stage == ScreenClassification.COMPLETED ||
            stage == ScreenClassification.UNASSIGNED ||
            confidence == Confidence.LOW

    private fun promotePendingIfOnlyCandidate(): String? {
        val pending = pendingOfferId ?: return activeAssignments.singleOrNull()
        activeAssignments.add(pending)
        pendingOfferId = null
        return pending
    }

    private fun chooseAssignmentForText(normalizedText: String): String? {
        val candidates = (listOfNotNull(pendingOfferId) + activeAssignments).distinct()
        val compactText = normalizeEntity(normalizedText)
        val matches = candidates.filter { assignmentId ->
            knownAssignments[assignmentId]?.restaurants.orEmpty().any { restaurant ->
                val normalizedRestaurant = normalizeEntity(restaurant)
                normalizedRestaurant.isNotBlank() && compactText.contains(normalizedRestaurant)
            }
        }
        return when {
            matches.size == 1 -> matches.first()
            candidates.size == 1 -> candidates.first()
            else -> null
        }
    }

    private fun chooseActiveAssignmentForCompletion(normalizedText: String): String? {
        val compactText = normalizeEntity(normalizedText)
        val matches = activeAssignments.filter { assignmentId ->
            knownAssignments[assignmentId]?.restaurants.orEmpty().any { restaurant ->
                val normalizedRestaurant = normalizeEntity(restaurant)
                normalizedRestaurant.isNotBlank() && compactText.contains(normalizedRestaurant)
            }
        }
        return when {
            matches.size == 1 -> matches.first()
            activeAssignments.size == 1 -> activeAssignments.first()
            else -> null
        }
    }

    private fun parseOffer(lines: List<String>, text: String): OfferFields {
        val restaurants = mutableListOf<String>()
        lines.forEachIndexed { index, line ->
            if (line.equals("Pickup", ignoreCase = true) ||
                line.equals("Retail pickup", ignoreCase = true) ||
                line.equals("Restaurant Pickup", ignoreCase = true)
            ) {
                lines.getOrNull(index + 1)
                    ?.takeUnless { it.equals("Customer dropoff", ignoreCase = true) }
                    ?.takeUnless { it.equals("Accept", ignoreCase = true) }
                    ?.takeUnless { it.equals("Decline", ignoreCase = true) }
                    ?.let(restaurants::add)
            }
        }
        val lowerLines = lines.map { it.lowercase(Locale.US) }.toSet()
        val pickupCount = lines.count {
            it.equals("Pickup", ignoreCase = true) ||
                it.equals("Retail pickup", ignoreCase = true) ||
                it.equals("Restaurant Pickup", ignoreCase = true)
        }
        val dropoffCount = lines.count { it.equals("Customer dropoff", ignoreCase = true) }
        val explicitOrders = ORDER_COUNT_PATTERN.find(text)?.groups?.drop(1)?.firstOrNull { it?.value != null }?.value?.toIntOrNull()
        val isAddToRoute = "add to route" in lowerLines
        val isBatched = pickupCount > 1 || dropoffCount > 1 || explicitOrders != null
        return OfferFields(
            expectedPay = MONEY_PATTERN.find(text)?.value?.replace(" ", "").orEmpty(),
            miles = MILES_PATTERN.find(text)?.value?.replace("Additional ", "").orEmpty(),
            deliverBy = DELIVER_BY_PATTERN.find(text)?.groups?.get(1)?.value.orEmpty(),
            restaurants = restaurants.distinct(),
            pickupCount = pickupCount,
            dropoffCount = dropoffCount,
            estimatedOrderCount = explicitOrders ?: maxOf(pickupCount, dropoffCount, 1),
            offerType = when {
                isAddToRoute -> "add_to_route"
                isBatched -> "batched"
                else -> "normal"
            },
            hasHighPayingOffer = text.contains("high paying offer", ignoreCase = true),
            hasTotalWillBeHigher = text.contains("total will be higher", ignoreCase = true) ||
                MONEY_PATTERN.find(text)?.value.orEmpty().contains("+")
        )
    }

    private fun parseCompletionPay(lines: List<String>): CompletionPay {
        val thisOfferIndex = lines.indexOfFirst { it.equals("This offer", ignoreCase = true) }
        val finalPay = if (thisOfferIndex >= 0) {
            lines.drop(thisOfferIndex + 1).take(6).firstOrNull { MONEY_PATTERN.matches(it) }.orEmpty()
        } else {
            ""
        }
        return CompletionPay(finalPay = finalPay)
    }

    private fun extractDashTotal(lines: List<String>): String? {
        val index = lines.indexOfFirst {
            it.equals("This dash", ignoreCase = true) ||
                it.equals("this dash", ignoreCase = true) ||
                it.equals("This dash so far", ignoreCase = true)
        }
        if (index < 0) return null
        findMoneyNear(lines, index - 7, index)?.let { return it }
        return findMoneyNear(lines, index + 1, index + 8)
    }

    private fun findMoneyNear(lines: List<String>, start: Int, end: Int): String? {
        val window = lines.subList(start.coerceAtLeast(0), end.coerceAtMost(lines.size))
        window.firstOrNull { MONEY_PATTERN.matches(it) }?.let { return it }
        val compact = window.joinToString(separator = "")
        return MONEY_PATTERN.find(compact)?.value
    }

    private fun extractButtonTexts(accessibilityValues: List<String>, visibleLines: List<String>): List<String> {
        val candidates = STAGE_BUTTON_TEXTS.filter { button ->
            visibleLines.any { it.equals(button, ignoreCase = true) }
        }
        return candidates.ifEmpty {
            accessibilityValues.filter { value ->
                STAGE_BUTTON_TEXTS.any { it.equals(value, ignoreCase = true) }
            }
        }.distinct()
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

    private data class ScreenAnalysis(
        val stage: ScreenClassification,
        val outcome: Outcome?,
        val outcomeReason: String?,
        val rawAssignmentId: String?,
        val assignedAssignmentId: String?,
        val timestampSource: TimestampSource,
        val confidence: Confidence,
        val buttonTexts: List<String>,
        val offer: OfferFields?,
        val dashTotal: String?,
        val shouldPersist: Boolean,
        val shouldCaptureScreenshot: Boolean,
        val persistKey: String,
        val persistReason: String
    )

    private data class AssignmentState(
        val assignmentId: String,
        val restaurants: List<String>,
        val expectedPay: String,
        val hasTotalWillBeHigher: Boolean,
        var stage: ScreenClassification = ScreenClassification.NEW_OFFER,
        var outcome: Outcome? = null,
        var finalPay: String = "",
        var dashTotalBefore: String? = null,
        var dashTotalAfter: String? = null
    ) {
        fun restaurantKey(): String = restaurants.joinToString(separator = ";") { normalizeEntity(it) }
    }

    private data class OfferFields(
        val expectedPay: String,
        val miles: String,
        val deliverBy: String,
        val restaurants: List<String>,
        val pickupCount: Int,
        val dropoffCount: Int,
        val estimatedOrderCount: Int,
        val offerType: String,
        val hasHighPayingOffer: Boolean,
        val hasTotalWillBeHigher: Boolean
    ) {
        fun importantKey(): String =
            listOf(expectedPay, miles, deliverBy, restaurants.joinToString(";"), offerType).joinToString("|")

        fun toJson(): JSONObject =
            JSONObject()
                .put("expectedPay", expectedPay)
                .put("miles", miles)
                .put("deliverBy", deliverBy)
                .put("restaurants", JSONArray(restaurants))
                .put("pickupCount", pickupCount)
                .put("dropoffCount", dropoffCount)
                .put("estimatedOrderCount", estimatedOrderCount)
                .put("offerType", offerType)
                .put("hasHighPayingOffer", hasHighPayingOffer)
                .put("hasTotalWillBeHigher", hasTotalWillBeHigher)
    }

    private data class CompletionPay(
        val finalPay: String
    )

    private enum class ScreenClassification {
        NEW_OFFER,
        DECLINE_CONFIRMATION,
        ACCEPTED,
        ARRIVED,
        PICKED_UP,
        COMPLETED,
        UNASSIGNED,
        NAVIGATION,
        OTHER
    }

    private enum class Outcome {
        COMPLETED,
        DECLINED,
        UNASSIGNED,
        UNKNOWN_OUTCOME
    }

    private enum class TimestampSource {
        CLICK_EVENT,
        FIRST_STAGE_SNAPSHOT,
        INFERRED_TRANSITION
    }

    private enum class Confidence {
        HIGH,
        MEDIUM,
        LOW
    }

    private class IntCounter {
        var value: Int = 0
    }

    private companion object {
        const val TAG = "DasherDataCollector"
        const val DOORDASH_PACKAGE_NAME = "com.doordash.driverapp"
        const val DEBOUNCE_MS = 500L
        const val PROBE_DIRECTORY = "dasher_data_collector"
        const val RAW_DIRECTORY = "raw"
        const val ACCESSIBILITY_TREE_FILE = "accessibility_tree.json"
        const val VISIBLE_TEXT_FILE = "visible_text.txt"
        const val META_FILE = "meta.json"
        const val SCREENSHOT_FILE = "screenshot.png"
        const val PNG_QUALITY = 100

        val MONEY_PATTERN = Regex("[+]?\\$\\s*\\d+(?:\\.\\d{2})?\\+?", RegexOption.IGNORE_CASE)
        val MILES_PATTERN = Regex("(?:additional\\s+)?\\b\\d+(?:\\.\\d+)?\\s*mi\\b", RegexOption.IGNORE_CASE)
        val DELIVER_BY_PATTERN = Regex("\\bdeliver by\\s+([0-9:]+\\s*[ap]m)\\b", RegexOption.IGNORE_CASE)
        val ORDER_COUNT_PATTERN = Regex("\\((\\d+)\\s+orders?\\)|\\b(\\d+)\\s+orders\\b", RegexOption.IGNORE_CASE)
        val DECLINE_SURE_PATTERN = Regex("\\bare you sure you want to decline this offer\\b\\??")
        val DECLINE_OFFER_PATTERN = Regex("\\bdecline offer\\b")
        val ARRIVED_PATTERNS = listOf(
            Regex("\\bstart pickup\\b"),
            Regex("\\bwaiting for your order\\??\\b"),
            Regex("\\bverify items\\b"),
            Regex("\\bscan barcodes\\b"),
            Regex("\\bcomplete pickup\\b"),
            Regex("\\ball items scanned\\b")
        )
        val PICKED_UP_STRONG_PATTERNS = listOf(
            Regex("\\bdeliver to\\b"),
            Regex("\\bdelivery for\\b"),
            Regex("\\btake photo of drop-off location\\b"),
            Regex("\\btake photo\\b"),
            Regex("\\bhanded order to customer\\b"),
            Regex("\\bhanded order directly to customer\\b"),
            Regex("\\bcomplete delivery\\b"),
            Regex("\\bcomplete delivery steps\\b"),
            Regex("\\bleave it at\\b")
        )
        val STAGE_BUTTON_TEXTS = listOf(
            "Accept",
            "Add to route",
            "Decline",
            "Decline offer",
            "Arrived at store",
            "Start pickup",
            "Verify items",
            "Scan barcodes",
            "Complete pickup",
            "Complete delivery",
            "Continue dashing"
        )
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

        fun normalizeEntity(value: String): String =
            value
                .lowercase(Locale.US)
                .replace(Regex("[^a-z0-9]+"), "")
    }
}
