import AVFoundation
import CoreImage
import RealtimeAgentDeviceKit
import SwiftUI
import UIKit

/// 相机预览和 SDK RGB 帧来源。
///
/// 主要功能：用一个 `AVCaptureSession` 同时支撑页面视频回显和 SDK 的 `sensor.rgb` 单帧上传。
final class CameraPreviewController: NSObject, ObservableObject, RealtimeAgentCameraFrameSource, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    let session = AVCaptureSession()

    @MainActor @Published private(set) var isRunning = false

    private let queue = DispatchQueue(label: "device-demo.camera")
    private let videoOutput = AVCaptureVideoDataOutput()
    private let context = CIContext()
    private let lock = NSLock()
    private var latestJPEG: Data?
    private var configured = false

    /// 启动相机采集。
    ///
    /// 主要逻辑：首次调用时配置输入、输出和预览 session，之后只恢复运行。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：无可用摄像头或 session 配置失败时抛出错误。
    @MainActor
    func start() async throws {
        try await configureIfNeeded()
        await withCheckedContinuation { continuation in
            queue.async {
                if !self.session.isRunning {
                    self.session.startRunning()
                }
                Task { @MainActor in
                    self.isRunning = true
                    continuation.resume()
                }
            }
        }
    }

    /// 停止相机采集。
    @MainActor
    func stop() {
        queue.async {
            if self.session.isRunning {
                self.session.stopRunning()
            }
            Task { @MainActor in
                self.isRunning = false
            }
        }
    }

    /// 捕获最近一帧 JPEG，供 SDK 响应 `sensor.rgb` 请求。
    func captureJPEG() async throws -> Data {
        for _ in 0..<40 {
            if let data = currentJPEG() {
                return data
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw CameraPreviewError.noFrame
    }

    func captureOutput(_: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from _: AVCaptureConnection) {
        guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: imageBuffer)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let data = context.jpegRepresentation(of: image, colorSpace: colorSpace, options: [:]) else { return }
        lock.lock()
        latestJPEG = data
        lock.unlock()
    }

    private func currentJPEG() -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return latestJPEG
    }

    @MainActor
    private func configureIfNeeded() async throws {
        if configured { return }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async {
                do {
                    self.session.beginConfiguration()
                    self.session.sessionPreset = .high

                    guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
                        ?? AVCaptureDevice.default(for: .video) else {
                        throw CameraPreviewError.noCamera
                    }
                    let input = try AVCaptureDeviceInput(device: device)
                    if self.session.canAddInput(input) {
                        self.session.addInput(input)
                    }

                    self.videoOutput.alwaysDiscardsLateVideoFrames = true
                    self.videoOutput.videoSettings = [
                        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                    ]
                    self.videoOutput.setSampleBufferDelegate(self, queue: self.queue)
                    if self.session.canAddOutput(self.videoOutput) {
                        self.session.addOutput(self.videoOutput)
                    }
                    self.videoOutput.connection(with: .video)?.videoOrientation = .portrait
                    self.session.commitConfiguration()
                    self.configured = true
                    continuation.resume()
                } catch {
                    self.session.commitConfiguration()
                    continuation.resume(throwing: error)
                }
            }
        }
    }
}

/// SwiftUI 摄像头预览。
struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context _: Context) -> PreviewView {
        let view = PreviewView()
        view.videoPreviewLayer.session = session
        view.videoPreviewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context _: Context) {
        uiView.videoPreviewLayer.session = session
    }
}

final class PreviewView: UIView {
    override class var layerClass: AnyClass {
        AVCaptureVideoPreviewLayer.self
    }

    var videoPreviewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }
}

private enum CameraPreviewError: LocalizedError {
    case noCamera
    case noFrame

    var errorDescription: String? {
        switch self {
        case .noCamera:
            return "没有可用摄像头"
        case .noFrame:
            return "相机还没有产生可上传的画面"
        }
    }
}
