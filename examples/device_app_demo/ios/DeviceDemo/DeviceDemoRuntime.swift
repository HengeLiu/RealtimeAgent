import Foundation
import OSLog
import RealtimeAgentDeviceKit

/// Device Demo 的运行阶段。
///
/// 主要功能：让 UI 用一个稳定枚举表达启动、连接、对话和失败状态。
enum DeviceDemoPhase: String {
    case idle = "空闲"
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
enum DeviceDemoFailureStage: Equatable {
    case launch
    case permission
    case registration
    case startConversation
    case closeConversation
    case connectionDisconnected

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
        case .connectionDisconnected:
            return "连接断开\n重连"
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

    @Published private(set) var phase: DeviceDemoPhase = .idle
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
        case .idle, .waiting:
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
        phase == .idle || phase == .waiting || phase == .failed
    }

    /// 对话状态条文案。
    ///
    /// 主要逻辑:结束中显示“正在结束”;已收到 server 下行音频 chunk 时显示“助手回复中”;否则显示“对话中”。
    /// 与 web-chat 的 `conversationText` 对齐,基于诊断快照动态切换,而不是固定文案。
    var conversationStatusText: String {
        if phase == .closing {
            return "正在结束"
        }
        return diagnostics.receivedOutputChunks > 0 ? "助手回复中" : "对话中"
    }

    /// 调试面板中的停止按钮是否可用。
    ///
    /// 主要逻辑：只有真正处于对话启动中或对话中时允许发起停止，避免等待态重复点击
    /// 把页面推进到“结束中”。
    var isStopConversationEnabled: Bool {
        phase == .startingConversation || phase == .conversation
    }

    init() {
        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? Self.defaultServerURL
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let logFileURL = documentsURL.appendingPathComponent("DeviceDemo.log")
        self.logFileURL = logFileURL
        self.logFilePath = logFileURL.path
    }

    /// SDK 初始化流程。
    ///
    /// 主要逻辑：创建 SDK client，申请硬件权限，完成设备注册；与 web-chat 对齐，由用户首次点击触发，
    /// 而不是 App 启动即执行。`startAfterRegister` 为 true 时注册成功后直接发起实时对话。
    /// 参数：`startAfterRegister` 控制注册成功后是否一气呵成进入对话。
    func bootstrap(startAfterRegister: Bool = false) {
        guard bootstrapTask == nil else { return }
        bootstrapTask = Task { [weak self] in
            await self?.bootstrapSDK(startAfterRegister: startAfterRegister)
            await MainActor.run {
                self?.bootstrapTask = nil
            }
        }
    }

    /// 处理主按钮点击。
    ///
    /// 主要逻辑：与 web-chat 对齐——空闲态首次点击申请权限、注册并直接开始对话；等待态(对话结束后)
    /// 直接开始对话；失败态根据失败阶段重试。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：具体错误由对应流程写入日志并进入失败状态。
    func handlePrimaryButtonTap() async {
        switch phase {
        case .idle:
            bootstrap(startAfterRegister: true)
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
            try await client.startConversation(reason: "device_app_demo_start_button")
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
        guard isStopConversationEnabled else {
            appendLog("stop ignored phase=\(phase.rawValue)")
            return
        }
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

    /// App 进入后台时释放连接。
    ///
    /// 主要逻辑：对齐 web-chat 的 `beforeunload` 资源释放——iOS 后台无法维持实时音视频链路，
    /// 因此关闭 client 并回到空闲态，用户回到前台后再点击重新连接。让 SDK 负责本地资源清理，
    /// server 侧最终通过 control 断开和心跳超时收口。
    func handleEnteredBackground() {
        guard client != nil || phase != .idle else { return }
        appendLog("app entered background -> release client")
        cameraPreview.stop()
        failureStage = nil
        phase = .idle
        Task { [weak self] in
            await self?.releaseClient()
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
                "demo.name": "device_app_demo",
                "demo.interaction": "audio_video_conversation",
                "realtime_agent.location": true,
                "realtime_agent.location_commands": ["device.location.get_current"],
            ],
            configuration: RealtimeAgentClientConfiguration(
                autoFailUnhandledCommands: false,
                logLevel: .debug
            )
        )
    }

    private func bootstrapSDK(startAfterRegister: Bool) async {
        failureStage = nil
        appendLog("bootstrap sdk start_after_register=\(startAfterRegister)")

        // 重连/重试前先释放旧 client，避免遗留心跳、control/stream 任务和重复连接。
        await releaseClient()

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

            if startAfterRegister {
                await startConversation()
            }
        } catch {
            fail(stage: currentStage, error: error, prefix: "bootstrap failed")
            await client?.close()
            client = nil
            cameraPreview.stop()
        }
    }

