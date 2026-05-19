package com.audiochat.phone.device

import android.content.Context
import android.util.Log
import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.DeviceSupports
import com.audiochat.phone.protocol.StreamChunk
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 设备管理器
 * 实现 Device 接口，负责设备注册、心跳、命令处理等业务逻辑
 * 底层通过 DeviceConnection 与服务器通信，解耦 WebSocket 实现
 */
class DeviceManager private constructor(
    private val config: DeviceConfig,
    private val connection: DeviceConnection
) : Device {

    companion object {
        private const val TAG = "DeviceManager"
        private const val DEFAULT_HEARTBEAT_INTERVAL_MS = 10_000L

        /**
         * 创建设备管理器
         */
        fun create(config: DeviceConfig): DeviceManager {
            val connection = WebSocketDeviceConnection(
                serverUrl = config.serverUrl,
                userId = config.userId,
                deviceId = config.deviceId,
                accessToken = config.accessToken
            )
            return DeviceManager(config, connection)
        }
    }

    override val deviceId: String = config.deviceId
    override val userId: String = config.userId

    private var listener: DeviceListener? = null
    private var context: Context? = null

    private val isRegistered = AtomicBoolean(false)
    private val isRunning = AtomicBoolean(false)
    private var heartbeatJob: Job? = null

    init {
        connection.setListener(createConnectionListener())
    }

    override fun setListener(listener: DeviceListener?) {
        this.listener = listener
    }

    override fun setContext(context: Context) {
        this.context = context
    }

    override val isRegistered: Boolean
        get() = isRegistered.get()

    override val isRunning: Boolean
        get() = isRunning.get()

    /**
     * 连接到服务器并注册设备
     */
    override suspend fun connect() {
        if (isRunning.get()) return

        isRunning.set(true)

        try {
            // 连接控制通道
            connection.connectControl()

            // 等待连接建立
            delay(500)

            // 发送设备注册请求
            val registerEvent = AudioChatEvent.createRegisterEvent(
                userId = userId,
                deviceId = deviceId,
                deviceName = config.deviceName,
                properties = config.properties,
                supports = config.supports
            )
            connection.sendEvent(registerEvent)
            Log.i(TAG, "设备注册请求已发送: $deviceId")

        } catch (e: Exception) {
            Log.e(TAG, "连接失败", e)
            isRunning.set(false)
            throw e
        }
    }

    /**
     * 启动流连接和心跳
     */
    override fun start() {
        if (!isRunning.get()) return
        startHeartbeat(DEFAULT_HEARTBEAT_INTERVAL_MS)
    }

    /**
     * 断开连接
     */
    override fun disconnect() {
        isRunning.set(false)
        isRegistered.set(false)

        heartbeatJob?.cancel()
        heartbeatJob = null

        connection.disconnect()
        Log.i(TAG, "设备已断开连接")
    }

    /**
     * 上传 RGB 图片
     */
    override fun uploadRgbImage(jpegData: ByteArray, requestId: String) {
        if (!isRunning.get()) {
            Log.w(TAG, "未连接，无法上传图片")
            return
        }

        CoroutineScope(Dispatchers.IO).launch {
            try {
                if (!connection.ensureStreamConnected()) {
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
                connection.sendEvent(openedEvent)

                // 发送图片数据
                val imageChunk = StreamChunkCodec.createImageChunk(
                    userId = userId,
                    sessionId = deviceId,
                    streamId = streamId,
                    jpegData = jpegData,
                    seq = 0,
                    requestId = requestId
                )
                connection.sendChunk(imageChunk)

                // 发送流关闭事件
                delay(50)
                val closedEvent = AudioChatEvent.createStreamInputClosedEvent(
                    userId = userId,
                    deviceId = deviceId,
                    streamId = streamId,
                    streamType = "sensor.rgb",
                    reason = "android_phone_rgb_uploaded"
                )
                connection.sendEvent(closedEvent)

                Log.d(TAG, "RGB 图片上传完成: ${jpegData.size} bytes")
            } catch (e: Exception) {
                Log.e(TAG, "上传 RGB 图片失败", e)
            }
        }
    }

    /**
     * 上传音频数据
     */
    override fun uploadAudioData(pcmData: ByteArray, streamId: String, seq: Int, isFinal: Boolean) {
        val chunk = StreamChunkCodec.createAudioChunk(
            userId = userId,
            sessionId = deviceId,
            streamId = streamId,
            pcmData = pcmData,
            seq = seq,
            isFinal = isFinal
        )
        connection.sendChunk(chunk)
    }

    /**
     * 发送命令响应
     */
    override fun sendCommandResponse(event: AudioChatEvent) {
        connection.sendEvent(event)
    }

    /**
     * 发送任意事件
     */
    override fun sendEvent(event: AudioChatEvent) {
        connection.sendEvent(event)
        listener?.onRawMessage("send", event.toJson())
    }

    /**
     * 创建连接监听器
     */
    private fun createConnectionListener() = object : DeviceConnectionListener {
        override fun onControlConnected() {
            Log.i(TAG, "控制通道已连接")
        }

        override fun onControlEvent(event: AudioChatEvent) {
            listener?.onRawMessage("recv", event.toJson())
            listener?.onEvent(event.event_name, event.payload.toString())
            handleControlEvent(event)
        }

        override fun onControlError(error: Throwable) {
            Log.e(TAG, "控制通道错误", error)
            listener?.onEvent("control.error", error.message ?: "")
        }

        override fun onControlDisconnected(code: Int, reason: String) {
            Log.w(TAG, "控制通道已关闭: code=$code reason=$reason")
            isRunning.set(false)
            isRegistered.set(false)
            listener?.onEvent("control.disconnected", "code=$code reason=$reason")
            listener?.onReconnectNeeded()
        }

        override fun onStreamConnected() {
            Log.i(TAG, "流通道已连接")
            listener?.onStreamConnected()
            listener?.onEvent("stream.connected", "")
        }

        override fun onStreamChunk(chunk: StreamChunk) {
            when (chunk.stream_type) {
                "actuator.speaker" -> {
                    listener?.onAudioOutputChunk(chunk)
                }
                else -> {
                    Log.d(TAG, "收到其他流数据: ${chunk.stream_type}")
                }
            }
        }

        override fun onStreamError(error: Throwable) {
            Log.e(TAG, "流通道错误", error)
            listener?.onEvent("stream.error", error.message ?: "")
        }

        override fun onStreamDisconnected(code: Int, reason: String) {
            Log.w(TAG, "流通道已关闭: code=$code reason=$reason")
            listener?.onEvent("stream.disconnected", "code=$code reason=$reason")
        }

        override fun onReconnectNeeded(type: String) {
            Log.w(TAG, "WebSocket $type 需要重连")
            listener?.onReconnectNeeded()
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

                listener?.onDeviceRegistered()
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
                        listener?.onRgbCaptureRequest(requestId ?: "")
                    }
                    else -> {
                        Log.w(TAG, "不支持的流类型请求: ${event.stream_type}")
                    }
                }
            }

            "command.requested" -> {
                Log.i(TAG, "收到命令请求: ${event.payload}")
                listener?.onCommandReceived(event)
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
                connection.sendEvent(finishedEvent)

                val closedEvent = AudioChatEvent(
                    event_name = "stream.output.closed",
                    user_id = userId,
                    producer_id = deviceId,
                    session_id = deviceId,
                    stream_id = event.stream_id,
                    stream_type = event.stream_type,
                    payload = mapOf("stream_type" to event.stream_type, "reason" to "android_phone_closed")
                )
                connection.sendEvent(closedEvent)
            }

            else -> {
                Log.d(TAG, "未处理的事件: ${event.event_name}")
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
                        connection.sendEvent(heartbeatEvent)
                        listener?.onHeartbeatReceived()
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
}