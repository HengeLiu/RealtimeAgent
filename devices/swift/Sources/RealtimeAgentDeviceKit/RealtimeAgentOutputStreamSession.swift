import Foundation

/// 输出 stream 会话 helper。
///
/// 主要功能：封装 speaker 输出的 started、closed、failed 和 cancelled 回执。
public struct RealtimeAgentOutputStreamSession: @unchecked Sendable {
    public let streamID: String
    public let streamType: String
    public let sessionID: String?

    private let sendEvent: @Sendable (String, [String: Any], String?, String?, String?) async throws -> Void
    private let appendChunkHandler: @Sendable (RealtimeAgentStreamChunk) async throws -> Void

    init(
        streamID: String,
        streamType: String,
        sessionID: String?,
        sendEvent: @escaping @Sendable (String, [String: Any], String?, String?, String?) async throws -> Void,
        appendChunkHandler: @escaping @Sendable (RealtimeAgentStreamChunk) async throws -> Void = { _ in }
    ) {
        self.streamID = streamID
        self.streamType = streamType
        self.sessionID = sessionID
        self.sendEvent = sendEvent
        self.appendChunkHandler = appendChunkHandler
    }

    /// 回报输出已开始。
    public func started() async throws {
        try await sendEvent("stream.output.started", ["stream_type": streamType], sessionID, streamID, streamType)
    }

    /// 追加一帧输出 chunk 给 App 自定义处理器。
    public func append(_ chunk: RealtimeAgentStreamChunk) async throws {
        try await appendChunkHandler(chunk)
    }

    /// 回报输出已关闭。
    public func closed(reason: String) async throws {
        try await sendEvent(
            "stream.output.closed",
            ["stream_type": streamType, "reason": reason],
            sessionID,
            streamID,
            streamType
        )
    }

    /// 回报输出失败。
    public func failed(code: String, message: String) async throws {
        try await sendEvent(
            "stream.output.failed",
            ["stream_type": streamType, "error": ["code": code, "message": message]],
            sessionID,
            streamID,
            streamType
        )
    }

    /// 回报输出已取消。
    public func cancelled(reason: String) async throws {
        try await sendEvent(
            "stream.output.cancelled",
            ["stream_type": streamType, "reason": reason],
            sessionID,
            streamID,
            streamType
        )
    }
}
