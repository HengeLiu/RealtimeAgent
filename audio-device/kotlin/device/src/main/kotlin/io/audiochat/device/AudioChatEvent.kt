package io.audiochat.device

import java.util.UUID

const val AUDIO_CHAT_PROTOCOL_VERSION = "audio-chat.v1"

fun nowMs(): Long = System.currentTimeMillis()

fun newId(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "").take(12)}"

fun validateEventName(eventName: String) {
    require(Regex("^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$").matches(eventName) && !eventName.contains("*")) {
        "invalid event_name format: $eventName"
    }
}

data class AudioChatEvent(
    val eventName: String,
    val userId: String,
    val producerId: String,
    val payload: Map<String, Any?> = emptyMap(),
    val version: String = AUDIO_CHAT_PROTOCOL_VERSION,
    val eventId: String = newId("evt"),
    val timestampMs: Long = nowMs(),
    val sessionId: String? = null,
    val streamId: String? = null,
    val streamType: String? = null,
) {
    fun toMap(): Map<String, Any?> {
        validateEventName(eventName)
        val data = linkedMapOf<String, Any?>(
            "version" to version,
            "event_id" to eventId,
            "event_name" to eventName,
            "timestamp_ms" to timestampMs,
            "user_id" to userId,
            "producer_id" to producerId,
            "payload" to payload,
        )
        if (sessionId != null) data["session_id"] = sessionId
        if (streamId != null) data["stream_id"] = streamId
        if (streamType != null) data["stream_type"] = streamType
        return data
    }
}
