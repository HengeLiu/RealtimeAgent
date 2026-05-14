import Foundation

public let audioChatProtocolVersion = "audio-chat.v1"

public enum AudioChatIDs {
    public static func make(prefix: String) -> String {
        "\(prefix)_\(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12).lowercased())"
    }

    public static func nowMS() -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1000)
    }
}

public enum AudioChatDeviceError: Error, LocalizedError {
    case invalidEvent(String)
    case invalidStreamChunk(String)

    public var errorDescription: String? {
        switch self {
        case let .invalidEvent(message):
            return "事件格式错误：\(message)"
        case let .invalidStreamChunk(message):
            return "stream chunk 格式错误：\(message)"
        }
    }
}

public struct AudioChatEvent {
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
        eventID: String = AudioChatIDs.make(prefix: "evt"),
        timestampMS: Int64 = AudioChatIDs.nowMS()
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
            throw AudioChatDeviceError.invalidEvent("missing event_name/user_id/producer_id")
        }
        self.eventName = eventName
        self.userID = userID
        self.producerID = producerID
        self.payload = dictionary["payload"] as? [String: Any] ?? [:]
        self.version = dictionary["version"] as? String ?? audioChatProtocolVersion
        self.eventID = dictionary["event_id"] as? String ?? AudioChatIDs.make(prefix: "evt")
        self.timestampMS = Int64(dictionary["timestamp_ms"] as? Int ?? Int(AudioChatIDs.nowMS()))
        self.sessionID = dictionary["session_id"] as? String
        self.streamID = dictionary["stream_id"] as? String
        self.streamType = dictionary["stream_type"] as? String
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
}
