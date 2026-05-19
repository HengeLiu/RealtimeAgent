package io.audiochat.device

/**
 * 设备声明构建器
 * 用于生成设备注册 payload
 */
class AudioChatDevice private constructor(val device_id: String) {
    private var user_id: String = ""
    var name: String = device_id
        private set
    private var role: String? = null
    private var runtime: Map<String, Any?> = mapOf("platform" to "unknown", "language" to "kotlin")
    private val properties = linkedMapOf<String, Any?>()
    private val sensors = mutableListOf<Map<String, Any?>>()
    private val actuators = mutableListOf<Map<String, Any?>>()

    companion object {
        @JvmStatic
        fun define(deviceId: String): AudioChatDevice = AudioChatDevice(deviceId)
    }

    fun user(userId: String): AudioChatDevice {
        this.user_id = userId
        return this
    }

    fun name(name: String): AudioChatDevice {
        this.name = name
        return this
    }

    fun role(role: String): AudioChatDevice {
        this.role = role
        return this
    }

    fun platform(platform: String): AudioChatDevice {
        this.runtime = this.runtime.toMutableMap().apply { put("platform", platform) }
        return this
    }

    fun property(key: String, value: Any?): AudioChatDevice {
        properties[key] = value
        return this
    }

    fun sensorRgb(
        modes: List<String> = listOf("single"),
        format: String = "jpeg",
        frequencyHz: Int? = null
    ): AudioChatDevice {
        val defaults = linkedMapOf<String, Any?>("format" to format)
        if (frequencyHz != null) defaults["frequency_hz"] = frequencyHz
        sensors.add(mapOf("type" to "rgb", "modes" to modes, "default" to defaults))
        return this
    }

    fun sensorMic(
        modes: List<String> = listOf("continuous"),
        sampleRate: Int = 16000,
        channels: Int = 1
    ): AudioChatDevice {
        sensors.add(mapOf(
            "type" to "mic",
            "modes" to modes,
            "default" to mapOf(
                "sample_rate" to sampleRate,
                "channels" to channels,
                "codec" to "pcm16le"
            )
        ))
        return this
    }

    fun actuatorSpeaker(
        modes: List<String> = listOf("continuous"),
        sampleRate: Int = 16000,
        channels: Int = 1
    ): AudioChatDevice {
        actuators.add(mapOf(
            "type" to "speaker",
            "modes" to modes,
            "default" to mapOf(
                "sample_rate" to sampleRate,
                "channels" to channels,
                "codec" to "pcm16le"
            )
        ))
        return this
    }

    fun actuatorVibrator(commands: List<String> = listOf("vibrate")): AudioChatDevice {
        actuators.add(mapOf("type" to "vibrator", "commands" to commands))
        return this
    }

    fun supports(supports: DeviceSupports): AudioChatDevice {
        supports.sensors.forEach { sensors.add(it.toMap()) }
        supports.actuators.forEach { actuators.add(it.toMap()) }
        return this
    }

    fun registrationPayload(): Map<String, Any?> {
        require(user_id.isNotBlank()) { "user_id is required" }
        val props = linkedMapOf<String, Any?>()
        props.putAll(properties)
        if (role != null) props["device_role"] = role

        val supportsMap = linkedMapOf<String, Any?>()
        if (sensors.isNotEmpty()) supportsMap["sensors"] = sensors
        if (actuators.isNotEmpty()) supportsMap["actuators"] = actuators

        return linkedMapOf(
            "device_id" to device_id,
            "name" to name,
            "device_name" to name,
            "client_type" to (runtime["platform"] ?: "unknown"),
            "sdk_version" to "1.0.0",
            "runtime" to runtime,
            "properties" to props,
            "supports" to supportsMap
        )
    }

    /**
     * 创建手机设备
     */
    fun asPhone(): AudioChatDevice {
        return this
            .role(DeviceConstants.DEVICE_TYPE_PHONE)
            .sensorMic()
            .actuatorSpeaker()
    }

    /**
     * 创建眼镜设备
     */
    fun asGlass(): AudioChatDevice {
        return this
            .role(DeviceConstants.DEVICE_TYPE_GLASS)
            .sensorRgb()
    }
}