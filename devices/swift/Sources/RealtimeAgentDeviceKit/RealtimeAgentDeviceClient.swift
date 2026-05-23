import Foundation

/// realtime-agent 端侧通讯客户端。
///
/// 主要功能：管理 control / stream WebSocket、设备注册、心跳、事件分发和 stream chunk 收发。
/// 主要方法：`connectAndRegister()` 建立连接并注册，`onCommand()` 和 `onStreamOpen()` 注册端侧回调。
public final class RealtimeAgentDeviceClient: @unchecked Sendable {
    public typealias CommandHandler = @Sendable (RealtimeAgentCommandResponder) async throws -> Void
    public typealias StreamOpenHandler = @Sendable (RealtimeAgentInputStreamRequest) async throws -> Void
    public typealias OutputStreamHandler = @Sendable (RealtimeAgentOutputStreamSession) async throws -> Void
    public typealias OutputChunkHandler = @Sendable (RealtimeAgentStreamChunk, RealtimeAgentOutputStreamSession) async throws -> Void
    public typealias EventHandler = @Sendable (RealtimeAgentEvent) async throws -> Void

    public let serverURL: URL
    public let device: RealtimeAgentDevice
    public let configuration: RealtimeAgentClientConfiguration

    private let transport: RealtimeAgentWebSocketTransport
    private var diagnostics = RealtimeAgentDiagnostics()
    private var heartbeatTask: Task<Void, Never>?
    private var controlReceiveTask: Task<Void, Never>?
    private var streamReceiveTask: Task<Void, Never>?
    private var commandHandlers: [String: CommandHandler] = [:]
    private var streamOpenHandlers: [String: StreamOpenHandler] = [:]
    private var outputStreamHandlers: [String: OutputStreamHandler] = [:]
    private var outputChunkHandlers: [String: OutputChunkHandler] = [:]
    private var eventHandlers: [String: EventHandler] = [:]
    private var outputSessions: [String: RealtimeAgentOutputStreamSession] = [:]
    private var startedOutputStreams = Set<String>()
    private var sequenceByStream: [String: Int] = [:]

    /// 创建默认 URLSession WebSocket 客户端。
    ///
    /// 参数：`serverURL` 为 HTTP server 地址，`device` 为设备声明，`configuration` 为 SDK 配置。
    public convenience init(
        serverURL: URL,
        device: RealtimeAgentDevice,
        configuration: RealtimeAgentClientConfiguration = .default
    ) {
        self.init(
            serverURL: serverURL,
            device: device,
            configuration: configuration,
            transport: URLSessionRealtimeAgentTransport()
        )
    }

    /// 创建可注入 transport 的客户端。
    ///
    /// 主要用途：单元测试可注入 mock transport，生产环境使用默认 URLSession transport。
    init(
        serverURL: URL,
        device: RealtimeAgentDevice,
        configuration: RealtimeAgentClientConfiguration = .default,
        transport: RealtimeAgentWebSocketTransport
    ) {
        self.serverURL = serverURL
        self.device = device
        self.configuration = configuration
        self.transport = transport
    }

    public var userID: String {
        device.userID
    }

    public var deviceID: String {
        device.deviceID
    }

    /// 连接 control WebSocket。
    public func connect() async throws {
        try await transport.connectControl(url: try websocketURL(path: "/ws/control"))
        diagnostics.controlState = "connected"
    }

    /// 发送注册事件并等待注册结果。
    ///
    /// 参数：`shouldStartHeartbeat` 为 true 时注册成功后自动发送心跳。
    /// 返回值：server 返回的 `control.device.registered` 事件。
    public func register(startHeartbeat shouldStartHeartbeat: Bool = true) async throws -> RealtimeAgentEvent {
        if diagnostics.controlState != "connected" {
            try await connect()
        }
        try await sendEvent(name: "control.device.register.requested", payload: registrationPayload())
        while true {
            let event = try await receiveEvent()
            if event.eventName == "control.device.registered" {
                diagnostics.registered = true
                diagnostics.controlState = "registered"
                if shouldStartHeartbeat {
                    let interval = event.payload["heartbeat_interval_seconds"] as? Double
                        ?? Double(event.payload["heartbeat_interval_seconds"] as? Int ?? 10)
                    startHeartbeat(intervalSeconds: interval)
                }
                return event
            }
            if event.eventName == "control.device.register.failed" {
                let reason = String(describing: event.payload["reason"] ?? event.payload)
                diagnostics.lastError = reason
                throw RealtimeAgentDeviceError.registrationFailed(reason)
            }
        }
    }

