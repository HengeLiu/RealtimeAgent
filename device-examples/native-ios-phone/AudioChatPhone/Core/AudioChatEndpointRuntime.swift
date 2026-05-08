import Combine
import Foundation

/// audio-chat iOS phone 参考端运行时。
///
/// 主要功能：
/// 1. 使用 `/ws/control` 注册设备并订阅事件。
/// 2. 使用 `/ws/stream` 上传 `sensor.rgb` / `sensor.mic` 测试 stream。
/// 3. 消费 `actuator.speaker` 下行 stream，把音频字节写入内存 buffer 并上报播放回执。
/// 4. 保持端侧只通过 event / stream 协议与 server 协作。
@MainActor
final class AudioChatEndpointRuntime: ObservableObject {
    @Published private(set) var controlState = "未连接"
    @Published private(set) var streamState = "未连接"
    @Published private(set) var eventLog: [String] = []
    @Published private(set) var speakerBytesBuffered = 0
    @Published private(set) var rgbUploadCount = 0
    @Published private(set) var phoneTaskEventLog: [String] = []
    @Published private(set) var directCameraState = "未启动"
    @Published private(set) var directCameraSinkURIs: [String] = []
    @Published private(set) var directCameraFrameCount = 0
    @Published private(set) var directCameraBytes = 0

    let config: AppConfig

    private var controlSocket: URLSessionWebSocketTask?
    private var streamSocket: URLSessionWebSocketTask?
    private var outputStreamsStarted = Set<String>()
    private var speakerBuffer = Data()
    private var sequenceByStream: [String: Int] = [:]
    private let phoneTaskRegistry = PhoneTaskRegistry()
    private var directCameraSinkServer: DirectCameraSinkServer?
    private var latestDirectCameraFrame: DirectCameraFrame?

    init(config: AppConfig) {
        self.config = config
    }

    /// 建立控制和 stream WebSocket，并发送注册事件。
    func connectAndRegister() async {
        do {
            startDirectCameraSink()
            try await ensureControlSocket()
            try await sendRegistration()
            try await ensureStreamSocket()
        } catch {
            appendLog("connect failed: \(error.localizedDescription)")
        }
    }

    /// 断开两条 WebSocket 连接。
    func disconnect() async {
        controlSocket?.cancel(with: .normalClosure, reason: nil)
        streamSocket?.cancel(with: .normalClosure, reason: nil)
        controlSocket = nil
        streamSocket = nil
        controlState = "已断开"
        streamState = "已断开"
        stopDirectCameraSink()
        appendLog("disconnected")
    }

    /// 启动端侧直连相机接收服务。
    ///
    /// 功能：让 ESP32 等端侧感知设备可以直接把 JPEG 帧推到手机端。手机仍然通过
    /// audio-chat 的 event / stream 协议把需要进入对话的图片上传到 server。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：端口被占用或网络不可用时只更新状态和日志，不向外抛出。
    func startDirectCameraSink() {
        if let server = directCameraSinkServer {
            directCameraSinkURIs = server.sinkURIs
            server.start()
            return
        }
        let server = DirectCameraSinkServer(
            port: config.directCameraSinkPort,
            onState: { [weak self] state in
                self?.directCameraState = state
                self?.appendLog("direct camera: \(state)")
            },
            onFrame: { [weak self] frame in
                self?.handleDirectCameraFrame(frame)
            }
        )
        directCameraSinkServer = server
        directCameraSinkURIs = server.sinkURIs
        server.start()
    }

    /// 停止端侧直连相机接收服务。
    func stopDirectCameraSink() {
        directCameraSinkServer?.stop()
        directCameraSinkServer = nil
        directCameraSinkURIs = []
        directCameraState = "未启动"
    }

    /// 手动上传一帧 `sensor.rgb` 测试图片。
    func uploadTestRGBFrame(reason: String) async {
        let sessionID = config.deviceID
        await uploadRGBFrame(sessionID: sessionID, requestID: nil, reason: reason)
    }

