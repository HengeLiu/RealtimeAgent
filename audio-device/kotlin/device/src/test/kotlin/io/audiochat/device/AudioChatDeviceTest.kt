package io.audiochat.device

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class AudioChatDeviceTest {
    @Test
    fun buildsRegistrationPayload() {
        val device = AudioChatDevice.define("dev-android-001")
            .user("user-001")
            .name("Android")
            .role("phone")
            .sensorRgb(frequencyHz = 1)
            .actuatorVibrator()

        val payload = device.registrationPayload()

        assertEquals("dev-android-001", payload["device_id"])
        assertEquals("Android", payload["name"])
        assertEquals("phone", (payload["properties"] as Map<*, *>)["device_role"])

        val supports = payload["supports"] as Map<*, *>
        val sensors = supports["sensors"] as List<*>
        assertEquals("rgb", (sensors.first() as Map<*, *>)["type"])
    }

    @Test
    fun createsPhoneDevice() {
        val device = AudioChatDevice.define("phone-001")
            .user("user-001")
            .asPhone()

        val payload = device.registrationPayload()
        assertEquals("phone", (payload["properties"] as Map<*, *>)["device_role"])

        val supports = payload["supports"] as Map<*, *>
        val sensors = supports["sensors"] as List<*>
        val actuators = supports["actuators"] as List<*>

        assertTrue(sensors.any { (it as Map<*, *>)["type"] == "mic" })
        assertTrue(actuators.any { (it as Map<*, *>)["type"] == "speaker" })
    }

    @Test
    fun createsGlassDevice() {
        val device = AudioChatDevice.define("glass-001")
            .user("user-001")
            .asGlass()

        val payload = device.registrationPayload()
        assertEquals("glass", (payload["properties"] as Map<*, *>)["device_role"])

        val supports = payload["supports"] as Map<*, *>
        val sensors = supports["sensors"] as List<*>

        assertTrue(sensors.any { (it as Map<*, *>)["type"] == "rgb" })
    }

    @Test
    fun streamChunkEncodeDecodeRoundTrip() {
        val chunk = StreamChunk(
            user_id = "user-001",
            session_id = "session-001",
            stream_id = "stream-001",
            stream_type = StreamTypes.SENSOR_MIC,
            seq = 0,
            payload = "abc".toByteArray(),
            codec = "pcm16le",
            sample_rate = 16000,
            channels = 1,
            duration_ms = 20,
            final = false
        )

        val encoded = StreamChunkCodec.encode(chunk)
        val decoded = StreamChunkCodec.decode(encoded)

        assertEquals(chunk.user_id, decoded.user_id)
        assertEquals(chunk.session_id, decoded.session_id)
        assertEquals(chunk.stream_id, decoded.stream_id)
        assertEquals(chunk.stream_type, decoded.stream_type)
        assertEquals(chunk.seq, decoded.seq)
        assertEquals(chunk.codec, decoded.codec)
        assertEquals(chunk.payload.toString(Charsets.UTF_8), decoded.payload.toString(Charsets.UTF_8))
    }

    @Test
    fun eventToJsonRoundTrip() {
        val event = AudioChatEvent(
            event_name = EventTypes.COMMAND_REQUESTED,
            user_id = "user-001",
            producer_id = "device-001",
            command_id = "cmd-001",
            payload = mapOf("task_type" to "haptic.vibrate", "command_id" to "cmd-001")
        )

        val json = event.toJson()
        val decoded = AudioChatEvent.fromJson(json)

        assertEquals(event.event_name, decoded.event_name)
        assertEquals(event.user_id, decoded.user_id)
        assertEquals(event.producer_id, decoded.producer_id)
        assertEquals(event.command_id, decoded.command_id)
        assertEquals(event.payload["task_type"], decoded.payload["task_type"])
    }

    @Test
    fun createsAudioChunk() {
        val chunk = StreamChunkCodec.createAudioChunk(
            userId = "user-001",
            sessionId = "session-001",
            streamId = "stream-001",
            pcmData = ByteArray(640), // 20ms @ 16kHz stereo
            seq = 0,
            sampleRate = 16000,
            channels = 1
        )

        assertEquals(StreamTypes.SENSOR_MIC, chunk.stream_type)
        assertEquals("pcm16le", chunk.codec)
        assertEquals(16000, chunk.sample_rate)
        assertEquals(1, chunk.channels)
    }

    @Test
    fun createsImageChunk() {
        val jpegData = ByteArray(1024)
        val chunk = StreamChunkCodec.createImageChunk(
            userId = "user-001",
            sessionId = "session-001",
            streamId = "stream-001",
            jpegData = jpegData,
            seq = 0,
            requestId = "req-001"
        )

        assertEquals(StreamTypes.SENSOR_RGB, chunk.stream_type)
        assertEquals("jpeg", chunk.codec)
        assertEquals(1, chunk.sample_rate)
        assertEquals(true, chunk.final)
        assertEquals("req-001", chunk.metadata["request_id"])
    }
}