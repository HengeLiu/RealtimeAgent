import Foundation

/// 输入 stream 请求 helper。
///
/// 主要功能：封装 `stream.control.open.requested` 之后的 opened、chunk、closed、failed 生命周期。
public struct RealtimeAgentInputStreamRequest: @unchecked Sendable {
    public let request: RealtimeAgentEvent
    public let streamID: String
    public let streamType: String
    public let requestID: String?

    private let userID: String
    private let deviceID: String
    private let sendEvent: @Sendable (String, [String: Any], String?, String?, String?) async throws -> Void
    private let sendChunk: @Sendable (RealtimeAgentStreamChunk) async throws -> Void
    private let nextSeq: @Sendable (String) async -> Int

    init(
        request: RealtimeAgentEvent,
        userID: String,
        deviceID: String,
        sendEvent: @escaping @Sendable (String, [String: Any], String?, String?, String?) async throws -> Void,
        sendChunk: @escaping @Sendable (RealtimeAgentStreamChunk) async throws -> Void,
        nextSeq: @escaping @Sendable (String) async -> Int
    ) {
        self.request = request
        self.streamID = request.streamID ?? request.payload["stream_id"] as? String ?? RealtimeAgentIDs.make(prefix: "stream")
        self.streamType = request.streamType ?? request.payload["stream_type"] as? String ?? ""
        self.requestID = request.payload["request_id"] as? String
        self.userID = userID
        self.deviceID = deviceID
        self.sendEvent = sendEvent
        self.sendChunk = sendChunk
        self.nextSeq = nextSeq
    }

    /// 回报输入 stream 已打开。
    public func opened(_ payload: [String: Any] = [:]) async throws {
        var data = payload
        data["stream_type"] = streamType
        try await sendEvent("stream.input.opened", data, request.sessionID ?? deviceID, streamID, streamType)
    }

    /// 写入一帧输入 stream chunk。
    public func write(
        _ payload: Data,
        codec: String,
        sampleRate: Int,
        channels: Int,
        durationMS: Int = 0,
        final: Bool = false,
        metadata: [String: Any] = [:]
    ) async throws {
        let seq = await nextSeq(streamID)
        let chunk = RealtimeAgentStreamChunk(
            userID: userID,
            sessionID: request.sessionID ?? deviceID,
            streamID: streamID,
            streamType: streamType,
            seq: seq,
            payload: payload,
            codec: codec,
            sampleRate: sampleRate,
            channels: channels,
            durationMS: durationMS,
            final: final,
            metadata: metadata
        )
        try await sendChunk(chunk)
    }

    /// 回报输入 stream 已关闭。
    public func closed(reason: String = "completed") async throws {
        try await sendEvent(
            "stream.input.closed",
            ["stream_type": streamType, "reason": reason],
            request.sessionID ?? deviceID,
            streamID,
            streamType
        )
    }

    /// 回报输入 stream 失败。
    public func failed(code: String, message: String) async throws {
        try await sendEvent(
            "stream.input.failed",
            ["stream_type": streamType, "error": ["code": code, "message": message]],
            request.sessionID ?? deviceID,
            streamID,
            streamType
        )
    }
}
