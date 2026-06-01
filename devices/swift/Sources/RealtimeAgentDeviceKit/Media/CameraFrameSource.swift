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
/// 主要功能：描述 `sensor.rgb` 单帧图片 chunk 的 codec、通道数和持续时间。
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
/// 主要功能：注册 `sensor.rgb` handler，把 server 请求转换为 opened、JPEG chunk 和 closed。
public enum CameraFrameUploader {
    /// 注册单帧 RGB handler。
    public static func registerSingleFrameHandler(
        client: RealtimeAgentDeviceClient,
        source: RealtimeAgentCameraFrameSource,
        options: CameraFrameUploadOptions = .init()
    ) {
        registerFrameHandler(client: client, source: source, options: options, defaultSampleCount: 1)
    }

    /// 注册 RGB handler。
    ///
    /// 主要功能：`mode=single` 时上传一张 `final=true` 图片并关闭逻辑流；
    /// `mode=continuous` 时按 `frequency_hz` 持续上传帧，直到 server 下发 close
    /// 事件取消当前 handler。
    public static func registerFrameHandler(
        client: RealtimeAgentDeviceClient,
        source: RealtimeAgentCameraFrameSource,
        options: CameraFrameUploadOptions = .init(),
        defaultSampleCount _: Int = 1
    ) {
        client.onStreamOpen("sensor.rgb") { request in
            let mode = request.request.payload["mode"] as? String ?? "single"
            let sampleLimit = sampleLimit(from: request)
            let frequencyHz = frequencyHz(from: request, fallback: options.sampleRate)
            let openedPayload: [String: Any] = [
                "request_id": request.requestID ?? "",
                "mode": mode,
                "sample_count": sampleLimit ?? 0,
                "frequency_hz": frequencyHz,
                "format": [
                    "codec": options.codec,
                    "sample_rate": options.sampleRate,
                    "channels": options.channels,
                    "chunk_ms": options.durationMS,
                ],
            ]
            try await request.opened(openedPayload)
            if mode == "continuous" {
                var sampleIndex = 0
                while !Task.isCancelled {
                    if let sampleLimit, sampleIndex >= sampleLimit {
                        break
                    }
                    try await uploadFrame(
                        request: request,
                        source: source,
                        options: options,
                        sampleIndex: sampleIndex,
                        sampleCount: sampleLimit ?? 0,
                        final: false
                    )
                    sampleIndex += 1
                    if options.sleepBetweenContinuousFrames {
                        let seconds = 1.0 / max(0.1, frequencyHz)
                        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                    } else {
                        await Task.yield()
                    }
                }
                try Task.checkCancellation()
                try await request.closed(reason: "camera_continuous_completed")
            } else {
                try await uploadFrame(
                    request: request,
                    source: source,
                    options: options,
                    sampleIndex: 0,
                    sampleCount: 1,
                    final: true
                )
                try await request.closed(reason: "camera_frame_uploaded")
            }
        }
    }

    private static func uploadFrame(
        request: RealtimeAgentInputStreamRequest,
        source: RealtimeAgentCameraFrameSource,
        options: CameraFrameUploadOptions,
        sampleIndex: Int,
        sampleCount: Int,
        final: Bool
    ) async throws {
        let jpeg = try await source.captureJPEG()
        var metadata = metadata(from: request)
        metadata["sample_index"] = sampleIndex
        metadata["sample_count"] = sampleCount
        try await request.write(
            jpeg,
            codec: options.codec,
            sampleRate: options.sampleRate,
            channels: options.channels,
            durationMS: options.durationMS,
            final: final,
            metadata: metadata
        )
    }

    private static func sampleLimit(from request: RealtimeAgentInputStreamRequest) -> Int? {
        if let value = request.request.payload["max_samples"] as? Int, value > 0 {
            return value
        }
        if let value = request.request.payload["sample_count"] as? Int, value > 0 {
            return value
        }
        return nil
    }

    private static func frequencyHz(from request: RealtimeAgentInputStreamRequest, fallback: Int) -> Double {
        if let value = request.request.payload["frequency_hz"] as? Double, value > 0 {
            return value
        }
        if let value = request.request.payload["frequency_hz"] as? Int, value > 0 {
            return Double(value)
        }
        return Double(max(1, fallback))
    }

    private static func metadata(from request: RealtimeAgentInputStreamRequest) -> [String: Any] {
        var metadata: [String: Any] = [:]
        if let requestID = request.requestID {
            metadata["request_id"] = requestID
        }
        if let turnID = request.request.payload["turn_id"] {
            metadata["turn_id"] = turnID
        }
        if let correlationID = request.request.payload["correlation_id"] {
            metadata["correlation_id"] = correlationID
        }
        if let ttlSeconds = request.request.payload["ttl_seconds"] {
            metadata["ttl_seconds"] = ttlSeconds
        }
        if let direction = request.request.payload["direction"] {
            metadata["direction"] = direction
        }
        if let captureReason = request.request.payload["capture_reason"] {
            metadata["capture_reason"] = captureReason
        }
        metadata["captured_at_ms"] = Int(Date().timeIntervalSince1970 * 1000)
        return metadata
    }

}
