import Foundation

/// 相机 JPEG 帧来源。
///
/// 主要功能：让 App 把真实 AVCaptureSession 或其他图像来源适配成 SDK 可调用的单帧 JPEG。
public protocol RealtimeAgentCameraFrameSource: Sendable {
    /// 捕获一帧 JPEG。
    func captureJPEG() async throws -> Data
}

/// 基于闭包的相机帧来源。
///
/// 主要功能：便于 App 或测试把自定义采集逻辑注入 SDK。
public struct ClosureCameraFrameSource: RealtimeAgentCameraFrameSource {
    private let capture: @Sendable () async throws -> Data

    /// 创建闭包相机帧来源。
    public init(capture: @escaping @Sendable () async throws -> Data) {
        self.capture = capture
    }

    /// 调用注入的闭包捕获 JPEG。
    public func captureJPEG() async throws -> Data {
        try await capture()
    }
}

/// RGB 上传参数。
///
/// 主要功能：描述 `sensor.rgb` chunk 的 codec、帧率语义值、通道数和持续时间。
public struct CameraFrameUploadOptions: Sendable, Equatable {
    public var codec: String
    public var sampleRate: Int
    public var channels: Int
    public var durationMS: Int
    public var sleepBetweenContinuousFrames: Bool

    /// 创建 RGB 上传参数。
    public init(
        codec: String = "jpeg",
        sampleRate: Int = 1,
        channels: Int = 1,
        durationMS: Int = 0,
        sleepBetweenContinuousFrames: Bool = true
    ) {
        self.codec = codec
        self.sampleRate = sampleRate
        self.channels = channels
        self.durationMS = durationMS
        self.sleepBetweenContinuousFrames = sleepBetweenContinuousFrames
    }
}

/// 相机帧上传工具。
///
/// 主要功能：注册 `sensor.rgb` 单帧 handler，把 server 请求转换为 opened、JPEG chunk 和 closed。
public enum CameraFrameUploader {
    /// 注册单帧 RGB handler。
    public static func registerSingleFrameHandler(
        client: RealtimeAgentDeviceClient,
        source: RealtimeAgentCameraFrameSource,
        options: CameraFrameUploadOptions = .init()
    ) {
        registerFrameHandler(client: client, source: source, options: options, defaultSampleCount: 1)
    }

    /// 注册 RGB handler，支持 single 和 continuous 请求。
    ///
    /// 主要功能：根据请求 payload 中的 `sample_count` / `frequency_hz` 连续上传多帧 JPEG。
    public static func registerFrameHandler(
        client: RealtimeAgentDeviceClient,
        source: RealtimeAgentCameraFrameSource,
        options: CameraFrameUploadOptions = .init(),
        defaultSampleCount: Int = 1
    ) {
        client.onStreamOpen("sensor.rgb") { request in
            let sampleCount = max(1, intValue(request.request.payload["sample_count"]) ?? defaultSampleCount)
            let frequencyHz = max(1, intValue(request.request.payload["frequency_hz"]) ?? options.sampleRate)
            let openedPayload: [String: Any] = [
                "request_id": request.requestID ?? "",
                "mode": request.request.payload["mode"] as? String ?? (sampleCount > 1 ? "continuous" : "single"),
                "sample_count": sampleCount,
                "frequency_hz": frequencyHz,
                "format": [
                    "codec": options.codec,
                    "sample_rate": frequencyHz,
                    "channels": options.channels,
                    "chunk_ms": options.durationMS,
                ],
            ]
            try await request.opened(openedPayload)
            for index in 0..<sampleCount {
                let jpeg = try await source.captureJPEG()
                var metadata = metadata(from: request)
                metadata["sample_index"] = index
                metadata["sample_count"] = sampleCount
                metadata["frequency_hz"] = frequencyHz
                try await request.write(
                    jpeg,
                    codec: options.codec,
                    sampleRate: frequencyHz,
                    channels: options.channels,
                    durationMS: options.durationMS,
                    final: index == sampleCount - 1,
                    metadata: metadata
                )
                if options.sleepBetweenContinuousFrames && index < sampleCount - 1 {
                    try await Task.sleep(nanoseconds: UInt64(1_000_000_000 / frequencyHz))
                }
            }
            try await request.closed(reason: "camera_frame_uploaded")
        }
    }

    private static func metadata(from request: RealtimeAgentInputStreamRequest) -> [String: Any] {
        var metadata: [String: Any] = [:]
        if let requestID = request.requestID {
            metadata["request_id"] = requestID
        }
        if let turnID = request.request.payload["turn_id"] {
            metadata["turn_id"] = turnID
        }
        if let captureReason = request.request.payload["capture_reason"] {
            metadata["capture_reason"] = captureReason
        }
        return metadata
    }

    private static func intValue(_ value: Any?) -> Int? {
        if let value = value as? Int {
            return value
        }
        if let value = value as? Double {
            return Int(value)
        }
        if let value = value as? String {
            return Int(value)
        }
        return nil
    }
}
