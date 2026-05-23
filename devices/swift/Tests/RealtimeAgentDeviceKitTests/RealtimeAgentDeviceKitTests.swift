import Foundation
import Testing
@testable import RealtimeAgentDeviceKit

final class MockRealtimeAgentTransport: RealtimeAgentWebSocketTransport, @unchecked Sendable {
    var controlConnectedURL: URL?
    var streamConnectedURL: URL?
    var sentControlTexts: [String] = []
    var controlInbox: [String] = []
    var sentStreamData: [Data] = []
    var streamInbox: [Data] = []

    func connectControl(url: URL) async throws {
        controlConnectedURL = url
    }

    func connectStream(url: URL) async throws {
        streamConnectedURL = url
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

    func sendStream(data: Data) async throws {
        sentStreamData.append(data)
    }

    func receiveStream() async throws -> Data {
        guard !streamInbox.isEmpty else {
            throw RealtimeAgentDeviceError.transportClosed("empty stream inbox")
        }
        return streamInbox.removeFirst()
    }

    func close() async {}
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

@Test func clientDispatchesCommandRequested() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    client.onCommand("phone.scan_object") { command in
        try await command.accepted(["state": "started"])
        try await command.progress(["progress": 1.0])
        try await command.completed(["result": ["status": "ok"]])
    }
    let event = RealtimeAgentEvent(
        eventName: "command.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["command": "phone.scan_object", "command_id": "cmd-001"],
        sessionID: "dev-ios-001"
    )

    #expect(try await client.dispatchEvent(event))

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["command.accepted", "command.progress", "command.completed"])
}

@Test func clientDispatchesAnyCommandHandler() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    client.onAnyCommand { command in
        try await command.completed(["result": ["handled": true]])
    }
    let event = RealtimeAgentEvent(
        eventName: "command.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: ["command": "phone.dynamic_task", "command_id": "cmd-002"],
        sessionID: "dev-ios-001"
    )

    #expect(try await client.dispatchEvent(event))

    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    #expect(sent.eventName == "command.completed")
    #expect(sent.payload["command"] as? String == "phone.dynamic_task")
}

@Test func clientDispatchesCustomControlEventHandler() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    client.onEvent("control.audio_session.close.requested") { event in
        try await client.sendEvent(
            name: "control.audio_session.closed",
            payload: ["reason": "test_closed"],
            sessionID: event.sessionID
        )
    }
    let event = RealtimeAgentEvent(
        eventName: "control.audio_session.close.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [:],
        sessionID: "session-001"
    )

    #expect(try await client.dispatchEvent(event))

    let sent = try RealtimeAgentEvent(jsonString: transport.sentControlTexts[0])
    #expect(sent.eventName == "control.audio_session.closed")
    #expect(sent.sessionID == "session-001")
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

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.input.opened", "stream.input.closed"])
    #expect(transport.streamConnectedURL?.absoluteString == "ws://127.0.0.1:8765/ws/stream?device_id=dev-ios-001")
    let chunk = try RealtimeAgentStreamChunkCodec.decode(transport.sentStreamData[0])
    #expect(chunk.streamType == "sensor.rgb")
    #expect(String(data: chunk.payload, encoding: .utf8) == "abc")
}

@Test func outputSessionSendsLifecycleEvents() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
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

    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.output.finished", "stream.output.closed"])
}

@Test func outputSessionSendsCancelEvent() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
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

@Test func clientDispatchesOutputChunkToHandler() async throws {
    let transport = MockRealtimeAgentTransport()
    let device = RealtimeAgentDevice(deviceID: "dev-ios-001").user("user-001")
    let client = RealtimeAgentDeviceClient(
        serverURL: URL(string: "http://127.0.0.1:8765")!,
        device: device,
        transport: transport
    )
    let speaker = SpeakerPlayer()
    speaker.bind(to: client)
    let chunk = RealtimeAgentStreamChunk(
        userID: "user-001",
        sessionID: "dev-ios-001",
        streamID: "stream-speaker-001",
        streamType: "actuator.speaker",
        seq: 0,
        payload: Data("pcm".utf8),
        codec: "pcm16le",
        sampleRate: 16000,
        channels: 1,
        durationMS: 20
    )

    #expect(try await client.dispatchStreamChunk(chunk))

    #expect(speaker.bufferedBytes == 3)
    #expect(speaker.chunks.first?.streamID == "stream-speaker-001")
    let names = try transport.sentControlTexts.map { try RealtimeAgentEvent(jsonString: $0).eventName }
    #expect(names == ["stream.output.started"])
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

    let chunk = try RealtimeAgentStreamChunkCodec.decode(transport.sentStreamData[0])
    #expect(chunk.streamType == "sensor.rgb")
    #expect(chunk.metadata["request_id"] as? String == "req-001")
    #expect(chunk.metadata["capture_reason"] as? String == "test")
}

@Test func cameraUploaderRespondsToContinuousRgbRequest() async throws {
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
    CameraFrameUploader.registerFrameHandler(
        client: client,
        source: source,
        options: CameraFrameUploadOptions(sampleRate: 5, sleepBetweenContinuousFrames: false)
    )
    let event = RealtimeAgentEvent(
        eventName: "stream.control.open.requested",
        userID: "user-001",
        producerID: "server-main",
        payload: [
            "stream_type": "sensor.rgb",
            "request_id": "req-002",
            "mode": "continuous",
            "sample_count": 3,
            "frequency_hz": 5,
        ],
        sessionID: "dev-ios-001",
        streamID: "stream-rgb-002",
        streamType: "sensor.rgb"
    )

    #expect(try await client.dispatchEvent(event))

    #expect(transport.sentStreamData.count == 3)
    let chunks = try transport.sentStreamData.map { try RealtimeAgentStreamChunkCodec.decode($0) }
    #expect(chunks.map(\.seq) == [0, 1, 2])
    #expect(chunks.map(\.final) == [false, false, true])
    #expect(chunks.last?.metadata["sample_count"] as? Int == 3)
    #expect(chunks.last?.metadata["frequency_hz"] as? Int == 5)
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
