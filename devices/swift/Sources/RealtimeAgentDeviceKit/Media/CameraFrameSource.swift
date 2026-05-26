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
    /// 主要功能：当前协议阶段只支持 server 请求触发的单帧采集。即使旧 server 误传
    /// `mode=continuous` 或频率字段，SDK 也只上传一张 `final=true` 图片并关闭逻辑流。
    public static func registerFrameHandler(
        client: RealtimeAgentDeviceClient,
        source: RealtimeAgentCameraFrameSource,
        options: CameraFrameUploadOptions = .init(),
        defaultSampleCount _: Int = 1
    ) {
        client.onStreamOpen("sensor.rgb") { request in
            var openedPayload: [String: Any] = [
                "request_id": request.requestID ?? "",
                "mode": "single",
                "sample_count": 1,
                "format": [
                    "codec": options.codec,
                    "sample_rate": options.sampleRate,
                    "channels": options.channels,
                    "chunk_ms": options.durationMS,
                ],
            ]
            if request.request.payload["mode"] as? String == "continuous" {
                openedPayload["requested_mode_ignored"] = "continuous"
            }
            try await request.opened(openedPayload)
            let jpeg = try await source.captureJPEG()
            var metadata = metadata(from: request)
            metadata["sample_index"] = 0
            metadata["sample_count"] = 1
            try await request.write(
                jpeg,
                codec: options.codec,
                sampleRate: options.sampleRate,
                channels: options.channels,
                durationMS: options.durationMS,
                final: true,
                metadata: metadata
            )
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

}
