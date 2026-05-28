import Foundation
import Testing
@testable import RealtimeAgentDeviceKit

final class MockRealtimeAgentTransport: RealtimeAgentWebSocketTransport, @unchecked Sendable {
    var controlConnectedURL: URL?
    var streamConnectedURL: URL?
    var streamConnectedURLs: [RealtimeAgentStreamChannel: URL] = [:]
    var sentControlTexts: [String] = []
    var controlInbox: [String] = []
    var sentStreamData: [Data] = []
    var sentStreamDataByChannel: [RealtimeAgentStreamChannel: [Data]] = [:]
    var streamInbox: [Data] = []
    var streamInboxByChannel: [RealtimeAgentStreamChannel: [Data]] = [:]
    var streamReceiveResults: [Result<Data, Error>] = []
    var streamConnectCount = 0

    func connectControl(url: URL) async throws {
        controlConnectedURL = url
    }

    func connectStream(channel: RealtimeAgentStreamChannel, url: URL) async throws {
        streamConnectedURL = url
        streamConnectedURLs[channel] = url
        streamConnectCount += 1
    }

    func sendControl(text: String) async throws {
        sentControlTexts.append(text)
    }

    func receiveControl() async throws -> String {
        guard !controlInbox.isEmpty else {
            throw RealtimeAgentDeviceError.transportClosed("empty control inbox")
        }
        return controlInbox.removeFirst()
    }

    func sendStream(data: Data, channel: RealtimeAgentStreamChannel) async throws {
        sentStreamData.append(data)
        sentStreamDataByChannel[channel, default: []].append(data)
    }

    func receiveStream(channel: RealtimeAgentStreamChannel) async throws -> Data {
        if !streamReceiveResults.isEmpty {
            return try streamReceiveResults.removeFirst().get()
        }
        if var inbox = streamInboxByChannel[channel], !inbox.isEmpty {
            let data = inbox.removeFirst()
            streamInboxByChannel[channel] = inbox
            return data
        }
        guard !streamInbox.isEmpty else {
            throw RealtimeAgentDeviceError.transportClosed("empty stream inbox")
        }
        return streamInbox.removeFirst()
    }

    func close() async {}
}

final class SendableFlag: @unchecked Sendable {
    var value = false
}

actor LogRecorder {
    private var messages: [String] = []

    func append(_ message: String) {
        messages.append(message)
    }

    func snapshot() -> [String] {
        messages
    }
}

class RecordingSpeakerSink: RealtimeAgentSpeakerSink, @unchecked Sendable {
    var chunks: [RealtimeAgentStreamChunk] = []
    var preparedFormats: [RealtimeAgentSpeakerFormat] = []
    var cancelCalled = false

    func prepare(format: RealtimeAgentSpeakerFormat) async throws {
        preparedFormats.append(format)
    }

    func write(_ chunk: RealtimeAgentStreamChunk) async throws {
        chunks.append(chunk)
    }

    func drain() async throws {}

    func cancel() async {
        cancelCalled = true
    }
}

final class SlowPrepareSpeakerSink: RecordingSpeakerSink, @unchecked Sendable {
    var prepareDelayNanoseconds: UInt64 = 80_000_000

    override func prepare(format: RealtimeAgentSpeakerFormat) async throws {
        try await Task.sleep(nanoseconds: prepareDelayNanoseconds)
        try await super.prepare(format: format)
    }
}

final class FailingPrepareSpeakerSink: RecordingSpeakerSink, @unchecked Sendable {
    override func prepare(format _: RealtimeAgentSpeakerFormat) async throws {
        throw RealtimeAgentDeviceError.transportClosed("prepare failed for test")
    }
}

final class FailingDrainSpeakerSink: RecordingSpeakerSink, @unchecked Sendable {
    override func drain() async throws {
        throw RealtimeAgentDeviceError.transportClosed("drain failed for test")
    }
}

final class BlockingDrainSpeakerSink: RecordingSpeakerSink, @unchecked Sendable {
    var drainStarted = false

    override func drain() async throws {
        drainStarted = true
        while !Task.isCancelled {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        throw CancellationError()
    }
}

func makeClient(
    transport: MockRealtimeAgentTransport,
    configuration: RealtimeAgentClientConfiguration = .default,
    audioInput: AudioInput = .disabled(),
    camera: Camera = .disabled(),
    speaker: Speaker = .disabled()
) -> RealtimeAgentDeviceClient {
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001")
        .user("user-001")
        .applying(audioInput: audioInput, camera: camera, speaker: speaker)
    return RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        configuration: configuration,
        transport: transport,
        audioInput: audioInput,
        camera: camera,
        speaker: speaker
    )
}

func eventJSON(
    _ eventName: String,
    payload: [String: Any] = [:],
    sessionID: String? = nil,
    streamID: String? = nil,
    streamType: String? = nil
) throws -> String {
    try RealtimeAgentEvent(
        eventName: eventName,
        userID: "user-001",
        producerID: "server-main",
        payload: payload,
        sessionID: sessionID,
        streamID: streamID,
        streamType: streamType,
        timestampMS: 1
    ).jsonString
}

