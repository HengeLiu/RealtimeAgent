import Foundation

/// realtime-agent 端侧通讯客户端。
///
/// 主要功能：管理 control / stream WebSocket、设备注册、心跳、标准事件状态机、`custom.*`
/// 事件分发和 stream chunk 收发。
/// 主要方法：`connectAndRegister()` 建立连接并注册，`onCustomCommand()` 和 `onEvent("custom.*")`
/// 注册 App 业务回调。
public final class RealtimeAgentDeviceClient: @unchecked Sendable {
    public typealias CustomCommandHandler = @Sendable (RealtimeAgentCustomCommandContext) async throws -> Void
    public typealias StreamOpenHandler = @Sendable (RealtimeAgentInputStreamRequest) async throws -> Void
    public typealias EventHandler = @Sendable (RealtimeAgentEvent) async throws -> Void

    public let serverURL: URL
    public let device: RealtimeAgentDevice
    public let configuration: RealtimeAgentClientConfiguration

    private let transport: RealtimeAgentWebSocketTransport
    private var diagnostics = RealtimeAgentDiagnostics()
    private var heartbeatTask: Task<Void, Never>?
    private var controlReceiveTask: Task<Void, Never>?
    private var streamReceiveTask: Task<Void, Never>?
    private var customCommandHandlers: [String: CustomCommandHandler] = [:]
    private var streamOpenHandlers: [String: StreamOpenHandler] = [:]
    private var eventHandlers: [String: EventHandler] = [:]
    private var outputSessions: [String: RealtimeAgentOutputStreamSession] = [:]
    private var speakerBuffers: [String: SpeakerPlaybackBuffer] = [:]
    private var speakerDrainTasks: [String: Task<Void, Never>] = [:]
    private var startedOutputStreams = Set<String>()
    private var sequenceByStream: [String: Int] = [:]
    private var audioInput: AudioInput = .disabled()
    private var camera: Camera = .disabled()
    private var speaker: Speaker = .disabled()
    private var microphoneTask: Task<Void, Never>?

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

    /// 使用标准语法糖创建设备客户端。
    ///
    /// 主要功能：App 只声明设备标识和显式启用的硬件能力，SDK 自动生成注册 profile。
    public convenience init(
        serverURL: String,
        deviceID: String,
        userID: String,
        name: String,
        clientType: String = "ios",
        audioInput: AudioInput = .disabled(),
        camera: Camera = .disabled(),
        speaker: Speaker = .disabled(),
        auth: [String: Any]? = nil,
        properties: [String: Any] = [:],
        configuration: RealtimeAgentClientConfiguration = .default
    ) throws {
        guard let url = URL(string: serverURL) else {
            throw RealtimeAgentDeviceError.invalidURL(serverURL)
        }
        var device = RealtimeAgentDevice(deviceID: deviceID)
            .user(userID)
            .named(name)
            .clientType(clientType)
            .sdkVersion("realtime-agent-swift-device-sdk-0.2.0")
            .properties(properties)
            .applying(audioInput: audioInput, camera: camera, speaker: speaker)
        if let auth {
            device = device.auth(auth)
        }
        self.init(serverURL: url, device: device, configuration: configuration)
        self.audioInput = audioInput
        self.camera = camera
        self.speaker = speaker
        installEnabledHardwareAdapters()
    }

    /// 创建可注入 transport 的客户端。
    ///
    /// 主要用途：单元测试可注入 mock transport，生产环境使用默认 URLSession transport。
    init(
        serverURL: URL,
        device: RealtimeAgentDevice,
        configuration: RealtimeAgentClientConfiguration = .default,
        transport: RealtimeAgentWebSocketTransport,
        audioInput: AudioInput = .disabled(),
        camera: Camera = .disabled(),
        speaker: Speaker = .disabled()
    ) {
        self.serverURL = serverURL
        self.device = device
        self.configuration = configuration
        self.transport = transport
        self.audioInput = audioInput
        self.camera = camera
        self.speaker = speaker
        installEnabledHardwareAdapters()
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
        microphoneTask?.cancel()
        speakerDrainTasks.values.forEach { $0.cancel() }
        heartbeatTask = nil
        controlReceiveTask = nil
        streamReceiveTask = nil
        microphoneTask = nil
        speakerDrainTasks = [:]
        await transport.close()
        diagnostics.controlState = "closed"
        diagnostics.streamState = "closed"
    }

