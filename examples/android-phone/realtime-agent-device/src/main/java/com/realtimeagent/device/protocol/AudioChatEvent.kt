package com.realtimeagent.device.protocol

import java.util.UUID

data class AudioChatEvent(
    val event_name: String,
    val user_id: String,
    val producer_id: String = "",
    val session_id: String = "",
    val stream_id: String = "",
    val stream_type: String = "",
    val command_id: String? = null,
    val trace_id: String? = null,
    val task_trace_id: String? = null,
    val payload: Map<String, Any?> = emptyMap(),
    val version: String = "audio-chat.v1",
    val event_id: String = newId(),
    val timestamp_ms: Long = System.currentTimeMillis()
) {
    fun toJson(): String {
        return GsonFactory.gson.toJson(this)
    }

    fun toMap(): Map<String, Any?> {
        val map = mutableMapOf<String, Any?>()
        map["version"] = version
        map["event_id"] = event_id
        map["event_name"] = event_name
        map["timestamp_ms"] = timestamp_ms
        map["user_id"] = user_id
        map["producer_id"] = producer_id
        if (session_id.isNotEmpty()) map["session_id"] = session_id
        if (stream_id.isNotEmpty()) map["stream_id"] = stream_id
        if (stream_type.isNotEmpty()) map["stream_type"] = stream_type
        if (command_id != null) map["command_id"] = command_id
        if (trace_id != null) map["trace_id"] = trace_id
        if (task_trace_id != null) map["task_trace_id"] = task_trace_id
        map["payload"] = payload
        return map
    }

    companion object {
        fun fromJson(json: String): AudioChatEvent {
            val map = GsonFactory.fromJson<Map<String, Any?>>(json)
            return fromMap(map)
        }

        fun fromMap(map: Map<String, Any?>): AudioChatEvent {
            return AudioChatEvent(
                event_name = map["event_name"] as String,
                user_id = map["user_id"] as String,
                producer_id = map["producer_id"] as? String ?: "",
                session_id = map["session_id"] as? String ?: "",
                stream_id = map["stream_id"] as? String ?: "",
                stream_type = map["stream_type"] as? String ?: "",
                command_id = map["command_id"] as? String,
                trace_id = map["trace_id"] as? String,
                task_trace_id = map["task_trace_id"] as? String,
                payload = (map["payload"] as? Map<String, Any?>) ?: emptyMap(),
                version = map["version"] as? String ?: "audio-chat.v1",
                event_id = map["event_id"] as? String ?: newId(),
                timestamp_ms = (map["timestamp_ms"] as? Number)?.toLong() ?: System.currentTimeMillis()
            )
        }

        fun createRegisterEvent(
            userId: String,
            deviceId: String,
            deviceName: String = "android-phone",
            clientType: String = "android",
            properties: Map<String, Any?> = emptyMap(),
            supports: DeviceSupports = DeviceSupports()
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = EventTypes.DEVICE_REGISTER_REQUESTED,
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                payload = mapOf(
                    "device_id" to deviceId,
                    "name" to deviceName,
                    "client_type" to clientType,
                    "sdk_version" to "1.0.0-android",
                    "runtime" to mapOf(
                        "platform" to "android",
                        "language" to "kotlin"
                    ),
                    "properties" to properties,
                    "supports" to supports.toMap()
                )
            )
        }

        fun createHeartbeatEvent(userId: String, deviceId: String): AudioChatEvent {
            return AudioChatEvent(
                event_name = EventTypes.DEVICE_HEARTBEAT,
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                payload = mapOf("device_id" to deviceId)
            )
        }

        fun createStreamInputOpenedEvent(
            userId: String,
            deviceId: String,
            streamId: String,
            streamType: String,
            requestId: String? = null,
            extraPayload: Map<String, Any?> = emptyMap()
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = EventTypes.STREAM_INPUT_OPENED,
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                stream_id = streamId,
                stream_type = streamType,
                payload = mapOf(
                    "stream_type" to streamType,
                    "format" to mapOf(
                        "codec" to if (streamType == StreamTypes.SENSOR_RGB) "jpeg" else "pcm16le",
                        "sample_rate" to if (streamType == StreamTypes.SENSOR_MIC) 16000 else 1,
                        "channels" to 1,
                        "chunk_ms" to if (streamType == StreamTypes.SENSOR_MIC) 20 else 1
                    ),
                    "request_id" to requestId
                ) + extraPayload
            )
        }

        fun createStreamInputClosedEvent(
            userId: String,
            deviceId: String,
            streamId: String,
            streamType: String,
            reason: String = ""
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = EventTypes.STREAM_INPUT_CLOSED,
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                stream_id = streamId,
                stream_type = streamType,
                payload = mapOf(
                    "stream_type" to streamType,
                    "reason" to reason
                )
            )
        }

        fun createCommandAcceptedEvent(
            userId: String,
            deviceId: String,
            commandId: String? = null,
            taskId: String? = null,
            taskType: String? = null,
            state: String = "started"
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = "command.accepted",
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                payload = mutableMapOf<String, Any?>().apply {
                    commandId?.let { put("command_id", it) }
                    taskId?.let { put("task_id", it) }
                    taskType?.let { put("task_type", it) }
                    put("state", state)
                }.toMap()
            )
        }

        fun createCommandCompletedEvent(
            userId: String,
            deviceId: String,
            commandId: String? = null,
            taskId: String? = null,
            taskType: String? = null,
            result: Map<String, Any?> = emptyMap()
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = "command.completed",
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                payload = mutableMapOf<String, Any?>().apply {
                    commandId?.let { put("command_id", it) }
                    taskId?.let { put("task_id", it) }
                    taskType?.let { put("task_type", it) }
                    putAll(result)
                }.toMap()
            )
        }

        fun createCommandFailedEvent(
            userId: String,
            deviceId: String,
            message: String,
            commandId: String? = null
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = "command.failed",
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                payload = mutableMapOf<String, Any?>().apply {
                    put("message", message)
                    commandId?.let { put("command_id", it) }
                }.toMap()
            )
        }

        fun newId(prefix: String = ""): String {
            val uuid = UUID.randomUUID().toString().replace("-", "").substring(0, 12)
            return if (prefix.isNotEmpty()) "${prefix}_$uuid" else uuid
        }
    }
}