func waitForSentEvent(
    _ eventName: String,
    transport: MockRealtimeAgentTransport,
    timeoutNanoseconds: UInt64 = 1_000_000_000
) async throws -> [RealtimeAgentEvent] {
    let startedAt = DispatchTime.now().uptimeNanoseconds
    while DispatchTime.now().uptimeNanoseconds - startedAt < timeoutNanoseconds {
        let events = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0) }
        if events.contains(where: { $0.eventName == eventName }) {
            return events
        }
        try await Task.sleep(nanoseconds: 10_000_000)
    }
    throw RealtimeAgentDeviceError.transportClosed("timeout waiting for sent event: \(eventName)")
}

@Test func streamCodecReadsGoldenFixture() throws {
    let chunkURL = Bundle.module.url(forResource: "rgb-chunk", withExtension: "bin")!
    let data = try Data(contentsOf: chunkURL)
    let chunk = try RealtimeAgentStreamChunkCodec.decode(data)
    #expect(chunk.streamID == "stream_rgb_001")
    #expect(chunk.streamType == "sensor.rgb")
    #expect(String(data: chunk.payload, encoding: .utf8) == "abc")
}

@Test func eventRoundTripsCommandFixture() throws {
    let event = RealtimeAgentEvent(
        eventName: "command.completed",
        userID: "user-001",
        producerID: "dev-ios-001",
        payload: ["command_id": "cmd-001", "result": ["status": "ok"]],
        sessionID: "session-001",
        eventID: "evt-command-completed",
        timestampMS: 1
    )
    let decoded = try RealtimeAgentEvent(jsonString: event.jsonString)
    #expect(decoded.eventName == "command.completed")
    #expect(decoded.payload["command_id"] as? String == "cmd-001")
    #expect(decoded.sessionID == "session-001")
}

@Test func streamCodecRejectsPayloadSizeMismatch() throws {
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-001",
        streamID: "stream-001",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("abc".utf8),
        codec: "pcm16le",
        sampleRate: 16000,
        channels: 1,
        durationMS: 20
    )
    var data = try RealtimeAgentStreamChunkCodec.encode(chunk)
    data.removeLast()
    #expect(throws: RealtimeAgentDeviceError.self) {
        _ = try RealtimeAgentStreamChunkCodec.decode(data)
    }
}

@Test func clientRegistersAndSendsHeartbeatOverTransport() async throws {
    let transport = MockRealtimeAgentTransport()
    transport.controlInbox = [
        try eventJSON(
            "control.device.registered",
            payload: ["device_id": "dev-ios-001", "connection_id": "conn-001", "heartbeat_interval_seconds": 60]
        ),
    ]
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001")
        .user("user-001")
        .named("iPhone")
        .role("phone")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )

    let registered = try await client.register(startHeartbeat: false)

    #expect(registered.eventName == "control.device.registered")
    #expect(transport.controlConnectedURL?.absoluteString == "ws://127.0.0.1:8765/ws/control")
    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    #expect(sent.eventName == "control.device.register.requested")
    #expect((sent.payload["properties"] as? [String: Any])?["device_role"] as? String == "phone")
    #expect(client.diagnosticsSnapshot().registered)
}

@Test func clientRegistrationIncludesCustomCallbackSubscriptions() async throws {
    let transport = MockRealtimeAgentTransport()
    transport.controlInbox = [
        try eventJSON(
            "control.device.registered",
            payload: ["device_id": "dev-ios-001", "connection_id": "conn-001", "heartbeat_interval_seconds": 60]
        ),
    ]
    let client = makeClient(transport: transport)
    client.onCustomCommand("haptic.vibrate") { _ in }
    client.onEvent("custom.navigation.route.updated") { _ in }

    _ = try await client.register(startHeartbeat: false)

    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    let properties = sent.payload["properties"] as? [String: Any]
    #expect(properties?["realtime_agent.custom_command_consumer"] as? Bool == true)
    #expect(properties?["realtime_agent.custom_commands"] as? [String] == ["haptic.vibrate"])
    #expect(properties?["realtime_agent.custom_event_subscriptions"] as? [String] == ["custom.navigation.route.updated"])
}

