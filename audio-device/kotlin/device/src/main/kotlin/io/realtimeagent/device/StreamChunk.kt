package io.realtimeagent.device

import java.nio.ByteBuffer
import java.nio.ByteOrder

data class StreamChunk(
    val userId: String,
    val sessionId: String,
    val streamId: String,
    val streamType: String,
    val seq: Int,
    val payload: ByteArray,
    val codec: String = "pcm16le",
    val sampleRate: Int = 16000,
    val channels: Int = 1,
    val durationMs: Int = 20,
    val timestampMs: Long = nowMs(),
    val version: String = REALTIME_AGENT_PROTOCOL_VERSION,
    val final: Boolean = false,
    val metadata: Map<String, Any?> = emptyMap(),
) {
    override fun equals(other: Any?): Boolean {
        return other is StreamChunk &&
            userId == other.userId &&
            sessionId == other.sessionId &&
            streamId == other.streamId &&
            streamType == other.streamType &&
            seq == other.seq &&
            payload.contentEquals(other.payload)
    }

    override fun hashCode(): Int = streamId.hashCode()
}

object StreamChunkCodec {
    fun encodeHeader(headerJson: String, payload: ByteArray): ByteArray {
        val headerBytes = headerJson.toByteArray(Charsets.UTF_8)
        return ByteBuffer.allocate(4 + headerBytes.size + payload.size)
            .order(ByteOrder.BIG_ENDIAN)
            .putInt(headerBytes.size)
            .put(headerBytes)
            .put(payload)
            .array()
    }

    fun decodeHeader(raw: ByteArray): Pair<String, ByteArray> {
        require(raw.size >= 4) { "StreamChunk message too short" }
        val headerLength = ByteBuffer.wrap(raw, 0, 4).order(ByteOrder.BIG_ENDIAN).int
        val headerEnd = 4 + headerLength
        require(headerLength > 0 && headerEnd <= raw.size) { "StreamChunk header length is invalid" }
        val headerJson = raw.copyOfRange(4, headerEnd).toString(Charsets.UTF_8)
        val payload = raw.copyOfRange(headerEnd, raw.size)
        val expected = Regex("\"payload_size\"\\s*:\\s*(\\d+)").find(headerJson)?.groupValues?.get(1)?.toInt()
        require(expected == payload.size) { "StreamChunk payload_size mismatch" }
        return headerJson to payload
    }
}
