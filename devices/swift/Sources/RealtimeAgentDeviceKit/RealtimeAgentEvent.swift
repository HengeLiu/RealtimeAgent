import Foundation

public let audioChatProtocolVersion = "realtime-agent.v1"

public enum RealtimeAgentIDs {
    public static func make(prefix: String) -> String {
        "\(prefix)_\(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12).lowercased())"
    }

    public static func nowMS() -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1000)
    }
}

public enum RealtimeAgentDeviceError: Error, LocalizedError {
    case invalidEvent(String)
    case invalidStreamChunk(String)
    case invalidURL(String)
    case missingWebSocket(String)
    case registrationFailed(String)
    case transportClosed(String)

    public var errorDescription: String? {
        switch self {
        case let .invalidEvent(message):
            return "事件格式错误：\(message)"
        case let .invalidStreamChunk(message):
            return "stream chunk 格式错误：\(message)"
        case let .invalidURL(message):
            return "URL 无效：\(message)"
        case let .missingWebSocket(message):
            return "WebSocket 未连接：\(message)"
        case let .registrationFailed(message):
            return "设备注册失败：\(message)"
        case let .transportClosed(message):
            return "连接已关闭：\(message)"
        }
    }
}

public struct RealtimeAgentEvent: @unchecked Sendable {
    public var eventName: String
    public var userID: String
    public var producerID: String
    public var payload: [String: Any]
    public var version: String
    public var eventID: String
    public var timestampMS: Int64
    public var sessionID: String?
    public var streamID: String?
    public var streamType: String?

    public init(
        eventName: String,
        userID: String,
        producerID: String,
        payload: [String: Any] = [:],
        sessionID: String? = nil,
        streamID: String? = nil,
        streamType: String? = nil,
        version: String = audioChatProtocolVersion,
        eventID: String = RealtimeAgentIDs.make(prefix: "evt"),
        timestampMS: Int64 = RealtimeAgentIDs.nowMS()
    ) {
        self.eventName = eventName
        self.userID = userID
        self.producerID = producerID
        self.payload = payload
        self.sessionID = sessionID
        self.streamID = streamID
        self.streamType = streamType
        self.version = version
        self.eventID = eventID
        self.timestampMS = timestampMS
    }

    public init(dictionary: [String: Any]) throws {
        guard let eventName = dictionary["event_name"] as? String,
              let userID = dictionary["user_id"] as? String,
              let producerID = dictionary["producer_id"] as? String else {
            throw RealtimeAgentDeviceError.invalidEvent("missing event_name/user_id/producer_id")
        }
        self.eventName = eventName
        self.userID = userID
        self.producerID = producerID
        self.payload = dictionary["payload"] as? [String: Any] ?? [:]
        self.version = dictionary["version"] as? String ?? audioChatProtocolVersion
        self.eventID = dictionary["event_id"] as? String ?? RealtimeAgentIDs.make(prefix: "evt")
        self.timestampMS = Int64(dictionary["timestamp_ms"] as? Int ?? Int(RealtimeAgentIDs.nowMS()))
        self.sessionID = dictionary["session_id"] as? String
        self.streamID = dictionary["stream_id"] as? String
        self.streamType = dictionary["stream_type"] as? String
    }

    public init(jsonData: Data) throws {
        guard let dictionary = try JSONSerialization.jsonObject(with: jsonData) as? [String: Any] else {
            throw RealtimeAgentDeviceError.invalidEvent("json is not object")
        }
        try self.init(dictionary: dictionary)
    }

    public init(jsonString: String) throws {
        guard let data = jsonString.data(using: .utf8) else {
            throw RealtimeAgentDeviceError.invalidEvent("json string is not utf8")
        }
        try self.init(jsonData: data)
    }

    public var dictionary: [String: Any] {
        var data: [String: Any] = [
            "version": version,
            "event_id": eventID,
            "event_name": eventName,
            "timestamp_ms": timestampMS,
            "user_id": userID,
            "producer_id": producerID,
            "payload": payload,
        ]
        if let sessionID { data["session_id"] = sessionID }
        if let streamID { data["stream_id"] = streamID }
        if let streamType { data["stream_type"] = streamType }
        return data
    }

    public var jsonData: Data {
        get throws {
            try JSONSerialization.data(withJSONObject: dictionary, options: [])
        }
    }

    public var jsonString: String {
        get throws {
            guard let text = String(data: try jsonData, encoding: .utf8) else {
                throw RealtimeAgentDeviceError.invalidEvent("json data is not utf8")
            }
            return text
        }
    }
}
