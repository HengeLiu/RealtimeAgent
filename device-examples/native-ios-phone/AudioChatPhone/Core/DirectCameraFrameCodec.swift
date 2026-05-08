import Foundation

enum DirectCameraFrameDecodeError: Error, LocalizedError, Equatable {
    case rawTooShort
    case invalidHeaderLength
    case invalidHeaderJSON
    case missingField(String)
    case invalidPayloadSize(expected: Int, actual: Int)
    case unsupportedFrame(String)

    var errorDescription: String? {
        switch self {
        case .rawTooShort:
            return "直连相机帧长度不足"
        case .invalidHeaderLength:
            return "直连相机帧 header 长度非法"
        case .invalidHeaderJSON:
            return "直连相机帧 header 不是 JSON"
        case let .missingField(field):
            return "直连相机帧缺少字段：\(field)"
        case let .invalidPayloadSize(expected, actual):
            return "直连相机帧 payload_size 不一致，声明 \(expected)，实际 \(actual)"
        case let .unsupportedFrame(frame):
            return "不支持的直连相机帧：\(frame)"
        }
    }
}

struct DirectCameraFrame: Equatable {
    let streamID: String
    let sequence: Int
    let timestampMS: Int64
    let codec: String
    let payload: Data
}

/// ESP32 -> iOS 直连相机帧编解码器。
///
/// audio-chat 直连帧使用 4 字节大端 header 长度、JSON header 和原始 JPEG payload。
/// header 必须声明 `stream_type=sensor.rgb`，不承载历史端侧协议字段。
enum DirectCameraFrameCodec {
    static func encodeDirectFrame(
        streamID: String,
        sequence: Int,
        payload: Data,
        timestampMS: Int64 = AudioChatIDs.nowMS(),
        codec: String = "jpeg"
    ) throws -> Data {
        let header: [String: Any] = [
            "stream_type": "sensor.rgb",
            "stream_id": streamID,
            "seq": sequence,
            "timestamp_ms": timestampMS,
            "codec": codec,
            "payload_size": payload.count,
        ]
        let headerData = try JSONSerialization.data(withJSONObject: header, options: [.sortedKeys])
        var result = Data()
        var headerLength = UInt32(headerData.count).bigEndian
        withUnsafeBytes(of: &headerLength) { result.append(contentsOf: $0) }
        result.append(headerData)
        result.append(payload)
        return result
    }

    static func decode(_ raw: Data) throws -> DirectCameraFrame {
        guard raw.count >= 4 else {
            throw DirectCameraFrameDecodeError.rawTooShort
        }
        let headerLength = raw.prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        let headerEnd = 4 + Int(headerLength)
        guard headerLength > 0, headerEnd <= raw.count else {
            throw DirectCameraFrameDecodeError.invalidHeaderLength
        }
        let headerData = raw.subdata(in: 4..<headerEnd)
        let payload = raw.subdata(in: headerEnd..<raw.count)
        guard let header = try JSONSerialization.jsonObject(with: headerData) as? [String: Any] else {
            throw DirectCameraFrameDecodeError.invalidHeaderJSON
        }

        let streamType = header["stream_type"] as? String
        guard streamType == "sensor.rgb" else {
            throw DirectCameraFrameDecodeError.unsupportedFrame(streamType ?? "unknown")
        }
        guard let streamID = header["stream_id"] as? String else {
            throw DirectCameraFrameDecodeError.missingField("stream_id")
        }
        guard let payloadSize = intValue(header["payload_size"]) else {
            throw DirectCameraFrameDecodeError.missingField("payload_size")
        }
        guard payloadSize == payload.count else {
            throw DirectCameraFrameDecodeError.invalidPayloadSize(expected: payloadSize, actual: payload.count)
        }
        return DirectCameraFrame(
            streamID: streamID,
            sequence: intValue(header["seq"]) ?? 0,
            timestampMS: int64Value(header["timestamp_ms"]) ?? AudioChatIDs.nowMS(),
            codec: header["codec"] as? String ?? "jpeg",
            payload: payload
        )
    }

    private static func intValue(_ value: Any?) -> Int? {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        return nil
    }

    private static func int64Value(_ value: Any?) -> Int64? {
        if let value = value as? Int64 {
            return value
        }
        if let value = value as? Int {
            return Int64(value)
        }
        if let value = value as? NSNumber {
            return value.int64Value
        }
        return nil
    }
}
