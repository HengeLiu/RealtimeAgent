import Foundation

/// 设备端 SDK 的麦克风能力配置。
///
/// 主要功能：表达 App 是否允许 SDK 自动注册并维护 `sensor.mic` 上行链路。
public struct AudioInput: Sendable {
    public var enabled: Bool
    public var configuration: RealtimeAgentMicrophoneConfiguration
    public var source: RealtimeAgentMicrophoneSource?

    private init(
        enabled: Bool,
        configuration: RealtimeAgentMicrophoneConfiguration = .init(),
        source: RealtimeAgentMicrophoneSource? = nil
    ) {
        self.enabled = enabled
        self.configuration = configuration
        self.source = source
    }

    public static func disabled() -> AudioInput {
        AudioInput(enabled: false)
    }

    public static func enabled(
        configuration: RealtimeAgentMicrophoneConfiguration = .init(),
        source: RealtimeAgentMicrophoneSource? = nil
    ) -> AudioInput {
        AudioInput(enabled: true, configuration: configuration, source: source ?? RealtimeAgentDefaultAdapters.microphoneSource())
    }
}

/// 设备端 SDK 的相机能力配置。
///
/// 主要功能：表达 App 是否允许 SDK 自动注册并维护请求驱动的 `sensor.rgb` 单帧输入链路。
public struct Camera: Sendable {
    public var enabled: Bool
    public var modes: [String]
    public var format: String
    public var frequencyHz: Double
    public var sampleCount: Int
    public var source: RealtimeAgentCameraFrameSource?

    private init(
        enabled: Bool,
        modes: [String] = ["single"],
        format: String = "jpeg",
        frequencyHz: Double = 1,
        sampleCount: Int = 1,
        source: RealtimeAgentCameraFrameSource? = nil
    ) {
        self.enabled = enabled
        self.modes = modes
        self.format = format
        self.frequencyHz = frequencyHz
        self.sampleCount = sampleCount
        self.source = source
    }

    public static func disabled() -> Camera {
        Camera(enabled: false)
    }

    public static func enabled(
        modes: [String] = ["single"],
        format: String = "jpeg",
        frequencyHz: Double = 1,
        sampleCount: Int = 1,
        source: RealtimeAgentCameraFrameSource? = nil
    ) -> Camera {
        Camera(
            enabled: true,
            modes: modes,
            format: format,
            frequencyHz: frequencyHz,
            sampleCount: sampleCount,
            source: source ?? RealtimeAgentDefaultAdapters.cameraFrameSource()
        )
    }
}

/// SDK 内置 speaker 播放 buffer 的水位线配置。
///
/// 主要功能：让 App 只配置 buffer 大小，由 SDK 决定暂停或恢复接收 server 下行音频。
public struct PlaybackBuffer: Sendable, Equatable {
    public var startWatermarkMS: Int
    public var lowWatermarkMS: Int
    public var highWatermarkMS: Int
    public var maxBufferMS: Int

    public init(
        startWatermarkMS: Int = 600,
        lowWatermarkMS: Int = 3000,
        highWatermarkMS: Int = 12000,
        maxBufferMS: Int = 20000
    ) {
        self.startWatermarkMS = startWatermarkMS
        self.lowWatermarkMS = lowWatermarkMS
        self.highWatermarkMS = highWatermarkMS
        self.maxBufferMS = maxBufferMS
    }

    public static let `default` = PlaybackBuffer()
}

/// 设备端 SDK 的扬声器能力配置。
///
/// 主要功能：表达 App 是否允许 SDK 自动注册并维护 `actuator.speaker` 下行播放链路。
public struct Speaker: Sendable {
    public var enabled: Bool
    public var buffer: PlaybackBuffer
    public var sink: RealtimeAgentSpeakerSink?

    private init(enabled: Bool, buffer: PlaybackBuffer = .default, sink: RealtimeAgentSpeakerSink? = nil) {
        self.enabled = enabled
        self.buffer = buffer
        self.sink = sink
    }

    public static func disabled() -> Speaker {
        Speaker(enabled: false)
    }

    public static func enabled(buffer: PlaybackBuffer = .default, sink: RealtimeAgentSpeakerSink? = nil) -> Speaker {
        Speaker(enabled: true, buffer: buffer, sink: sink ?? RealtimeAgentDefaultAdapters.speakerSink())
    }
}

/// App 注入给 SDK 的麦克风数据来源。
///
/// 主要功能：把真实系统麦克风、测试音频或文件回放适配成 SDK 可消费的 PCM chunk。
public protocol RealtimeAgentMicrophoneSource: Sendable {
    func streamPCM16LE(configuration: RealtimeAgentMicrophoneConfiguration) -> AsyncThrowingStream<Data, Error>
}

/// App 注入给 SDK 的扬声器输出目标。
///
/// 主要功能：把 SDK playback buffer drain 出来的音频 chunk 写入真实 speaker 或测试 sink。
public protocol RealtimeAgentSpeakerSink: Sendable {
    func prepare(format: RealtimeAgentSpeakerFormat) async throws
    func write(_ chunk: RealtimeAgentStreamChunk) async throws
    func drain() async throws
    func cancel() async
}

/// speaker sink 可选诊断接口。
///
/// 主要功能：让真实音频适配器把系统音频会话、播放器和最近一次准备耗时暴露给 SDK 调试日志。
public protocol RealtimeAgentSpeakerSinkDiagnostics: Sendable {
    func diagnosticSummary() async -> String
}

/// speaker 输出格式。
public struct RealtimeAgentSpeakerFormat: Sendable, Equatable {
    public var codec: String
    public var sampleRate: Int
    public var channels: Int

    public init(codec: String = "pcm16le", sampleRate: Int = 16_000, channels: Int = 1) {
        self.codec = codec
        self.sampleRate = sampleRate
        self.channels = channels
    }
}

/// 默认空 speaker sink。
///
/// 主要功能：在没有注入真实播放器时仍能维护协议状态机和水位线，便于模拟器与契约测试。
public struct RealtimeAgentNoopSpeakerSink: RealtimeAgentSpeakerSink {
    public init() {}

    public func prepare(format _: RealtimeAgentSpeakerFormat) async throws {}
    public func write(_: RealtimeAgentStreamChunk) async throws {}
    public func drain() async throws {}
    public func cancel() async {}
}

enum RealtimeAgentDefaultAdapters {
    static func microphoneSource() -> RealtimeAgentMicrophoneSource? {
        #if canImport(AVFoundation)
        RealtimeAgentDefaultMicrophoneSource()
        #else
        nil
        #endif
    }

    static func cameraFrameSource() -> RealtimeAgentCameraFrameSource? {
        #if canImport(AVFoundation)
        RealtimeAgentDefaultCameraFrameSource()
        #else
        nil
        #endif
    }

    static func speakerSink() -> RealtimeAgentSpeakerSink? {
        #if canImport(AVFoundation)
        RealtimeAgentDefaultSpeakerSink()
        #else
        nil
        #endif
    }
}

/// Swift SDK 对外推荐入口名称。
public typealias DeviceClient = RealtimeAgentDeviceClient
