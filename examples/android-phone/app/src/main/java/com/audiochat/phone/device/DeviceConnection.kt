package com.audiochat.phone.device

import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.StreamChunk

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
 * 设备事件监听器
 * 用于接收来自服务器的事件
 */
interface DeviceConnectionListener {
    /** 控制通道已连接 */
    fun onControlConnected() {}

    /** 控制通道断开 */
    fun onControlDisconnected(code: Int, reason: String) {}

    /** 收到控制事件 */
    fun onControlEvent(event: AudioChatEvent) {}

    /** 控制通道错误 */
    fun onControlError(error: Throwable) {}

    /** 流通道已连接 */
    fun onStreamConnected() {}

    /** 流通道断开 */
    fun onStreamDisconnected(code: Int, reason: String) {}

    /** 收到流数据 */
    fun onStreamChunk(chunk: StreamChunk) {}

    /** 流通道错误 */
    fun onStreamError(error: Throwable) {}

    /** 需要重连 */
    fun onReconnectNeeded(type: String) {}
}

/**
 * 设备连接接口
 * 定义设备与服务器之间的通信操作
 */
interface DeviceConnection {
    /** 当前连接状态 */
    val controlState: ConnectionState
    val streamState: ConnectionState

    /** 是否已连接 */
    val isConnected: Boolean
    val isStreamConnected: Boolean

    /** 设置事件监听器 */
    fun setListener(listener: DeviceConnectionListener)

    /** 连接控制通道 */
    fun connectControl()

    /** 连接流通道 */
    fun connectStream(): Boolean

    /** 断开连接 */
    fun disconnect()

    /** 发送事件 */
    fun sendEvent(event: AudioChatEvent): Boolean

    /** 发送流数据 */
    fun sendChunk(chunk: StreamChunk): Boolean

    /** 按需建立流连接 */
    fun ensureStreamConnected(): Boolean
}