@Test func standardClientBuildsProfileFromEnabledHardware() async throws {
    let client = try DeviceClient(
        serverURL: "http://127.0.0.1:8765",
        deviceID: "dev-ios-001",
        userID: "user-001",
        name: "iPhone",
        audioInput: .enabled(),
        camera: .enabled(frequencyHz: 2, sampleCount: 3),
        speaker: .enabled(buffer: .default)
    )

    let payload = client.device.registrationPayload
    let properties = payload["properties"] as? [String: Any]
    #expect(properties?["realtime_agent.audio_input"] as? String == "sensor.mic")
    #expect(properties?["realtime_agent.audio_output"] as? String == "actuator.speaker")
    let supports = payload["supports"] as? [String: Any]
    let sensors = supports?["sensors"] as? [[String: Any]]
    #expect(sensors?.first?["type"] as? String == "rgb")
    #expect(sensors?.first?["modes"] as? [String] == ["single"])
    #expect((sensors?.first?["default"] as? [String: Any])?["sample_count"] as? Int == 1)
    #expect((sensors?.first?["default"] as? [String: Any])?["frequency_hz"] == nil)
}

@Test func enabledHardwareUsesDefaultAVFoundationAdaptersWhenAvailable() throws {
    #if canImport(AVFoundation)
    #expect(AudioInput.enabled().source != nil)
    #expect(Camera.enabled().source != nil)
    #expect(Speaker.enabled().sink != nil)
    #endif
}

struct ArrayMicrophoneSource: RealtimeAgentMicrophoneSource {
    var chunks: [Data]

    func streamPCM16LE(configuration _: RealtimeAgentMicrophoneConfiguration) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            for chunk in chunks {
                continuation.yield(chunk)
            }
            continuation.finish()
        }
    }
}

struct DelayedMicrophoneSource: RealtimeAgentMicrophoneSource {
    var chunks: [Data]
    var delayNanoseconds: UInt64

    func streamPCM16LE(configuration _: RealtimeAgentMicrophoneConfiguration) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            Task {
                for chunk in chunks {
                    try await Task.sleep(nanoseconds: delayNanoseconds)
                    continuation.yield(chunk)
                }
                continuation.finish()
            }
        }
    }
}

@Test func audioSessionOpenStartsEnabledMicrophoneSource() async throws {
    let transport = MockRealtimeAgentTransport()
    let source = ArrayMicrophoneSource(chunks: [Data(repeating: 1, count: 640), Data(repeating: 2, count: 640)])
    let client = makeClient(transport: transport, audioInput: .enabled(source: source))
    let event = RealtimeAgentEvent(
        eventName: "control.audio_session.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [:],
        sessionID: "session-001"
    )

    #expect(try await client.dispatchEvent(event))
    try await Task.sleep(nanoseconds: 30_000_000)

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["control.audio_session.opened"])
    #expect(transport.sentStreamData.count == 2)
    #expect(transport.streamConnectedURLs[.audioInput]?.absoluteString == "ws://127.0.0.1:8765/ws/stream/audio/input?device_id=dev-ios-001")
    let chunk = try RealtimeAgentStreamChunkCodec.decode(transport.sentStreamData[0])
    #expect(chunk.streamType == "sensor.mic")
    #expect(chunk.sessionID == "session-001")
}

@Test func clientDispatchesCustomCommandRequested() async throws {
    let transport = MockRealtimeAgentTransport()
    let client = makeClient(transport: transport)
    client.onCustomCommand("haptic.vibrate") { context in
        let durationMS = context.payload["duration_ms"] as? Int ?? 0
        try await context.emit("custom.haptic.vibrate.done", ["duration_ms": durationMS])
    }
    let event = RealtimeAgentEvent(
        eventName: "custom.command.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["command": "haptic.vibrate", "payload": ["duration_ms": 120]],
        sessionID: "dev-ios-001"
    )

    #expect(try await client.dispatchEvent(event))
    try await Task.sleep(nanoseconds: 30_000_000)

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["custom.haptic.vibrate.done"])
    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    #expect(sent.payload["duration_ms"] as? Int == 120)
}

@Test func clientDispatchesCustomEventHandler() async throws {
    let transport = MockRealtimeAgentTransport()
    let client = makeClient(transport: transport)
    client.onEvent("custom.navigation.route.updated") { event in
        try await client.sendEvent(name: "custom.navigation.route.applied", payload: event.payload)
    }
    let event = RealtimeAgentEvent(
        eventName: "custom.navigation.route.updated",
        userID: "user-001",
        producerID: "server-main",
        payload: ["route_id": "route-001"],
        sessionID: "dev-ios-001"
    )

    #expect(try await client.dispatchEvent(event))

    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    #expect(sent.eventName == "custom.navigation.route.applied")
    #expect(sent.payload["route_id"] as? String == "route-001")
}