    /// 连接、注册并启动 control / stream 接收循环。
    public func connectAndRegister(startHeartbeat shouldStartHeartbeat: Bool = true) async throws {
        _ = try await register(startHeartbeat: shouldStartHeartbeat)
        startControlReceiveLoop()
        try await ensureStream()
        startStreamReceiveLoop()
    }

    /// 关闭 WebSocket、心跳和接收任务。
    public func close() async {
        heartbeatTask?.cancel()
        controlReceiveTask?.cancel()
        streamReceiveTask?.cancel()
        heartbeatTask = nil
        controlReceiveTask = nil
        streamReceiveTask = nil
        await transport.close()
        diagnostics.controlState = "closed"
        diagnostics.streamState = "closed"
    }

    /// 注册端侧命令处理器。
    public func onCommand(_ command: String, handler: @escaping CommandHandler) {
        commandHandlers[command] = handler
    }

    /// 注册所有 command 的兜底处理器。
    ///
    /// 主要功能：端侧参考应用可统一处理由 Task 下发的 `command.requested`，再自行根据 payload 分派。
    public func onAnyCommand(handler: @escaping CommandHandler) {
        commandHandlers["*"] = handler
    }

    /// 注册指定控制事件处理器。
    ///
    /// 主要功能：让 App 处理 SDK 暂未内置语义的协议事件，例如 audio session 生命周期。
    public func onEvent(_ eventName: String, handler: @escaping EventHandler) {
        eventHandlers[eventName] = handler
    }

    /// 注册输入 stream 打开请求处理器。
    public func onStreamOpen(_ streamType: String, handler: @escaping StreamOpenHandler) {
        streamOpenHandlers[streamType] = handler
    }

    /// 注册输出 stream 处理器。
    public func onOutputStream(_ streamType: String, handler: @escaping OutputStreamHandler) {
        outputStreamHandlers[streamType] = handler
    }

    /// 注册输出 chunk 处理器。
    ///
    /// 主要功能：让 App 消费 `actuator.speaker` 等下行 stream 的真实 payload。
    public func onOutputChunk(_ streamType: String, handler: @escaping OutputChunkHandler) {
        outputChunkHandlers[streamType] = handler
    }

    /// 发送控制事件。
    public func sendEvent(_ event: RealtimeAgentEvent) async throws {
        try await transport.sendControl(text: try event.jsonString)
        diagnostics.sentEvents += 1
        diagnostics.lastEventName = event.eventName
    }

    /// 按事件名构造并发送控制事件。
    public func sendEvent(
        name: String,
        payload: [String: Any] = [:],
        sessionID: String? = nil,
        streamID: String? = nil,
        streamType: String? = nil
    ) async throws {
        try await sendEvent(
            RealtimeAgentEvent(
                eventName: name,
                userID: userID,
                producerID: deviceID,
                payload: payload,
                sessionID: sessionID,
                streamID: streamID,
                streamType: streamType,
                version: configuration.protocolVersion
            )
        )
    }

    /// 从 control WebSocket 读取一个控制事件。
    public func receiveEvent() async throws -> RealtimeAgentEvent {
        let text = try await transport.receiveControl()
        let event = try RealtimeAgentEvent(jsonString: text)
        diagnostics.receivedEvents += 1
        diagnostics.lastEventName = event.eventName
        return event
    }

    /// 分发一个控制事件。
    ///
    /// 返回值：命中 SDK 或 App 注册的处理器时返回 true，否则返回 false。
    @discardableResult
    public func dispatchEvent(_ event: RealtimeAgentEvent) async throws -> Bool {
        if let handler = eventHandlers[event.eventName] {
            try await handler(event)
            return true
        }
        switch event.eventName {
        case "command.requested":
            return try await dispatchCommand(event)
        case "stream.control.open.requested":
            return try await dispatchStreamOpen(event)
        case "stream.output.open.requested":
            _ = outputSession(for: event)
            return true
        case "stream.output.close.requested", "stream.output.finish.requested":
            let session = outputSession(for: event)
            try await session.finished()
            try await session.closed(reason: event.eventName == "stream.output.finish.requested" ? "finished" : "closed")
            return true
        case "stream.output.cancel.requested":
            let session = outputSession(for: event)
            try await session.cancelled(reason: "cancel_requested")
            return true
        default:
            diagnostics.unhandledEvents += 1
            return false
        }
    }

