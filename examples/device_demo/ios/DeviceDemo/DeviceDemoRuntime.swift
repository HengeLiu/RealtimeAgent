import AVFoundation
import Foundation
import RealtimeAgentDeviceKit

/// Device Demo 的运行阶段。
///
/// 主要功能：让 UI 用一个稳定枚举表达启动、连接、对话和失败状态。
enum DeviceDemoPhase: String {
    case idle = "未开始"
    case connecting = "连接中"
    case conversation = "对话中"
    case failed = "失败"
}

/// Device Demo 运行时。
///
/// 主要功能：
/// 1. 通过代码式 SDK API 声明设备、注册能力和启用默认硬件链路。
/// 2. 维护 App 页面状态、诊断快照和最近日志。
/// 3. 把相机预览与 SDK 的 `sensor.rgb` 单帧上传共用同一个采集源，避免 App 开发者手写 stream chunk。
@MainActor
final class DeviceDemoRuntime: ObservableObject {
    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: Self.serverURLKey) }
    }

    @Published private(set) var phase: DeviceDemoPhase = .idle
    @Published private(set) var diagnostics = RealtimeAgentDiagnostics()
    @Published private(set) var logs: [String] = []

    let cameraPreview = CameraPreviewController()

    private var client: DeviceClient?
    private var diagnosticsTask: Task<Void, Never>?

    private static let serverURLKey = "DeviceDemo.serverURL"
    private static let defaultServerURL = "http://192.168.10.10:8765"

    init() {
        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? Self.defaultServerURL
    }

    /// 开始实时音视频对话。
    ///
    /// 主要逻辑：先申请系统权限，再启动相机预览，最后用新的 SDK 代码式注册方式连接 server。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：权限被拒绝、server 地址错误或 WebSocket 连接失败时进入失败状态并写入日志。
    func startConversation() async {
        guard phase != .connecting else { return }
        phase = .connecting
        appendLog("start conversation")

        do {
            try await requestMediaPermissions()
            try await cameraPreview.start()

            let client = try makeClient()
            configureCustomEvents(client)
            self.client = client
            try await client.connectAndRegister()
            try await sendWakeDetected(client)
            diagnostics = client.diagnosticsSnapshot()
            startDiagnosticsLoop(client)
            phase = .conversation
            appendLog("device registered and wake sent")
        } catch {
            phase = .failed
            appendLog("start failed: \(error.localizedDescription)")
            await client?.close()
            client = nil
            cameraPreview.stop()
        }
    }

    /// 停止实时音视频对话。
    func stopConversation() async {
        appendLog("stop conversation")
        diagnosticsTask?.cancel()
        diagnosticsTask = nil
        await client?.close()
        client = nil
        cameraPreview.stop()
        phase = .idle
        diagnostics = RealtimeAgentDiagnostics()
    }

    private func makeClient() throws -> DeviceClient {
        try DeviceClient(
            serverURL: serverURL,
            deviceID: "dev-device-demo-ios-001",
            userID: "user-device-demo",
            name: "Device Demo iPhone",
            clientType: "ios-device-demo",
            audioInput: .enabled(),
            camera: .enabled(
                modes: ["single", "continuous"],
                format: "jpeg",
                frequencyHz: 1,
                sampleCount: 1,
                source: cameraPreview
            ),
            speaker: .enabled(
                buffer: PlaybackBuffer(
                    startWatermarkMS: 120,
                    lowWatermarkMS: 300,
                    highWatermarkMS: 800,
                    maxBufferMS: 1200
                )
            ),
            auth: ["mode": "disabled"],
            properties: [
                "demo.name": "device_demo",
                "demo.interaction": "audio_video_conversation",
            ],
            configuration: RealtimeAgentClientConfiguration(
                autoFailUnhandledCommands: false,
                logLevel: .debug
            )
        )
    }

    private func configureCustomEvents(_ client: DeviceClient) {
        client.onCustomCommand("demo.ping") { [weak self] context in
            await self?.appendLog("custom command <- demo.ping")
            try await context.emit("custom.demo.pong", ["ok": true])
        }
        client.onEvent("custom.demo.message") { [weak self] event in
            await self?.appendLog("custom event <- \(event.eventName)")
        }
    }

    private func sendWakeDetected(_ client: DeviceClient) async throws {
        try await client.sendEvent(
            name: "control.user.wake.detected",
            payload: [
                "wake_source": "device_demo_start_button",
                "camera_preview": true,
                "audio_input": true,
            ],
            sessionID: client.deviceID
        )
        appendLog("event -> control.user.wake.detected")
    }

    private func requestMediaPermissions() async throws {
        let cameraGranted = await AVCaptureDevice.requestAccess(for: .video)
        guard cameraGranted else {
            throw DeviceDemoError.permissionDenied("相机权限被拒绝")
        }

        let microphoneGranted = await AVCaptureDevice.requestAccess(for: .audio)
        guard microphoneGranted else {
            throw DeviceDemoError.permissionDenied("麦克风权限被拒绝")
        }
    }

    private func startDiagnosticsLoop(_ client: DeviceClient) {
        diagnosticsTask?.cancel()
        diagnosticsTask = Task { [weak self] in
            while !Task.isCancelled {
                await MainActor.run {
                    self?.diagnostics = client.diagnosticsSnapshot()
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
    }

    private func appendLog(_ message: String) {
        logs.insert(message, at: 0)
        if logs.count > 80 {
            logs.removeLast(logs.count - 80)
        }
    }
}

private enum DeviceDemoError: LocalizedError {
    case permissionDenied(String)

    var errorDescription: String? {
        switch self {
        case let .permissionDenied(message):
            return message
        }
    }
}
