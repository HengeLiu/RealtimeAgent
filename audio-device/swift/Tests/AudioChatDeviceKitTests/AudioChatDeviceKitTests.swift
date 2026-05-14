import Foundation
import Testing
@testable import AudioChatDeviceKit

@Test func streamCodecReadsGoldenFixture() throws {
    let chunkURL = Bundle.module.url(forResource: "rgb-chunk", withExtension: "bin")!
    let data = try Data(contentsOf: chunkURL)
    let chunk = try AudioChatStreamChunkCodec.decode(data)
    #expect(chunk.streamID == "stream_rgb_001")
    #expect(chunk.streamType == "sensor.rgb")
    #expect(String(data: chunk.payload, encoding: .utf8) == "abc")
}

@Test func streamCodecRoundTripsChunk() throws {
    let chunk = AudioChatStreamChunk(
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
    let decoded = try AudioChatStreamChunkCodec.decode(try AudioChatStreamChunkCodec.encode(chunk))
    #expect(decoded.streamID == chunk.streamID)
    #expect(decoded.payload == chunk.payload)
}

@Test func deviceBuildsRegistrationPayload() throws {
    let device = AudioChatDevice(deviceID: "dev-ios-001")
        .user("user-001")
        .named("iPhone")
        .role("phone")
        .sensorRgb(modes: ["single"], format: "jpeg", frequencyHz: 1)
        .actuatorVibrator()

    let payload = device.registrationPayload
    #expect(payload["device_id"] as? String == "dev-ios-001")
    let supports = payload["supports"] as? [String: Any]
    #expect((supports?["sensors"] as? [[String: Any]])?.first?["type"] as? String == "rgb")
}
