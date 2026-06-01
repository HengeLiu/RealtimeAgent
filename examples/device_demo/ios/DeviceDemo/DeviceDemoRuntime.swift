import Foundation
import OSLog
import RealtimeAgentDeviceKit

/// Device Demo 的运行阶段。
///
/// 主要功能：让 UI 用一个稳定枚举表达启动、连接、对话和失败状态。
enum DeviceDemoPhase: String {
    case launching = "启动中"
    case requestingPermissions = "申请权限"
    case registering = "注册中"
    case waiting = "等待开始"
    case startingConversation = "准备通话"
    case conversation = "对话中"
    case closing = "结束中"
    case failed = "失败"
}

/// Device Demo 的失败阶段。
///
/// 主要功能：记录失败发生在哪个用户可重试步骤，让 UI 能显示明确的重试按钮。
enum DeviceDemoFailureStage {
    case launch
    case permission
    case registration
    case startConversation
    case closeConversation

    var retryTitle: String {
        switch self {
        case .launch:
            return "启动失败\n重试"
        case .permission:
            return "申请权限失败\n重试"
        case .registration:
            return "注册失败\n重试"
        case .startConversation:
            return "开始失败\n重试"
        case .closeConversation:
            return "结束失败\n重试"
        }
    }
}

/// Device Demo 运行时。
///
/// 主要功能：
/// 1. 通过代码式 SDK API 声明设备、注册能力和启用默认硬件链路。
/// 2. 维护 App 页面状态、诊断快照和最近日志。
/// 3. 保持相机预览连续运行，并把按需单帧相机采集交给 SDK。
@MainActor
final class DeviceDemoRuntime: ObservableObject {
    @Published var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: Self.serverURLKey) }
    }

    @Published private(set) var phase: DeviceDemoPhase = .launching
    @Published private(set) var failureStage: DeviceDemoFailureStage?
    @Published private(set) var diagnostics = RealtimeAgentDiagnostics()
    @Published private(set) var logs: [String] = []

    let cameraPreview = CameraPreviewController()
    let logFilePath: String

    private var client: DeviceClient?
    private var diagnosticsTask: Task<Void, Never>?
    private var bootstrapTask: Task<Void, Never>?
    private let logFileURL: URL
    private let logFileQueue = DispatchQueue(label: "realtime-agent.device-demo.log-file")
    private let logger = Logger(subsystem: "dev.realtimeagent.device-demo", category: "runtime")

    private static let serverURLKey = "DeviceDemo.serverURL"
    private static let defaultServerURL = "http://192.168.10.10:8765"

    /// 主按钮标题。
    ///
    /// 主要逻辑：等待态显示开始对话；失败态优先显示具体失败阶段；其他阶段显示当前阶段。
    /// 参数：无。
    /// 返回值：用于主按钮展示的多行文字。
    /// 异常情况：无。
    var primaryButtonTitle: String {
        switch phase {
        case .waiting:
            return "开始\n音视频对话"
        case .failed:
            return failureStage?.retryTitle ?? "失败\n重试"
        default:
            return phase.rawValue
        }
    }

    /// 主按钮是否可点击。
    ///
    /// 主要逻辑：等待态用于开始通话，失败态用于重试；其他中间态禁止重复点击。
    var isPrimaryButtonEnabled: Bool {
        phase == .waiting || phase == .failed
    }

    init() {
        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? Self.defaultServerURL
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let logFileURL = documentsURL.appendingPathComponent("DeviceDemo.log")
        self.logFileURL = logFileURL
        self.logFilePath = logFileURL.path
    }

    /// App 启动后的 SDK 初始化流程。
    ///
    /// 主要逻辑：创建 SDK client，申请硬件权限，完成设备注册；成功后进入等待状态，
    /// UI 的“开始通话”按钮才允许触发实时对话。
    func bootstrap() {
        guard bootstrapTask == nil else { return }
        bootstrapTask = Task { [weak self] in
            await self?.bootstrapSDK()
            await MainActor.run {
                self?.bootstrapTask = nil
            }
        }
    }

    /// 处理主按钮点击。
    ///
    /// 主要逻辑：等待态开始通话；失败态根据失败阶段重新执行初始化或开始通话。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：具体错误由对应流程写入日志并进入失败状态。
    func handlePrimaryButtonTap() async {
        switch phase {
        case .waiting:
            await startConversation()
        case .failed:
            await retryFailedStep()
        default:
            break
        }
    }

    /// 开始实时音视频对话。
    ///
    /// 主要逻辑：App 只启动页面需要的相机预览，然后调用 SDK 的 `startConversation`；
    /// 设备注册、stream 准备、wake 事件和 speaker 链路都由 SDK 处理。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：权限被拒绝、server 地址错误或 WebSocket 连接失败时进入失败状态并写入日志。
    func startConversation() async {
        guard phase == .waiting, let client else { return }
        failureStage = nil
        phase = .startingConversation
        appendLog("start conversation")

        do {
            try await cameraPreview.start()
            try await client.startConversation(reason: "device_demo_start_button")
            diagnostics = client.diagnosticsSnapshot()
            appendLog("sdk startConversation sent")
        } catch {
            fail(stage: .startConversation, error: error, prefix: "start failed")
            cameraPreview.stop()
        }
    }

    /// 请求停止实时音视频对话。
    ///
    /// 主要逻辑：App 不直接关闭 WebSocket，而是调用 SDK 发出端侧结束请求；
    /// server 后续下发标准 close 事件时，SDK 继续负责资源清理。
    func stopConversation() async {
        appendLog("stop conversation")
        guard let client else {
            cameraPreview.stop()
            phase = .waiting
            return
        }
        failureStage = nil
        phase = .closing
        do {
            try await client.requestConversationClose(reason: "user_tapped_end")
            diagnostics = client.diagnosticsSnapshot()
            appendLog("sdk requestConversationClose sent")
        } catch {
            fail(stage: .closeConversation, error: error, prefix: "close request failed")
            return
        }
    }

    /// 清空调试弹窗和沙盒文件中的日志。
    ///
    /// 主要用途：真机复现播放问题前清掉历史事件，避免把旧日志误认为本次链路状态。
    func clearLogs() {
        logs.removeAll()
        logFileQueue.async { [logFileURL] in
            try? FileManager.default.removeItem(at: logFileURL)
        }
        appendLog("logs cleared")
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
                modes: ["single"],
                format: "jpeg",
                frequencyHz: 1,
                sampleCount: 1,
                source: cameraPreview
            ),
            speaker: .enabled(
                buffer: .default,
                duplexMode: .fullDuplexServerBargeIn
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

    private func bootstrapSDK() async {
        failureStage = nil
        phase = .launching
        appendLog("bootstrap sdk")

        var currentStage = DeviceDemoFailureStage.launch
        do {
            let client = try makeClient()
            configureCustomEvents(client)
            self.client = client

            currentStage = .permission
            phase = .requestingPermissions
            let permissions = try await client.requestPermissions()
            guard permissions.isAuthorized else {
                throw DeviceDemoError.permissionDenied("硬件权限未授权：mic=\(permissions.microphone.rawValue) camera=\(permissions.camera.rawValue)")
            }

            currentStage = .registration
            phase = .registering
            _ = try await client.register()
            diagnostics = client.diagnosticsSnapshot()
            startDiagnosticsLoop(client)
            failureStage = nil
            phase = .waiting
            appendLog("device registered and permissions granted")
        } catch {
            fail(stage: currentStage, error: error, prefix: "bootstrap failed")
            await client?.close()
            client = nil
            cameraPreview.stop()
        }
    }

    private func retryFailedStep() async {
        let stage = failureStage
        appendLog("retry failed step: \(String(describing: stage))")
        switch stage {
        case .startConversation:
            if client == nil {
                bootstrap()
            } else {
                phase = .waiting
                await startConversation()
            }
        case .closeConversation:
            await stopConversation()
        case .launch, .permission, .registration, .none:
            bootstrap()
        }
    }

    private func fail(stage: DeviceDemoFailureStage, error: Error, prefix: String) {
        failureStage = stage
        phase = .failed
        diagnostics = client?.diagnosticsSnapshot() ?? diagnostics
        appendLog("\(prefix): \(error.localizedDescription)")
    }

    private func configureCustomEvents(_ client: DeviceClient) {
        client.onDebugLog { [weak self] message in
            await self?.appendSDKDebugLog(message)
        }
        client.onConversationStateChange { [weak self] state in
            await self?.handleConversationState(state)
        }
        client.onCustomCommand("demo.ping") { [weak self] context in
            await self?.appendLog("custom command <- demo.ping")
            try await context.emit("custom.demo.pong", ["ok": true])
        }
        client.onEvent("custom.demo.message") { [weak self] event in
            await self?.appendLog("custom event <- \(event.eventName)")
        }
    }

    private func handleConversationState(_ state: DeviceConversationState) {
        switch state {
        case .waiting:
            cameraPreview.stop()
            phase = .waiting
            appendLog("sdk conversation waiting")
        case .starting:
            phase = .startingConversation
            appendLog("sdk conversation starting")
        case .active:
            phase = .conversation
            appendLog("sdk conversation active")
        case .closing:
            phase = .closing
            appendLog("sdk conversation closing")
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
        let line = "\(Self.logTimestamp()) \(message)"
        logs.insert(line, at: 0)
        if logs.count > 200 {
            logs.removeLast(logs.count - 200)
        }
        logger.info("\(line, privacy: .public)")
        writeLogLineToFile(line)
    }

    private func appendSDKDebugLog(_ message: String) {
        appendLog("sdk \(message)")
    }

    private func writeLogLineToFile(_ line: String) {
        logFileQueue.async { [logFileURL] in
            let data = Data((line + "\n").utf8)
            if !FileManager.default.fileExists(atPath: logFileURL.path) {
                _ = FileManager.default.createFile(atPath: logFileURL.path, contents: nil)
            }
            guard let handle = try? FileHandle(forWritingTo: logFileURL) else {
                return
            }
            defer {
                try? handle.close()
            }
            _ = try? handle.seekToEnd()
            handle.write(data)
        }
    }

    private static func logTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
        return formatter.string(from: Date())
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