    /// 确保 stream WebSocket 已连接。
    public func ensureStream() async throws {
        if diagnostics.streamState == "connected" {
            return
        }
        try await transport.connectStream(
            url: try websocketURL(
                path: "/ws/stream",
                query: [URLQueryItem(name: "device_id", value: deviceID)]
            )
        )
        diagnostics.streamState = "connected"
    }

    /// 发送一帧 stream chunk。
    public func sendStreamChunk(_ chunk: RealtimeAgentStreamChunk) async throws {
        try await ensureStream()
        try await transport.sendStream(data: try RealtimeAgentStreamChunkCodec.encode(chunk))
        diagnostics.sentStreamChunks += 1
    }

    /// 从 stream WebSocket 读取一帧 chunk。
    public func receiveStreamChunk() async throws -> RealtimeAgentStreamChunk {
        try await ensureStream()
        let chunk = try RealtimeAgentStreamChunkCodec.decode(try await transport.receiveStream())
        if chunk.streamType.starts(with: "actuator.") {
            diagnostics.receivedOutputChunks += 1
        }
        return chunk
    }

    /// 分发一帧 stream chunk。
    ///
    /// 返回值：chunk 属于输出 stream 并被 SDK 处理时返回 true，否则返回 false。
    @discardableResult
    public func dispatchStreamChunk(_ chunk: RealtimeAgentStreamChunk) async throws -> Bool {
        guard chunk.streamType.starts(with: "actuator.") else {
            return false
        }
        try await handleOutputChunk(chunk)
        return true
    }

    /// 返回当前诊断快照。
    public func diagnosticsSnapshot() -> RealtimeAgentDiagnostics {
        diagnostics
    }

    func nextSeq(streamID: String) -> Int {
        let current = sequenceByStream[streamID] ?? 0
        sequenceByStream[streamID] = current + 1
        return current
    }

    private func registrationPayload() -> [String: Any] {
        let allowed = ["device_id", "name", "device_name", "client_type", "sdk_version", "auth", "supports", "properties", "runtime"]
        return device.registrationPayload.filter { allowed.contains($0.key) }
    }

    private func dispatchCommand(_ event: RealtimeAgentEvent) async throws -> Bool {
        let command = event.payload["command"] as? String ?? ""
        guard let handler = commandHandlers[command] ?? commandHandlers["*"] else {
            diagnostics.unhandledEvents += 1
            if configuration.autoFailUnhandledCommands {
                let responder = commandResponder(for: event)
                try await responder.failed(code: "command.unhandled", message: "no handler registered for command: \(command)")
                return true
            }
            return false
        }
        try await handler(commandResponder(for: event))
        return true
    }

    private func dispatchStreamOpen(_ event: RealtimeAgentEvent) async throws -> Bool {
        let streamType = event.streamType ?? event.payload["stream_type"] as? String ?? ""
        guard let handler = streamOpenHandlers[streamType] else {
            diagnostics.unhandledEvents += 1
            return false
        }
        try await handler(inputStreamRequest(for: event))
        return true
    }

    private func commandResponder(for event: RealtimeAgentEvent) -> RealtimeAgentCommandResponder {
        RealtimeAgentCommandResponder(request: event) { [weak self] name, payload in
            guard let self else {
                throw RealtimeAgentDeviceError.transportClosed("client released")
            }
            try await self.sendEvent(
                name: name,
                payload: payload,
                sessionID: event.sessionID,
                streamID: event.streamID,
                streamType: event.streamType
            )
        }
    }