    /// 手动上传一段 20ms 静音 PCM，验证 `sensor.mic` stream 格式。
    func uploadTestMicPCM() async {
        do {
            try await ensureStreamSocket()
            let sessionID = config.deviceID
            let streamID = AudioChatIDs.make(prefix: "stream_mic")
            try await openInputStream(streamType: "sensor.mic", sessionID: sessionID, streamID: streamID, payload: [
                "stream_type": "sensor.mic",
                "format": ["codec": "pcm16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 20],
            ])
            let payload = Data(repeating: 0, count: 640)
            let chunk = AudioChatStreamChunk(
                userID: config.userID,
                sessionID: sessionID,
                streamID: streamID,
                streamType: "sensor.mic",
                seq: nextSeq(streamID: streamID),
                payload: payload,
                codec: "pcm16le",
                sampleRate: 16000,
                channels: 1,
                durationMS: 20,
                final: true
            )
            try await streamSocket?.send(.data(AudioChatStreamChunkCodec.encode(chunk)))
            try await sendControlEvent(
                AudioChatEvent(
                eventName: "stream.input.closed",
                userID: config.userID,
                producerID: config.deviceID,
                payload: ["stream_type": "sensor.mic", "reason": "ios_test_pcm_done"],
                sessionID: chunk.sessionID,
                streamID: streamID,
                streamType: "sensor.mic"
                )
            )
            appendLog("sensor.mic uploaded bytes=\(payload.count)")
        } catch {
            appendLog("sensor.mic upload failed: \(error.localizedDescription)")
        }
    }

    private func ensureControlSocket() async throws {
        if controlSocket != nil {
            return
        }
        let socket = URLSession.shared.webSocketTask(with: try websocketURL(path: "/ws/control"))
        controlSocket = socket
        socket.resume()
        controlState = "已连接，等待注册"
        Task { await receiveControlLoop(socket) }
    }

    private func ensureStreamSocket() async throws {
        if streamSocket != nil {
            return
        }
        let socket = URLSession.shared.webSocketTask(with: try websocketURL(path: "/ws/stream", query: [
            URLQueryItem(name: "device_id", value: config.deviceID)
        ]))
        streamSocket = socket
        socket.resume()
        streamState = "已连接"
        Task { await receiveStreamLoop(socket) }
    }

    private func sendRegistration() async throws {
        var properties = config.properties.mapValues { $0.object }
        properties["direct.camera_sink"] = true
        properties["direct.camera_sink.path"] = "/ws/camera"
        properties["direct.camera_sink.port"] = Int(config.directCameraSinkPort)
        properties["direct.camera_sink.uris"] = directCameraSinkURIs
        properties["direct.camera_sink.frame_format"] = "audio_chat.direct_frame.v1"
        let event = AudioChatEvent(
            eventName: "control.device.register.requested",
            userID: config.userID,
            producerID: config.deviceID,
            payload: [
                "device_id": config.deviceID,
                "name": "iOS 设备示例",
                "device_name": "ios-phone-reference",
                "client_type": "ios-phone",
                "sdk_version": "audio-chat-ios-reference-0.1.0",
                "auth": config.auth.payload,
                "properties": properties,
                "subscriptions": config.subscriptions.map { $0.payload },
            ],
            version: config.protocolVersion
        )
        try await sendControlEvent(event)
        appendLog("registration sent")
    }

    private func receiveControlLoop(_ socket: URLSessionWebSocketTask) async {
        while controlSocket === socket {
            do {
                let message = try await socket.receive()
                guard case let .string(text) = message,
                      let data = text.data(using: .utf8),
                      let dictionary = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    continue
                }
                let event = try AudioChatEvent(dictionary: dictionary)
                await handleControlEvent(event)
            } catch {
                controlState = "接收中断"
                appendLog("control receive stopped: \(error.localizedDescription)")
                return
            }
        }
    }

    private func receiveStreamLoop(_ socket: URLSessionWebSocketTask) async {
        while streamSocket === socket {
            do {
                let message = try await socket.receive()
                guard case let .data(data) = message else {
                    continue
                }
                let chunk = try AudioChatStreamChunkCodec.decode(data)
                await handleOutputChunk(chunk)
            } catch {
                streamState = "接收中断"
                appendLog("stream receive stopped: \(error.localizedDescription)")
                return
            }
        }
    }

