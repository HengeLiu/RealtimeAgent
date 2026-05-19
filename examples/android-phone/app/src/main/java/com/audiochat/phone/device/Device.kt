package com.audiochat.phone.device

import android.content.Context
import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.DeviceSupports
import com.audiochat.phone.protocol.StreamChunk

/**
 * 设备事件监听器
 */
interface DeviceListener {
    /** 设备注册成功 */
    fun onDeviceRegistered() {}

    /** 流通道已连接 */
    fun onStreamConnected() {}

    /** 收到心跳响应 */
    fun onHeartbeatReceived() {}

    /** 收到 RGB 采集请求 */
    fun onRgbCaptureRequest(requestId: String) {}

    /** 收到命令请求 */
    fun onCommandReceived(event: AudioChatEvent) {}

    /** 收到音频输出流 */
    fun onAudioOutputChunk(chunk: StreamChunk) {}

    /** 收到通用事件 */
    fun onEvent(eventName: String, detail: String) {}

    /** 收到原始消息 */
    fun onRawMessage(direction: String, message: String) {}

    /** 需要重连 */
    fun onReconnectNeeded() {}

    /** YOLO 模型加载状态 */
    fun onYoloModelLoaded(loaded: Boolean) {}

    /** Peer Video 帧处理完成 */
    fun onPeerVideoFrame(frameResult: Map<String, Any>) {}

    /** Peer Video 任务完成 */
    fun onPeerVideoTaskCompleted(result: Map<String, Any>) {}

    /** Peer Video 客户端已连接 */
    fun onPeerVideoClientConnected(clientIp: String) {}

    /** Peer Video 客户端已断开 */
    fun onPeerVideoClientDisconnected() {}
}

/**
 * 设备接口
 * 定义设备的基本操作：连接、断开、上传数据、发送命令等
 */
interface Device {
    /** 设备 ID */
    val deviceId: String

    /** 用户 ID */
    val userId: String

    /** 是否已注册 */
    val isRegistered: Boolean

    /** 是否正在运行 */
    val isRunning: Boolean

    /** 设置设备监听器 */
    fun setListener(listener: DeviceListener?)

    /** 设置上下文（用于初始化 PeerVideo 等功能） */
    fun setContext(context: Context)

    /** 连接到服务器并注册设备 */
    suspend fun connect()

    /** 启动流连接和心跳 */
    fun start()

    /** 断开连接 */
    fun disconnect()

    /** 上传 RGB 图片 */
    fun uploadRgbImage(jpegData: ByteArray, requestId: String)

    /** 上传音频数据 */
    fun uploadAudioData(pcmData: ByteArray, streamId: String, seq: Int, isFinal: Boolean = false)

    /** 发送命令响应 */
    fun sendCommandResponse(event: AudioChatEvent)

    /** 发送任意事件 */
    fun sendEvent(event: AudioChatEvent)
}

/**
 * 设备配置
 * 用于创建设备时的配置参数
 */
data class DeviceConfig(
    val serverUrl: String,
    val userId: String,
    val deviceId: String,
    val accessToken: String? = null,
    val deviceName: String = "android-phone",
    val properties: Map<String, Any?> = emptyMap(),
    val supports: DeviceSupports = DeviceSupports()
)

/**
 * 设备构建器
 */
class DeviceBuilder {
    private var serverUrl: String = ""
    private var userId: String = ""
    private var deviceId: String = ""
    private var accessToken: String? = null
    private var deviceName: String = "android-phone"
    private var properties: Map<String, Any?> = emptyMap()
    private var supports: DeviceSupports = DeviceSupports()

    fun serverUrl(url: String) = apply { this.serverUrl = url }
    fun userId(id: String) = apply { this.userId = id }
    fun deviceId(id: String) = apply { this.deviceId = id }
    fun accessToken(token: String?) = apply { this.accessToken = token }
    fun deviceName(name: String) = apply { this.deviceName = name }
    fun properties(props: Map<String, Any?>) = apply { this.properties = props }
    fun supports(s: DeviceSupports) = apply { this.supports = s }

    fun build(): DeviceConfig = DeviceConfig(
        serverUrl = serverUrl,
        userId = userId,
        deviceId = deviceId,
        accessToken = accessToken,
        deviceName = deviceName,
        properties = properties,
        supports = supports
    )
}