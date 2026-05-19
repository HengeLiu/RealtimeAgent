package io.audiochat.device

import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.concurrent.TimeUnit

/**
 * 设备连接状态
 */
enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    ERROR
}

/**
 * 客户端事件监听器
 * 使用者实现此接口来处理各种事件
 */
interface AudioChatClientListener {
    // 连接状态
    fun onConnected() {}
    fun onDisconnected(code: Int, reason: String) {}
    fun onError(error: Throwable) {}

    // 控制事件
    fun onControlEvent(event: AudioChatEvent) {}
    fun onCommandRequested(commandId: String, taskType: String, payload: Map<String, Any?>) {}
    fun onStreamOpenRequested(streamId: String, streamType: String, payload: Map<String, Any?>) {}
    fun onSessionCloseRequested() {}
    fun onWakeRequested() {}
    fun onInterruptRequested() {}

    // 流事件
    fun onStreamConnected() {}
    fun onStreamDisconnected(code: Int, reason: String) {}
    fun onStreamChunk(chunk: StreamChunk) {}
    fun onStreamError(error: Throwable) {}

    // 重连
    fun onReconnectNeeded(type: String) {}
}

/**
 * AudioChat 设备客户端
 * 负责 WebSocket 连接、事件收发、心跳维持
 *
 * 使用示例：
 * ```kotlin
 * val device = AudioChatDevice.define("device-001")
 *     .user("user-001")
 *     .asPhone()
 *
 * val client = AudioChatClient(
 *     serverUrl = "http://127.0.0.1:8765",
 *     device = device
 * )
 *
 * client.listener = object : AudioChatClientListener {
 *     override fun onCommandRequested(commandId, taskType, payload) {
 *         // 处理命令
 *     }
 * }
 *
 * client.connect()
 * ```
 */
