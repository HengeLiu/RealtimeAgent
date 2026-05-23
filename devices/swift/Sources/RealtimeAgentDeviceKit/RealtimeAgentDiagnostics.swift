import Foundation

/// SDK 日志级别。
///
/// 主要功能：表达 SDK 内部诊断日志的粗粒度级别。
public enum RealtimeAgentLogLevel: String, Sendable {
    case debug
    case info
    case warning
    case error
}

/// 断线重连策略。
///
/// 主要功能：描述控制连接或 stream 连接异常后的重试策略。
public enum RealtimeAgentReconnectPolicy: Sendable, Equatable {
    case disabled
    case exponentialBackoff(maxAttempts: Int)
}

/// 设备客户端配置。
///
/// 主要功能：集中管理协议版本、超时、重连、未处理命令策略和日志级别。
public struct RealtimeAgentClientConfiguration: Sendable, Equatable {
    public var protocolVersion: String
    public var connectTimeoutSeconds: TimeInterval
    public var heartbeatGraceSeconds: TimeInterval
    public var reconnectPolicy: RealtimeAgentReconnectPolicy
    public var autoFailUnhandledCommands: Bool
    public var logLevel: RealtimeAgentLogLevel

    /// 创建设备客户端配置。
    ///
    /// 参数：各字段分别控制协议版本、连接超时、心跳宽限、重连策略、未处理命令策略和日志级别。
    public init(
        protocolVersion: String = audioChatProtocolVersion,
        connectTimeoutSeconds: TimeInterval = 8,
        heartbeatGraceSeconds: TimeInterval = 3,
        reconnectPolicy: RealtimeAgentReconnectPolicy = .exponentialBackoff(maxAttempts: 5),
        autoFailUnhandledCommands: Bool = false,
        logLevel: RealtimeAgentLogLevel = .debug
    ) {
        self.protocolVersion = protocolVersion
        self.connectTimeoutSeconds = connectTimeoutSeconds
        self.heartbeatGraceSeconds = heartbeatGraceSeconds
        self.reconnectPolicy = reconnectPolicy
        self.autoFailUnhandledCommands = autoFailUnhandledCommands
        self.logLevel = logLevel
    }

    public static let `default` = RealtimeAgentClientConfiguration()
}

/// SDK 诊断快照。
///
/// 主要功能：记录连接、注册、事件、stream 和媒体相关的最新状态，便于 App 展示和排障。
public struct RealtimeAgentDiagnostics: Sendable, Equatable {
    public var controlState: String
    public var streamState: String
    public var registered: Bool
    public var lastError: String?
    public var lastEventName: String?
    public var sentEvents: Int
    public var receivedEvents: Int
    public var sentStreamChunks: Int
    public var receivedOutputChunks: Int
    public var lastMediaError: String?
    public var unhandledEvents: Int

    /// 创建诊断快照。
    ///
    /// 参数：所有参数都有默认值，通常由 `RealtimeAgentDeviceClient` 内部维护。
    public init(
        controlState: String = "disconnected",
        streamState: String = "disconnected",
        registered: Bool = false,
        lastError: String? = nil,
        lastEventName: String? = nil,
        sentEvents: Int = 0,
        receivedEvents: Int = 0,
        sentStreamChunks: Int = 0,
        receivedOutputChunks: Int = 0,
        lastMediaError: String? = nil,
        unhandledEvents: Int = 0
    ) {
        self.controlState = controlState
        self.streamState = streamState
        self.registered = registered
        self.lastError = lastError
        self.lastEventName = lastEventName
        self.sentEvents = sentEvents
        self.receivedEvents = receivedEvents
        self.sentStreamChunks = sentStreamChunks
        self.receivedOutputChunks = receivedOutputChunks
        self.lastMediaError = lastMediaError
        self.unhandledEvents = unhandledEvents
    }
}
