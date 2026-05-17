package com.audiochat.phone.video

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import org.java_websocket.WebSocket
import org.java_websocket.handshake.ClientHandshake
import org.java_websocket.server.WebSocketServer
import java.net.InetSocketAddress
import java.nio.ByteBuffer

/**
 * Peer Video Receiver Server
 * 接收眼镜端发送的视频帧 (JPEG)
 */
class PeerVideoReceiverServer(
    private val port: Int = 19081,
    private val scope: CoroutineScope
) {
    companion object {
        private const val TAG = "PeerVideoReceiver"
    }

    private var server: VideoWebSocketServer? = null
    private var currentSessionId: String? = null
    private var currentConnection: WebSocket? = null

    private val _frameFlow = MutableSharedFlow<VideoFrame>(extraBufferCapacity = 64)
    val frameFlow: SharedFlow<VideoFrame> = _frameFlow

    private val _stateFlow = MutableSharedFlow<ReceiverState>(replay = 1)
    val stateFlow: SharedFlow<ReceiverState> = _stateFlow

    var onFrameReceived: ((VideoFrame) -> Unit)? = null
    var onClientConnected: ((String) -> Unit)? = null
    var onClientDisconnected: (() -> Unit)? = null
    var onError: ((String) -> Unit)? = null

    data class VideoFrame(
        val bitmap: Bitmap,
        val jpegData: ByteArray,
        val seq: Long,
        val timestamp: Long = System.currentTimeMillis()
    )

    sealed class ReceiverState {
        object Idle : ReceiverState()
        data class Starting(val sessionId: String) : ReceiverState()
        data class Running(val sessionId: String, val clientIp: String) : ReceiverState()
        data class Error(val message: String) : ReceiverState()
        object Stopped : ReceiverState()
    }

    /**
     * 启动服务器等待指定 session 的连接
     */
    fun start(sessionId: String): String {
        if (server != null) {
            Log.w(TAG, "Server already running")
            return getWebSocketUrl(sessionId)
        }

        currentSessionId = sessionId
        server = VideoWebSocketServer(port)

        try {
            server?.start()
            _stateFlow.tryEmit(ReceiverState.Starting(sessionId))
            Log.i(TAG, "Peer video receiver started on port $port, sessionId=$sessionId")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start server", e)
            _stateFlow.tryEmit(ReceiverState.Error(e.message ?: "Failed to start server"))
            onError?.invoke(e.message ?: "Failed to start server")
        }

        return getWebSocketUrl(sessionId)
    }

    /**
     * 停止服务器
     */
    fun stop() {
        try {
            server?.stop(1000)
            server = null
            currentConnection = null
            currentSessionId = null
            _stateFlow.tryEmit(ReceiverState.Stopped)
            Log.i(TAG, "Peer video receiver stopped")
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping server", e)
        }
    }

    /**
     * 获取 WebSocket URL
     */
    fun getWebSocketUrl(sessionId: String): String {
        return "ws://0.0.0.0:$port/peer-video/$sessionId"
    }

    /**
     * 获取本地网络 IP 的 WebSocket URL
     */
    fun getLocalWebSocketUrl(sessionId: String, localIp: String): String {
        return "ws://$localIp:$port/peer-video/$sessionId"
    }

    /**
     * 内部 WebSocket Server 实现
     */
    private inner class VideoWebSocketServer(port: Int) : WebSocketServer(InetSocketAddress(port)) {
        private var frameSeq = 0L

        override fun onOpen(conn: WebSocket?, handshake: ClientHandshake?) {
            val path = handshake?.resourceDescriptor ?: ""
            Log.i(TAG, "Client connected: ${conn?.remoteSocketAddress}, path=$path")

            val pathSessionId = path.removePrefix("/peer-video/").removePrefix("/")
            
            if (pathSessionId != currentSessionId) {
                Log.w(TAG, "Invalid session: expected=$currentSessionId, got=$pathSessionId")
                conn?.close(4000, "Invalid session")
                return
            }

            currentConnection = conn
            frameSeq = 0

            scope.launch {
                _stateFlow.emit(ReceiverState.Running(
                    sessionId = currentSessionId ?: "",
                    clientIp = conn?.remoteSocketAddress?.toString() ?: ""
                ))
            }

            onClientConnected?.invoke(conn?.remoteSocketAddress?.toString() ?: "")
        }

        override fun onClose(conn: WebSocket?, code: Int, reason: String?, remote: Boolean) {
            Log.i(TAG, "Client disconnected: code=$code, reason=$reason")
            currentConnection = null

            scope.launch {
                _stateFlow.emit(ReceiverState.Starting(currentSessionId ?: ""))
            }

            onClientDisconnected?.invoke()
        }

        override fun onMessage(conn: WebSocket?, message: String?) {
            Log.d(TAG, "Text message received: $message")
        }

        override fun onMessage(conn: WebSocket?, message: ByteBuffer?) {
            message ?: return

            try {
                val jpegData = ByteArray(message.remaining())
                message.get(jpegData)

                val bitmap = BitmapFactory.decodeByteArray(jpegData, 0, jpegData.size)
                if (bitmap == null) {
                    Log.w(TAG, "Failed to decode JPEG frame")
                    return
                }

                val frame = VideoFrame(
                    bitmap = bitmap,
                    jpegData = jpegData,
                    seq = frameSeq++
                )

                scope.launch {
                    _frameFlow.emit(frame)
                }

                onFrameReceived?.invoke(frame)

            } catch (e: Exception) {
                Log.e(TAG, "Error processing frame", e)
            }
        }

        override fun onError(conn: WebSocket?, ex: Exception?) {
            Log.e(TAG, "WebSocket error", ex)
            scope.launch {
                _stateFlow.emit(ReceiverState.Error(ex?.message ?: "Unknown error"))
            }
            onError?.invoke(ex?.message ?: "Unknown error")
        }

        override fun onStart() {
            Log.i(TAG, "WebSocket server started on port $port")
        }
    }
}
