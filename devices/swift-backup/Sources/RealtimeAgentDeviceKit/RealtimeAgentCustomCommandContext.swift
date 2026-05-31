import Foundation

/// 自定义业务命令上下文。
///
/// 主要功能：让 App handler 读取 `custom.command.requested` 的业务 payload，并通过
/// `emit(...)` 发送 `custom.*` 业务结果。它不暴露标准 command 生命周期。
public struct RealtimeAgentCustomCommandContext: @unchecked Sendable {
    public let event: RealtimeAgentEvent
    public let command: String
    public let payload: [String: Any]

    private let emitHandler: @Sendable (String, [String: Any]) async throws -> Void

    init(
        event: RealtimeAgentEvent,
        emitHandler: @escaping @Sendable (String, [String: Any]) async throws -> Void
    ) {
        self.event = event
        self.command = event.payload["command"] as? String ?? ""
        if let nestedPayload = event.payload["payload"] as? [String: Any] {
            self.payload = nestedPayload
        } else {
            self.payload = event.payload
        }
        self.emitHandler = emitHandler
    }

    /// 发送自定义业务事件。
    ///
    /// 参数：`eventName` 必须以 `custom.` 开头，`payload` 为业务自定义结果。
    public func emit(_ eventName: String, _ payload: [String: Any] = [:]) async throws {
        guard eventName.starts(with: "custom.") else {
            throw RealtimeAgentDeviceError.invalidEvent("custom command context can only emit custom.* events")
        }
        try await emitHandler(eventName, payload)
    }
}
