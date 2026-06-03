package com.realtimeagent.device.protocol

object DeviceConstants {
    const val DEVICE_TYPE_PHONE = "phone"
    const val DEVICE_TYPE_GLASS = "glass"

    val ROLE_PHONE = mapOf(
        "device_role" to DEVICE_TYPE_PHONE,
        "endpoint.role.phone" to true,
        "endpoint.compute.vision" to true,
        "peer.video.receiver" to true
    )

    val ROLE_GLASS = mapOf(
        "device_role" to DEVICE_TYPE_GLASS,
        "endpoint.role.glass" to true,
        "peer.video.sender" to true
    )

    val AUDIO_CAPABILITIES = mapOf(
        "audio_chat.audio_input" to "sensor.mic",
        "audio_chat.audio_output" to "actuator.speaker"
    )

    val DEFAULT_SENSOR_CONFIG = listOf(
        mapOf(
            "type" to "rgb",
            "modes" to listOf("single", "continuous"),
            "default" to mapOf(
                "format" to "jpeg",
                "frequency_hz" to 1,
                "sample_count" to 1
            )
        )
    )

    val DEFAULT_ACTUATOR_CONFIG = listOf(
        mapOf(
            "type" to "vibrator",
            "commands" to listOf("vibrate")
        )
    )
}

object EventTypes {
    const val DEVICE_REGISTER_REQUESTED = "control.device.register.requested"
    const val DEVICE_REGISTERED = "control.device.registered"
    const val DEVICE_REGISTER_FAILED = "control.device.register.failed"
    const val DEVICE_HEARTBEAT = "control.device.heartbeat.received"

    const val STREAM_INPUT_OPENED = "stream.input.opened"
    const val STREAM_INPUT_CLOSED = "stream.input.closed"
    const val STREAM_OUTPUT_OPENED = "stream.output.opened"
    const val STREAM_OUTPUT_CLOSED = "stream.output.closed"
    const val STREAM_CONTROL_OPEN_REQUESTED = "stream.control.open.requested"
    const val STREAM_OUTPUT_CLOSE_REQUESTED = "stream.output.close.requested"

    const val COMMAND_REQUESTED = "command.requested"
    const val COMMAND_COMPLETED = "command.completed"
    const val COMMAND_FAILED = "command.failed"

    const val SESSION_CLOSE_REQUESTED = "control.session.close.requested"
    const val DEVICE_WAKE_REQUESTED = "control.device.wake.requested"
    const val DEVICE_INTERRUPT_REQUESTED = "control.device.interrupt.requested"
}

object StreamTypes {
    const val SENSOR_MIC = "sensor.mic"
    const val SENSOR_RGB = "sensor.rgb"
    const val ACTUATOR_SPEAKER = "actuator.speaker"
}
