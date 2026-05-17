package com.audiochat.phone.device

import android.content.Context
import android.util.Log
import com.audiochat.phone.network.AudioChatEventListener
import com.audiochat.phone.network.AudioChatWebSocketClient
import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.DeviceSupports
import com.audiochat.phone.protocol.StreamChunk
import com.audiochat.phone.protocol.StreamChunkCodec
import com.audiochat.phone.video.PeerVideoTaskManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 设备管理器
 * 完全复刻 Python 端 NetworkPythonPhoneMockEndpoint 的核心逻辑：
 * 1. 设备注册
 * 2. 心跳保持
 * 3. 控制事件处理
 * 4. 流数据传输
 * 5. Peer Video 接收
 */
class DeviceManager(
    private val serverUrl: String,
    val userId: String,
    val deviceId: String,
    private val accessToken: String? = null,
    private val deviceName: String = "android-phone",
    private val properties: Map<String, Any?> = emptyMap(),
    private val supports: DeviceSupports = DeviceSupports(),
    private val context: Context? = null
) {
    companion object {
        private const val TAG = "DeviceManager"
        private const val DEFAULT_HEARTBEAT_INTERVAL_MS = 10_000L
    }

    private var wsClient: AudioChatWebSocketClient? = null
    private var heartbeatJob: Job? = null
    private val isRegistered = AtomicBoolean(false)
    private val isRunning = AtomicBoolean(false)
    private var peerVideoTaskManager: PeerVideoTaskManager? = null

    // 回调接口
    var onRgbCaptureRequest: ((requestId: String) -> Unit)? = null
    var onCommandReceived: ((event: AudioChatEvent) -> Unit)? = null
    var onAudioOutputChunk: ((chunk: StreamChunk) -> Unit)? = null
    var onDeviceRegistered: (() -> Unit)? = null
    var onStreamConnected: (() -> Unit)? = null
    var onHeartbeatReceived: (() -> Unit)? = null
    var onEventReceived: ((eventName: String, detail: String) -> Unit)? = null
    var onRawMessage: ((direction: String, message: String) -> Unit)? = null
    var onPeerVideoFrame: ((PeerVideoTaskManager.FrameResult) -> Unit)? = null
    var onPeerVideoTaskCompleted: ((Map<String, Any>) -> Unit)? = null
    var onPeerVideoClientConnected: ((String) -> Unit)? = null
    var onPeerVideoClientDisconnected: (() -> Unit)? = null
    var onReconnectNeeded: (() -> Unit)? = null
    var onYoloModelLoaded: ((Boolean) -> Unit)? = null

    /**
     * 连接并注册设备
     */
    suspend fun connectAndRegister() {
        if (isRunning.get()) return

        isRunning.set(true)
        wsClient = AudioChatWebSocketClient(
            serverUrl = serverUrl,
            userId = userId,
            deviceId = deviceId,
            accessToken = accessToken,
            eventListener = createEventListener()
        )

        if (context != null) {
            peerVideoTaskManager = PeerVideoTaskManager(context, this)
            val initialized = peerVideoTaskManager?.initialize() ?: false
            peerVideoTaskManager?.onFrameProcessed = { frameResult ->
                onPeerVideoFrame?.invoke(frameResult)
            }
            peerVideoTaskManager?.onTaskCompleted = { result ->
                onPeerVideoTaskCompleted?.invoke(result)
            }
            peerVideoTaskManager?.onPeerConnected = { clientIp ->
                onPeerVideoClientConnected?.invoke(clientIp)
            }
            peerVideoTaskManager?.onPeerDisconnected = {
                onPeerVideoClientDisconnected?.invoke()
            }
            onYoloModelLoaded?.invoke(initialized)
        }

        try {
            // 连接控制 WebSocket
            wsClient?.connectControl()
            
            // 等待连接建立
            delay(500)
            
            // 发送设备注册请求
            val registerEvent = AudioChatEvent.createRegisterEvent(
                userId = userId,
                deviceId = deviceId,
                deviceName = deviceName,
                properties = properties,
                supports = supports
            )
            wsClient?.sendEvent(registerEvent)
            Log.i(TAG, "设备注册请求已发送: $deviceId")
            
        } catch (e: Exception) {
            Log.e(TAG, "连接失败", e)
            isRunning.set(false)
            throw e
        }
    }

    /**
     * 启动流连接和心跳
     * 注意：Stream WebSocket 现在按需建立，不再在启动时自动连接
     */
    fun startStreamAndHeartbeat() {
        if (!isRunning.get()) return

        // 不再自动连接 Stream WebSocket，按需建立
        // 启动心跳（心跳通过 Control WebSocket 发送）
        startHeartbeat(DEFAULT_HEARTBEAT_INTERVAL_MS)
    }

    /**
     * 确保流连接已建立（按需）
     */
    fun ensureStreamConnected(): Boolean {
        return wsClient?.ensureStreamConnected() ?: false
    }

    /**
     * 断开连接
     */
    fun disconnect() {
        isRunning.set(false)
        isRegistered.set(false)
        
        heartbeatJob?.cancel()
        heartbeatJob = null
        
        wsClient?.disconnect()
        wsClient = null
        
        Log.i(TAG, "设备已断开连接")
    }

    /**
     * 上传 RGB 图片
     */
    fun uploadRgbImage(jpegData: ByteArray, requestId: String) {
        if (!isRunning.get() || wsClient == null) {
            Log.w(TAG, "未连接，无法上传图片")
            return
        }

        val scope = CoroutineScope(Dispatchers.IO)
        scope.launch {
            try {
                // 按需建立流连接
                if (!ensureStreamConnected()) {
                    Log.e(TAG, "无法建立流连接")
                    return@launch
                }

                val streamId = AudioChatEvent.newId("stream_rgb")

                // 发送流打开事件
                val openedEvent = AudioChatEvent.createStreamInputOpenedEvent(
                    userId = userId,
                    deviceId = deviceId,
                    streamId = streamId,
                    streamType = "sensor.rgb",
                    requestId = requestId
                )
                wsClient?.sendEvent(openedEvent)

                // 发送图片数据
                val imageChunk = StreamChunkCodec.createImageChunk(
                    userId = userId,
                    sessionId = deviceId,
                    streamId = streamId,
                    jpegData = jpegData,
                    seq = 0,
                    requestId = requestId
                )
                wsClient?.sendChunk(imageChunk)

                // 发送流关闭事件
                delay(50)
                val closedEvent = AudioChatEvent.createStreamInputClosedEvent(
                    userId = userId,
                    deviceId = deviceId,
                    streamId = streamId,
                    streamType = "sensor.rgb",
                    reason = "android_phone_rgb_uploaded"
                )
                wsClient?.sendEvent(closedEvent)

                Log.d(TAG, "RGB 图片上传完成: ${jpegData.size} bytes")
            } catch (e: Exception) {
                Log.e(TAG, "上传 RGB 图片失败", e)
            }
        }
    }

    /**
     * 上传音频数据
     */
    fun uploadAudioData(pcmData: ByteArray, streamId: String, seq: Int, isFinal: Boolean = false) {
        if (wsClient == null) return

        val chunk = StreamChunkCodec.createAudioChunk(
            userId = userId,
            sessionId = deviceId,
            streamId = streamId,
            pcmData = pcmData,
            seq = seq,
            isFinal = isFinal
        )
        wsClient?.sendChunk(chunk)
    }

    /**
     * 发送命令响应
     */
    fun sendCommandResponse(event: AudioChatEvent) {
        wsClient?.sendEvent(event)
    }

    /**
     * 发送任意事件
     */
    fun sendEvent(event: AudioChatEvent) {
        wsClient?.sendEvent(event)
        onRawMessage?.invoke("send", event.toJson())
    }

    /**
     * 创建事件监听器
     */
    private fun createEventListener(): AudioChatEventListener {
        return object : AudioChatEventListener {
            override fun onControlConnected() {
                Log.i(TAG, "控制 WebSocket 已连接")
            }

            override fun onControlEvent(event: AudioChatEvent) {
                onRawMessage?.invoke("recv", event.toJson())
                onEventReceived?.invoke(event.event_name, event.payload.toString())
                handleControlEvent(event)
            }

            override fun onControlError(error: Throwable) {
                Log.e(TAG, "控制 WebSocket 错误", error)
                onEventReceived?.invoke("control.error", error.message ?: "")
            }

            override fun onControlDisconnected(code: Int, reason: String) {
                Log.w(TAG, "控制 WebSocket 已关闭: code=$code reason=$reason")
                isRunning.set(false)
                isRegistered.set(false)
                onEventReceived?.invoke("control.disconnected", "code=$code reason=$reason")
                onReconnectNeeded?.invoke()
            }

            override fun onReconnectNeeded(type: String) {
                Log.w(TAG, "WebSocket $type 需要重连")
                onReconnectNeeded?.invoke()
            }

            override fun onStreamConnected() {
                Log.i(TAG, "流 WebSocket 已连接")
                onStreamConnected?.invoke()
                onEventReceived?.invoke("stream.connected", "")
            }

            override fun onStreamChunk(chunk: StreamChunk) {
                when (chunk.stream_type) {
                    "actuator.speaker" -> {
                        onAudioOutputChunk?.invoke(chunk)
                    }
                    else -> {
                        Log.d(TAG, "收到其他流数据: ${chunk.stream_type}")
                    }
                }
            }

            override fun onStreamError(error: Throwable) {
                Log.e(TAG, "流 WebSocket 错误", error)
                onEventReceived?.invoke("stream.error", error.message ?: "")
            }

            override fun onStreamDisconnected(code: Int, reason: String) {
                Log.w(TAG, "流 WebSocket 已关闭: code=$code reason=$reason")
                onEventReceived?.invoke("stream.disconnected", "code=$code reason=$reason")
            }
        }
    }

    /**
     * 处理控制事件
     */
    private fun handleControlEvent(event: AudioChatEvent) {
        Log.d(TAG, "处理控制事件: ${event.event_name}")

        when (event.event_name) {
            "control.device.registered" -> {
                Log.i(TAG, "设备注册成功!")
                isRegistered.set(true)
                
                // 提取心跳间隔
                val heartbeatInterval = (event.payload["heartbeat_interval_seconds"] as? Number)?.toLong()
                    ?.times(1000) ?: DEFAULT_HEARTBEAT_INTERVAL_MS
                
                // 重启心跳（使用 server 返回的间隔）
                heartbeatJob?.cancel()
                startHeartbeat(heartbeatInterval)
                
                onDeviceRegistered?.invoke()
            }

            "control.device.register.failed" -> {
                Log.e(TAG, "设备注册失败: ${event.payload}")
                isRegistered.set(false)
            }

            "stream.control.open.requested" -> {
                // Server 请求打开传感器流
                when (event.stream_type) {
                    "sensor.rgb" -> {
                        val requestId = event.payload["request_id"] as? String
                        Log.i(TAG, "收到 RGB 采集请求: requestId=$requestId")
                        onRgbCaptureRequest?.invoke(requestId ?: "")
                    }
                    else -> {
                        Log.w(TAG, "不支持的流类型请求: ${event.stream_type}")
                    }
                }
            }

            "command.requested" -> {
                // 收到命令请求
                Log.i(TAG, "收到命令请求: ${event.payload}")
                handleCommandRequest(event)
                onCommandReceived?.invoke(event)
            }

            "stream.output.close.requested" -> {
                // Server 请求关闭输出流
                val finishedEvent = AudioChatEvent(
                    event_name = "stream.output.finished",
                    user_id = userId,
                    producer_id = deviceId,
                    session_id = deviceId,
                    stream_id = event.stream_id,
                    stream_type = event.stream_type,
                    payload = mapOf("stream_type" to event.stream_type)
                )
                wsClient?.sendEvent(finishedEvent)

                val closedEvent = AudioChatEvent(
                    event_name = "stream.output.closed",
                    user_id = userId,
                    producer_id = deviceId,
                    session_id = deviceId,
                    stream_id = event.stream_id,
                    stream_type = event.stream_type,
                    payload = mapOf("stream_type" to event.stream_type, "reason" to "android_phone_closed")
                )
                wsClient?.sendEvent(closedEvent)
            }

            else -> {
                Log.d(TAG, "未处理的事件: ${event.event_name}")
            }
        }
    }

    /**
     * 处理命令请求
     */
    private fun handleCommandRequest(event: AudioChatEvent) {
        val command = event.payload["command"] as? String ?: return

        when (command) {
            "peer.video.receiver.start" -> {
                Log.i(TAG, "处理 peer.video.receiver.start 命令")
                peerVideoTaskManager?.handleReceiverStartCommand(event)
            }
            "peer.video.receiver.stop" -> {
                Log.i(TAG, "处理 peer.video.receiver.stop 命令")
                peerVideoTaskManager?.handleReceiverStopCommand(event)
            }
            else -> {
                Log.w(TAG, "未处理的命令: $command")
            }
        }
    }

    /**
     * 启动心跳
     */
    private fun startHeartbeat(intervalMs: Long) {
        heartbeatJob = CoroutineScope(Dispatchers.IO).launch {
            while (isActive && isRunning.get()) {
                try {
                    delay(intervalMs)
                    
                    if (isRunning.get()) {
                        val heartbeatEvent = AudioChatEvent.createHeartbeatEvent(userId, deviceId)
                        wsClient?.sendEvent(heartbeatEvent)
                        onHeartbeatReceived?.invoke()
                        Log.d(TAG, "心跳已发送")
                    }
                } catch (e: Exception) {
                    if (isActive) {
                        Log.e(TAG, "心跳发送失败", e)
                    }
                }
            }
        }
    }

    val registered: Boolean
        get() = isRegistered.get()

    val running: Boolean
        get() = isRunning.get()
}