    private func handleControlEvent(_ event: AudioChatEvent) async {
        appendLog("event <- \(event.eventName)")
        switch event.eventName {
        case "control.device.registered":
            controlState = "已注册"
            let heartbeatConnection = event.payload["connection_id"] as? String
            try? await sendControlEvent(
                AudioChatEvent(
                    eventName: "control.device.heartbeat.received",
                    userID: config.userID,
                    producerID: config.deviceID,
                    payload: ["connection_id": heartbeatConnection ?? ""],
                    version: config.protocolVersion
                )
            )
        case "stream.control.configure.requested" where event.streamType == "sensor.rgb":
            await uploadRGBFrame(
                sessionID: event.sessionID ?? config.deviceID,
                requestID: event.payload["request_id"] as? String,
                reason: "server_requested"
            )
        case "control.device.command.requested":
            await handlePhoneTaskCommand(event)
        case "stream.output.close.requested":
            await finishOutputStream(event)
        case "stream.output.cancel.requested":
            await cancelOutputStream(event)
        case "control.audio_session.close.requested":
            try? await sendControlEvent(
                AudioChatEvent(
                    eventName: "control.audio_session.closed",
                    userID: config.userID,
                    producerID: config.deviceID,
                    payload: ["reason": "ios_phone_closed"],
                    sessionID: event.sessionID,
                    version: config.protocolVersion
                )
            )
        default:
            break
        }
    }

    private func handlePhoneTaskCommand(_ event: AudioChatEvent) async {
        let taskType = event.payload["task_type"] as? String ?? ""
        let taskID = event.payload["task_id"] as? String ?? AudioChatIDs.make(prefix: "ios_phone_task")
        guard let handler = phoneTaskRegistry.handler(taskType: taskType) else {
            try? await sendPhoneTaskEvent(
                "control.device.command.failed",
                command: event,
                payload: ["task_id": taskID, "task_type": taskType, "message": "unknown phone task"]
            )
            return
        }
        try? await sendPhoneTaskEvent(
            "control.device.command.started",
            command: event,
            payload: ["task_id": taskID, "task_type": taskType, "state": "started"]
        )
        await uploadRGBFrame(
            sessionID: config.deviceID,
            requestID: taskID,
            reason: "phone_task"
        )
        try? await sendPhoneTaskEvent(
            "control.device.command.progress",
            command: event,
            payload: ["task_id": taskID, "task_type": taskType, "progress": 1.0]
        )
        let result = handler.result(command: event, frameCount: max(1, directCameraFrameCount))
        try? await sendPhoneTaskEvent(
            "control.device.command.completed",
            command: event,
            payload: [
                "task_id": taskID,
                "task_type": taskType,
                "summary": result.summary,
                "result": result.payload,
            ]
        )
    }

    private func sendPhoneTaskEvent(_ eventName: String, command: AudioChatEvent, payload: [String: Any]) async throws {
        let event = AudioChatEvent(
            eventName: eventName,
            userID: config.userID,
            producerID: config.deviceID,
            payload: payload,
            sessionID: config.deviceID,
            version: config.protocolVersion
        )
        try await sendControlEvent(event)
        phoneTaskEventLog.insert(eventName, at: 0)
        if phoneTaskEventLog.count > 30 {
            phoneTaskEventLog.removeLast(phoneTaskEventLog.count - 30)
        }
    }

    private func handleOutputChunk(_ chunk: AudioChatStreamChunk) async {
        if chunk.streamType == "actuator.speaker" {
            speakerBuffer.append(chunk.payload)
            speakerBytesBuffered = speakerBuffer.count
        }
        if !outputStreamsStarted.contains(chunk.streamID) {
            outputStreamsStarted.insert(chunk.streamID)
            try? await sendControlEvent(
                AudioChatEvent(
                    eventName: "stream.output.started",
                    userID: config.userID,
                    producerID: config.deviceID,
                    payload: ["stream_type": chunk.streamType],
                    sessionID: chunk.sessionID,
                    streamID: chunk.streamID,
                    streamType: chunk.streamType,
                    version: config.protocolVersion
                )
            )
        }
        appendLog("stream <- \(chunk.streamType) bytes=\(chunk.payload.count)")
    }

