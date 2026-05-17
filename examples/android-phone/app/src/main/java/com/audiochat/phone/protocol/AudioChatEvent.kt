package com.audiochat.phone.protocol

import java.util.UUID

/**
 * AudioChat 协议事件
 * 完全复刻 Python 端 AudioChatEvent 协议
 */
data class AudioChatEvent(
    val event_name: String,
    val user_id: String,
    val producer_id: String = "",
    val session_id: String = "",
    val stream_id: String = "",
    val stream_type: String = "",
    val command_id: String? = null,
    val payload: Map<String, Any?> = emptyMap(),
    val version: String = "audio-chat.v1",
    val timestamp_ms: Long = System.currentTimeMillis()
) {
    fun toJson(): String {
        return GsonFactory.gson.toJson(this)
    }

    companion object {
        fun fromJson(json: String): AudioChatEvent {
            return GsonFactory.gson.fromJson(json, AudioChatEvent::class.java)
        }

        /**
         * 创建设备注册事件
         */
        fun createRegisterEvent(
            userId: String,
            deviceId: String,
            deviceName: String = "android-phone",
            clientType: String = "android",
            properties: Map<String, Any?> = emptyMap(),
            supports: DeviceSupports = DeviceSupports()
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = "control.device.register.requested",
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

        /**
         * 创建心跳事件
         */
        fun createHeartbeatEvent(userId: String, deviceId: String): AudioChatEvent {
            return AudioChatEvent(
                event_name = "control.device.heartbeat.received",
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                payload = mapOf("device_id" to deviceId)
            )
        }

        /**
         * 创建流打开响应事件
         */
        fun createStreamInputOpenedEvent(
            userId: String,
            deviceId: String,
            streamId: String,
            streamType: String,
            requestId: String? = null,
            extraPayload: Map<String, Any?> = emptyMap()
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = "stream.input.opened",
                user_id = userId,
                producer_id = deviceId,
                session_id = deviceId,
                stream_id = streamId,
                stream_type = streamType,
                payload = mapOf(
                    "stream_type" to streamType,
                    "format" to mapOf(
                        "codec" to if (streamType == "sensor.rgb") "jpeg" else "pcm16le",
                        "sample_rate" to if (streamType == "sensor.mic") 16000 else 1,
                        "channels" to 1,
                        "chunk_ms" to if (streamType == "sensor.mic") 20 else 1
                    ),
                    "request_id" to requestId
                ) + extraPayload
            )
        }

        /**
         * 创建流关闭事件
         */
        fun createStreamInputClosedEvent(
            userId: String,
            deviceId: String,
            streamId: String,
            streamType: String,
            reason: String = ""
        ): AudioChatEvent {
            return AudioChatEvent(
                event_name = "stream.input.closed",
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

        /**
         * 创建命令接受事件
         */
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

        /**
         * 创建命令完成事件
         */
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

        /**
         * 创建命令失败事件
         */
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

        /**
         * 生成唯一 ID
         */
        fun newId(prefix: String = ""): String {
            val uuid = UUID.randomUUID().toString().replace("-", "").substring(0, 12)
            return if (prefix.isNotEmpty()) "${prefix}_$uuid" else uuid
        }
    }
}