@Test func standardEventsDoNotTriggerOnEventHandler() async throws {
    let transport = MockRealtimeAgentTransport()
    let client = makeClient(transport: transport)
    let customEventCalled = SendableFlag()
    client.onEvent("custom.test.event") { _ in
        customEventCalled.value = true
    }
    let event = RealtimeAgentEvent(
        eventName: "stream.output.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchEvent(event) == false)
    #expect(customEventCalled.value == false)
}

@Test func clientDispatchesStreamOpenAndUploadsChunk() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    client.onStreamOpen("sensor.rgb") { request in
        try await request.opened(["request_id": request.requestID ?? ""])
        try await request.write(
            Data("abc".utf8),
            codec: "jpeg",
            sampleRate: 1,
            channels: 1,
            durationMS: 0,
            final: true,
            metadata: ["request_id": request.requestID ?? ""]
        )
        try await request.closed(reason: "test_done")
    }
    let event = RealtimeAgentEvent(
        eventName: "stream.control.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "sensor.rgb", "request_id": "req-001"],
        sessionID: "dev-ios-001",
        streamID: "stream-rgb-001",
        streamType: "sensor.rgb"
    )

    #expect(try await client.dispatchEvent(event))
    try await Task.sleep(nanoseconds: 30_000_000)

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.input.opened", "stream.input.closed"])
    #expect(transport.streamConnectedURLs[.visualInput]?.absoluteString == "ws://127.0.0.1:8765/ws/stream/visual/input?device_id=dev-ios-001")
    let chunk = try RealtimeAgentStreamChunkCodec.decode(transport.sentStreamData[0])
    #expect(chunk.streamType == "sensor.rgb")
    #expect(String(data: chunk.payload, encoding: .utf8) == "abc")
}

@Test func outputSessionSendsLifecycleEvents() async throws {
    let transport = MockRealtimeAgentTransport()
    let client = makeClient(transport: transport, speaker: .enabled())
    let open = RealtimeAgentEvent(
        eventName: "stream.output.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker"
    )
    let close = RealtimeAgentEvent(
        eventName: "stream.output.close.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchEvent(open))
    #expect(try await client.dispatchEvent(close))

    let events = try await waitForSentEvent("stream.output.closed", transport: transport)
    let names = events.map(\.eventName)
    #expect(names == ["stream.output.ready", "stream.output.closed"])
}

@Test func outputSessionSendsCancelEvent() async throws {
    let transport = MockRealtimeAgentTransport()
    let client = makeClient(transport: transport, speaker: .enabled())
    let cancel = RealtimeAgentEvent(
        eventName: "stream.output.cancel.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchEvent(cancel))

    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    #expect(sent.eventName == "stream.output.cancelled")
    #expect(sent.payload["reason"] as? String == "cancel_requested")
}

@Test func speakerSinkReceivesOutputChunkThroughSDKBuffer() async throws {
    // 测试目标：speaker chunk 先于 output open 控制事件到达时，SDK 必须使用 chunk 自带格式准备播放器。
    // 测试方法：直接分发一帧 24k PCM speaker chunk，不先分发 stream.output.open.requested。
    // 预期结果：测试 sink 收到 chunk，且 prepare 使用 24k 采样率，避免真机按 16k 播放导致低速低频。
    let transport = MockRealtimeAgentTransport()
    let sink = RecordingSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )

    #expect(try await client.dispatchStreamChunk(chunk))
    try await Task.sleep(nanoseconds: 40_000_000)

    #expect(sink.chunks.first?.streamID == "stream-speaker-001")
    #expect(sink.preparedFormats.first?.sampleRate == 24000)
    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.output.ready", "stream.output.started"])
}

@Test func speakerDebugLogReportsPlaybackLifecycle() async throws {
    // 测试目标：端侧 App 能通过 SDK debug 回调看到 speaker 播放链路状态。
    // 测试方法：注册 onDebugLog 后分发一帧达到起播水位的 speaker chunk。
    // 预期结果：日志中包含 prepare、started 和 drain tick，便于真机排查无声或卡顿。
    let transport = MockRealtimeAgentTransport()
    let sink = RecordingSpeakerSink()
    let recorder = LogRecorder()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    client.onDebugLog { message in
        await recorder.append(message)
    }
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-debug",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )

    #expect(try await client.dispatchStreamChunk(chunk))
    try await Task.sleep(nanoseconds: 50_000_000)

    let logs = await recorder.snapshot()
    #expect(logs.contains { $0.contains("speaker prepare stream=stream-speaker-debug") })
    #expect(logs.contains { $0.contains("speaker action started stream=stream-speaker-debug") })
    #expect(logs.contains { $0.contains("speaker drain tick stream=stream-speaker-debug") })
}

@Test func streamReceiveLoopReconnectsAndKeepsSpeakerOutputAlive() async throws {
    // 测试目标：stream WebSocket 中途断开后，SDK 不能永久停止接收 speaker 音频。
    // 测试方法：注册后让 stream receive 先抛一次断开错误，再返回一帧 speaker chunk。
    // 预期结果：SDK 会重新连接 stream，并把断线后的 speaker chunk 写入播放 sink。
    let transport = MockRealtimeAgentTransport()
    transport.controlInbox = [
        try eventJSON(
            "control.device.registered",
            payload: ["device_id": "dev-ios-001", "connection_id": "conn-001", "heartbeat_interval_seconds": 60]
        ),
    ]
    let sink = RecordingSpeakerSink()
    let recorder = LogRecorder()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    client.onDebugLog { message in
        await recorder.append(message)
    }
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-reconnect",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    transport.streamReceiveResults = [
        .failure(RealtimeAgentDeviceError.transportClosed("test stream drop")),
        .success(try RealtimeAgentStreamChunkCodec.encode(chunk)),
    ]

    try await client.connectAndRegister(startHeartbeat: false)
    try await Task.sleep(nanoseconds: 300_000_000)
    await client.close()

    #expect(transport.streamConnectCount >= 2)
    #expect(sink.chunks.first?.streamID == "stream-speaker-reconnect")
    #expect(sink.preparedFormats.first?.sampleRate == 24000)
    let logs = await recorder.snapshot()
    #expect(logs.contains { $0.contains("stream receive error attempt=1") })
    #expect(logs.contains { $0.contains("speaker chunk received stream=stream-speaker-reconnect") })
}

