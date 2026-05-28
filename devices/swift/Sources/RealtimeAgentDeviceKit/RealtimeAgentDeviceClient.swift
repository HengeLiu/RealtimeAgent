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
    public typealias DebugLogHandler = @Sendable (String) async -> Void

    public let serverURL: URL
    public let device: RealtimeAgentDevice
    public let configuration: RealtimeAgentClientConfiguration

    private let transport: RealtimeAgentWebSocketTransport
    private let stateLock = NSRecursiveLock()
    private var diagnostics = RealtimeAgentDiagnostics()
    private var heartbeatTask: Task<Void, Never>?
    private var controlReceiveTask: Task<Void, Never>?
    private var streamReceiveTask: Task<Void, Never>?
    private var streamReceiveLoopRunning = false
    private var connectedStreamChannels = Set<RealtimeAgentStreamChannel>()
    private var customCommandHandlers: [String: CustomCommandHandler] = [:]
    private var streamOpenHandlers: [String: StreamOpenHandler] = [:]
    private var eventHandlers: [String: EventHandler] = [:]
    private var inputStreamTasks: [String: Task<Void, Never>] = [:]
    private var outputSessions: [String: RealtimeAgentOutputStreamSession] = [:]
    private var speakerBuffers: [String: SpeakerPlaybackBuffer] = [:]
    private var speakerPreparationTasks: [String: Task<SpeakerPlaybackBuffer, Error>] = [:]
    private var speakerDrainTasks: [String: Task<Void, Never>] = [:]
    private var speakerFinishTasks: [String: Task<Void, Never>] = [:]
    private var speakerReceivedChunkCounts: [String: Int] = [:]
    private var speakerDrainedChunkCounts: [String: Int] = [:]
    private var speakerAppendedLastSeq: [String: Int] = [:]
    private var readyOutputStreams = Set<String>()
    private var startedOutputStreams = Set<String>()
    private var completedOutputStreams = Set<String>()
    private var sequenceByStream: [String: Int] = [:]
    private var debugLogHandler: DebugLogHandler?
    private var audioInput: AudioInput = .disabled()
    private var camera: Camera = .disabled()
    private var speaker: Speaker = .disabled()
    private var microphoneTask: Task<Void, Never>?
    private static let speakerDrainIdleSleepNanoseconds: UInt64 = 5_000_000
    private static let speakerDrainYieldEveryChunks = 64
    private static let streamReceiveReconnectBaseSleepNanoseconds: UInt64 = 100_000_000
    private static let speakerFinishGraceNanoseconds: UInt64 = 300_000_000
    private static let speakerFinishExpectedChunkTimeoutNanoseconds: UInt64 = 2_000_000_000
    private static let speakerFinishExpectedChunkPollNanoseconds: UInt64 = 20_000_000

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
        if audioInput.enabled {
            try await ensureStream(channel: .audioInput)
        }
        if speaker.enabled {
            try await ensureStream(channel: .audioOutput)
            ensureStreamReceiveLoop()
        }
    }

    /// 关闭 WebSocket、心跳和接收任务。
    public func close() async {
        await debugLog("client close requested")
        heartbeatTask?.cancel()
        controlReceiveTask?.cancel()
        streamReceiveTask?.cancel()
        markStreamReceiveLoopStopped()
        microphoneTask?.cancel()
        let closeState = withStateLock {
            let inputTasks = Array(inputStreamTasks.values)
            let preparationTasks = Array(speakerPreparationTasks.values)
            let drainTasks = Array(speakerDrainTasks.values)
            let finishTasks = Array(speakerFinishTasks.values)
            let buffers = Array(speakerBuffers.values)
            inputStreamTasks = [:]
            speakerPreparationTasks = [:]
            speakerDrainTasks = [:]
            speakerFinishTasks = [:]
            speakerBuffers = [:]
            speakerReceivedChunkCounts = [:]
            speakerDrainedChunkCounts = [:]
            speakerAppendedLastSeq = [:]
            readyOutputStreams = []
            startedOutputStreams = []
            completedOutputStreams = []
            connectedStreamChannels = []
            return (inputTasks, preparationTasks, drainTasks, finishTasks, buffers)
        }
        closeState.0.forEach { $0.cancel() }
        closeState.1.forEach { $0.cancel() }
        closeState.2.forEach { $0.cancel() }
        closeState.3.forEach { $0.cancel() }
        for buffer in closeState.4 {
            await buffer.cancel()
        }
        heartbeatTask = nil
        controlReceiveTask = nil
        streamReceiveTask = nil
        microphoneTask = nil
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

    /// 注册 SDK 内部诊断日志处理器。
    ///
    /// 主要功能：把 speaker 播放缓冲、起播、暂停、恢复和错误等端侧状态交给 App 展示或落盘。
    public func onDebugLog(_ handler: @escaping DebugLogHandler) {
        debugLogHandler = handler
    }

    /// 发送控制事件。
    public func sendEvent(_ event: RealtimeAgentEvent) async throws {
        try await transport.sendControl(text: try event.jsonString)
        diagnostics.sentEvents += 1
        diagnostics.lastEventName = event.eventName
        await debugLog("control -> \(event.eventName) stream=\(event.streamID ?? "-") type=\(event.streamType ?? "-")")
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
        await debugLog("control <- \(event.eventName) stream=\(event.streamID ?? "-") type=\(event.streamType ?? "-")")
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
            let session = outputSession(for: event)
            do {
                try await prepareSpeakerSession(for: event)
                try await markSpeakerOutputReady(session: session)
            } catch {
                try? await failSpeakerOutput(
                    session: session,
                    code: "speaker.prepare_failed",
                    message: error.localizedDescription
                )
            }
            return true
        case "stream.output.close.requested", "stream.output.finish.requested":
            let session = outputSession(for: event)
            await debugLog("speaker close requested stream=\(session.streamID) event=\(event.eventName)")
            scheduleSpeakerOutputFinish(
                session: session,
                reason: event.eventName == "stream.output.finish.requested" ? "finished" : "closed",
                expectedLastSeq: outputExpectedLastSeq(for: event)
            )
            return true
        case "stream.output.cancel.requested":
            let session = outputSession(for: event)
            await debugLog("speaker cancel requested stream=\(session.streamID)")
            let cleanup = removeSpeakerPlaybackState(streamID: session.streamID, markCompleted: true)
            cleanup.finishTask?.cancel()
            cleanup.drainTask?.cancel()
            cleanup.preparationTask?.cancel()
            await cleanup.buffer?.cancel()
            try await session.cancelled(reason: "cancel_requested")
            return true
        default:
            diagnostics.unhandledEvents += 1
            return false
        }
    }

    /// 确保当前配置需要的 stream WebSocket 已连接。
    public func ensureStream() async throws {
        if audioInput.enabled {
            try await ensureStream(channel: .audioInput)
        }
        if speaker.enabled {
            try await ensureStream(channel: .audioOutput)
        }
    }

    /// 发送一帧 stream chunk。
    public func sendStreamChunk(_ chunk: RealtimeAgentStreamChunk) async throws {
        let channel = streamChannel(for: chunk.streamType)
        try await ensureStream(channel: channel)
        try await transport.sendStream(data: try RealtimeAgentStreamChunkCodec.encode(chunk), channel: channel)
        diagnostics.sentStreamChunks += 1
    }

    /// 从 stream WebSocket 读取一帧 chunk。
    public func receiveStreamChunk() async throws -> RealtimeAgentStreamChunk {
        try await ensureStream(channel: .audioOutput)
        let chunk = try RealtimeAgentStreamChunkCodec.decode(try await transport.receiveStream(channel: .audioOutput))
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
            if chunk.streamType.starts(with: "actuator.") {
                await debugLog("output chunk ignored stream_type=\(chunk.streamType) speaker_enabled=\(speaker.enabled)")
            }
            return false
        }
        guard !withStateLock({ completedOutputStreams.contains(chunk.streamID) }) else {
            await debugLog("speaker late chunk ignored stream=\(chunk.streamID) seq=\(chunk.seq)")
            return false
        }
        do {
            try await handleOutputChunk(chunk)
        } catch {
            let session = outputSession(for: outputOpenEvent(for: chunk))
            try? await failSpeakerOutput(
                session: session,
                code: "speaker.chunk_failed",
                message: error.localizedDescription
            )
            throw error
        }
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

    private func ensureStream(channel: RealtimeAgentStreamChannel) async throws {
        if connectedStreamChannels.contains(channel), diagnostics.streamState == "connected" {
            return
        }
        try await transport.connectStream(
            channel: channel,
            url: try websocketURL(
                path: streamPath(for: channel),
                query: [URLQueryItem(name: "device_id", value: deviceID)]
            )
        )
        connectedStreamChannels.insert(channel)
        diagnostics.streamState = "connected"
        await debugLog("stream connected channel=\(channel.debugName)")
    }

    private func streamPath(for channel: RealtimeAgentStreamChannel) -> String {
        switch channel {
        case .audioInput:
            return "/ws/stream/audio/input"
        case .audioOutput:
            return "/ws/stream/audio/output"
        case .visualInput:
            return "/ws/stream/visual/input"
        }
    }

    private func streamChannel(for streamType: String) -> RealtimeAgentStreamChannel {
        switch streamType {
        case "sensor.mic":
            return .audioInput
        case "sensor.rgb":
            return .visualInput
        case "actuator.speaker":
            return .audioOutput
        default:
            return streamType.starts(with: "actuator.") ? .audioOutput : .visualInput
        }
    }

    private func debugLog(_ message: String) async {
        await debugLogHandler?(message)
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
        let request = inputStreamRequest(for: event)
        await debugLog("input stream open requested stream=\(request.streamID) type=\(request.streamType) request_id=\(request.requestID ?? "-")")
        inputStreamTasks[request.streamID]?.cancel()
        inputStreamTasks[request.streamID] = Task { [weak self, request] in
            do {
                try await handler(request)
                await self?.debugLog("input stream handler completed stream=\(request.streamID) type=\(request.streamType)")
            } catch is CancellationError {
                try? await request.closed(reason: "server_requested")
            } catch {
                await self?.debugLog(
                    "input stream failed stream=\(request.streamID) type=\(request.streamType) error=\(error.localizedDescription)"
                )
                try? await request.failed(code: "input_stream.handler_failed", message: error.localizedDescription)
            }
            self?.inputStreamTasks.removeValue(forKey: request.streamID)
        }
        return true
    }

    private func dispatchStreamClose(_ event: RealtimeAgentEvent) async throws -> Bool {
        let streamType = event.streamType ?? event.payload["stream_type"] as? String ?? ""
        guard streamType == "sensor.rgb" else {
            diagnostics.unhandledEvents += 1
            return false
        }
        if let streamID = event.streamID ?? event.payload["stream_id"] as? String,
           let task = inputStreamTasks.removeValue(forKey: streamID) {
            task.cancel()
        }
        return true
    }

    private func handleAudioSessionOpen(_ event: RealtimeAgentEvent) async throws -> Bool {
        guard audioInput.enabled else {
            diagnostics.unhandledEvents += 1
            return false
        }
        try await ensureStream()
        if speaker.enabled {
            ensureStreamReceiveLoop()
        }
        let streamID = event.streamID ?? event.payload["stream_id"] as? String ?? RealtimeAgentIDs.make(prefix: "stream_mic")
        await debugLog("audio session open requested session=\(event.sessionID ?? deviceID) mic_stream=\(streamID)")
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
        for streamID in Array(speakerBuffers.keys) {
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
        if let session = withStateLock({ outputSessions[streamID] }) {
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
        return withStateLock {
            if let existing = outputSessions[streamID] {
                return existing
            }
            outputSessions[streamID] = session
            return session
        }
    }

    private func outputStreamType(for event: RealtimeAgentEvent) -> String {
        event.streamType ?? event.payload["stream_type"] as? String ?? ""
    }

    private func outputExpectedLastSeq(for event: RealtimeAgentEvent) -> Int? {
        if let lastSeq = intValue(event.payload["output_last_seq"]) {
            return lastSeq
        }
        if let lastSeq = intValue(event.payload["last_seq"]) {
            return lastSeq
        }
        if let chunkCount = intValue(event.payload["output_chunk_count"]), chunkCount > 0 {
            return chunkCount - 1
        }
        return nil
    }

    private func prepareSpeakerSession(for event: RealtimeAgentEvent) async throws {
        let session = outputSession(for: event)
        if withStateLock({ completedOutputStreams.contains(session.streamID) }) {
            return
        }
        if withStateLock({ speakerBuffers[session.streamID] != nil }) {
            return
        }
        if let task = withStateLock({ speakerPreparationTasks[session.streamID] }) {
            let buffer = try await task.value
            withStateLock {
                if speakerBuffers[session.streamID] == nil,
                   !completedOutputStreams.contains(session.streamID) {
                    speakerBuffers[session.streamID] = buffer
                }
            }
            return
        }
        let sink = speaker.sink ?? RealtimeAgentNoopSpeakerSink()
        let format = speakerFormat(for: event)
        let bufferConfiguration = speaker.buffer
        let task = Task<SpeakerPlaybackBuffer, Error> {
            try await sink.prepare(format: format)
            return SpeakerPlaybackBuffer(configuration: bufferConfiguration, sink: sink)
        }
        withStateLock {
            speakerPreparationTasks[session.streamID] = task
        }
        await debugLog(
            "speaker prepare stream=\(session.streamID) codec=\(format.codec) sample_rate=\(format.sampleRate) channels=\(format.channels) start_ms=\(bufferConfiguration.startWatermarkMS) low_ms=\(bufferConfiguration.lowWatermarkMS) high_ms=\(bufferConfiguration.highWatermarkMS) max_ms=\(bufferConfiguration.maxBufferMS)"
        )
        do {
            let buffer = try await task.value
            withStateLock {
                speakerPreparationTasks.removeValue(forKey: session.streamID)
                if speakerBuffers[session.streamID] == nil,
                   !completedOutputStreams.contains(session.streamID) {
                    speakerBuffers[session.streamID] = buffer
                }
            }
        } catch {
            _ = withStateLock {
                speakerPreparationTasks.removeValue(forKey: session.streamID)
            }
            throw error
        }
        await debugLog("speaker prepared stream=\(session.streamID)")
    }

    private func speakerFormat(for event: RealtimeAgentEvent) -> RealtimeAgentSpeakerFormat {
        let format = event.payload["format"] as? [String: Any] ?? [:]
        return RealtimeAgentSpeakerFormat(
            codec: format["codec"] as? String ?? "pcm16le",
            sampleRate: intValue(format["sample_rate"]) ?? 16_000,
            channels: intValue(format["channels"]) ?? 1
        )
    }

    private func markSpeakerOutputReady(session: RealtimeAgentOutputStreamSession) async throws {
        let shouldSendReady = withStateLock {
            if readyOutputStreams.contains(session.streamID) {
                return false
            }
            readyOutputStreams.insert(session.streamID)
            return true
        }
        if shouldSendReady {
            await debugLog("speaker ready stream=\(session.streamID)")
            try await session.ready()
        }
    }

    private func outputOpenEvent(for chunk: RealtimeAgentStreamChunk) -> RealtimeAgentEvent {
        RealtimeAgentEvent(
            eventName: "stream.output.open.requested",
            userID: chunk.userID,
            producerID: "server",
            payload: [
                "stream_type": chunk.streamType,
                "format": [
                    "codec": chunk.codec,
                    "sample_rate": chunk.sampleRate,
                    "channels": chunk.channels,
                ],
            ],
            sessionID: chunk.sessionID,
            streamID: chunk.streamID,
            streamType: chunk.streamType
        )
    }

    private func handleOutputChunk(_ chunk: RealtimeAgentStreamChunk) async throws {
        let event = outputOpenEvent(for: chunk)
        let session = outputSession(for: event)
        let receivedCount = withStateLock {
            let count = (speakerReceivedChunkCounts[chunk.streamID] ?? 0) + 1
            speakerReceivedChunkCounts[chunk.streamID] = count
            return count
        }
        if shouldLogSpeakerChunk(count: receivedCount) {
            await debugLog(
                "speaker chunk received stream=\(chunk.streamID) seq=\(chunk.seq) count=\(receivedCount) bytes=\(chunk.payload.count) duration_ms=\(chunk.durationMS)"
            )
        }
        if withStateLock({ speakerBuffers[chunk.streamID] == nil }) {
            try await prepareSpeakerSession(for: event)
        }
        try await markSpeakerOutputReady(session: session)
        try await session.append(chunk)
        let buffer = withStateLock { speakerBuffers[chunk.streamID] }
        let actions = try await buffer?.append(chunk) ?? []
        if let snapshot = await buffer?.snapshot(),
           shouldLogSpeakerBuffer(count: receivedCount, actions: actions) {
            await debugLog(
                "speaker buffer append stream=\(chunk.streamID) buffered_ms=\(snapshot.bufferedMS) queued=\(snapshot.queuedChunks) bytes=\(snapshot.bufferedBytes) started=\(snapshot.hasStarted) paused=\(snapshot.isPaused) actions=\(describeSpeakerActions(actions))"
            )
        }
        try await applySpeakerActions(actions, session: session)
        withStateLock {
            let currentLastSeq = speakerAppendedLastSeq[chunk.streamID] ?? -1
            speakerAppendedLastSeq[chunk.streamID] = max(currentLastSeq, chunk.seq)
        }
    }

    private func finishSpeakerOutput(streamID: String, expectedLastSeq: Int?) async throws {
        if let expectedLastSeq {
            await waitForExpectedSpeakerChunk(streamID: streamID, expectedLastSeq: expectedLastSeq)
        } else {
            try await Task.sleep(nanoseconds: Self.speakerFinishGraceNanoseconds)
        }
        if let task = withStateLock({ speakerPreparationTasks[streamID] }) {
            _ = try await task.value
        }
        _ = withStateLock {
            completedOutputStreams.insert(streamID)
        }
        try await drainSpeakerIfNeeded(streamID: streamID)
    }

    private func scheduleSpeakerOutputFinish(
        session: RealtimeAgentOutputStreamSession,
        reason: String,
        expectedLastSeq: Int?
    ) {
        withStateLock {
            speakerFinishTasks[session.streamID]?.cancel()
        }
        let task = Task { [weak self, session] in
            guard let self else { return }
            do {
                try await self.finishSpeakerOutput(
                    streamID: session.streamID,
                    expectedLastSeq: expectedLastSeq
                )
                guard !Task.isCancelled else { return }
                try await session.closed(reason: reason)
            } catch is CancellationError {
                await self.debugLog("speaker finish cancelled stream=\(session.streamID)")
            } catch {
                try? await self.failSpeakerOutput(
                    session: session,
                    code: "speaker.finish_failed",
                    message: error.localizedDescription
                )
            }
            _ = self.withStateLock {
                self.speakerFinishTasks.removeValue(forKey: session.streamID)
            }
        }
        withStateLock {
            speakerFinishTasks[session.streamID] = task
        }
    }

    private func failSpeakerOutput(
        session: RealtimeAgentOutputStreamSession,
        code: String,
        message: String
    ) async throws {
        diagnostics.lastMediaError = message
        await debugLog("speaker failed stream=\(session.streamID) code=\(code) message=\(message)")
        let cleanup = removeSpeakerPlaybackState(streamID: session.streamID, markCompleted: true)
        cleanup.finishTask?.cancel()
        cleanup.drainTask?.cancel()
        cleanup.preparationTask?.cancel()
        await cleanup.buffer?.cancel()
        try await session.failed(code: code, message: message)
    }

    private func waitForExpectedSpeakerChunk(streamID: String, expectedLastSeq: Int) async {
        let startedAt = DispatchTime.now().uptimeNanoseconds
        await debugLog(
            "speaker finish wait stream=\(streamID) expected_last_seq=\(expectedLastSeq) current_last_seq=\(withStateLock { speakerAppendedLastSeq[streamID] ?? -1 })"
        )
        while !Task.isCancelled {
            let currentLastSeq = withStateLock { speakerAppendedLastSeq[streamID] ?? -1 }
            if currentLastSeq >= expectedLastSeq {
                await debugLog(
                    "speaker finish wait completed stream=\(streamID) expected_last_seq=\(expectedLastSeq) current_last_seq=\(currentLastSeq)"
                )
                return
            }
            let elapsed = DispatchTime.now().uptimeNanoseconds - startedAt
            if elapsed >= Self.speakerFinishExpectedChunkTimeoutNanoseconds {
                await debugLog(
                    "speaker finish wait timeout stream=\(streamID) expected_last_seq=\(expectedLastSeq) current_last_seq=\(currentLastSeq)"
                )
                return
            }
            try? await Task.sleep(nanoseconds: Self.speakerFinishExpectedChunkPollNanoseconds)
        }
    }

    private func drainSpeakerIfNeeded(streamID: String) async throws {
        await debugLog("speaker drain requested stream=\(streamID)")
        let drainTask = withStateLock { speakerDrainTasks.removeValue(forKey: streamID) }
        drainTask?.cancel()
        if let drainTask {
            await drainTask.value
        }
        guard let buffer = withStateLock({ speakerBuffers[streamID] }) else { return }
        let before = await buffer.snapshot()
        await debugLog(
            "speaker drain available stream=\(streamID) buffered_ms=\(before.bufferedMS) queued=\(before.queuedChunks) bytes=\(before.bufferedBytes)"
        )
        let actions = try await buffer.drainAvailable()
        if let session = withStateLock({ outputSessions[streamID] }) {
            try await applySpeakerActions(actions, session: session)
        }
        try await buffer.drainSink()
        withStateLock {
            speakerBuffers.removeValue(forKey: streamID)
            speakerPreparationTasks.removeValue(forKey: streamID)
            speakerReceivedChunkCounts.removeValue(forKey: streamID)
            speakerDrainedChunkCounts.removeValue(forKey: streamID)
            speakerAppendedLastSeq.removeValue(forKey: streamID)
            readyOutputStreams.remove(streamID)
            startedOutputStreams.remove(streamID)
        }
        await debugLog("speaker drain completed stream=\(streamID)")
    }

    private func applySpeakerActions(_ actions: [SpeakerPlaybackAction], session: RealtimeAgentOutputStreamSession) async throws {
        for action in actions {
            switch action {
            case .started:
                let shouldStart = withStateLock {
                    if startedOutputStreams.contains(session.streamID) {
                        return false
                    }
                    startedOutputStreams.insert(session.streamID)
                    return true
                }
                if shouldStart {
                    await debugLog("speaker action started stream=\(session.streamID)")
                    try await session.started()
                    startSpeakerDrainLoop(session: session)
                }
            case let .pause(bufferedMS, highWatermarkMS):
                await debugLog(
                    "speaker action pause stream=\(session.streamID) buffered_ms=\(bufferedMS) high_ms=\(highWatermarkMS)"
                )
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
                await debugLog(
                    "speaker action resume stream=\(session.streamID) buffered_ms=\(bufferedMS) low_ms=\(lowWatermarkMS)"
                )
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
            case let .overflow(bufferedMS, overflowMS):
                diagnostics.lastMediaError = "speaker buffer overflow"
                await debugLog(
                    "speaker action overflow stream=\(session.streamID) buffered_ms=\(bufferedMS) overflow_ms=\(overflowMS)"
                )
            }
        }
    }

    private func shouldLogSpeakerChunk(count: Int) -> Bool {
        count <= 5 || count % 50 == 0
    }

    private func shouldLogSpeakerBuffer(count: Int, actions: [SpeakerPlaybackAction]) -> Bool {
        shouldLogSpeakerChunk(count: count) || !actions.isEmpty
    }

    private func describeSpeakerActions(_ actions: [SpeakerPlaybackAction]) -> String {
        if actions.isEmpty {
            return "none"
        }
        return actions.map { action in
            switch action {
            case let .started(bufferedMS):
                return "started(\(bufferedMS)ms)"
            case let .pause(bufferedMS, highWatermarkMS):
                return "pause(\(bufferedMS)/\(highWatermarkMS)ms)"
            case let .resume(bufferedMS, lowWatermarkMS):
                return "resume(\(bufferedMS)/\(lowWatermarkMS)ms)"
            case let .overflow(bufferedMS, overflowMS):
                return "overflow(\(bufferedMS)+\(overflowMS)ms)"
            }
        }.joined(separator: ",")
    }

    private func startSpeakerDrainLoop(session: RealtimeAgentOutputStreamSession) {
        let streamID = session.streamID
        let buffer = withStateLock { () -> SpeakerPlaybackBuffer? in
            if speakerDrainTasks[streamID] != nil {
                return nil
            }
            return speakerBuffers[streamID]
        }
        guard let buffer else {
            return
        }
        let task = Task { [weak self, buffer] in
            await self?.debugLog("speaker drain loop started stream=\(streamID)")
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    var drainedInBurst = 0
                    while !(await buffer.isEmpty), !Task.isCancelled {
                        let actions = try await buffer.drainNext()
                        try await self.applySpeakerActions(actions, session: session)
                        let drainedCount = self.withStateLock {
                            let count = (self.speakerDrainedChunkCounts[streamID] ?? 0) + 1
                            self.speakerDrainedChunkCounts[streamID] = count
                            return count
                        }
                        drainedInBurst += 1
                        if self.shouldLogSpeakerChunk(count: drainedCount) {
                            let snapshot = await buffer.snapshot()
                            await self.debugLog(
                                "speaker drain tick stream=\(streamID) count=\(drainedCount) buffered_ms=\(snapshot.bufferedMS) queued=\(snapshot.queuedChunks) bytes=\(snapshot.bufferedBytes)"
                            )
                        }
                        if drainedInBurst % Self.speakerDrainYieldEveryChunks == 0 {
                            await Task.yield()
                        }
                    }
                    if drainedInBurst == 0 {
                        try await Task.sleep(nanoseconds: Self.speakerDrainIdleSleepNanoseconds)
                    } else {
                        await Task.yield()
                    }
                } catch is CancellationError {
                    await self.debugLog("speaker drain loop cancelled stream=\(streamID)")
                    return
                } catch {
                    self.diagnostics.lastMediaError = error.localizedDescription
                    await self.debugLog("speaker drain error stream=\(streamID) error=\(error.localizedDescription)")
                    return
                }
            }
            await self?.debugLog("speaker drain loop stopped stream=\(streamID)")
        }
        withStateLock {
            if speakerDrainTasks[streamID] == nil {
                speakerDrainTasks[streamID] = task
            } else {
                task.cancel()
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
                    sampleRate: 1,
                    sleepBetweenContinuousFrames: true
                ),
                defaultSampleCount: 1
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
            await self.debugLog("mic source started session=\(sessionID) stream=\(streamID)")
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
                    if sequence <= 5 || sequence % 50 == 0 {
                        await self.debugLog("mic chunk sent stream=\(streamID) seq=\(sequence - 1) bytes=\(payload.count)")
                    }
                }
                await self.debugLog("mic source finished stream=\(streamID) chunks=\(sequence)")
            } catch {
                self.diagnostics.lastMediaError = error.localizedDescription
                await self.debugLog("mic source error stream=\(streamID) error=\(error.localizedDescription)")
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

    private func ensureStreamReceiveLoop() {
        let shouldStart = withStateLock {
            if streamReceiveLoopRunning {
                return false
            }
            streamReceiveLoopRunning = true
            return true
        }
        guard shouldStart else {
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            defer {
                self.markStreamReceiveLoopStopped()
            }
            var consecutiveFailures = 0
            while !Task.isCancelled {
                do {
                    let chunk = try await self.receiveStreamChunk()
                    consecutiveFailures = 0
                    _ = try await self.dispatchStreamChunk(chunk)
                } catch is CancellationError {
                    return
                } catch {
                    self.recordStreamError(error)
                    consecutiveFailures += 1
                    await self.debugLog(
                        "stream receive error attempt=\(consecutiveFailures) error=\(error.localizedDescription)"
                    )
                    guard self.shouldRetryStreamReceive(after: consecutiveFailures) else {
                        await self.debugLog("stream receive loop stopped after \(consecutiveFailures) failures")
                        return
                    }
                    self.diagnostics.streamState = "disconnected"
                    try? await Task.sleep(
                        nanoseconds: self.streamReceiveReconnectDelayNanoseconds(attempt: consecutiveFailures)
                    )
                }
            }
        }
        withStateLock {
            streamReceiveTask = task
        }
    }

    private func markStreamReceiveLoopStopped() {
        withStateLock {
            streamReceiveLoopRunning = false
            streamReceiveTask = nil
        }
    }

    private func shouldRetryStreamReceive(after consecutiveFailures: Int) -> Bool {
        switch configuration.reconnectPolicy {
        case .disabled:
            return false
        case let .exponentialBackoff(maxAttempts):
            return consecutiveFailures <= maxAttempts
        }
    }

    private func streamReceiveReconnectDelayNanoseconds(attempt: Int) -> UInt64 {
        let cappedExponent = min(max(attempt - 1, 0), 4)
        let multiplier = UInt64(1 << cappedExponent)
        return Self.streamReceiveReconnectBaseSleepNanoseconds * multiplier
    }

    private func recordControlError(_ error: Error) {
        diagnostics.controlState = "error"
        diagnostics.lastError = error.localizedDescription
    }

    private func recordStreamError(_ error: Error) {
        diagnostics.streamState = "error"
        diagnostics.lastError = error.localizedDescription
    }

    private func removeSpeakerPlaybackState(
        streamID: String,
        markCompleted: Bool
    ) -> (
        finishTask: Task<Void, Never>?,
        drainTask: Task<Void, Never>?,
        preparationTask: Task<SpeakerPlaybackBuffer, Error>?,
        buffer: SpeakerPlaybackBuffer?
    ) {
        withStateLock {
            let finishTask = speakerFinishTasks.removeValue(forKey: streamID)
            let drainTask = speakerDrainTasks.removeValue(forKey: streamID)
            let preparationTask = speakerPreparationTasks.removeValue(forKey: streamID)
            let buffer = speakerBuffers.removeValue(forKey: streamID)
            speakerReceivedChunkCounts.removeValue(forKey: streamID)
            speakerDrainedChunkCounts.removeValue(forKey: streamID)
            speakerAppendedLastSeq.removeValue(forKey: streamID)
            readyOutputStreams.remove(streamID)
            startedOutputStreams.remove(streamID)
            if markCompleted {
                completedOutputStreams.insert(streamID)
            }
            return (finishTask, drainTask, preparationTask, buffer)
        }
    }

    private func withStateLock<T>(_ body: () -> T) -> T {
        stateLock.lock()
        defer { stateLock.unlock() }
        return body()
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
