package io.audiochat.device

import okhttp3.*
import okio.ByteString
import java.util.concurrent.TimeUnit

/**
 * OkHttp WebSocket 实现
 * 使用 OkHttp 库实现 WebSocket 连接
 */
class OkHttpWebSocketConnection(
    url: String,
    listener: Listener,
    private val accessToken: String? = null
) : WebSocketConnection(url, listener) {

    private var ws: WebSocket? = null

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MINUTES)
        .writeTimeout(10, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    override fun connect() {
        val requestBuilder = Request.Builder().url(url)
        accessToken?.let {
            requestBuilder.addHeader("Authorization", "Bearer $it")
        }

        ws = client.newWebSocket(requestBuilder.build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                listener.onOpen()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                listener.onMessage(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                listener.onMessage(bytes.toByteArray())
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                listener.onClose(code, reason)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                listener.onError(t)
            }
        })
    }

    override fun send(text: String): Boolean {
        return ws?.send(text) ?: false
    }

    override fun sendBinary(bytes: ByteArray): Boolean {
        return ws?.send(ByteString.of(*bytes)) ?: false
    }

    override fun close(code: Int, reason: String) {
        ws?.close(code, reason)
        ws = null
    }
}

/**
 * 简单的 Java-WebSocket 实现（可选）
 * 如果不使用 OkHttp，可以使用此实现
 */
class JavaWebSocketConnection(
    url: String,
    listener: Listener
) : WebSocketConnection(url, listener) {

    // 注意：需要添加 java-websocket 依赖
    // 此处仅作框架占位，实际使用时根据平台选择合适的 WebSocket 库

    override fun connect() {
        // TODO: 实现 Java WebSocket 连接
        throw UnsupportedOperationException("需要添加 java-websocket 依赖")
    }

    override fun send(text: String): Boolean {
        throw UnsupportedOperationException("需要添加 java-websocket 依赖")
    }

    override fun sendBinary(bytes: ByteArray): Boolean {
        throw UnsupportedOperationException("需要添加 java-websocket 依赖")
    }

    override fun close(code: Int, reason: String) {
        throw UnsupportedOperationException("需要添加 java-websocket 依赖")
    }
}