@Test func audioSessionOpenRestartsStoppedSpeakerReceiveLoop() async throws {
    // 测试目标：audio output 接收循环曾经因断线停止后，下一次实时音频会话打开必须重新接收 speaker chunk。
    // 测试方法：用禁用重连策略让首次 receive 抛错并停止循环，再分发 audio_session.open.requested 和一帧 speaker chunk。
    // 预期结果：SDK 在新会话打开时重启 audio output 接收循环，speaker sink 收到 chunk 并发送 stream.output.started。
    let transport = MockRealtimeAgentTransport()
    transport.controlInbox = [
        try eventJSON(
            "control.device.registered",
            payload: ["device_id": "dev-ios-001", "connection_id": "conn-001", "heartbeat_interval_seconds": 60]
        ),
    ]
    let sink = RecordingSpeakerSink()
    let client = makeClient(
        transport: transport,
        configuration: RealtimeAgentClientConfiguration(reconnectPolicy: .disabled),
        audioInput: .enabled(source: ArrayMicrophoneSource(chunks: [])),
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-new-session",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    let openAudio = RealtimeAgentEvent(
        eventName: "control.audio_session.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [:],
        sessionID: "dev-ios-001"
    )
    transport.streamReceiveResults = [
        .failure(RealtimeAgentDeviceError.transportClosed("test stream receive stopped")),
    ]

    try await client.connectAndRegister(startHeartbeat: false)
    try await Task.sleep(nanoseconds: 80_000_000)
    transport.streamInboxByChannel[.audioOutput] = [try RealtimeAgentStreamChunkCodec.encode(chunk)]
    #expect(try await client.dispatchEvent(openAudio))
    _ = try await waitForSentEvent("stream.output.started", transport: transport)
    await client.close()

    #expect(sink.chunks.first?.streamID == "stream-speaker-new-session")
}

@Test func outputSessionCreationIsSafeAcrossControlAndStreamTasks() async throws {
    // 测试目标：control 事件和 stream chunk 并发到达时，共享 output session 状态不能发生字典并发访问崩溃。
    // 测试方法：并发分发同一个 speaker stream 的 open 控制事件和首个 speaker chunk。
    // 预期结果：SDK 能稳定复用同一个 output session，speaker sink 收到音频并发送 started 回执。
    let transport = MockRealtimeAgentTransport()
    let sink = RecordingSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let open = RealtimeAgentEvent(
        eventName: "stream.output.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-concurrent-session",
        streamType: "actuator.speaker"
    )
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-concurrent-session",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )

    async let openHandled = client.dispatchEvent(open)
    async let chunkHandled = client.dispatchStreamChunk(chunk)

    #expect(try await openHandled)
    #expect(try await chunkHandled)
    _ = try await waitForSentEvent("stream.output.started", transport: transport)
    #expect(sink.chunks.first?.streamID == "stream-speaker-concurrent-session")
}

@Test func outputFinishWaitsForInFlightSpeakerChunkAndIgnoresTooLateChunks() async throws {
    // 测试目标：finish 控制事件早于 stream chunk 处理完成时，SDK 不能漏播已在处理中的音频，也不能让更晚到达的 chunk 重新进入播放队列。
    // 测试方法：用慢 prepare 模拟真机播放器准备耗时，并发处理首个 chunk 与 finish 事件，finish 完成后再投递迟到 chunk。
    // 预期结果：首个 chunk 被写入 sink；finish 完成后的迟到 chunk 被忽略，不会再次进入播放队列。
    let transport = MockRealtimeAgentTransport()
    let sink = SlowPrepareSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let firstChunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-finish-race",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm0".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    let finish = RealtimeAgentEvent(
        eventName: "stream.output.finish.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-finish-race",
        streamType: "actuator.speaker"
    )
    let lateChunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-finish-race",
        streamType: "actuator.speaker",
        seq: 1,
        payload: Data("pcm1".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )

    async let firstHandled = client.dispatchStreamChunk(firstChunk)
    try await Task.sleep(nanoseconds: 10_000_000)
    #expect(try await client.dispatchEvent(finish))
    #expect(try await firstHandled)
    #expect(sink.chunks.map(\.seq) == [0])
    _ = try await waitForSentEvent("stream.output.closed", transport: transport)

    #expect(try await client.dispatchStreamChunk(lateChunk) == false)
    #expect(sink.chunks.map(\.seq) == [0])
}

