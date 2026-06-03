package com.realtimeagent.device.device

import com.realtimeagent.device.protocol.AudioChatEvent
import com.realtimeagent.device.protocol.StreamChunk

enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    ERROR
}

interface DeviceConnectionListener {
    fun onControlConnected() {}
    fun onControlDisconnected(code: Int, reason: String) {}
    fun onControlEvent(event: AudioChatEvent) {}
    fun onControlError(error: Throwable) {}
    fun onStreamConnected() {}
    fun onStreamDisconnected(code: Int, reason: String) {}
    fun onStreamChunk(chunk: StreamChunk) {}
    fun onStreamError(error: Throwable) {}
    fun onReconnectNeeded(type: String) {}
}

interface DeviceConnection {
    val controlState: ConnectionState
    val streamState: ConnectionState
    val isConnected: Boolean
    val isStreamConnected: Boolean

    fun setListener(listener: DeviceConnectionListener)
    fun connectControl()
    fun connectStream(): Boolean
    fun disconnect()
    fun sendEvent(event: AudioChatEvent): Boolean
    fun sendChunk(chunk: StreamChunk): Boolean
    fun ensureStreamConnected(): Boolean
}
