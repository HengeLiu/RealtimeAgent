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
        startWatermarkMS: Int = 120,
        lowWatermarkMS: Int = 300,
        highWatermarkMS: Int = 800,
        maxBufferMS: Int = 1200
    ) {
        self.startWatermarkMS = startWatermarkMS
        self.lowWatermarkMS = lowWatermarkMS
        self.highWatermarkMS = highWatermarkMS
        self.maxBufferMS = maxBufferMS
    }

    public static let `default` = PlaybackBuffer()
}

/// 端侧对话双工能力模式。
///
/// 主要功能：把“是否支持全双工可插话”变成可配置能力，而不是散落在播放、录音和 App 代码中。
public enum ConversationDuplexMode: Sendable, Equatable {
    /// 只处理播放，不承诺播放期间持续上传可用于打断判断的麦克风音频。
    case playbackOnly
    /// 默认模式。SDK 持续上传经过系统 voice processing 的麦克风音频，由 server/provider 判断插话并下发 cancel。
    case fullDuplexServerBargeIn
    /// 本地诊断模式。只用于实验或排障，不作为正式协议打断来源。
    case fullDuplexLocalDiagnostic(speechHoldMS: Int)
}

/// 设备端 SDK 的扬声器能力配置。
///
/// 主要功能：表达 App 是否允许 SDK 自动注册并维护 `actuator.speaker` 下行播放链路。
public struct Speaker: Sendable {
    public var enabled: Bool
    public var buffer: PlaybackBuffer
    public var duplexMode: ConversationDuplexMode
    public var sink: RealtimeAgentSpeakerSink?

    private init(
        enabled: Bool,
        buffer: PlaybackBuffer = .default,
        duplexMode: ConversationDuplexMode = .fullDuplexServerBargeIn,
        sink: RealtimeAgentSpeakerSink? = nil
    ) {
        self.enabled = enabled
        self.buffer = buffer
        self.duplexMode = duplexMode
        self.sink = sink
    }

    public static func disabled() -> Speaker {
        Speaker(enabled: false)
    }

    public static func enabled(
        buffer: PlaybackBuffer = .default,
        duplexMode: ConversationDuplexMode = .fullDuplexServerBargeIn,
        sink: RealtimeAgentSpeakerSink? = nil
    ) -> Speaker {
        Speaker(
            enabled: true,
            buffer: buffer,
            duplexMode: duplexMode,
            sink: sink ?? RealtimeAgentDefaultAdapters.speakerSink()
        )
    }
}

/// 单项硬件权限状态。
public enum HardwarePermissionState: String, Sendable, Equatable {
    case notRequired
    case notDetermined
    case granted
    case denied
    case restricted
    case unavailable
}

/// SDK 申请硬件权限后的汇总结果。
///
/// 主要功能：让 App 只判断能否进入等待状态，不需要重复理解麦克风和相机权限细节。
public struct HardwarePermissionStatus: Sendable, Equatable {
    public var microphone: HardwarePermissionState
    public var camera: HardwarePermissionState

    public init(
        microphone: HardwarePermissionState = .notRequired,
        camera: HardwarePermissionState = .notRequired
    ) {
        self.microphone = microphone
        self.camera = camera
    }

    public var isAuthorized: Bool {
        [microphone, camera].allSatisfy { state in
            state == .notRequired || state == .granted
        }
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