@Test func outputFinishWaitsForExpectedLastSeqAcrossControlAndStreamRace() async throws {
    // 测试目标：finish 控制事件先于最后一帧 stream chunk 到达时，SDK 不能提前关闭本轮播放。
    // 测试方法：先处理 seq=0，再让携带 output_last_seq=1 的 finish 进入等待，随后补到 seq=1。
    // 预期结果：seq=1 仍会进入 speaker sink，且 SDK 最后再发送 stream.output.closed。
    let transport = MockRealtimeAgentTransport()
    let sink = RecordingSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let firstChunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-ordered-finish",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm0".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    let secondChunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-ordered-finish",
        streamType: "actuator.speaker",
        seq: 1,
        payload: Data("pcm1".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    let finish = RealtimeAgentEvent(
        eventName: "stream.output.finish.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [
            "stream_type": "actuator.speaker",
            "output_chunk_count": 2,
            "output_last_seq": 1,
        ],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-ordered-finish",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchStreamChunk(firstChunk))
    async let finishHandled: Bool = client.dispatchEvent(finish)
    try await Task.sleep(nanoseconds: 450_000_000)
    let namesBeforeLastChunk = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(!namesBeforeLastChunk.contains("stream.output.closed"))

    #expect(try await client.dispatchStreamChunk(secondChunk))
    #expect(try await finishHandled)
    _ = try await waitForSentEvent("stream.output.closed", transport: transport)

    #expect(sink.chunks.map(\.seq) == [0, 1])
    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names.contains("stream.output.closed"))
}

@Test func speakerPrepareFailureReportsOutputFailed() async throws {
    // 测试目标：默认播放器准备失败时，SDK 不能让 control 接收循环直接退出并导致服务端等待 endpoint ack 超时。
    // 测试方法：注入 prepare 必定失败的 speaker sink，然后分发 output open 控制事件。
    // 预期结果：SDK 发送 stream.output.failed，错误原因可从控制事件中回传给服务端。
    let transport = MockRealtimeAgentTransport()
    let sink = FailingPrepareSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let open = RealtimeAgentEvent(
        eventName: "stream.output.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-prepare-failed",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchEvent(open))

    let events = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0) }
    #expect(events.map(\.eventName) == ["stream.output.failed"])
    let error = events.first?.payload["error"] as? [String: Any]
    #expect(error?["code"] as? String == "speaker.prepare_failed")
}

@Test func speakerDrainFailureReportsOutputFailed() async throws {
    // 测试目标：播放器 drain 失败或超时时，SDK 必须给服务端明确失败回执，避免输出流长期停在 finish_requested。
    // 测试方法：注入 drain 必定失败的 speaker sink，先处理一帧 speaker chunk，再处理 finish 控制事件。
    // 预期结果：SDK 先发送 stream.output.ready 和 stream.output.started，随后发送 stream.output.failed。
    let transport = MockRealtimeAgentTransport()
    let sink = FailingDrainSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-drain-failed",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm0".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    let finish = RealtimeAgentEvent(
        eventName: "stream.output.finish.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [
            "stream_type": "actuator.speaker",
            "output_chunk_count": 1,
            "output_last_seq": 0,
        ],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-drain-failed",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchStreamChunk(chunk))
    #expect(try await client.dispatchEvent(finish))

    let events = try await waitForSentEvent("stream.output.failed", transport: transport)
    #expect(events.map(\.eventName) == ["stream.output.ready", "stream.output.started", "stream.output.failed"])
    let error = events.last?.payload["error"] as? [String: Any]
    #expect(error?["code"] as? String == "speaker.finish_failed")
}

@Test func outputCancelPreemptsPendingFinishDrain() async throws {
    // 测试目标：播放 drain 尚未结束时，cancel 控制事件必须抢占 finish，避免真机打断晚到。
    // 测试方法：注入永不自然 drain 完成的 speaker sink，先触发 finish，再立即触发 cancel。
    // 预期结果：SDK 先发送 stream.output.cancelled，不发送 stream.output.closed，且 sink 收到 cancel。
    let transport = MockRealtimeAgentTransport()
    let sink = BlockingDrainSpeakerSink()
    let client = makeClient(
        transport: transport,
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-cancel-finish",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm0".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )
    let finish = RealtimeAgentEvent(
        eventName: "stream.output.finish.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [
            "stream_type": "actuator.speaker",
            "output_chunk_count": 1,
            "output_last_seq": 0,
        ],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-cancel-finish",
        streamType: "actuator.speaker"
    )
    let cancel = RealtimeAgentEvent(
        eventName: "stream.output.cancel.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-cancel-finish",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchStreamChunk(chunk))
    #expect(try await client.dispatchEvent(finish))
    try await Task.sleep(nanoseconds: 40_000_000)
    #expect(sink.drainStarted)
    #expect(try await client.dispatchEvent(cancel))
    try await Task.sleep(nanoseconds: 80_000_000)

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names.contains("stream.output.started"))
    #expect(names.contains("stream.output.cancelled"))
    #expect(!names.contains("stream.output.closed"))
    #expect(sink.cancelCalled)
}

