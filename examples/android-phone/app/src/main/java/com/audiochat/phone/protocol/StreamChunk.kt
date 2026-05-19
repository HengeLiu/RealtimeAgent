package com.audiochat.phone.protocol

import com.audiochat.phone.audio.AudioConstants
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

/**
 * Stream 数据块
 * 完全复刻 Python 端 StreamChunk 协议
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
    val timestamp_ms: Long = System.currentTimeMillis(),
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

    override fun hashCode(): Int {
        return payload.contentHashCode()
    }
}

/**
 * Stream Chunk 编解码器
 * 完全复刻 Python 端 StreamChunkCodec 协议
 *
 * 二进制帧格式：
 * - 4 bytes big-endian header length
 * - header JSON bytes
 * - payload bytes
 */
object StreamChunkCodec {

    /**
     * 编码 StreamChunk 为二进制数据
     */
    fun encode(chunk: StreamChunk): ByteArray {
        val header = mutableMapOf(
            "version" to "audio-chat.v1",
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

        val headerJson = GsonFactory.gson.toJson(header)
        val headerBytes = headerJson.toByteArray(Charsets.UTF_8)
        val payloadBytes = chunk.payload

        // 总长度 = 4 bytes (header length) + header + payload
        val totalLength = 4 + headerBytes.size + payloadBytes.size
        val buffer = ByteBuffer.allocate(totalLength).order(ByteOrder.BIG_ENDIAN)

        // 写入 header 长度 (4 bytes big-endian)
        buffer.putInt(headerBytes.size)

        // 写入 header JSON
        buffer.put(headerBytes)

        // 写入 payload
        buffer.put(payloadBytes)

        return buffer.array()
    }

    /**
     * 解码二进制数据为 StreamChunk
     */
    fun decode(data: ByteArray): StreamChunk {
        val buffer = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN)

        // 读取 header 长度
        val headerLength = buffer.int

        // 读取 header JSON
        val headerBytes = ByteArray(headerLength)
        buffer.get(headerBytes)
        val headerJson = String(headerBytes, Charsets.UTF_8)

        // 解析 header
        val header = GsonFactory.fromJson<Map<String, Any>>(headerJson)

        // 读取 payload
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
            timestamp_ms = (header["timestamp_ms"] as? Number)?.toLong() ?: System.currentTimeMillis(),
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
            sample_rate = AudioConstants.INPUT_SAMPLE_RATE,
            channels = AudioConstants.INPUT_CHANNELS,
            duration_ms = pcmData.size / 32, // 16kHz / 1000 * channels * 2bytes
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