class AudioChatClient(
    private val serverUrl: String,
    val device: AudioChatDevice
) {
    // WebSocket（使用 OkHttp 风格接口，便于不同平台实现）
    private var controlWs: WebSocketConnection? = null
    private var streamWs: WebSocketConnection? = null

    // 状态
    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState

    private val _streamState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val streamState: StateFlow<ConnectionState> = _streamState

    // 协程作用域
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    // 心跳
    private var heartbeatJob: Job? = null
    private val heartbeatIntervalMs = 30000L

    // 重连
    private var isReconnecting = false
    private val maxReconnectAttempts = 3
    private var reconnectAttempt = 0

    // 监听器
    var listener: AudioChatClientListener? = null

    val isConnected: Boolean
        get() = _connectionState.value == ConnectionState.CONNECTED

    val isStreamConnected: Boolean
        get() = _streamState.value == ConnectionState.CONNECTED

    /**
     * 连接到服务器
     */
    fun connect() {
        scope.launch {
            _connectionState.value = ConnectionState.CONNECTING
            try {
                // 连接控制 WebSocket
                controlWs = WebSocketConnection(
                    url = "$serverUrl/ws/control",
                    listener = controlWsListener
                )
                controlWs?.connect()
            } catch (e: Exception) {
                _connectionState.value = ConnectionState.ERROR
                listener?.onError(e)
                scheduleReconnect("control")
            }
        }
    }

    /**
     * 连接流 WebSocket
     */
    fun connectStream(): Boolean {
        if (streamWs != null && isStreamConnected) return true

        return try {
            streamWs = WebSocketConnection(
                url = "$serverUrl/ws/stream?device_id=${device.device_id}",
                listener = streamWsListener
            )
            streamWs?.connect()
            true
        } catch (e: Exception) {
            listener?.onStreamError(e)
            false
        }
    }

    /**
     * 断开连接
     */
    fun disconnect() {
        heartbeatJob?.cancel()
        heartbeatJob = null

        controlWs?.close(1000, "client disconnect")
        streamWs?.close(1000, "client disconnect")

        controlWs = null
        streamWs = null

        _connectionState.value = ConnectionState.DISCONNECTED
        _streamState.value = ConnectionState.DISCONNECTED
    }

    /**
     * 发送控制事件
     */
    fun sendEvent(event: AudioChatEvent): Boolean {
        return controlWs?.send(event.toJson()) ?: false
    }

    /**
     * 发送流数据
     */
    fun sendChunk(chunk: StreamChunk): Boolean {
        return streamWs?.sendBinary(StreamChunkCodec.encode(chunk)) ?: false
    }

    /**
     * 注册设备
     */
    fun register(): Boolean {
        val payload = device.registrationPayload()
        val event = AudioChatEvent(
            event_name = EventTypes.DEVICE_REGISTER_REQUESTED,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            payload = payload
        )
        return sendEvent(event)
    }

    /**
     * 发送心跳
     */
    fun sendHeartbeat(): Boolean {
        val event = AudioChatEvent(
            event_name = EventTypes.DEVICE_HEARTBEAT,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            payload = mapOf("device_id" to device.device_id)
        )
        return sendEvent(event)
    }

    /**
     * 发送命令接受事件
     */
    fun sendCommandAccepted(commandId: String, taskId: String? = null, taskType: String? = null): Boolean {
        val event = AudioChatEvent(
            event_name = EventTypes.COMMAND_COMPLETED,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            command_id = commandId,
            payload = mutableMapOf<String, Any?>().apply {
                put("command_id", commandId)
                taskId?.let { put("task_id", it) }
                taskType?.let { put("task_type", it) }
                put("state", "started")
            }
        )
        return sendEvent(event)
    }

    /**
     * 发送命令完成事件
     */
    fun sendCommandCompleted(commandId: String, taskId: String? = null, taskType: String? = null, result: Map<String, Any?> = emptyMap()): Boolean {
        val event = AudioChatEvent(
            event_name = EventTypes.COMMAND_COMPLETED,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            command_id = commandId,
            payload = mutableMapOf<String, Any?>().apply {
                put("command_id", commandId)
                taskId?.let { put("task_id", it) }
                taskType?.let { put("task_type", it) }
                putAll(result)
            }
        )
        return sendEvent(event)
    }

    /**
     * 发送命令失败事件
     */
    fun sendCommandFailed(commandId: String, message: String): Boolean {
        val event = AudioChatEvent(
            event_name = EventTypes.COMMAND_FAILED,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            command_id = commandId,
            payload = mapOf("command_id" to commandId, "message" to message)
        )
        return sendEvent(event)
    }

    /**
     * 发送流打开事件
     */
    fun sendStreamInputOpened(streamId: String, streamType: String, requestId: String? = null): Boolean {
        val event = AudioChatEvent(
            event_name = EventTypes.STREAM_INPUT_OPENED,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            stream_id = streamId,
            stream_type = streamType,
            payload = mutableMapOf<String, Any?>().apply {
                put("stream_type", streamType)
                put("format", if (streamType == StreamTypes.SENSOR_RGB) mapOf("codec" to "jpeg", "sample_rate" to 1, "channels" to 1) else mapOf("codec" to "pcm16le", "sample_rate" to 16000, "channels" to 1))
                requestId?.let { put("request_id", it) }
            }
        )
        return sendEvent(event)
    }

    /**
     * 发送流关闭事件
     */
    fun sendStreamInputClosed(streamId: String, streamType: String, reason: String = ""): Boolean {
        val event = AudioChatEvent(
            event_name = EventTypes.STREAM_INPUT_CLOSED,
            user_id = device.user_id.takeIf { it.isNotBlank() } ?: "",
            producer_id = device.device_id,
            stream_id = streamId,
            stream_type = streamType,
            payload = mapOf("stream_type" to streamType, "reason" to reason)
        )
        return sendEvent(event)
    }

    // ==================== 心跳 ====================

    private fun startHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            while (isActive && isConnected) {
                delay(heartbeatIntervalMs)
                sendHeartbeat()
            }
        }
    }

    // ==================== 重连 ====================

    private fun scheduleReconnect(type: String) {
        if (isReconnecting) return
        isReconnecting = true
        reconnectAttempt = 0
        listener?.onReconnectNeeded(type)
    }

    fun reconnect(type: String) {
        if (reconnectAttempt > maxReconnectAttempts) {
            isReconnecting = false
            return
        }
        reconnectAttempt++

        scope.launch {
            delay(1000L * reconnectAttempt) // 指数退避
            when (type) {
                "control" -> connect()
                "stream" -> connectStream()
            }
        }
    }

    // ==================== 控制 WebSocket 监听器 ====================

    private val controlWsListener = object : WebSocketConnection.Listener {
        override fun onOpen() {
            _connectionState.value = ConnectionState.CONNECTED
            isReconnecting = false
            reconnectAttempt = 0
            listener?.onConnected()
            startHeartbeat()
            register()
        }

        override fun onMessage(text: String) {
            try {
                val event = AudioChatEvent.fromJson(text)
                handleControlEvent(event)
            } catch (e: Exception) {
                // 解析失败
            }
        }

        override fun onClose(code: Int, reason: String) {
            _connectionState.value = ConnectionState.DISCONNECTED
            heartbeatJob?.cancel()
            listener?.onDisconnected(code, reason)
            if (code != 1000) {
                scheduleReconnect("control")
            }
        }

        override fun onError(error: Throwable) {
            _connectionState.value = ConnectionState.ERROR
            listener?.onError(error)
        }
    }

    // ==================== 流 WebSocket 监听器 ====================

    private val streamWsListener = object : WebSocketConnection.Listener {
        override fun onOpen() {
            _streamState.value = ConnectionState.CONNECTED
            listener?.onStreamConnected()
        }

        override fun onMessage(text: String) {
            // 流 WebSocket 不处理文本消息
        }

        override fun onMessage(bytes: ByteArray) {
            try {
                val chunk = StreamChunkCodec.decode(bytes)
                listener?.onStreamChunk(chunk)
            } catch (e: Exception) {
                listener?.onStreamError(e)
            }
        }

        override fun onClose(code: Int, reason: String) {
            _streamState.value = ConnectionState.DISCONNECTED
            listener?.onStreamDisconnected(code, reason)
        }

        override fun onError(error: Throwable) {
            _streamState.value = ConnectionState.ERROR
            listener?.onStreamError(error)
        }
    }

    // ==================== 事件处理 ====================

    private fun handleControlEvent(event: AudioChatEvent) {
        listener?.onControlEvent(event)

        when (event.event_name) {
            EventTypes.COMMAND_REQUESTED -> {
                val commandId = event.command_id ?: (event.payload["command_id"] as? String) ?: ""
                val taskType = event.payload["task_type"] as? String ?: ""
                listener?.onCommandRequested(commandId, taskType, event.payload)
            }
            EventTypes.STREAM_CONTROL_OPEN_REQUESTED -> {
                val streamId = event.stream_id ?: newId("stream")
                val streamType = event.stream_type ?: (event.payload["stream_type"] as? String) ?: ""
                listener?.onStreamOpenRequested(streamId, streamType, event.payload)
            }
            EventTypes.SESSION_CLOSE_REQUESTED -> {
                listener?.onSessionCloseRequested()
            }
            EventTypes.DEVICE_WAKE_REQUESTED -> {
                listener?.onWakeRequested()
            }
            EventTypes.DEVICE_INTERRUPT_REQUESTED -> {
                listener?.onInterruptRequested()
            }
        }
    }
}

/**
 * WebSocket 连接接口
 * 平台实现此接口以适配不同的 WebSocket 库
 */
abstract class WebSocketConnection(
    val url: String,
    protected val listener: Listener
) {
    interface Listener {
        fun onOpen() {}
        fun onMessage(text: String) {}
        fun onMessage(bytes: ByteArray) {}
        fun onClose(code: Int, reason: String) {}
        fun onError(error: Throwable) {}
    }

    abstract fun connect()
    abstract fun send(text: String): Boolean
    abstract fun sendBinary(bytes: ByteArray): Boolean
    abstract fun close(code: Int, reason: String)
}