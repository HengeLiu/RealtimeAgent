import Foundation

/// 端侧命令回执 helper。
///
/// 主要功能：封装 `command.accepted/progress/completed/failed`，避免 App 手写控制事件。
public struct RealtimeAgentCommandResponder: @unchecked Sendable {
    public let request: RealtimeAgentEvent
    public let commandID: String
    public let command: String

    private let send: @Sendable (String, [String: Any]) async throws -> Void

    init(request: RealtimeAgentEvent, send: @escaping @Sendable (String, [String: Any]) async throws -> Void) {
        self.request = request
        self.commandID = request.payload["command_id"] as? String ?? request.eventID
        self.command = request.payload["command"] as? String ?? ""
        self.send = send
    }

    /// 回报命令已接受。
    public func accepted(_ payload: [String: Any] = [:]) async throws {
        try await send("command.accepted", responsePayload(payload))
    }

    /// 回报命令进度。
    public func progress(_ payload: [String: Any] = [:]) async throws {
        try await send("command.progress", responsePayload(payload))
    }

    /// 回报命令完成。
    public func completed(_ payload: [String: Any] = [:]) async throws {
        try await send("command.completed", responsePayload(payload))
    }

    /// 回报命令失败。
    public func failed(code: String, message: String, retryable: Bool = false) async throws {
        try await send(
            "command.failed",
            responsePayload([
                "error": [
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                ],
            ])
        )
    }

    private func responsePayload(_ payload: [String: Any]) -> [String: Any] {
        var data: [String: Any] = ["command_id": commandID]
        if !command.isEmpty {
            data["command"] = command
        }
        payload.forEach { data[$0.key] = $0.value }
        return data
    }
}
