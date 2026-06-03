package com.realtimeagent.device.device

import android.content.Context
import android.util.Log
import com.realtimeagent.device.protocol.AudioChatEvent
import com.realtimeagent.device.protocol.DeviceSupports
import com.realtimeagent.device.protocol.StreamChunk
import com.realtimeagent.device.protocol.StreamChunkCodec
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

class DeviceManager private constructor(
    private val config: DeviceConfig,
    private val connection: DeviceConnection
) : Device {

    companion object {
        private const val TAG = "DeviceManager"
        private const val DEFAULT_HEARTBEAT_INTERVAL_MS = 10_000L

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

    private val _isRegistered = AtomicBoolean(false)
    private val _isRunning = AtomicBoolean(false)
    private var heartbeatJob: Job? = null
    private var scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

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
        get() = _isRegistered.get()

    override val isRunning: Boolean
        get() = _isRunning.get()

    override suspend fun connect() {
        if (_isRunning.get()) return

        _isRunning.set(true)

        try {
            connection.connectControl()
            delay(500)

            val registerEvent = AudioChatEvent.createRegisterEvent(
                userId = userId,
                deviceId = deviceId,
                deviceName = config.deviceName,
                properties = config.properties,
                supports = config.supports
            )
            connection.sendEvent(registerEvent)
            Log.i(TAG, "Device register request sent: $deviceId")
        } catch (e: Exception) {
            Log.e(TAG, "Connection failed", e)
            _isRunning.set(false)
            throw e
        }
    }

    override fun start() {
        if (!_isRunning.get()) return
        startHeartbeat(DEFAULT_HEARTBEAT_INTERVAL_MS)
    }

    override fun disconnect() {
        _isRunning.set(false)
        _isRegistered.set(false)

        heartbeatJob?.cancel()
        heartbeatJob = null

        scope.cancel()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

        connection.disconnect()
        Log.i(TAG, "Device disconnected")
    }

    override fun uploadRgbImage(jpegData: ByteArray, requestId: String) {
        if (!_isRunning.get()) {
            Log.w(TAG, "Not connected, cannot upload image")
            return
        }

        scope.launch {
            try {
                if (!connection.ensureStreamConnected()) {
                    Log.e(TAG, "Cannot establish stream connection")
                    return@launch
                }

                val streamId = AudioChatEvent.newId("stream_rgb")

                val openedEvent = AudioChatEvent.createStreamInputOpenedEvent(
                    userId = userId,
                    deviceId = deviceId,
                    streamId = streamId,
                    streamType = "sensor.rgb",
                    requestId = requestId
                )
                connection.sendEvent(openedEvent)

                val imageChunk = StreamChunkCodec.createImageChunk(
                    userId = userId,
                    sessionId = deviceId,
                    streamId = streamId,
                    jpegData = jpegData,
                    seq = 0,
                    requestId = requestId
                )
                connection.sendChunk(imageChunk)

                delay(50)
                val closedEvent = AudioChatEvent.createStreamInputClosedEvent(
                    userId = userId,
                    deviceId = deviceId,
                    streamId = streamId,
                    streamType = "sensor.rgb",
                    reason = "android_phone_rgb_uploaded"
                )
                connection.sendEvent(closedEvent)

                Log.d(TAG, "RGB image uploaded: ${jpegData.size} bytes")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to upload RGB image", e)
            }
        }
    }

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

    override fun sendCommandResponse(event: AudioChatEvent) {
        connection.sendEvent(event)
    }

    override fun sendEvent(event: AudioChatEvent) {
        connection.sendEvent(event)
        listener?.onRawMessage("send", event.toJson())
    }

    private fun createConnectionListener() = object : DeviceConnectionListener {
        override fun onControlConnected() {
            Log.i(TAG, "Control channel connected")
        }

        override fun onControlEvent(event: AudioChatEvent) {
            listener?.onRawMessage("recv", event.toJson())
            listener?.onEvent(event.event_name, event.payload.toString())
            handleControlEvent(event)
        }

        override fun onControlError(error: Throwable) {
            Log.e(TAG, "Control channel error", error)
            listener?.onEvent("control.error", error.message ?: "")
        }

        override fun onControlDisconnected(code: Int, reason: String) {
            Log.w(TAG, "Control channel closed: code=$code reason=$reason")
            _isRunning.set(false)
            _isRegistered.set(false)
            listener?.onEvent("control.disconnected", "code=$code reason=$reason")
            listener?.onReconnectNeeded()
        }

        override fun onStreamConnected() {
            Log.i(TAG, "Stream channel connected")
            listener?.onStreamConnected()
            listener?.onEvent("stream.connected", "")
        }

        override fun onStreamChunk(chunk: StreamChunk) {
            when (chunk.stream_type) {
                "actuator.speaker" -> {
                    listener?.onAudioOutputChunk(chunk)
                }
                else -> {
                    Log.d(TAG, "Received other stream data: ${chunk.stream_type}")
                }
            }
        }

        override fun onStreamError(error: Throwable) {
            Log.e(TAG, "Stream channel error", error)
            listener?.onEvent("stream.error", error.message ?: "")
        }

        override fun onStreamDisconnected(code: Int, reason: String) {
            Log.w(TAG, "Stream channel closed: code=$code reason=$reason")
            listener?.onEvent("stream.disconnected", "code=$code reason=$reason")
        }

        override fun onReconnectNeeded(type: String) {
            Log.w(TAG, "WebSocket $type needs reconnect")
            listener?.onReconnectNeeded()
        }
    }

    private fun handleControlEvent(event: AudioChatEvent) {
        Log.d(TAG, "Handling control event: ${event.event_name}")

        when (event.event_name) {
            "control.device.registered" -> {
                Log.i(TAG, "Device registered successfully!")
                _isRegistered.set(true)

                val heartbeatInterval = (event.payload["heartbeat_interval_seconds"] as? Number)?.toLong()
                    ?.times(1000) ?: DEFAULT_HEARTBEAT_INTERVAL_MS

                heartbeatJob?.cancel()
                startHeartbeat(heartbeatInterval)

                listener?.onDeviceRegistered()
            }

            "control.device.register.failed" -> {
                Log.e(TAG, "Device registration failed: ${event.payload}")
                _isRegistered.set(false)
            }

            "stream.control.open.requested" -> {
                when (event.stream_type) {
                    "sensor.rgb" -> {
                        val requestId = event.payload["request_id"] as? String
                        Log.i(TAG, "RGB capture request: requestId=$requestId")
                        listener?.onRgbCaptureRequest(requestId ?: "")
                    }
                    else -> {
                        Log.w(TAG, "Unsupported stream type request: ${event.stream_type}")
                    }
                }
            }

            "command.requested" -> {
                Log.i(TAG, "Command request: ${event.payload}")
                listener?.onCommandReceived(event)
            }

            "stream.output.close.requested" -> {
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
                Log.d(TAG, "Unhandled event: ${event.event_name}")
            }
        }
    }

    private fun startHeartbeat(intervalMs: Long) {
        heartbeatJob = scope.launch {
            while (isActive && _isRunning.get()) {
                try {
                    delay(intervalMs)

                    if (_isRunning.get()) {
                        val heartbeatEvent = AudioChatEvent.createHeartbeatEvent(userId, deviceId)
                        connection.sendEvent(heartbeatEvent)
                        listener?.onHeartbeatReceived()
                        Log.d(TAG, "Heartbeat sent")
                    }
                } catch (e: Exception) {
                    if (isActive) {
                        Log.e(TAG, "Heartbeat failed", e)
                    }
                }
            }
        }
    }
}
