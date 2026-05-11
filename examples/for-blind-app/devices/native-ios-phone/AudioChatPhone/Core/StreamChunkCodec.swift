import Foundation

/// audio-chat stream chunk。
///
/// 主要功能：
/// 1. 表达 `/ws/stream` 二进制消息中的 JSON header 和 payload。
/// 2. 支持端侧上传 `sensor.rgb` / `sensor.mic`，也支持消费 `actuator.speaker`。
struct AudioChatStreamChunk {
    var userID: String
    var sessionID: String
    var streamID: String
    var streamType: String
    var seq: Int
    var payload: Data
    var codec: String
    var sampleRate: Int
    var channels: Int
    var durationMS: Int
    var timestampMS: Int64
    var version: String
    var final: Bool
    var metadata: [String: Any]

    init(
        userID: String,
        sessionID: String,
        streamID: String,
        streamType: String,
        seq: Int,
        payload: Data,
        codec: String,
        sampleRate: Int,
        channels: Int,
        durationMS: Int,
        timestampMS: Int64 = AudioChatIDs.nowMS(),
        version: String = "audio-chat.v1",
        final: Bool = false,
        metadata: [String: Any] = [:]
    ) {
        self.userID = userID
        self.sessionID = sessionID
        self.streamID = streamID
        self.streamType = streamType
        self.seq = seq
        self.payload = payload
        self.codec = codec
        self.sampleRate = sampleRate
        self.channels = channels
        self.durationMS = durationMS
        self.timestampMS = timestampMS
        self.version = version
        self.final = final
        self.metadata = metadata
    }
}

/// stream chunk 二进制编解码器。
///
/// 主要功能：
/// 1. 按 server 侧 `StreamChunkCodec` 格式写入 4 字节 header 长度。
/// 2. header 使用 JSON，payload 保持原始字节。
/// 3. 解码下行 `actuator.speaker` 时校验 payload_size。
enum AudioChatStreamChunkCodec {
    static func encode(_ chunk: AudioChatStreamChunk) throws -> Data {
        let header: [String: Any] = [
            "version": chunk.version,
            "user_id": chunk.userID,
            "session_id": chunk.sessionID,
            "stream_id": chunk.streamID,
            "stream_type": chunk.streamType,
            "seq": chunk.seq,
            "timestamp_ms": chunk.timestampMS,
            "codec": chunk.codec,
            "sample_rate": chunk.sampleRate,
            "channels": chunk.channels,
            "duration_ms": chunk.durationMS,
            "payload_size": chunk.payload.count,
            "final": chunk.final,
            "metadata": chunk.metadata,
        ]
        let headerData = try JSONSerialization.data(withJSONObject: header, options: [.sortedKeys])
        var result = Data()
        var headerLength = UInt32(headerData.count).bigEndian
        withUnsafeBytes(of: &headerLength) { result.append(contentsOf: $0) }
        result.append(headerData)
        result.append(chunk.payload)
        return result
    }

    static func decode(_ data: Data) throws -> AudioChatStreamChunk {
        guard data.count >= 4 else {
            throw AudioChatEndpointError.invalidStreamChunk("message too short")
        }
        let headerLength = data.prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        let headerEnd = 4 + Int(headerLength)
        guard headerLength > 0, headerEnd <= data.count else {
            throw AudioChatEndpointError.invalidStreamChunk("invalid header length")
        }
        let headerData = data.subdata(in: 4..<headerEnd)
        let payload = data.subdata(in: headerEnd..<data.count)
        guard let header = try JSONSerialization.jsonObject(with: headerData) as? [String: Any] else {
            throw AudioChatEndpointError.invalidStreamChunk("header is not json object")
        }
        let payloadSize = header["payload_size"] as? Int ?? -1
        guard payloadSize == payload.count else {
            throw AudioChatEndpointError.invalidStreamChunk("payload_size mismatch")
        }
        return AudioChatStreamChunk(
            userID: try string(header, "user_id"),
            sessionID: try string(header, "session_id"),
            streamID: try string(header, "stream_id"),
            streamType: try string(header, "stream_type"),
            seq: try int(header, "seq"),
            payload: payload,
            codec: try string(header, "codec"),
            sampleRate: try int(header, "sample_rate"),
            channels: try int(header, "channels"),
            durationMS: try int(header, "duration_ms"),
            timestampMS: Int64(try int(header, "timestamp_ms")),
            version: header["version"] as? String ?? "audio-chat.v1",
            final: header["final"] as? Bool ?? false,
            metadata: header["metadata"] as? [String: Any] ?? [:]
        )
    }

    private static func string(_ header: [String: Any], _ key: String) throws -> String {
        guard let value = header[key] as? String else {
            throw AudioChatEndpointError.invalidStreamChunk("missing \(key)")
        }
        return value
    }

    private static func int(_ header: [String: Any], _ key: String) throws -> Int {
        if let value = header[key] as? Int {
            return value
        }
        if let value = header[key] as? Double {
            return Int(value)
        }
        throw AudioChatEndpointError.invalidStreamChunk("missing \(key)")
    }
}
