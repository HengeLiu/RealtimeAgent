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

/**
 * 协议事件信封
 * 对应 audio-chat.v1 协议
 */
data class AudioChatEvent(
    val event_name: String,
    val user_id: String,
    val producer_id: String,
    val payload: Map<String, Any?> = emptyMap(),
    val version: String = AUDIO_CHAT_PROTOCOL_VERSION,
    val event_id: String = newId("evt"),
    val timestamp_ms: Long = nowMs(),
    val session_id: String? = null,
    val stream_id: String? = null,
    val stream_type: String? = null,
    val command_id: String? = null,
    val trace_id: String? = null,
    val task_trace_id: String? = null
) {
    fun toMap(): Map<String, Any?> {
        validateEventName(event_name)
        val data = linkedMapOf<String, Any?>(
            "version" to version,
            "event_id" to event_id,
            "event_name" to event_name,
            "timestamp_ms" to timestamp_ms,
            "user_id" to user_id,
            "producer_id" to producer_id,
            "payload" to payload
        )
        session_id?.let { data["session_id"] = it }
        stream_id?.let { data["stream_id"] = it }
        stream_type?.let { data["stream_type"] = it }
        command_id?.let { data["command_id"] = it }
        trace_id?.let { data["trace_id"] = it }
        task_trace_id?.let { data["task_trace_id"] = it }
        return data
    }

    companion object {
        fun fromMap(map: Map<String, Any?>): AudioChatEvent {
            return AudioChatEvent(
                event_name = map["event_name"] as String,
                user_id = map["user_id"] as String,
                producer_id = map["producer_id"] as String,
                payload = (map["payload"] as? Map<String, Any?>) ?: emptyMap(),
                version = map["version"] as? String ?: AUDIO_CHAT_PROTOCOL_VERSION,
                event_id = map["event_id"] as? String ?: newId("evt"),
                timestamp_ms = (map["timestamp_ms"] as? Number)?.toLong() ?: nowMs(),
                session_id = map["session_id"] as? String,
                stream_id = map["stream_id"] as? String,
                stream_type = map["stream_type"] as? String,
                command_id = map["command_id"] as? String,
                trace_id = map["trace_id"] as? String,
                task_trace_id = map["task_trace_id"] as? String
            )
        }

        fun fromJson(json: String): AudioChatEvent {
            val map = GsonFactory.fromJson<Map<String, Any?>>(json)
            return fromMap(map)
        }
    }

    fun toJson(): String = GsonFactory.toJson(toMap())
}

/**
 * 设备能力声明
 */
data class DeviceSupports(
    val sensors: List<SensorConfig> = listOf(
        SensorConfig(type = "rgb", modes = listOf("single", "continuous"), default = SensorDefault())
    ),
    val actuators: List<ActuatorConfig> = listOf(
        ActuatorConfig(type = "vibrator", commands = listOf("vibrate"))
    )
) {
    fun toMap(): Map<String, Any> {
        return mapOf(
            "sensors" to sensors.map { it.toMap() },
            "actuators" to actuators.map { it.toMap() }
        )
    }
}

data class SensorConfig(
    val type: String,
    val modes: List<String> = emptyList(),
    val default: SensorDefault? = null
) {
    fun toMap(): Map<String, Any> {
        return buildMap {
            put("type", type)
            if (modes.isNotEmpty()) put("modes", modes)
            default?.let { put("default", it.toMap()) }
        }
    }
}

data class SensorDefault(
    val format: String = "jpeg",
    val frequency_hz: Int = 1,
    val sample_count: Int = 1
) {
    fun toMap(): Map<String, Any> {
        return mapOf(
            "format" to format,
            "frequency_hz" to frequency_hz,
            "sample_count" to sample_count
        )
    }
}

data class ActuatorConfig(
    val type: String,
    val commands: List<String> = emptyList()
) {
    fun toMap(): Map<String, Any> {
        return buildMap {
            put("type", type)
            if (commands.isNotEmpty()) put("commands", commands)
        }
    }
}