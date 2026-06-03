package com.audiochat.phone.protocol

/**
 * 设备能力声明
 * 完全复刻 Python 端 supports 结构
 */
data class DeviceSupports(
    val sensors: List<SensorConfig> = listOf(
        SensorConfig(
            type = "rgb",
            modes = listOf("single", "continuous"),
            default = SensorDefault(format = "jpeg", frequency_hz = 1, sample_count = 1)
        )
    ),
    val actuators: List<ActuatorConfig> = listOf(
        ActuatorConfig(type = "vibrator", commands = listOf("vibrate"))
    )
) {
    fun toMap(): Map<String, Any> {
        return mapOf(
            "sensors" to sensors.map { it.toMap() },
            "actuators" to actuators.map { it.toMap() }
        )
    }
}

data class SensorConfig(
    val type: String,
    val modes: List<String> = emptyList(),
    val default: SensorDefault? = null
) {
    fun toMap(): Map<String, Any> {
        return buildMap {
            put("type", type)
            if (modes.isNotEmpty()) put("modes", modes)
            default?.let { put("default", it.toMap()) }
        }
    }
}

data class SensorDefault(
    val format: String = "jpeg",
    val frequency_hz: Int = 1,
    val sample_count: Int = 1
) {
    fun toMap(): Map<String, Any> {
        return mapOf(
            "format" to format,
            "frequency_hz" to frequency_hz,
            "sample_count" to sample_count
        )
    }
}

data class ActuatorConfig(
    val type: String,
    val commands: List<String> = emptyList()
) {
    fun toMap(): Map<String, Any> {
        return buildMap {
            put("type", type)
            if (commands.isNotEmpty()) put("commands", commands)
        }
    }
}