@Test func speakerPlaybackDoesNotPauseMicrophoneUpload() async throws {
    // 测试目标：确认播放 speaker 下行期间，SDK 仍持续上传 sensor.mic。
    // 测试方法：打开音频会话后立即分发一帧 speaker chunk，同时让麦克风 source 延迟产出三帧 PCM。
    // 预期结果：三帧麦克风 chunk 都发送到 stream WebSocket，符合全双工和文档约定。
    let transport = MockRealtimeAgentTransport()
    let source = DelayedMicrophoneSource(
        chunks: [
            Data(repeating: 1, count: 640),
            Data(repeating: 2, count: 640),
            Data(repeating: 3, count: 640),
        ],
        delayNanoseconds: 20_000_000
    )
    let sink = RecordingSpeakerSink()
    let client = makeClient(
        transport: transport,
        audioInput: .enabled(source: source),
        speaker: .enabled(buffer: PlaybackBuffer(startWatermarkMS: 20), sink: sink)
    )
    let openAudio = RealtimeAgentEvent(
        eventName: "control.audio_session.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [:],
        sessionID: "session-001"
    )
    let speakerChunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-duplex",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 24000,
        channels: 1,
        durationMS: 20
    )

    #expect(try await client.dispatchEvent(openAudio))
    #expect(try await client.dispatchStreamChunk(speakerChunk))
    try await Task.sleep(nanoseconds: 120_000_000)

    let uploaded = try transport.sentStreamData.map { try RealtimeAgentStreamChunkCodec.decode($0) }
    #expect(uploaded.filter { $0.streamType == "sensor.mic" }.count == 3)
}

@Test func speakerBufferSendsPauseAndResumeByWatermark() async throws {
    let transport = MockRealtimeAgentTransport()
    let buffer = PlaybackBuffer(startWatermarkMS: 20, lowWatermarkMS: 20, highWatermarkMS: 40, maxBufferMS: 80)
    let client = makeClient(transport: transport, speaker: .enabled(buffer: buffer))
    let chunk1 = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm1".utf8),
        codec: "pcm16le",
        sampleRate: 16000,
        channels: 1,
        durationMS: 20
    )
    let chunk2 = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker",
        seq: 1,
        payload: Data("pcm2".utf8),
        codec: "pcm16le",
        sampleRate: 16000,
        channels: 1,
        durationMS: 20
    )
    let close = RealtimeAgentEvent(
        eventName: "stream.output.close.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "actuator.speaker"],
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker"
    )

    #expect(try await client.dispatchStreamChunk(chunk1))
    #expect(try await client.dispatchStreamChunk(chunk2))
    try await Task.sleep(nanoseconds: 80_000_000)
    #expect(try await client.dispatchEvent(close))

    let events = try await waitForSentEvent("stream.output.closed", transport: transport)
    let names = events.map(\.eventName)
    #expect(names == [
        "stream.output.ready",
        "stream.output.started",
        "downstream.pause.requested",
        "downstream.resume.requested",
        "stream.output.closed",
    ])
}

@Test func audioPCMConverterCreatesTwentyMSMonoChunk() throws {
    let samples = [Float](repeating: 0.5, count: 320)
    let data = AudioPCMConverter.pcm16LE(fromFloat32: samples)

    #expect(data.count == AudioPCMConverter.expectedPCM16ByteCount(sampleRate: 16000, channels: 1, durationMS: 20))
    let first = data.withUnsafeBytes { $0.load(as: Int16.self) }
    #expect(Int16(littleEndian: first) == 16_384)
}

@Test func microphoneStreamerUploadsPCMChunk() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    let microphone = MicrophoneStreamer(client: client)

    try await microphone.open()
    try await microphone.sendPCM16LE(Data(repeating: 0, count: 640), final: true)
    try await microphone.close(reason: "test_done")

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.input.opened", "stream.input.closed"])
    #expect(transport.streamConnectedURLs[.audioInput]?.absoluteString == "ws://127.0.0.1:8765/ws/stream/audio/input?device_id=dev-ios-001")
    let chunk = try RealtimeAgentStreamChunkCodec.decode(transport.sentStreamData[0])
    #expect(chunk.streamType == "sensor.mic")
    #expect(chunk.codec == "pcm16le")
    #expect(chunk.payload.count == 640)
}

