import Foundation

/// audio-chat 控制事件信封。
///
/// 主要功能：
/// 1. 按公共协议构造注册、stream 打开、stream 关闭和播放回执事件。
/// 2. 保证 iOS 参考端不使用隐藏 RPC 或固定设备类型字段。
/// 3. 和 server 侧 `Event.to_dict()` 保持同一字段语义。
struct AudioChatEvent {
    var eventName: String
    var userID: String
    var producerID: String
    var payload: [String: Any]
    var version: String
    var eventID: String
    var timestampMS: Int64
    var sessionID: String?
    var streamID: String?
    var streamType: String?

    init(
        eventName: String,
        userID: String,
        producerID: String,
        payload: [String: Any] = [:],
        sessionID: String? = nil,
        streamID: String? = nil,
        streamType: String? = nil,
        version: String = "audio-chat.v1",
        eventID: String = AudioChatIDs.make(prefix: "evt"),
        timestampMS: Int64 = AudioChatIDs.nowMS()
    ) {
        self.eventName = eventName
        self.userID = userID
        self.producerID = producerID
        self.payload = payload
        self.version = version
        self.eventID = eventID
        self.timestampMS = timestampMS
        self.sessionID = sessionID
        self.streamID = streamID
        self.streamType = streamType
    }

    init(dictionary: [String: Any]) throws {
        guard let eventName = dictionary["event_name"] as? String,
              let userID = dictionary["user_id"] as? String,
              let producerID = dictionary["producer_id"] as? String else {
            throw AudioChatEndpointError.invalidEvent("missing event_name/user_id/producer_id")
        }
        self.eventName = eventName
        self.userID = userID
        self.producerID = producerID
        self.payload = dictionary["payload"] as? [String: Any] ?? [:]
        self.version = dictionary["version"] as? String ?? "audio-chat.v1"
        self.eventID = dictionary["event_id"] as? String ?? AudioChatIDs.make(prefix: "evt")
        self.timestampMS = Int64(dictionary["timestamp_ms"] as? Int ?? Int(AudioChatIDs.nowMS()))
        self.sessionID = dictionary["session_id"] as? String
        self.streamID = dictionary["stream_id"] as? String
        self.streamType = dictionary["stream_type"] as? String
    }

    var dictionary: [String: Any] {
        var data: [String: Any] = [
            "version": version,
            "event_id": eventID,
            "event_name": eventName,
            "timestamp_ms": timestampMS,
            "user_id": userID,
            "producer_id": producerID,
            "payload": payload,
        ]
        if let sessionID {
            data["session_id"] = sessionID
        }
        if let streamID {
            data["stream_id"] = streamID
        }
        if let streamType {
            data["stream_type"] = streamType
        }
        return data
    }

    var jsonString: String {
        let data = try? JSONSerialization.data(withJSONObject: dictionary, options: [])
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
    }
}

/// audio-chat id 和时间工具。
enum AudioChatIDs {
    static func make(prefix: String) -> String {
        "\(prefix)_\(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12).lowercased())"
    }

    static func nowMS() -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1000)
    }
}

/// iOS 参考端协议错误。
enum AudioChatEndpointError: LocalizedError {
    case invalidEvent(String)
    case invalidStreamChunk(String)
    case missingWebSocket(String)
    case invalidURL(String)

    var errorDescription: String? {
        switch self {
        case let .invalidEvent(message):
            return "事件格式错误：\(message)"
        case let .invalidStreamChunk(message):
            return "stream chunk 格式错误：\(message)"
        case let .missingWebSocket(message):
            return "WebSocket 未连接：\(message)"
        case let .invalidURL(message):
            return "URL 无效：\(message)"
        }
    }
}
