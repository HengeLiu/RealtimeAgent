import Foundation

/// speaker 播放期间的麦克风上行策略。
///
/// 主要功能：在允许用户打断和抑制外放回采之间做取舍。
public enum MicrophoneDuringSpeakerPlayback: String, Sendable, Equatable {
    /// 播放期间继续上传麦克风，默认用于支持用户边播边说打断。
    case allowInterruptions
    /// 播放期间上传静音 PCM，用于临时诊断或强抑制外放回采。
    case muteDuringSpeakerPlayback
}

/// 麦克风 stream 配置。
///
/// 主要功能：描述 SDK 发送 `sensor.mic` chunk 时使用的 codec、采样率、通道数、分片时长和
/// speaker 播放期间的麦克风上行策略。
public struct RealtimeAgentMicrophoneConfiguration: Sendable, Equatable {
    public var codec: String
    public var sampleRate: Int
    public var channels: Int
    public var chunkMS: Int
    public var streamType: String
    public var microphoneDuringSpeakerPlayback: MicrophoneDuringSpeakerPlayback
    public var speakerPlaybackWarmupMuteMS: Int

    /// 创建麦克风 stream 配置。
    public init(
        codec: String = "pcm16le",
        sampleRate: Int = 16_000,
        channels: Int = 1,
        chunkMS: Int = 20,
        streamType: String = "sensor.mic",
        microphoneDuringSpeakerPlayback: MicrophoneDuringSpeakerPlayback = .allowInterruptions,
        speakerPlaybackWarmupMuteMS: Int = 500
    ) {
        self.codec = codec
        self.sampleRate = sampleRate
        self.channels = channels
        self.chunkMS = chunkMS
        self.streamType = streamType
        self.microphoneDuringSpeakerPlayback = microphoneDuringSpeakerPlayback
        self.speakerPlaybackWarmupMuteMS = max(0, speakerPlaybackWarmupMuteMS)
    }
}

/// 麦克风输入 stream 适配器。
///
/// 主要功能：封装 `sensor.mic` 的 opened、PCM chunk、closed 和 failed 生命周期。
/// 说明：它接收已经转换好的 PCM16LE bytes，真实 AVAudioEngine 采集由 App target 负责。
public final class MicrophoneStreamer: @unchecked Sendable {
    public let configuration: RealtimeAgentMicrophoneConfiguration
    private let client: RealtimeAgentDeviceClient
    private var streamID: String?
    private var sequence = 0

    /// 创建麦克风输入 stream 适配器。
    public init(client: RealtimeAgentDeviceClient, configuration: RealtimeAgentMicrophoneConfiguration = .init()) {
        self.client = client
        self.configuration = configuration
    }

    /// 打开 `sensor.mic` 输入 stream。
    public func open(sessionID: String? = nil, metadata: [String: Any] = [:]) async throws {
        let streamID = RealtimeAgentIDs.make(prefix: "stream_mic")
        self.streamID = streamID
        sequence = 0
        try await client.sendEvent(
            name: "stream.input.opened",
            payload: [
                "stream_type": configuration.streamType,
                "format": [
                    "codec": configuration.codec,
                    "sample_rate": configuration.sampleRate,
                    "channels": configuration.channels,
                    "chunk_ms": configuration.chunkMS,
                ],
                "metadata": metadata,
            ],
            sessionID: sessionID ?? client.deviceID,
            streamID: streamID,
            streamType: configuration.streamType
        )
    }

    /// 发送一帧 PCM16LE 音频。
    public func sendPCM16LE(_ payload: Data, sessionID: String? = nil, final: Bool = false, metadata: [String: Any] = [:]) async throws {
        guard let streamID else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("microphone stream is not opened")
        }
        let chunk = RealtimeAgentStreamChunk(
            userID: client.userID,
            sessionID: sessionID ?? client.deviceID,
            streamID: streamID,
            streamType: configuration.streamType,
            seq: sequence,
            payload: payload,
            codec: configuration.codec,
            sampleRate: configuration.sampleRate,
            channels: configuration.channels,
            durationMS: configuration.chunkMS,
            final: final,
            metadata: metadata
        )
        sequence += 1
        try await client.sendStreamChunk(chunk)
    }

    /// 关闭 `sensor.mic` 输入 stream。
    public func close(sessionID: String? = nil, reason: String = "completed") async throws {
        guard let streamID else { return }
        try await client.sendEvent(
            name: "stream.input.closed",
            payload: ["stream_type": configuration.streamType, "reason": reason],
            sessionID: sessionID ?? client.deviceID,
            streamID: streamID,
            streamType: configuration.streamType
        )
        self.streamID = nil
    }

    /// 回报 `sensor.mic` 输入 stream 失败。
    public func fail(sessionID: String? = nil, code: String, message: String) async throws {
        guard let streamID else { return }
        try await client.sendEvent(
            name: "stream.input.failed",
            payload: ["stream_type": configuration.streamType, "error": ["code": code, "message": message]],
            sessionID: sessionID ?? client.deviceID,
            streamID: streamID,
            streamType: configuration.streamType
        )
        self.streamID = nil
    }
}
