package com.audiochat.phone.network

import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.StreamChunk
import com.audiochat.phone.protocol.StreamChunkCodec
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import timber.log.Timber
import java.util.concurrent.TimeUnit

class AudioChatWebSocketClient(
    private val serverUrl: String,
    private val userId: String,
    private val deviceId: String,
    private val accessToken: String? = null,
    private val eventListener: AudioChatEventListener? = null
) {
    private var controlWs: WebSocket? = null
    private var streamWs: WebSocket? = null
    private var isControlConnected = false
    private var isStreamConnected = false

    // 重连相关
    private var isReconnecting = false
    private val maxReconnectAttempts = 3
    private var reconnectAttempt = 0

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MINUTES)
        .writeTimeout(10, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    fun connectControl(): WebSocket {
        val url = "$serverUrl/ws/control"
        Timber.d("连接控制 WebSocket: $url")

        val requestBuilder = Request.Builder().url(url)
        
        if (!accessToken.isNullOrEmpty()) {
            requestBuilder.addHeader("Authorization", "Bearer $accessToken")
            Timber.d("添加 Authorization header")
        }

        val request = requestBuilder.build()

        controlWs = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Timber.i("控制 WebSocket 已打开")
                isControlConnected = true
                isReconnecting = false
                eventListener?.onControlConnected()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val event = AudioChatEvent.fromJson(text)
                    Timber.d("收到控制事件: ${event.event_name}")
                    eventListener?.onControlEvent(event)
                } catch (e: Exception) {
                    Timber.e(e, "解析控制事件失败")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Timber.e(t, "控制 WebSocket 错误")
                isControlConnected = false
                eventListener?.onControlError(t)
                // 尝试重连
                scheduleReconnect("control")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Timber.w("控制 WebSocket 已关闭: code=$code reason=$reason")
                isControlConnected = false
                eventListener?.onControlDisconnected(code, reason)
                // 非正常关闭时尝试重连
                if (code != 1000) {
                    scheduleReconnect("control")
                }
            }
        })

        return controlWs!!
    }

    private fun scheduleReconnect(type: String) {
        if (isReconnecting) return
        isReconnecting = true
        reconnectAttempt = 0
        Timber.w("WebSocket $type 断开, 准备重连...")
        // 通过 listener 通知上层进行重连
        eventListener?.onReconnectNeeded(type)
    }

    fun reconnect(type: String) {
        reconnectAttempt++
        if (reconnectAttempt > maxReconnectAttempts) {
            Timber.e("WebSocket $type 重连次数已达上限")
            isReconnecting = false
            return
        }
        Timber.w("WebSocket $type 正在重连 (第 $reconnectAttempt 次)")
        when (type) {
            "control" -> {
                connectControl()
            }
            "stream" -> {
                connectStream()
            }
        }
    }

    fun connectStream(): WebSocket {
        val url = "$serverUrl/ws/stream?device_id=$deviceId"
        Timber.d("连接流 WebSocket: $url")

        val requestBuilder = Request.Builder().url(url)
        
        if (!accessToken.isNullOrEmpty()) {
            requestBuilder.addHeader("Authorization", "Bearer $accessToken")
            Timber.d("添加 Authorization header")
        }

        val request = requestBuilder.build()

        streamWs = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Timber.i("流 WebSocket 已打开")
                isStreamConnected = true
                eventListener?.onStreamConnected()
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                try {
                    val chunk = StreamChunkCodec.decode(bytes.toByteArray())
                    Timber.d("收到流数据: ${chunk.stream_type} seq=${chunk.seq}")
                    eventListener?.onStreamChunk(chunk)
                } catch (e: Exception) {
                    Timber.e(e, "解析流数据失败")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Timber.e(t, "流 WebSocket 错误")
                isStreamConnected = false
                eventListener?.onStreamError(t)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Timber.w("流 WebSocket 已关闭: code=$code reason=$reason")
                isStreamConnected = false
                eventListener?.onStreamDisconnected(code, reason)
            }
        })

        return streamWs!!
    }

    fun sendEvent(event: AudioChatEvent): Boolean {
        return controlWs?.send(event.toJson()) ?: false
    }

    fun sendChunk(chunk: StreamChunk): Boolean {
        return streamWs?.send(ByteString.of(*StreamChunkCodec.encode(chunk))) ?: false
    }

    fun disconnect() {
        controlWs?.close(1000, "客户端主动关闭")
        streamWs?.close(1000, "客户端主动关闭")
        controlWs = null
        streamWs = null
        isControlConnected = false
        isStreamConnected = false
    }

    val isConnected: Boolean
        get() = isControlConnected

    val isStreamReady: Boolean
        get() = isStreamConnected

    fun ensureStreamConnected(): Boolean {
        if (isStreamConnected) return true
        return try {
            connectStream()
            true
        } catch (e: Exception) {
            Timber.e(e, "建立流连接失败")
            false
        }
    }
}

interface AudioChatEventListener {
    fun onControlConnected() {}
    fun onControlEvent(event: AudioChatEvent) {}
    fun onControlError(error: Throwable) {}
    fun onControlDisconnected(code: Int, reason: String) {}
    fun onReconnectNeeded(type: String) {}

    fun onStreamConnected() {}
    fun onStreamChunk(chunk: StreamChunk) {}
    fun onStreamError(error: Throwable) {}
    fun onStreamDisconnected(code: Int, reason: String) {}
}