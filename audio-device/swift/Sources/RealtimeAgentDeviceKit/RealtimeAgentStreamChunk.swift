import Foundation

public struct RealtimeAgentStreamChunk {
    public var userID: String
    public var sessionID: String
    public var streamID: String
    public var streamType: String
    public var seq: Int
    public var payload: Data
    public var codec: String
    public var sampleRate: Int
    public var channels: Int
    public var durationMS: Int
    public var timestampMS: Int64
    public var version: String
    public var final: Bool
    public var metadata: [String: Any]

    public init(
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
        timestampMS: Int64 = RealtimeAgentIDs.nowMS(),
        version: String = audioChatProtocolVersion,
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

public enum RealtimeAgentStreamChunkCodec {
    public static func encode(_ chunk: RealtimeAgentStreamChunk) throws -> Data {
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
        let headerData = try JSONSerialization.data(withJSONObject: header, options: [])
        var result = Data()
        var headerLength = UInt32(headerData.count).bigEndian
        withUnsafeBytes(of: &headerLength) { result.append(contentsOf: $0) }
        result.append(headerData)
        result.append(chunk.payload)
        return result
    }

    public static func decode(_ data: Data) throws -> RealtimeAgentStreamChunk {
        guard data.count >= 4 else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("message too short")
        }
        let headerLength = data.prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        let headerEnd = 4 + Int(headerLength)
        guard headerLength > 0, headerEnd <= data.count else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("invalid header length")
        }
        let headerData = data.subdata(in: 4..<headerEnd)
        let payload = data.subdata(in: headerEnd..<data.count)
        guard let header = try JSONSerialization.jsonObject(with: headerData) as? [String: Any] else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("header is not json object")
        }
        let payloadSize = header["payload_size"] as? Int ?? -1
        guard payloadSize == payload.count else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("payload_size mismatch")
        }
        return RealtimeAgentStreamChunk(
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
            version: header["version"] as? String ?? audioChatProtocolVersion,
            final: header["final"] as? Bool ?? false,
            metadata: header["metadata"] as? [String: Any] ?? [:]
        )
    }

    private static func string(_ header: [String: Any], _ key: String) throws -> String {
        guard let value = header[key] as? String else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("missing \(key)")
        }
        return value
    }

    private static func int(_ header: [String: Any], _ key: String) throws -> Int {
        if let value = header[key] as? Int { return value }
        if let value = header[key] as? Double { return Int(value) }
        throw RealtimeAgentDeviceError.invalidStreamChunk("missing \(key)")
    }
}