    private func uploadRGBFrame(sessionID: String, requestID: String?, reason: String) async {
        do {
            try await ensureStreamSocket()
            let streamID = AudioChatIDs.make(prefix: "stream_rgb")
            try await sendControlEvent(
                AudioChatEvent(
                    eventName: "stream.input.opened",
                    userID: config.userID,
                    producerID: config.deviceID,
                    payload: [
                        "stream_type": "sensor.rgb",
                        "format": ["codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1],
                        "reason": reason,
                    ],
                    sessionID: sessionID,
                    streamID: streamID,
                    streamType: "sensor.rgb",
                    version: config.protocolVersion
                )
            )
            let directFrame = latestDirectCameraFrame
            let payload = directFrame?.payload ?? Self.testJPEGPayload()
            var metadata: [String: Any] = requestID.map { ["request_id": $0] } ?? [:]
            if let directFrame {
                metadata["source"] = "direct_camera_sink"
                metadata["direct_stream_id"] = directFrame.streamID
                metadata["direct_seq"] = directFrame.sequence
                metadata["direct_ts_ms"] = directFrame.timestampMS
            } else {
                metadata["source"] = "ios_reference_test_frame"
            }
            let chunk = AudioChatStreamChunk(
                userID: config.userID,
                sessionID: sessionID,
                streamID: streamID,
                streamType: "sensor.rgb",
                seq: nextSeq(streamID: streamID),
                payload: payload,
                codec: "jpeg",
                sampleRate: 1,
                channels: 1,
                durationMS: 1,
                final: true,
                metadata: metadata
            )
            try await streamSocket?.send(.data(AudioChatStreamChunkCodec.encode(chunk)))
            let closeReason = directFrame == nil ? "ios_rgb_uploaded" : "ios_direct_rgb_uploaded"
            try await sendControlEvent(
                AudioChatEvent(
                    eventName: "stream.input.closed",
                    userID: config.userID,
                    producerID: config.deviceID,
                    payload: ["stream_type": "sensor.rgb", "reason": closeReason],
                    sessionID: sessionID,
                    streamID: streamID,
                    streamType: "sensor.rgb",
                    version: config.protocolVersion
                )
            )
            rgbUploadCount += 1
            appendLog("sensor.rgb uploaded bytes=\(payload.count)")
        } catch {
            appendLog("sensor.rgb upload failed: \(error.localizedDescription)")
        }
    }

    private func handleDirectCameraFrame(_ frame: DirectCameraFrame) {
        latestDirectCameraFrame = frame
        directCameraFrameCount += 1
        directCameraBytes += frame.payload.count
        directCameraState = "已接收 \(directCameraFrameCount) 帧"
        appendLog("direct camera frame bytes=\(frame.payload.count) seq=\(frame.sequence)")
    }

    private func finishOutputStream(_ event: AudioChatEvent) async {
        try? await sendControlEvent(
            AudioChatEvent(
                eventName: "stream.output.finished",
                userID: config.userID,
                producerID: config.deviceID,
                payload: ["stream_type": event.streamType ?? ""],
                sessionID: event.sessionID,
                streamID: event.streamID,
                streamType: event.streamType,
                version: config.protocolVersion
            )
        )
        try? await sendControlEvent(
            AudioChatEvent(
                eventName: "stream.output.closed",
                userID: config.userID,
                producerID: config.deviceID,
                payload: ["stream_type": event.streamType ?? "", "reason": "ios_phone_buffered"],
                sessionID: event.sessionID,
                streamID: event.streamID,
                streamType: event.streamType,
                version: config.protocolVersion
            )
        )
    }

    private func cancelOutputStream(_ event: AudioChatEvent) async {
        try? await sendControlEvent(
            AudioChatEvent(
                eventName: "stream.output.cancelled",
                userID: config.userID,
                producerID: config.deviceID,
                payload: ["stream_type": event.streamType ?? "", "reason": "ios_phone_cancelled"],
                sessionID: event.sessionID,
                streamID: event.streamID,
                streamType: event.streamType,
                version: config.protocolVersion
            )
        )
    }

    private func openInputStream(streamType: String, sessionID: String, streamID: String, payload: [String: Any]) async throws {
        try await sendControlEvent(
            AudioChatEvent(
                eventName: "stream.input.opened",
                userID: config.userID,
                producerID: config.deviceID,
                payload: payload,
                sessionID: sessionID,
                streamID: streamID,
                streamType: streamType,
                version: config.protocolVersion
            )
        )
    }

    private func sendControlEvent(_ event: AudioChatEvent) async throws {
        guard let controlSocket else {
            throw AudioChatEndpointError.missingWebSocket("control")
        }
        try await controlSocket.send(.string(event.jsonString))
        appendLog("event -> \(event.eventName)")
    }

    private func websocketURL(path: String, query: [URLQueryItem] = []) throws -> URL {
        guard var components = URLComponents(string: config.serverURL) else {
            throw AudioChatEndpointError.invalidURL(config.serverURL)
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.path = path
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else {
            throw AudioChatEndpointError.invalidURL("\(config.serverURL)\(path)")
        }
        return url
    }

    private func nextSeq(streamID: String) -> Int {
        let current = sequenceByStream[streamID] ?? 0
        sequenceByStream[streamID] = current + 1
        return current
    }

    private func appendLog(_ message: String) {
        eventLog.insert(message, at: 0)
        if eventLog.count > 30 {
            eventLog.removeLast(eventLog.count - 30)
        }
    }

    private static func testJPEGPayload() -> Data {
        var data = Data([0xFF, 0xD8])
        data.append(Data("audio-chat-ios-rgb".utf8))
        data.append(contentsOf: [0xFF, 0xD9])
        return data
    }
}

/// iOS phone 参考端任务 handler 结果。
struct PhoneTaskCommandResult {
    var summary: String
    var payload: [String: Any]
}

/// iOS phone 参考端任务 handler 协议。
protocol PhoneTaskHandler {
    var taskType: String { get }
    func result(command: AudioChatEvent, frameCount: Int) -> PhoneTaskCommandResult
}

/// 找物任务 handler 样板。
struct FindObjectPhoneTaskHandler: PhoneTaskHandler {
    let taskType = "find_object_phone_task"

    func result(command: AudioChatEvent, frameCount: Int) -> PhoneTaskCommandResult {
        let input = command.payload["input"] as? [String: Any] ?? [:]
        let target = input["target"] as? String ?? "目标物"
        return PhoneTaskCommandResult(
            summary: "找到\(target)",
            payload: [
                "target": target,
                "found": true,
                "frame_count": frameCount,
                "source": "ios-phone-reference",
            ]
        )
    }
}

/// 红绿灯任务 handler 样板。
struct TrafficLightPhoneTaskHandler: PhoneTaskHandler {
    let taskType = "traffic_light_phone_task"

    func result(command: AudioChatEvent, frameCount: Int) -> PhoneTaskCommandResult {
        let input = command.payload["input"] as? [String: Any] ?? [:]
        let color = input["expected_color"] as? String ?? "green"
        return PhoneTaskCommandResult(
            summary: "红绿灯识别结果：\(color)",
            payload: [
                "color": color,
                "confidence": 0.9,
                "frame_count": frameCount,
                "source": "ios-phone-reference",
            ]
        )
    }
}

/// iOS phone 参考端任务注册表。
final class PhoneTaskRegistry {
    private let handlers: [String: PhoneTaskHandler]

    init() {
        let builtins: [PhoneTaskHandler] = [
            FindObjectPhoneTaskHandler(),
            TrafficLightPhoneTaskHandler(),
        ]
        self.handlers = Dictionary(uniqueKeysWithValues: builtins.map { ($0.taskType, $0) })
    }

    func handler(taskType: String) -> PhoneTaskHandler? {
        handlers[taskType]
    }
}