    /// 释放当前 client 并停止相关后台循环。
    ///
    /// 主要用途：重连、重试和 App 进入后台前调用，确保旧连接的心跳、接收循环和诊断轮询被取消，
    /// 不与新 client 争抢协议状态机。
    private func releaseClient() async {
        guard let existing = client else { return }
        diagnosticsTask?.cancel()
        diagnosticsTask = nil
        client = nil
        // 解绑回调，避免旧 client 在 close 过程中把 .closed/.waiting 回写到已切换的 UI 状态。
        existing.onConnectionStateChange { _ in }
        existing.onConversationStateChange { _ in }
        existing.onDebugLog { _ in }
        await existing.close(force: true)
    }

    private func retryFailedStep() async {
        let stage = failureStage
        appendLog("retry failed step: \(String(describing: stage))")
        switch stage {
        case .startConversation:
            if client == nil {
                bootstrap(startAfterRegister: true)
            } else {
                phase = .waiting
                await startConversation()
            }
        case .closeConversation:
            await stopConversation()
        case .connectionDisconnected:
            // 断线重连：重新创建 client、注册并直接恢复对话，旧 client 由 bootstrap 统一释放。
            bootstrap(startAfterRegister: true)
        case .launch, .permission, .registration, .none:
            bootstrap(startAfterRegister: true)
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
        client.onConnectionStateChange { [weak self] state in
            await self?.handleConnectionState(state)
        }
        client.onCustomCommand("demo.ping") { [weak self] context in
            await self?.appendLog("custom command <- demo.ping")
            try await context.emit("custom.demo.pong", ["ok": true])
        }
        client.onEvent("custom.demo.message") { [weak self] event in
            await self?.appendLog("custom event <- \(event.eventName)")
        }
    }

    private func handleConnectionState(_ state: DeviceConnectionState) {
        diagnostics = client?.diagnosticsSnapshot() ?? diagnostics
        switch state {
        case .registered:
            failureStage = nil
            if phase != .conversation, phase != .startingConversation, phase != .closing {
                phase = .waiting
            }
            appendLog("sdk connection registered")
        case let .disconnected(reason):
            diagnosticsTask?.cancel()
            diagnosticsTask = nil
            cameraPreview.stop()
            failureStage = .connectionDisconnected
            phase = .failed
            appendLog("sdk connection disconnected: \(describeDisconnectReason(reason))")
        case .closed:
            appendLog("sdk connection closed")
        case .connecting:
            appendLog("sdk connection connecting")
        case .registering:
            appendLog("sdk connection registering")
        case .idle:
            appendLog("sdk connection idle")
        }
    }

    private func describeDisconnectReason(_ reason: DeviceDisconnectReason) -> String {
        switch reason {
        case let .heartbeatFailed(message):
            return "heartbeat_failed \(message)"
        case let .controlReceiveFailed(message):
            return "control_receive_failed \(message)"
        case let .streamReceiveFailed(message):
            return "stream_receive_failed \(message)"
        case let .serverClosed(message):
            return "server_closed \(message)"
        case .localClose:
            return "local_close"
        }
    }

    private func handleConversationState(_ state: DeviceConversationState) {
        if failureStage == .connectionDisconnected {
            return
        }
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
