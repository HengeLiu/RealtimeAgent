package com.realtimeagent.device.device

import android.content.Context
import com.realtimeagent.device.protocol.AudioChatEvent
import com.realtimeagent.device.protocol.DeviceSupports
import com.realtimeagent.device.protocol.StreamChunk

interface DeviceListener {
    fun onDeviceRegistered() {}
    fun onStreamConnected() {}
    fun onHeartbeatReceived() {}
    fun onRgbCaptureRequest(requestId: String) {}
    fun onCommandReceived(event: AudioChatEvent) {}
    fun onAudioOutputChunk(chunk: StreamChunk) {}
    fun onEvent(eventName: String, detail: String) {}
    fun onRawMessage(direction: String, message: String) {}
    fun onReconnectNeeded() {}
    fun onYoloModelLoaded(loaded: Boolean) {}
    fun onPeerVideoFrame(frameResult: Map<String, Any>) {}
    fun onPeerVideoTaskCompleted(result: Map<String, Any>) {}
    fun onPeerVideoClientConnected(clientIp: String) {}
    fun onPeerVideoClientDisconnected() {}
}

interface Device {
    val deviceId: String
    val userId: String
    val isRegistered: Boolean
    val isRunning: Boolean

    fun setListener(listener: DeviceListener?)
    fun setContext(context: Context)
    suspend fun connect()
    fun start()
    fun disconnect()
    fun uploadRgbImage(jpegData: ByteArray, requestId: String)
    fun uploadAudioData(pcmData: ByteArray, streamId: String, seq: Int, isFinal: Boolean = false)
    fun sendCommandResponse(event: AudioChatEvent)
    fun sendEvent(event: AudioChatEvent)
}

data class DeviceConfig(
    val serverUrl: String,
    val userId: String,
    val deviceId: String,
    val accessToken: String? = null,
    val deviceName: String = "android-phone",
    val properties: Map<String, Any?> = emptyMap(),
    val supports: DeviceSupports = DeviceSupports()
)

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
