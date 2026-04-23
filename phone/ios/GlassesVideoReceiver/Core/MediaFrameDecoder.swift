import Foundation

/// 媒体帧解码错误。
///
/// 主要功能：
/// 1. 表达媒体帧长度、头部和内容校验失败。
/// 2. 为单元测试和页面错误展示提供明确原因。
enum MediaFrameDecodeError: Error, LocalizedError, Equatable {
    case rawTooShort
    case invalidHeaderLength
    case invalidHeaderJSON
    case missingField(String)
    case invalidFrameType(String)
    case invalidPayloadSize(expected: Int, actual: Int)

    var errorDescription: String? {
        switch self {
        case .rawTooShort:
            return "媒体帧长度不足，无法读取头长度"
        case .invalidHeaderLength:
            return "媒体帧头长度非法"
        case .invalidHeaderJSON:
            return "媒体帧头 JSON 解析失败"
        case let .missingField(field):
            return "媒体帧缺少字段：\(field)"
        case let .invalidFrameType(frameType):
            return "不支持的 frame_type：\(frameType)"
        case let .invalidPayloadSize(expected, actual):
            return "payload_size 不一致，声明 \(expected)，实际 \(actual)"
        }
    }
}

/// 已解析的相机媒体帧。
///
/// 主要属性：
/// 1. `streamID`：流标识。
/// 2. `sequence`：帧序号。
/// 3. `codec`：编码格式，当前期望为 jpeg。
/// 4. `payload`：真实 JPEG 字节。
struct CameraMediaFrame: Equatable {
    let streamID: String
    let sequence: Int
    let timestampMs: Int64
    let codec: String
    let payload: Data
}

/// 媒体帧解码器。
///
/// 主要功能：
/// 1. 解析统一 `MediaFrame` 二进制格式。
/// 2. 校验 `camera_frame` 头字段。
/// 3. 提供供 UI 使用的结构化解码结果。
enum MediaFrameDecoder {
    /// 从原始二进制中解码相机媒体帧。
    ///
    /// 主要逻辑：
    /// 1. 读取头长度。
    /// 2. 解析头 JSON。
    /// 3. 校验关键字段。
    /// 4. 返回结构化结果。
    ///
    /// 参数：
    /// 1. `raw`：完整媒体帧原始字节。
    ///
    /// 返回值：
    /// 1. 已校验的 `CameraMediaFrame`。
    ///
    /// 异常情况：
    /// 1. 任一长度或字段校验失败时抛出 `MediaFrameDecodeError`。
    static func decodeCameraFrame(from raw: Data) throws -> CameraMediaFrame {
        guard raw.count >= 4 else {
            throw MediaFrameDecodeError.rawTooShort
        }

        let headerLength = raw.prefix(4).reduce(0) { partialResult, byte in
            (partialResult << 8) | Int(byte)
        }
        guard headerLength > 0, raw.count >= 4 + headerLength else {
            throw MediaFrameDecodeError.invalidHeaderLength
        }

        let headerData = raw.subdata(in: 4..<(4 + headerLength))
        let payload = raw.advanced(by: 4 + headerLength)
        guard
            let object = try JSONSerialization.jsonObject(with: headerData) as? [String: Any]
        else {
            throw MediaFrameDecodeError.invalidHeaderJSON
        }

        guard let streamID = object["stream_id"] as? String else {
            throw MediaFrameDecodeError.missingField("stream_id")
        }
        guard let frameType = object["frame_type"] as? String else {
            throw MediaFrameDecodeError.missingField("frame_type")
        }
        guard frameType == "camera_frame" else {
            throw MediaFrameDecodeError.invalidFrameType(frameType)
        }
        guard let sequence = numberValue(from: object["seq"]) else {
            throw MediaFrameDecodeError.missingField("seq")
        }
        guard let timestampMs = int64Value(from: object["ts_ms"]) else {
            throw MediaFrameDecodeError.missingField("ts_ms")
        }
        guard let codec = object["codec"] as? String else {
            throw MediaFrameDecodeError.missingField("codec")
        }
        guard let payloadSize = numberValue(from: object["payload_size"]) else {
            throw MediaFrameDecodeError.missingField("payload_size")
        }
        guard payloadSize == payload.count else {
            throw MediaFrameDecodeError.invalidPayloadSize(expected: payloadSize, actual: payload.count)
        }

        return CameraMediaFrame(
            streamID: streamID,
            sequence: sequence,
            timestampMs: timestampMs,
            codec: codec,
            payload: payload
        )
    }

    /// 从松散 JSON 值中提取整数。
    ///
    /// 参数：
    /// 1. `value`：头字段中的原始值。
    ///
    /// 返回值：
    /// 1. 成功时返回整数，失败时返回 `nil`。
    private static func numberValue(from value: Any?) -> Int? {
        if let intValue = value as? Int {
            return intValue
        }
        if let number = value as? NSNumber {
            return number.intValue
        }
        return nil
    }

    /// 从松散 JSON 值中提取 64 位整数。
    ///
    /// 参数：
    /// 1. `value`：头字段中的原始值。
    ///
    /// 返回值：
    /// 1. 成功时返回 64 位整数，失败时返回 `nil`。
    private static func int64Value(from value: Any?) -> Int64? {
        if let intValue = value as? Int64 {
            return intValue
        }
        if let intValue = value as? Int {
            return Int64(intValue)
        }
        if let number = value as? NSNumber {
            return number.int64Value
        }
        return nil
    }
}

private extension Data {
    /// 返回移除前缀字节后的数据。
    ///
    /// 参数：
    /// 1. `offset`：要跳过的字节数。
    ///
    /// 返回值：
    /// 1. 新的数据切片。
    func advanced(by offset: Int) -> Data {
        subdata(in: offset..<count)
    }
}
