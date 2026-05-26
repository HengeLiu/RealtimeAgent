import Combine
import Foundation
import RealtimeAgentDeviceKit

/// realtime-agent iOS phone 参考端运行时。
///
/// 主要功能：
/// 1. 使用 `/ws/control` 注册设备并订阅事件。
/// 2. 使用拆分后的媒体 WebSocket 上传 `sensor.rgb` / `sensor.mic` 测试 stream。
/// 3. 通过 SDK speaker sink 观察 `actuator.speaker` 下行音频字节统计。
/// 4. 保持 App 只通过 DeviceClient 标准入口和 custom 回调与 server 协作。
@MainActor
final class RealtimeAgentEndpointRuntime: ObservableObject {
    @Published private(set) var controlState = "未连接"
    @Published private(set) var streamState = "未连接"
    @Published private(set) var eventLog: [String] = []
    @Published private(set) var speakerBytesBuffered = 0
    @Published private(set) var rgbUploadCount = 0
    @Published private(set) var directCameraState = "未启动"
    @Published private(set) var directCameraSinkURIs: [String] = []
    @Published private(set) var directCameraFrameCount = 0
    @Published private(set) var directCameraBytes = 0

    let config: AppConfig
    let logFilePath: String

    private var client: RealtimeAgentDeviceClient?
    private var microphone: MicrophoneStreamer?
    private var speakerBuffer = Data()
    private var speakerChunkCount = 0
    private var directCameraSinkServer: DirectCameraSinkServer?
    private var latestDirectCameraFrame: DirectCameraFrame?
    private let logFileURL: URL
    private let logFileQueue = DispatchQueue(label: "realtime-agent.phone.log-file")

    init(config: AppConfig) {
        self.config = config
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let logFileURL = documentsURL.appendingPathComponent("RealtimeAgentPhone.log")
        self.logFileURL = logFileURL
        self.logFilePath = logFileURL.path
    }

    /// 建立控制和 stream WebSocket，并发送注册事件。
    func connectAndRegister() async {
        do {
            startDirectCameraSink()
            let client = try makeClient()
            configureClient(client)
            self.client = client
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
        speakerChunkCount = 0
        controlState = "已断开"
        streamState = "已断开"
        stopDirectCameraSink()
        appendLog("disconnected")
    }

    /// 清空 App 内最近事件和沙盒日志文件。
    ///
    /// 主要用途：真机联调前清掉历史噪音，只保留本次播放链路日志。
    func clearLogs() {
        eventLog.removeAll()
        logFileQueue.async { [logFileURL] in
            try? FileManager.default.removeItem(at: logFileURL)
        }
        appendLog("logs cleared")
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
        let configuration = RealtimeAgentClientConfiguration(
            protocolVersion: config.protocolVersion,
            autoFailUnhandledCommands: false
        )
        let audioInput: AudioInput = config.audioInput.enabled ? .enabled() : .disabled()
        let camera: Camera = config.camera.enabled ? .enabled() : .disabled()
        let speakerBuffer = PlaybackBuffer(
            startWatermarkMS: config.speaker.buffer.startWatermarkMS,
            lowWatermarkMS: config.speaker.buffer.lowWatermarkMS,
            highWatermarkMS: config.speaker.buffer.highWatermarkMS,
            maxBufferMS: config.speaker.buffer.maxBufferMS
        )
        let speaker: Speaker = config.speaker.enabled ? .enabled(buffer: speakerBuffer) : .disabled()
        return try DeviceClient(
            serverURL: serverURL.absoluteString,
            deviceID: config.deviceID,
            userID: config.userID,
            name: "ios-phone-reference",
            clientType: "ios-phone",
            audioInput: audioInput,
            camera: camera,
            speaker: speaker,
            auth: config.auth.payload,
            properties: properties,
            configuration: configuration
        )
    }

    private func configureClient(_ client: RealtimeAgentDeviceClient) {
        client.onDebugLog { [weak self] message in
            await self?.appendSDKDebugLog(message)
        }
        client.onCustomCommand("haptic.vibrate") { [weak self] context in
            await self?.handleHapticCommand(context)
        }
        client.onEvent("custom.navigation.route.updated") { [weak self] event in
            await self?.handleNavigationEvent(event)
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

    private func handleHapticCommand(_ context: RealtimeAgentCustomCommandContext) async {
        do {
            let durationMS = context.payload["duration_ms"] as? Int ?? 120
            appendLog("custom command haptic.vibrate duration=\(durationMS)")
            try await context.emit("custom.haptic.vibrate.done", ["duration_ms": durationMS])
            appendLog("event -> custom.haptic.vibrate.done")
        } catch {
            appendLog("custom command failed: \(error.localizedDescription)")
        }
    }

    private func handleNavigationEvent(_ event: RealtimeAgentEvent) async {
        appendLog("custom event <- \(event.eventName)")
    }
    private func handleOutputChunk(_ chunk: RealtimeAgentStreamChunk) async {
        if chunk.streamType == "actuator.speaker" {
            speakerBuffer.append(chunk.payload)
            speakerBytesBuffered = speakerBuffer.count
            speakerChunkCount += 1
            if shouldLogSpeakerChunk(count: speakerChunkCount) {
                appendLog(
                    "app speaker chunk stream=\(chunk.streamID) seq=\(chunk.seq) count=\(speakerChunkCount) bytes=\(chunk.payload.count) total_bytes=\(speakerBytesBuffered)"
                )
            }
            return
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
        let line = "\(Self.logTimestamp()) \(message)"
        eventLog.insert(line, at: 0)
        if eventLog.count > 200 {
            eventLog.removeLast(eventLog.count - 200)
        }
        writeLogLineToFile(line)
    }

    private func appendSDKDebugLog(_ message: String) {
        appendLog("sdk \(message)")
    }

    private func shouldLogSpeakerChunk(count: Int) -> Bool {
        count <= 5 || count % 50 == 0
    }

    private func writeLogLineToFile(_ line: String) {
        logFileQueue.async { [logFileURL] in
            let data = Data((line + "\n").utf8)
            if !FileManager.default.fileExists(atPath: logFileURL.path) {
                _ = FileManager.default.createFile(atPath: logFileURL.path, contents: nil)
            }
            guard let handle = try? FileHandle(forWritingTo: logFileURL) else {
                return
            }
            defer {
                try? handle.close()
            }
            _ = try? handle.seekToEnd()
            handle.write(data)
        }
    }

    private static func logTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
        return formatter.string(from: Date())
    }

    private static func testJPEGPayload() -> Data {
        var data = Data([0xFF, 0xD8])
        data.append(Data("realtime-agent-ios-rgb".utf8))
        data.append(contentsOf: [0xFF, 0xD9])
        return data
    }
}
