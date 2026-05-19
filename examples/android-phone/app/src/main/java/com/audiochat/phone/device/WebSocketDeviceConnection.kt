package com.audiochat.phone.device

import android.util.Log
import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.StreamChunk
import com.audiochat.phone.protocol.StreamChunkCodec
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.util.concurrent.TimeUnit

/**
 * WebSocket 实现的设备连接
 * 实现 DeviceConnection 接口，负责底层 WebSocket 通信
 */
class WebSocketDeviceConnection(
    private val serverUrl: String,
    private val userId: String,
    private val deviceId: String,
    private val accessToken: String? = null
) : DeviceConnection {

    companion object {
        private const val TAG = "WebSocketDeviceConnection"
        private const val NORMAL_CLOSE = 1000
    }

    private var controlWs: WebSocket? = null
    private var streamWs: WebSocket? = null
    private var listener: DeviceConnectionListener? = null

    private var _controlState = ConnectionState.DISCONNECTED
    private var _streamState = ConnectionState.DISCONNECTED

    override val controlState: ConnectionState get() = _controlState
    override val streamState: ConnectionState get() = _streamState

    override val isConnected: Boolean
        get() = _controlState == ConnectionState.CONNECTED

    override val isStreamConnected: Boolean
        get() = _streamState == ConnectionState.CONNECTED

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MINUTES)
        .writeTimeout(10, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    override fun setListener(listener: DeviceConnectionListener) {
        this.listener = listener
    }

    override fun connectControl() {
        if (_controlState == ConnectionState.CONNECTING) return

        val url = "$serverUrl/ws/control"
        Log.i(TAG, "连接控制 WebSocket: $url")

        val requestBuilder = Request.Builder().url(url)
        accessToken?.let {
            requestBuilder.addHeader("Authorization", "Bearer $it")
        }

        _controlState = ConnectionState.CONNECTING
        controlWs = client.newWebSocket(requestBuilder.build(), createControlListener())
    }

    override fun connectStream(): Boolean {
        if (_streamState == ConnectionState.CONNECTED) return true

        val url = "$serverUrl/ws/stream?device_id=$deviceId"
        Log.i(TAG, "连接流 WebSocket: $url")

        val requestBuilder = Request.Builder().url(url)
        accessToken?.let {
            requestBuilder.addHeader("Authorization", "Bearer $it")
        }

        streamWs = client.newWebSocket(requestBuilder.build(), createStreamListener())
        return true
    }

    override fun disconnect() {
        controlWs?.close(NORMAL_CLOSE, "客户端主动关闭")
        streamWs?.close(NORMAL_CLOSE, "客户端主动关闭")
        controlWs = null
        streamWs = null
        _controlState = ConnectionState.DISCONNECTED
        _streamState = ConnectionState.DISCONNECTED
    }

    override fun sendEvent(event: AudioChatEvent): Boolean {
        return controlWs?.send(event.toJson()) ?: false
    }

    override fun sendChunk(chunk: StreamChunk): Boolean {
        return streamWs?.send(ByteString.of(*StreamChunkCodec.encode(chunk))) ?: false
    }

    override fun ensureStreamConnected(): Boolean {
        if (_streamState == ConnectionState.CONNECTED) return true
        return try {
            connectStream()
            true
        } catch (e: Exception) {
            Log.e(TAG, "建立流连接失败", e)
            false
        }
    }

    private fun createControlListener() = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "控制 WebSocket 已打开")
            _controlState = ConnectionState.CONNECTED
            listener?.onControlConnected()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            try {
                val event = AudioChatEvent.fromJson(text)
                Log.d(TAG, "收到控制事件: ${event.event_name}")
                listener?.onControlEvent(event)
            } catch (e: Exception) {
                Log.e(TAG, "解析控制事件失败", e)
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(TAG, "控制 WebSocket 错误", t)
            _controlState = ConnectionState.ERROR
            listener?.onControlError(t)
            listener?.onReconnectNeeded("control")
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.w(TAG, "控制 WebSocket 已关闭: code=$code reason=$reason")
            _controlState = ConnectionState.DISCONNECTED
            listener?.onControlDisconnected(code, reason)
            if (code != NORMAL_CLOSE) {
                listener?.onReconnectNeeded("control")
            }
        }
    }

    private fun createStreamListener() = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "流 WebSocket 已打开")
            _streamState = ConnectionState.CONNECTED
            listener?.onStreamConnected()
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            try {
                val chunk = StreamChunkCodec.decode(bytes.toByteArray())
                Log.d(TAG, "收到流数据: ${chunk.stream_type} seq=${chunk.seq}")
                listener?.onStreamChunk(chunk)
            } catch (e: Exception) {
                Log.e(TAG, "解析流数据失败", e)
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(TAG, "流 WebSocket 错误", t)
            _streamState = ConnectionState.ERROR
            listener?.onStreamError(t)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.w(TAG, "流 WebSocket 已关闭: code=$code reason=$reason")
            _streamState = ConnectionState.DISCONNECTED
            listener?.onStreamDisconnected(code, reason)
        }
    }
}