@Test func cameraUploaderRespondsToRgbRequest() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    let source = ClosureCameraFrameSource {
        Data([0xFF, 0xD8, 0xFF, 0xD9])
    }
    CameraFrameUploader.registerSingleFrameHandler(client: client, source: source)
    let event = RealtimeAgentEvent(
        eventName: "stream.control.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["stream_type": "sensor.rgb", "request_id": "req-001", "capture_reason": "test"],
        sessionID: "dev-ios-001",
        streamID: "stream-rgb-001",
        streamType: "sensor.rgb"
    )

    #expect(try await client.dispatchEvent(event))
    try await Task.sleep(nanoseconds: 30_000_000)

    let opened = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    let format = opened.payload["format"] as? [String: Any]
    #expect(format?["codec"] as? String == "jpeg")
    #expect(format?["sample_rate"] as? Int == 1)
    #expect(format?["channels"] as? Int == 1)
    let chunk = try RealtimeAgentStreamChunkCodec.decode(transport.sentStreamData[0])
    #expect(chunk.streamType == "sensor.rgb")
    #expect(transport.streamConnectedURLs[.visualInput]?.absoluteString == "ws://127.0.0.1:8765/ws/stream/visual/input?device_id=dev-ios-001")
    #expect(chunk.codec == format?["codec"] as? String)
    #expect(chunk.sampleRate == format?["sample_rate"] as? Int)
    #expect(chunk.channels == format?["channels"] as? Int)
    #expect(chunk.metadata["request_id"] as? String == "req-001")
    #expect(chunk.metadata["capture_reason"] as? String == "test")
}

@Test func cameraUploaderTreatsLegacyContinuousRgbRequestAsSingleFrame() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    let source = ClosureCameraFrameSource {
        Data("jpeg".utf8)
    }
    CameraFrameUploader.registerFrameHandler(client: client, source: source, options: CameraFrameUploadOptions(sampleRate: 1))
    let event = RealtimeAgentEvent(
        eventName: "stream.control.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [
            "stream_type": "sensor.rgb",
            "request_id": "req-002",
            "mode": "continuous",
            "frequency_hz": 50,
        ],
        sessionID: "dev-ios-001",
        streamID: "stream-rgb-002",
        streamType: "sensor.rgb"
    )

    #expect(try await client.dispatchEvent(event))
    try await Task.sleep(nanoseconds: 30_000_000)

    #expect(transport.sentStreamData.count == 1)
    let opened = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    let format = opened.payload["format"] as? [String: Any]
    #expect(format?["codec"] as? String == "jpeg")
    #expect(format?["sample_rate"] as? Int == 1)
    #expect(opened.payload["mode"] as? String == "single")
    #expect(opened.payload["sample_count"] as? Int == 1)
    #expect(opened.payload["requested_mode_ignored"] as? String == "continuous")
    let chunks = try transport.sentStreamData.map { try RealtimeAgentStreamChunkCodec.decode($0) }
    #expect(chunks.map(\.seq) == [0])
    #expect(chunks.allSatisfy { $0.final })
    #expect(chunks.last?.metadata["sample_count"] as? Int == 1)
    #expect(chunks.last?.metadata["frequency_hz"] == nil)
    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.input.opened", "stream.input.closed"])
}

@Test func streamCodecRoundTripsChunk() throws {
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-001",
        streamID: "stream-001",
        streamType: "sensor.rgb",
        seq: 0,
        payload: Data("abc".utf8),
        codec: "jpeg",
        sampleRate: 1,
        channels: 1,
        durationMS: 0,
        final: true
    )
    let decoded = try RealtimeAgentStreamChunkCodec.decode(try RealtimeAgentStreamChunkCodec.encode(chunk))
    #expect(decoded.streamID == chunk.streamID)
    #expect(decoded.payload == chunk.payload)
}

@Test func deviceBuildsRegistrationPayload() throws {
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001")
        .user("user-001")
        .named("iPhone")
        .role("phone")
        .clientType("ios-phone")
        .sdkVersion("realtime-agent-ios-reference-0.1.0")
        .auth(["mode": "disabled"])
        .properties(["direct.camera_sink": true])
        .sensorRgb(modes: ["single"], format: "jpeg", frequencyHz: 1)
        .actuatorVibrator()

    let payload = device.registrationPayload
    #expect(payload["device_id"] as? String == "dev-ios-001")
    #expect(payload["client_type"] as? String == "ios-phone")
    #expect(payload["sdk_version"] as? String == "realtime-agent-ios-reference-0.1.0")
    #expect((payload["auth"] as? [String: Any])?["mode"] as? String == "disabled")
    #expect((payload["properties"] as? [String: Any])?["direct.camera_sink"] as? Bool == true)
    let supports = payload["supports"] as? [String: Any]
    #expect((supports?["sensors"] as? [[String: Any]])?.first?["type"] as? String == "rgb")
}
