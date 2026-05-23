import Combine
import Foundation
import RealtimeAgentDeviceKit

/// realtime-agent iOS phone 参考端运行时。
///
/// 主要功能：
/// 1. 使用 `/ws/control` 注册设备并订阅事件。
/// 2. 使用 `/ws/stream` 上传 `sensor.rgb` / `sensor.mic` 测试 stream。
/// 3. 消费 `actuator.speaker` 下行 stream，把音频字节写入内存 buffer 并上报播放回执。
/// 4. 保持端侧只通过 event / stream 协议与 server 协作。
@MainActor
final class RealtimeAgentEndpointRuntime: ObservableObject {
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

    private var client: RealtimeAgentDeviceClient?
    private var microphone: MicrophoneStreamer?
    private var speakerBuffer = Data()
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
            let client = try makeClient()
            configureClient(client)
            self.client = client
            microphone = MicrophoneStreamer(client: client)
            controlState = "已连接，等待注册"
            try await client.connectAndRegister()
            controlState = "已注册"
            streamState = "已连接"
            appendLog("registration sent")
        } catch {
            appendLog("connect failed: \(error.localizedDescription)")
        }
    }

    /// 断开两条 WebSocket 连接。
    func disconnect() async {
        await client?.close()
        client = nil
        microphone = nil
        controlState = "已断开"
        streamState = "已断开"
        stopDirectCameraSink()
        appendLog("disconnected")
    }

    /// 启动端侧直连相机接收服务。
    ///
    /// 功能：让 ESP32 等端侧感知设备可以直接把 JPEG 帧推到手机端。手机仍然通过
    /// realtime-agent 的 event / stream 协议把需要进入对话的图片上传到 server。
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
            let microphone = try ensureMicrophone()
            let payload = Data(repeating: 0, count: 640)
            try await microphone.open(sessionID: config.deviceID)
            try await microphone.sendPCM16LE(payload, sessionID: config.deviceID, final: true)
            try await microphone.close(sessionID: config.deviceID, reason: "ios_test_pcm_done")
            appendLog("sensor.mic uploaded bytes=\(payload.count)")
        } catch {
            appendLog("sensor.mic upload failed: \(error.localizedDescription)")
        }
    }

    private func makeClient() throws -> RealtimeAgentDeviceClient {
        guard let serverURL = URL(string: config.serverURL) else {
            throw RealtimeAgentDeviceError.invalidURL(config.serverURL)
        }
        var properties = config.properties.mapValues { $0.object }
        properties["direct.camera_sink"] = true
        properties["direct.camera_sink.path"] = "/ws/camera"
        properties["direct.camera_sink.port"] = Int(config.directCameraSinkPort)
        properties["direct.camera_sink.uris"] = directCameraSinkURIs
        properties["direct.camera_sink.frame_format"] = "realtime_agent.direct_frame.v1"
        let device = RealtimeAgentDevice(deviceID: config.deviceID)
            .user(config.userID)
            .named("ios-phone-reference")
            .clientType("ios-phone")
            .sdkVersion("realtime-agent-ios-reference-0.1.0")
            .auth(config.auth.payload)
            .properties(properties)
            .supports(config.supports.mapValues { $0.object })
        let configuration = RealtimeAgentClientConfiguration(
            protocolVersion: config.protocolVersion,
            autoFailUnhandledCommands: false
        )
        return RealtimeAgentDeviceClient(serverURL: serverURL, device: device, configuration: configuration)
    }

    private func configureClient(_ client: RealtimeAgentDeviceClient) {
        client.onStreamOpen("sensor.rgb") { [weak self] request in
            await self?.uploadRGBFrame(request: request, reason: "server_requested")
        }
        client.onAnyCommand { [weak self] responder in
            await self?.handlePhoneTaskCommand(responder)
        }
        client.onOutputChunk("actuator.speaker") { [weak self] chunk, _ in
            await self?.handleOutputChunk(chunk)
        }
        client.onEvent("control.audio_session.close.requested") { [weak self] event in
            await self?.closeAudioSession(event)
        }
    }

    private func ensureClient() throws -> RealtimeAgentDeviceClient {
        guard let client else {
            throw RealtimeAgentDeviceError.transportClosed("client is not connected")
        }
        return client
    }

    private func ensureMicrophone() throws -> MicrophoneStreamer {
        if let microphone {
            return microphone
        }
        let microphone = MicrophoneStreamer(client: try ensureClient())
        self.microphone = microphone
        return microphone
    }

    private func closeAudioSession(_ event: RealtimeAgentEvent) async {
        do {
            try await ensureClient().sendEvent(
                name: "control.audio_session.closed",
                payload: ["reason": "ios_phone_closed"],
                sessionID: event.sessionID
            )
            appendLog("event -> control.audio_session.closed")
        } catch {
            appendLog("audio session close failed: \(error.localizedDescription)")
        }
    }
    private func handlePhoneTaskCommand(_ responder: RealtimeAgentCommandResponder) async {
        let event = responder.request
        let taskType = event.payload["task_type"] as? String ?? ""
        let taskID = event.payload["task_id"] as? String ?? RealtimeAgentIDs.make(prefix: "ios_phone_task")
        guard let handler = phoneTaskRegistry.handler(taskType: taskType) else {
            try? await responder.failed(code: "phone_task.unknown", message: "unknown phone task")
            appendPhoneTaskEvent("command.failed")
            return
        }
        try? await responder.accepted(["task_id": taskID, "task_type": taskType, "state": "started"])
        appendPhoneTaskEvent("command.accepted")
        await uploadRGBFrame(
            sessionID: config.deviceID,
            requestID: taskID,
            reason: "phone_task"
        )
        try? await responder.progress(["task_id": taskID, "task_type": taskType, "progress": 1.0])
        appendPhoneTaskEvent("command.progress")
        let result = handler.result(command: event, frameCount: max(1, directCameraFrameCount))
        try? await responder.completed([
            "task_id": taskID,
            "task_type": taskType,
            "summary": result.summary,
            "result": result.payload,
        ])
        appendPhoneTaskEvent("command.completed")
    }

    private func appendPhoneTaskEvent(_ eventName: String) {
        phoneTaskEventLog.insert(eventName, at: 0)
        if phoneTaskEventLog.count > 30 {
            phoneTaskEventLog.removeLast(phoneTaskEventLog.count - 30)
        }
    }

    private func handleOutputChunk(_ chunk: RealtimeAgentStreamChunk) async {
        if chunk.streamType == "actuator.speaker" {
            speakerBuffer.append(chunk.payload)
            speakerBytesBuffered = speakerBuffer.count
        }
        appendLog("stream <- \(chunk.streamType) bytes=\(chunk.payload.count)")
    }

    private func uploadRGBFrame(request: RealtimeAgentInputStreamRequest, reason: String) async {
        do {
            let (payload, metadata, closeReason) = rgbPayloadAndMetadata(requestID: request.requestID)
            var openedPayload: [String: Any] = [
                "format": ["codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1],
                "reason": reason,
            ]
            if let requestID = request.requestID {
                openedPayload["request_id"] = requestID
            }
            try await request.opened(openedPayload)
            try await request.write(
                payload,
                codec: "jpeg",
                sampleRate: 1,
                channels: 1,
                durationMS: 1,
                final: true,
                metadata: metadata
            )
            try await request.closed(reason: closeReason)
            rgbUploadCount += 1
            appendLog("sensor.rgb uploaded bytes=\(payload.count)")
        } catch {
            appendLog("sensor.rgb upload failed: \(error.localizedDescription)")
        }
    }

    private func uploadRGBFrame(sessionID: String, requestID: String?, reason: String) async {
        do {
            let client = try ensureClient()
            let streamID = RealtimeAgentIDs.make(prefix: "stream_rgb")
            var openPayload: [String: Any] = [
                "stream_type": "sensor.rgb",
                "format": ["codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1],
                "reason": reason,
            ]
            if let requestID {
                openPayload["request_id"] = requestID
            }
            let openEvent = RealtimeAgentEvent(
                eventName: "stream.input.opened",
                userID: config.userID,
                producerID: config.deviceID,
                payload: openPayload,
                sessionID: sessionID,
                streamID: streamID,
                streamType: "sensor.rgb",
                version: config.protocolVersion
            )
            try await client.sendEvent(openEvent)
            appendLog("event -> stream.input.opened")
            let (payload, metadata, closeReason) = rgbPayloadAndMetadata(requestID: requestID)
            let chunk = RealtimeAgentStreamChunk(
                userID: config.userID,
                sessionID: sessionID,
                streamID: streamID,
                streamType: "sensor.rgb",
                seq: 0,
                payload: payload,
                codec: "jpeg",
                sampleRate: 1,
                channels: 1,
                durationMS: 1,
                final: true,
                metadata: metadata
            )
            try await client.sendStreamChunk(chunk)
            var closePayload: [String: Any] = [
                "stream_type": "sensor.rgb",
                "reason": closeReason,
            ]
            if let requestID {
                closePayload["request_id"] = requestID
            }
            let closeEvent = RealtimeAgentEvent(
                eventName: "stream.input.closed",
                userID: config.userID,
                producerID: config.deviceID,
                payload: closePayload,
                sessionID: sessionID,
                streamID: streamID,
                streamType: "sensor.rgb",
                version: config.protocolVersion
            )
            try await client.sendEvent(closeEvent)
            appendLog("event -> stream.input.closed")
            rgbUploadCount += 1
            appendLog("sensor.rgb uploaded bytes=\(payload.count)")
        } catch {
            appendLog("sensor.rgb upload failed: \(error.localizedDescription)")
        }
    }

    private func rgbPayloadAndMetadata(requestID: String?) -> (Data, [String: Any], String) {
        let directFrame = latestDirectCameraFrame
        let payload = directFrame?.payload ?? Self.testJPEGPayload()
        var metadata: [String: Any] = requestID.map { ["request_id": $0] } ?? [:]
        if let directFrame {
            metadata["source"] = "direct_camera_sink"
            metadata["direct_stream_id"] = directFrame.streamID
            metadata["direct_seq"] = directFrame.sequence
            metadata["direct_ts_ms"] = directFrame.timestampMS
            return (payload, metadata, "ios_direct_rgb_uploaded")
        }
        metadata["source"] = "ios_reference_test_frame"
        return (payload, metadata, "ios_rgb_uploaded")
    }

    private func handleDirectCameraFrame(_ frame: DirectCameraFrame) {
        latestDirectCameraFrame = frame
        directCameraFrameCount += 1
        directCameraBytes += frame.payload.count
        directCameraState = "已接收 \(directCameraFrameCount) 帧"
        appendLog("direct camera frame bytes=\(frame.payload.count) seq=\(frame.sequence)")
    }

    private func appendLog(_ message: String) {
        eventLog.insert(message, at: 0)
        if eventLog.count > 30 {
            eventLog.removeLast(eventLog.count - 30)
        }
    }

    private static func testJPEGPayload() -> Data {
        var data = Data([0xFF, 0xD8])
        data.append(Data("realtime-agent-ios-rgb".utf8))
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
    func result(command: RealtimeAgentEvent, frameCount: Int) -> PhoneTaskCommandResult
}

/// iOS phone 参考端任务注册表。
final class PhoneTaskRegistry {
    private let handlers: [String: PhoneTaskHandler]

    init() {
        let builtins: [PhoneTaskHandler] = []
        self.handlers = Dictionary(uniqueKeysWithValues: builtins.map { ($0.taskType, $0) })
    }

    func handler(taskType: String) -> PhoneTaskHandler? {
        handlers[taskType]
    }
}