    /// 注册自定义业务命令处理器。
    ///
    /// 说明：只处理 `custom.command.requested` 中的业务 `payload.command`，不复用标准 command 生命周期。
    public func onCustomCommand(_ command: String, handler: @escaping CustomCommandHandler) {
        customCommandHandlers[command] = handler
    }

    /// 注册指定控制事件处理器。
    ///
    /// 主要功能：让 App 处理 SDK 暂未内置语义的协议事件，例如 audio session 生命周期。
    public func onEvent(_ eventName: String, handler: @escaping EventHandler) {
        precondition(eventName.starts(with: "custom."), "onEvent only accepts custom.* events")
        eventHandlers[eventName] = handler
    }

    /// 注册输入 stream 打开请求处理器。
    public func onStreamOpen(_ streamType: String, handler: @escaping StreamOpenHandler) {
        streamOpenHandlers[streamType] = handler
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
        if event.eventName.starts(with: "custom.") {
            return try await dispatchCustomEvent(event)
        }
        switch event.eventName {
        case "control.audio_session.open.requested":
            return try await handleAudioSessionOpen(event)
        case "control.audio_session.close.requested":
            return try await handleAudioSessionClose(event)
        case "command.requested":
            return try await dispatchCommand(event)
        case "stream.control.open.requested":
            return try await dispatchStreamOpen(event)
        case "stream.control.close.requested":
            return try await dispatchStreamClose(event)
        case "stream.output.open.requested":
            guard outputStreamType(for: event) == "actuator.speaker", speaker.enabled else {
                diagnostics.unhandledEvents += 1
                return false
            }
            try await prepareSpeakerSession(for: event)
            return true
        case "stream.output.close.requested", "stream.output.finish.requested":
            let session = outputSession(for: event)
            try await drainSpeakerIfNeeded(streamID: session.streamID)
            try await session.closed(reason: event.eventName == "stream.output.finish.requested" ? "finished" : "closed")
            return true
        case "stream.output.cancel.requested":
            let session = outputSession(for: event)
            speakerDrainTasks[session.streamID]?.cancel()
            speakerDrainTasks.removeValue(forKey: session.streamID)
            await speakerBuffers[session.streamID]?.cancel()
            speakerBuffers.removeValue(forKey: session.streamID)
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
        guard chunk.streamType == "actuator.speaker", speaker.enabled else {
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
        var payload = device.registrationPayload.filter { allowed.contains($0.key) }
        var properties = payload["properties"] as? [String: Any] ?? [:]
        if !customCommandHandlers.isEmpty {
            properties["realtime_agent.custom_command_consumer"] = true
            properties["realtime_agent.custom_commands"] = Array(customCommandHandlers.keys).sorted()
        }
        if !eventHandlers.isEmpty {
            properties["realtime_agent.custom_event_subscriptions"] = Array(eventHandlers.keys).sorted()
        }
        payload["properties"] = properties
        return payload
    }

    private func dispatchCustomEvent(_ event: RealtimeAgentEvent) async throws -> Bool {
        if event.eventName == "custom.command.requested" {
            let command = event.payload["command"] as? String ?? ""
            guard let handler = customCommandHandlers[command] else {
                diagnostics.unhandledEvents += 1
                return false
            }
            let context = customCommandContext(for: event)
            try await handler(context)
            return true
        }
        if let handler = eventHandlers[event.eventName] {
            try await handler(event)
            return true
        }
        diagnostics.unhandledEvents += 1
        return false
    }

    private func dispatchCommand(_ event: RealtimeAgentEvent) async throws -> Bool {
        let command = event.payload["command"] as? String ?? ""
        diagnostics.unhandledEvents += 1
        if configuration.autoFailUnhandledCommands {
            let responder = commandResponder(for: event)
            try await responder.failed(code: "command.unhandled", message: "no handler registered for command: \(command)")
            return true
        }
        return false
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

    private func dispatchStreamClose(_ event: RealtimeAgentEvent) async throws -> Bool {
        let streamType = event.streamType ?? event.payload["stream_type"] as? String ?? ""
        guard streamType == "sensor.rgb" else {
            diagnostics.unhandledEvents += 1
            return false
        }
        try await sendEvent(
            name: "stream.input.closed",
            payload: ["stream_type": streamType, "reason": "server_requested"],
            sessionID: event.sessionID,
            streamID: event.streamID,
            streamType: streamType
        )
        return true
    }

    private func handleAudioSessionOpen(_ event: RealtimeAgentEvent) async throws -> Bool {
        guard audioInput.enabled else {
            diagnostics.unhandledEvents += 1
            return false
        }
        try await ensureStream()
        let streamID = event.streamID ?? event.payload["stream_id"] as? String ?? RealtimeAgentIDs.make(prefix: "stream_mic")
        try await sendEvent(
            name: "control.audio_session.opened",
            payload: [
                "stream_type": audioInput.configuration.streamType,
                "stream_id": streamID,
                "format": [
                    "codec": audioInput.configuration.codec,
                    "sample_rate": audioInput.configuration.sampleRate,
                    "channels": audioInput.configuration.channels,
                    "chunk_ms": audioInput.configuration.chunkMS,
                ],
            ],
            sessionID: event.sessionID ?? deviceID,
            streamID: streamID,
            streamType: audioInput.configuration.streamType
        )
        startMicrophoneSourceIfNeeded(sessionID: event.sessionID ?? deviceID, streamID: streamID)
        return true
    }

    private func handleAudioSessionClose(_ event: RealtimeAgentEvent) async throws -> Bool {
        microphoneTask?.cancel()
        microphoneTask = nil
        for streamID in speakerBuffers.keys {
            try await drainSpeakerIfNeeded(streamID: streamID)
        }
        try await sendEvent(
            name: "control.audio_session.closed",
            payload: ["reason": "device_audio_session_closed"],
            sessionID: event.sessionID ?? deviceID
        )
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

    private func customCommandContext(for event: RealtimeAgentEvent) -> RealtimeAgentCustomCommandContext {
        RealtimeAgentCustomCommandContext(event: event) { [weak self] name, payload in
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

    private func outputStreamType(for event: RealtimeAgentEvent) -> String {
        event.streamType ?? event.payload["stream_type"] as? String ?? ""
    }

    private func prepareSpeakerSession(for event: RealtimeAgentEvent) async throws {
        let session = outputSession(for: event)
        if speakerBuffers[session.streamID] == nil {
            let sink = speaker.sink ?? RealtimeAgentNoopSpeakerSink()
            try await sink.prepare(format: speakerFormat(for: event))
            speakerBuffers[session.streamID] = SpeakerPlaybackBuffer(configuration: speaker.buffer, sink: sink)
        }
    }

    private func speakerFormat(for event: RealtimeAgentEvent) -> RealtimeAgentSpeakerFormat {
        let format = event.payload["format"] as? [String: Any] ?? [:]
        return RealtimeAgentSpeakerFormat(
            codec: format["codec"] as? String ?? "pcm16le",
            sampleRate: intValue(format["sample_rate"]) ?? 16_000,
            channels: intValue(format["channels"]) ?? 1
        )
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
        if speakerBuffers[chunk.streamID] == nil {
            try await prepareSpeakerSession(for: event)
        }
        try await session.append(chunk)
        let actions = try await speakerBuffers[chunk.streamID]?.append(chunk) ?? []
        try await applySpeakerActions(actions, session: session)
    }

    private func drainSpeakerIfNeeded(streamID: String) async throws {
        guard let buffer = speakerBuffers[streamID] else { return }
        let actions = try await buffer.drainAvailable()
        if let session = outputSessions[streamID] {
            try await applySpeakerActions(actions, session: session)
        }
        try await buffer.drainSink()
        speakerDrainTasks[streamID]?.cancel()
        speakerDrainTasks.removeValue(forKey: streamID)
        speakerBuffers.removeValue(forKey: streamID)
    }

    private func applySpeakerActions(_ actions: [SpeakerPlaybackAction], session: RealtimeAgentOutputStreamSession) async throws {
        for action in actions {
            switch action {
            case .started:
                if !startedOutputStreams.contains(session.streamID) {
                    startedOutputStreams.insert(session.streamID)
                    try await session.started()
                    startSpeakerDrainLoop(session: session)
                }
            case let .pause(bufferedMS, highWatermarkMS):
                try await sendEvent(
                    name: "downstream.pause.requested",
                    payload: [
                        "stream_id": session.streamID,
                        "stream_type": session.streamType,
                        "buffered_ms": bufferedMS,
                        "high_watermark_ms": highWatermarkMS,
                        "reason": "speaker_buffer_high",
                    ],
                    sessionID: session.sessionID,
                    streamID: session.streamID,
                    streamType: session.streamType
                )
            case let .resume(bufferedMS, lowWatermarkMS):
                try await sendEvent(
                    name: "downstream.resume.requested",
                    payload: [
                        "stream_id": session.streamID,
                        "stream_type": session.streamType,
                        "buffered_ms": bufferedMS,
                        "low_watermark_ms": lowWatermarkMS,
                        "reason": "speaker_buffer_low",
                    ],
                    sessionID: session.sessionID,
                    streamID: session.streamID,
                    streamType: session.streamType
                )
            case .overflow:
                diagnostics.lastMediaError = "speaker buffer overflow"
            }
        }
    }

    private func startSpeakerDrainLoop(session: RealtimeAgentOutputStreamSession) {
        if speakerDrainTasks[session.streamID] != nil {
            return
        }
        speakerDrainTasks[session.streamID] = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    if let buffer = self.speakerBuffers[session.streamID], !buffer.isEmpty {
                        let actions = try await buffer.drainNext()
                        try await self.applySpeakerActions(actions, session: session)
                    }
                    try await Task.sleep(nanoseconds: 20_000_000)
                } catch {
                    self.diagnostics.lastMediaError = error.localizedDescription
                    return
                }
            }
        }
    }

    private func installEnabledHardwareAdapters() {
        if camera.enabled, let source = camera.source {
            CameraFrameUploader.registerFrameHandler(
                client: self,
                source: source,
                options: CameraFrameUploadOptions(
                    codec: camera.format,
                    sampleRate: Int(camera.frequencyHz),
                    sleepBetweenContinuousFrames: true
                ),
                defaultSampleCount: camera.sampleCount
            )
        }
    }

    private func startMicrophoneSourceIfNeeded(sessionID: String, streamID: String) {
        guard let source = audioInput.source else { return }
        microphoneTask?.cancel()
        let configuration = audioInput.configuration
        microphoneTask = Task { [weak self] in
            guard let self else { return }
            var sequence = 0
            do {
                for try await payload in source.streamPCM16LE(configuration: configuration) {
                    if Task.isCancelled { return }
                    let chunk = RealtimeAgentStreamChunk(
                        userID: self.userID,
                        sessionID: sessionID,
                        streamID: streamID,
                        streamType: configuration.streamType,
                        seq: sequence,
                        payload: payload,
                        codec: configuration.codec,
                        sampleRate: configuration.sampleRate,
                        channels: configuration.channels,
                        durationMS: configuration.chunkMS
                    )
                    sequence += 1
                    try await self.sendStreamChunk(chunk)
                }
            } catch {
                self.diagnostics.lastMediaError = error.localizedDescription
            }
        }
    }

    private func intValue(_ value: Any?) -> Int? {
        if let value = value as? Int { return value }
        if let value = value as? Double { return Int(value) }
        if let value = value as? String { return Int(value) }
        return nil
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
