package io.audiochat.device

import kotlin.test.Test
import kotlin.test.assertEquals

class AudioChatDeviceTest {
    @Test
    fun buildsRegistrationPayload() {
        val payload = AudioChatDevice.define("dev-android-001")
            .user("user-001")
            .name("Android")
            .role("phone")
            .sensorRgb(frequencyHz = 1)
            .actuatorVibrator()
            .registrationPayload()

        assertEquals("dev-android-001", payload["device_id"])
        val supports = payload["supports"] as Map<*, *>
        val sensors = supports["sensors"] as List<*>
        assertEquals("rgb", (sensors.first() as Map<*, *>)["type"])
    }

    @Test
    fun streamHeaderCodecRoundTripsPayload() {
        val raw = StreamChunkCodec.encodeHeader(
            """{"stream_id":"s1","payload_size":3}""",
            "abc".toByteArray(),
        )
        val (header, payload) = StreamChunkCodec.decodeHeader(raw)
        assertEquals(true, header.contains("\"stream_id\":\"s1\""))
        assertEquals("abc", payload.toString(Charsets.UTF_8))
    }
}
