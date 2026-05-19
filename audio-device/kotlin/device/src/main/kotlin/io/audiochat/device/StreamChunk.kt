package io.audiochat.device

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Stream 数据块
 * 对应 audio-chat.v1 协议的二进制流格式
 */
data class StreamChunk(
    val user_id: String,
    val session_id: String,
    val stream_id: String,
    val stream_type: String,
    val seq: Int,
    val payload: ByteArray,
    val codec: String = "pcm16le",
    val sample_rate: Int = 16000,
    val channels: Int = 1,
    val duration_ms: Int = 0,
    val final: Boolean = false,
    val metadata: Map<String, Any?> = emptyMap(),
    val timestamp_ms: Long = nowMs(),
    val trace_id: String? = null,
    val task_trace_id: String? = null
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (javaClass != other?.javaClass) return false
        other as StreamChunk
        if (!payload.contentEquals(other.payload)) return false
        return true
    }

    override fun hashCode(): Int = payload.contentHashCode()
}

/**
 * Stream Chunk 编解码器
 * 二进制帧格式：
 * - 4 bytes big-endian header length
 * - header JSON bytes
 * - payload bytes
 */
object StreamChunkCodec {

    fun encode(chunk: StreamChunk): ByteArray {
        val header = mutableMapOf(
            "version" to AUDIO_CHAT_PROTOCOL_VERSION,
            "user_id" to chunk.user_id,
            "session_id" to chunk.session_id,
            "stream_id" to chunk.stream_id,
            "stream_type" to chunk.stream_type,
            "seq" to chunk.seq,
            "timestamp_ms" to chunk.timestamp_ms,
            "codec" to chunk.codec,
            "sample_rate" to chunk.sample_rate,
            "channels" to chunk.channels,
            "duration_ms" to chunk.duration_ms,
            "payload_size" to chunk.payload.size,
            "final" to chunk.final,
            "metadata" to chunk.metadata
        )
        chunk.trace_id?.let { header["trace_id"] = it }
        chunk.task_trace_id?.let { header["task_trace_id"] = it }

        val headerJson = GsonFactory.toJson(header)
        val headerBytes = headerJson.toByteArray(Charsets.UTF_8)
        val payloadBytes = chunk.payload

        val totalLength = 4 + headerBytes.size + payloadBytes.size
        val buffer = ByteBuffer.allocate(totalLength).order(ByteOrder.BIG_ENDIAN)

        buffer.putInt(headerBytes.size)
        buffer.put(headerBytes)
        buffer.put(payloadBytes)

        return buffer.array()
    }

    fun decode(data: ByteArray): StreamChunk {
        val buffer = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN)

        val headerLength = buffer.int
        val headerBytes = ByteArray(headerLength)
        buffer.get(headerBytes)
        val headerJson = String(headerBytes, Charsets.UTF_8)
        val header = GsonFactory.fromJson<Map<String, Any?>>(headerJson)

        val payloadSize = (header["payload_size"] as Number).toInt()
        val payloadBytes = ByteArray(payloadSize)
        buffer.get(payloadBytes)

        return StreamChunk(
            user_id = header["user_id"] as String,
            session_id = header["session_id"] as String,
            stream_id = header["stream_id"] as String,
            stream_type = header["stream_type"] as String,
            seq = (header["seq"] as Number).toInt(),
            payload = payloadBytes,
            codec = header["codec"] as? String ?: "pcm16le",
            sample_rate = (header["sample_rate"] as? Number)?.toInt() ?: 16000,
            channels = (header["channels"] as? Number)?.toInt() ?: 1,
            duration_ms = (header["duration_ms"] as? Number)?.toInt() ?: 0,
            final = header["final"] as? Boolean ?: false,
            metadata = (header["metadata"] as? Map<String, Any?>) ?: emptyMap(),
            timestamp_ms = (header["timestamp_ms"] as? Number)?.toLong() ?: nowMs(),
            trace_id = header["trace_id"] as? String,
            task_trace_id = header["task_trace_id"] as? String
        )
    }

    /**
     * 创建音频 Chunk
     */
    fun createAudioChunk(
        userId: String,
        sessionId: String,
        streamId: String,
        pcmData: ByteArray,
        seq: Int,
        sampleRate: Int = 16000,
        channels: Int = 1,
        isFinal: Boolean = false
    ): StreamChunk {
        return StreamChunk(
            user_id = userId,
            session_id = sessionId,
            stream_id = streamId,
            stream_type = StreamTypes.SENSOR_MIC,
            seq = seq,
            payload = pcmData,
            codec = "pcm16le",
            sample_rate = sampleRate,
            channels = channels,
            duration_ms = pcmData.size / (sampleRate / 1000 * channels * 2),
            final = isFinal
        )
    }

    /**
     * 创建图片 Chunk
     */
    fun createImageChunk(
        userId: String,
        sessionId: String,
        streamId: String,
        jpegData: ByteArray,
        seq: Int = 0,
        requestId: String? = null
    ): StreamChunk {
        return StreamChunk(
            user_id = userId,
            session_id = sessionId,
            stream_id = streamId,
            stream_type = StreamTypes.SENSOR_RGB,
            seq = seq,
            payload = jpegData,
            codec = "jpeg",
            sample_rate = 1,
            channels = 1,
            duration_ms = 1,
            final = true,
            metadata = requestId?.let { mapOf("request_id" to it) } ?: emptyMap()
        )
    }
}