    private func inputStreamRequest(for event: RealtimeAgentEvent) -> RealtimeAgentInputStreamRequest {
        RealtimeAgentInputStreamRequest(
            request: event,
            userID: userID,
            deviceID: deviceID,
            sendEvent: { [weak self] name, payload, sessionID, streamID, streamType in
                guard let self else {
                    throw RealtimeAgentDeviceError.transportClosed("client released")
                }
                try await self.sendEvent(name: name, payload: payload, sessionID: sessionID, streamID: streamID, streamType: streamType)
            },
            sendChunk: { [weak self] chunk in
                guard let self else {
                    throw RealtimeAgentDeviceError.transportClosed("client released")
                }
                try await self.sendStreamChunk(chunk)
            },
            nextSeq: { [weak self] streamID in
                guard let self else { return 0 }
                return self.nextSeq(streamID: streamID)
            }
        )
    }

    private func outputSession(for event: RealtimeAgentEvent) -> RealtimeAgentOutputStreamSession {
        let streamID = event.streamID ?? event.payload["stream_id"] as? String ?? RealtimeAgentIDs.make(prefix: "stream_out")
        if let session = outputSessions[streamID] {
            return session
        }
        let streamType = event.streamType ?? event.payload["stream_type"] as? String ?? ""
        let session = RealtimeAgentOutputStreamSession(
            streamID: streamID,
            streamType: streamType,
            sessionID: event.sessionID,
            sendEvent: { [weak self] name, payload, sessionID, streamID, streamType in
                guard let self else {
                    throw RealtimeAgentDeviceError.transportClosed("client released")
                }
                try await self.sendEvent(name: name, payload: payload, sessionID: sessionID, streamID: streamID, streamType: streamType)
            }
        )
        outputSessions[streamID] = session
        return session
    }

    private func handleOutputChunk(_ chunk: RealtimeAgentStreamChunk) async throws {
        let event = RealtimeAgentEvent(
            eventName: "stream.output.open.requested",
            userID: chunk.userID,
            producerID: "server",
            payload: ["stream_type": chunk.streamType],
            sessionID: chunk.sessionID,
            streamID: chunk.streamID,
            streamType: chunk.streamType
        )
        let session = outputSession(for: event)
        if !startedOutputStreams.contains(chunk.streamID) {
            startedOutputStreams.insert(chunk.streamID)
            try await session.started()
            if let handler = outputStreamHandlers[chunk.streamType] {
                try await handler(session)
            }
        }
        if let handler = outputChunkHandlers[chunk.streamType] {
            try await handler(chunk, session)
        }
        try await session.append(chunk)
    }

    private func startHeartbeat(intervalSeconds: Double) {
        heartbeatTask?.cancel()
        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                let nanoseconds = UInt64(max(intervalSeconds, 1) * 1_000_000_000)
                try? await Task.sleep(nanoseconds: nanoseconds)
                guard !Task.isCancelled, let self else { return }
                try? await self.sendEvent(
                    name: "control.device.heartbeat.received",
                    payload: ["connection_state": "online", "client_type": self.device.registrationPayload["client_type"] as? String ?? "ios"]
                )
            }
        }
    }

    private func startControlReceiveLoop() {
        controlReceiveTask?.cancel()
        controlReceiveTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let event = try await self.receiveEvent()
                    _ = try await self.dispatchEvent(event)
                } catch {
                    self.recordControlError(error)
                    return
                }
            }
        }
    }

    private func startStreamReceiveLoop() {
        streamReceiveTask?.cancel()
        streamReceiveTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    let chunk = try await self.receiveStreamChunk()
                    _ = try await self.dispatchStreamChunk(chunk)
                } catch {
                    self.recordStreamError(error)
                    return
                }
            }
        }
    }

    private func recordControlError(_ error: Error) {
        diagnostics.controlState = "error"
        diagnostics.lastError = error.localizedDescription
    }

    private func recordStreamError(_ error: Error) {
        diagnostics.streamState = "error"
        diagnostics.lastError = error.localizedDescription
    }

    private func websocketURL(path: String, query: [URLQueryItem] = []) throws -> URL {
        guard var components = URLComponents(url: serverURL, resolvingAgainstBaseURL: false) else {
            throw RealtimeAgentDeviceError.invalidURL(serverURL.absoluteString)
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.path = path
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else {
            throw RealtimeAgentDeviceError.invalidURL("\(serverURL.absoluteString)\(path)")
        }
        return url
    }
}
