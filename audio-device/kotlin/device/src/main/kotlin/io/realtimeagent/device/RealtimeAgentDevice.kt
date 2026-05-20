package io.realtimeagent.device

class RealtimeAgentDevice private constructor(private val deviceId: String) {
    private var userId: String = ""
    private var name: String = deviceId
    private var role: String? = null
    private var runtime: Map<String, Any?> = mapOf("platform" to "android", "language" to "kotlin")
    private val properties = linkedMapOf<String, Any?>()
    private val sensors = mutableListOf<Map<String, Any?>>()
    private val actuators = mutableListOf<Map<String, Any?>>()

    companion object {
        @JvmStatic
        fun define(deviceId: String): RealtimeAgentDevice = RealtimeAgentDevice(deviceId)
    }

    fun user(userId: String): RealtimeAgentDevice {
        this.userId = userId
        return this
    }

    fun name(name: String): RealtimeAgentDevice {
        this.name = name
        return this
    }

    fun role(role: String): RealtimeAgentDevice {
        this.role = role
        return this
    }

    fun property(key: String, value: Any?): RealtimeAgentDevice {
        properties[key] = value
        return this
    }

    fun sensorRgb(
        modes: List<String> = listOf("single"),
        format: String = "jpeg",
        frequencyHz: Number? = null,
    ): RealtimeAgentDevice {
        val defaults = linkedMapOf<String, Any?>("format" to format)
        if (frequencyHz != null) defaults["frequency_hz"] = frequencyHz
        sensors.add(mapOf("type" to "rgb", "modes" to modes, "default" to defaults))
        return this
    }

    fun actuatorVibrator(commands: List<String> = listOf("vibrate")): RealtimeAgentDevice {
        actuators.add(mapOf("type" to "vibrator", "commands" to commands))
        return this
    }

    fun registrationPayload(): Map<String, Any?> {
        require(userId.isNotBlank()) { "user_id is required" }
        val props = linkedMapOf<String, Any?>()
        props.putAll(properties)
        if (role != null) props["device_role"] = role
        val supports = linkedMapOf<String, Any?>()
        if (sensors.isNotEmpty()) supports["sensors"] = sensors
        if (actuators.isNotEmpty()) supports["actuators"] = actuators
        require(supports.isNotEmpty()) { "device supports must not be empty" }
        return linkedMapOf(
            "device_id" to deviceId,
            "name" to name,
            "device_name" to name,
            "client_type" to (runtime["platform"] ?: "android"),
            "sdk_version" to "0.1.0",
            "runtime" to runtime,
            "properties" to props,
            "supports" to supports,
        )
    }
}
