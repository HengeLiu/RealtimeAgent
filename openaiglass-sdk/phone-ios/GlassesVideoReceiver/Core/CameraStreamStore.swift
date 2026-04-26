import Observation
import SwiftUI

/// 视频回显页面共享状态。
///
/// 主要功能：
/// 1. 保存监听状态、连接状态和最近错误。
/// 2. 保存最近一帧图像及其元信息。
/// 3. 维护最近事件列表，用于联调排查。
@MainActor
@Observable
final class CameraStreamStore {
    /// 手机端任务能力运行时。
    private let capabilityRuntime: any PhoneTaskCapabilityRuntime

    /// 当前是否已由用户结束接收。
    var isFinished = false

    /// 是否已经开始监听。
    var isListening = false

    /// 当前是否已建立推流连接。
    var isConnected = false

    /// 监听端口。
    var listenPort: UInt16 = 9001

    /// 当前可连接地址列表。
    var sinkURIs: [String] = []

    /// 最近一帧图像。
    var latestImage: UIImage?

    /// 最近一帧序号。
    var latestSequence: Int?

    /// 最近接收时间。
    var latestReceivedAt: Date?

    /// 最近错误信息。
    var lastError: String?

    /// 最近事件日志。
    var events: [String] = []

    /// 手机端是否已完成服务端注册。
    var isServerRegistered = false

    /// 当前服务端连接状态说明。
    var controlStatusText = "尚未注册到服务器"

    /// 当前已绑定的眼镜编号。
    var boundGlassDeviceID: String?

    /// 当前手机编号。
    var phoneDeviceID: String?

    /// 当前由 SDK 启动的手机端任务状态。
    var activePhoneTaskState: PhoneTaskState?

    /// 当前控制连接是否处于重试等待中。
    var isServerRetryScheduled = false

    /// 下一次控制连接重试时间。
    var serverRetryAt: Date?

    init(capabilityRuntime: any PhoneTaskCapabilityRuntime) {
        self.capabilityRuntime = capabilityRuntime
    }

    /// 当前页面状态文字。
    var statusText: String {
        if isFinished {
            return "已结束视频接收"
        }
        if isConnected {
            return "正在接收视频帧"
        }
        if isListening {
            return "等待眼镜连接"
        }
        return "尚未启动监听"
    }

    /// 当前页面状态图标。
    var statusIconName: String {
        if isFinished {
            return "checkmark.circle"
        }
        if isConnected {
            return "dot.radiowaves.left.and.right"
        }
        if isListening {
            return "antenna.radiowaves.left.and.right"
        }
        return "pause.circle"
    }

    /// 当前页面状态颜色。
    var statusColor: Color {
        if isFinished {
            return .blue
        }
        if isConnected {
            return .green
        }
        if isListening {
            return .orange
        }
        return .secondary
    }

    /// 最近接收时间的格式化文本。
    var latestReceivedAtText: String? {
        guard let latestReceivedAt else {
            return nil
        }
        return Self.dateFormatter.string(from: latestReceivedAt)
    }

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter
    }()

    /// 当前最优接收地址。
    var preferredSinkURI: String? {
        sinkURIs.first
    }

    /// 刷新可连接地址列表。
    ///
    /// 主要逻辑：
    /// 1. 读取本机 IPv4 地址。
    /// 2. 组合为眼镜可直接连接的 `ws://` 地址。
    func refreshSinkURIs() {
        sinkURIs = IPAddressProvider.loadIPv4Addresses().sorted(by: Self.compareAddressPriority).map { address in
            "ws://\(address):\(listenPort)/ws/camera"
        }
        appendEvent("已刷新接收地址，共识别到 \(sinkURIs.count) 个 IPv4 地址")
    }

    /// 标记监听已启动。
    ///
    /// 参数：
    /// 1. `port`：当前监听端口。
    func markListening(port: UInt16) {
        listenPort = port
        isFinished = false
        isListening = true
        appendEvent("监听已启动：端口 \(port)")
    }

    /// 标记有连接接入。
    ///
    /// 参数：
    /// 1. `description`：连接来源说明。
    func markConnected(_ description: String) {
        isConnected = true
        lastError = nil
        appendEvent("已建立连接：\(description)")
    }

    /// 标记连接结束。
    ///
    /// 参数：
    /// 1. `reason`：结束原因。
    func markDisconnected(_ reason: String) {
        isConnected = false
        appendEvent("连接结束：\(reason)")
    }

    /// 标记监听已结束。
    ///
    /// 参数：
    /// 1. `reason`：结束原因。
    func markStopped(_ reason: String) {
        isFinished = true
        isListening = false
        isConnected = false
        appendEvent("接收已结束：\(reason)")
    }

    /// 记录最近收到的视频帧。
    ///
    /// 主要逻辑：
    /// 1. 更新最近一帧图像。
    /// 2. 更新序号和时间。
    /// 3. 清空历史错误。
    ///
    /// 参数：
    /// 1. `image`：最新 JPEG 图像。
    /// 2. `sequence`：帧序号。
    func updateLatestFrame(image: UIImage, sequence: Int) {
        latestImage = image
        latestSequence = sequence
        latestReceivedAt = Date()
        lastError = nil
        appendEvent("收到视频帧：seq=\(sequence)")
        capabilityRuntime.processFrame(store: self, image: image, sequence: sequence)
    }

    /// 记录错误信息。
    ///
    /// 参数：
    /// 1. `message`：错误描述。
    func markError(_ message: String) {
        lastError = message
        appendEvent("发生错误：\(message)")
    }

    /// 标记手机已注册到服务端。
    ///
    /// 参数：
    /// 1. `phoneDeviceID`：手机设备编号。
    func markServerRegistered(phoneDeviceID: String) {
        isServerRegistered = true
        isServerRetryScheduled = false
        serverRetryAt = nil
        self.phoneDeviceID = phoneDeviceID
        controlStatusText = "已注册到服务器"
        appendEvent("手机注册成功：device_id=\(phoneDeviceID)")
    }

    /// 标记服务端连接状态已断开。
    ///
    /// 参数：
    /// 1. `reason`：断开原因。
    func markServerDisconnected(_ reason: String) {
        isServerRegistered = false
        isServerRetryScheduled = false
        serverRetryAt = nil
        controlStatusText = "服务器连接已断开"
        appendEvent("服务器连接断开：\(reason)")
    }

    /// 标记正在尝试连接服务端。
    ///
    /// 参数：
    /// 1. `reason`：当前发起连接的原因。
    func markServerConnecting(_ reason: String) {
        isServerRegistered = false
        isServerRetryScheduled = false
        serverRetryAt = nil
        controlStatusText = "正在连接服务器"
        appendEvent("开始连接服务端：\(reason)")
    }

    /// 标记注册失败，并安排稍后重试。
    ///
    /// 参数：
    /// 1. `reason`：失败原因。
    /// 2. `retryAfterSeconds`：距离下次重试的秒数。
    func markServerRetryScheduled(reason: String, retryAfterSeconds: Int = 5) {
        isServerRegistered = false
        isServerRetryScheduled = true
        serverRetryAt = Date().addingTimeInterval(TimeInterval(retryAfterSeconds))
        controlStatusText = "注册失败，\(retryAfterSeconds) 秒后重试"
        appendEvent("服务端注册待重试：\(reason)")
    }

    /// 标记控制连接已断开，并安排稍后重试。
    ///
    /// 参数：
    /// 1. `reason`：断开原因。
    /// 2. `retryAfterSeconds`：距离下次重试的秒数。
    func markServerReconnectScheduled(reason: String, retryAfterSeconds: Int = 5) {
        isServerRegistered = false
        isServerRetryScheduled = true
        serverRetryAt = Date().addingTimeInterval(TimeInterval(retryAfterSeconds))
        controlStatusText = "服务器连接已断开，\(retryAfterSeconds) 秒后重试"
        appendEvent("服务器连接断开，准备重试：\(reason)")
    }

    /// 标记设备已完成绑定。
    ///
    /// 参数：
    /// 1. `glassDeviceID`：眼镜设备编号。
    /// 2. `phoneDeviceID`：手机设备编号。
    func markBound(glassDeviceID: String, phoneDeviceID: String) {
        boundGlassDeviceID = glassDeviceID
        self.phoneDeviceID = phoneDeviceID
        appendEvent("绑定完成：glass=\(glassDeviceID) phone=\(phoneDeviceID)")
    }

    /// 清空当前绑定状态。
    func clearBinding() {
        boundGlassDeviceID = nil
    }

    /// 启动一个由能力运行时处理的手机任务。
    func startPhoneTask(
        taskID: String,
        taskType: String,
        streamID: String,
        glassDeviceID: String,
        phoneDeviceID: String,
        params: [String: Any]
    ) {
        activePhoneTaskState = PhoneTaskState(
            taskID: taskID,
            taskType: taskType,
            streamID: streamID,
            glassDeviceID: glassDeviceID,
            phoneDeviceID: phoneDeviceID
        )
        capabilityRuntime.startTask(
            store: self,
            taskID: taskID,
            taskType: taskType,
            streamID: streamID,
            glassDeviceID: glassDeviceID,
            phoneDeviceID: phoneDeviceID,
            params: params
        )
    }

    /// 停止一个由能力运行时处理的手机任务。
    func stopPhoneTask(taskID: String, taskType: String, reason: String) {
        if activePhoneTaskState?.taskID == taskID || taskID.isEmpty {
            activePhoneTaskState = nil
        }
        capabilityRuntime.stopTask(
            store: self,
            taskID: taskID,
            taskType: taskType,
            reason: reason
        )
        finishCurrentVideoSession("手机任务结束：\(reason)")
    }

    /// 结束当前视频会话，但保持应用继续待命。
    ///
    /// 主要逻辑：
    /// 1. 清空最近一帧和时间信息。
    /// 2. 保持监听与服务端注册状态不变。
    /// 3. 让页面立即回到“等待眼镜连接”状态，便于再次开启视频。
    ///
    /// 参数：
    /// 1. `reason`：结束原因。
    func finishCurrentVideoSession(_ reason: String) {
        isFinished = false
        isConnected = false
        latestImage = nil
        latestSequence = nil
        latestReceivedAt = nil
        appendEvent("当前视频会话已结束：\(reason)")
    }

    /// 追加事件日志。
    ///
    /// 参数：
    /// 1. `message`：事件内容。
    private func appendEvent(_ message: String) {
        let timestamp = Self.dateFormatter.string(from: Date())
        events.insert("[\(timestamp)] \(message)", at: 0)
        if events.count > 12 {
            events = Array(events.prefix(12))
        }
    }

    /// 比较两个 IPv4 地址的优先级。
    ///
    /// 主要逻辑：
    /// 1. 优先保留常见局域网地址。
    /// 2. 尽量把链路本地地址和异常地址排到后面。
    private static func compareAddressPriority(_ lhs: String, _ rhs: String) -> Bool {
        score(for: lhs) > score(for: rhs)
    }

    /// 计算单个 IPv4 地址的优先级分数。
    ///
    /// 参数：
    /// 1. `address`：原始 IPv4 地址。
    ///
    /// 返回值：
    /// 1. 分数越高越优先。
    private static func score(for address: String) -> Int {
        if address.hasPrefix("10.") || address.hasPrefix("172.") || address.hasPrefix("192.168.") {
            return 30
        }
        if address.hasPrefix("169.254.") {
            return 10
        }
        if address.hasPrefix("192.0.0.") {
            return 5
        }
        return 20
    }

    /// 当前是否存在能力层正在运行的手机任务。
    var activeTaskDescription: String? {
        capabilityRuntime.activeTaskDescription
    }

    /// 最近一次能力层输出摘要。
    var latestCapabilitySummary: String? {
        capabilityRuntime.latestSummary
    }

    /// 最近一次能力层输出是否命中目标。
    var latestCapabilitySuccess: Bool? {
        capabilityRuntime.latestSuccess
    }
}

/// 手机端任务能力注册表。
///
/// 主要功能：
/// 1. 按 `taskType` 保存业务能力运行时工厂。
/// 2. 为 `CameraStreamStore` 提供可同时承载多个业务能力的组合运行时。
/// 3. 让通用 SDK运行时 只负责注册和分发，不感知具体业务实现。
@MainActor
enum PhoneTaskCapabilityRegistry {
    private static var builders: [String: () -> any PhoneTaskCapabilityRuntime] = [:]

    /// 注册指定任务类型的手机能力工厂。
    ///
    /// 参数：
    /// 1. `taskType`：服务端 `start_phone_task` 下发的任务类型。
    /// 2. `runtimeBuilder`：创建业务能力运行时的工厂。
    ///
    /// 异常情况：
    /// 1. `taskType` 为空时触发断言并忽略注册。
    static func register(taskType: String, runtimeBuilder: @escaping () -> any PhoneTaskCapabilityRuntime) {
        let normalizedTaskType = taskType.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedTaskType.isEmpty else {
            assertionFailure("PhoneTaskCapabilityRegistry.register(taskType:) 不允许为空")
            return
        }
        builders[normalizedTaskType] = runtimeBuilder
    }

    /// 查询某个任务类型是否已有业务能力。
    ///
    /// 参数：
    /// 1. `taskType`：服务端下发的任务类型。
    ///
    /// 返回值：
    /// 1. 已注册返回 `true`，否则返回 `false`。
    static func contains(taskType: String) -> Bool {
        builders[taskType] != nil
    }

    /// 创建组合手机能力运行时。
    ///
    /// 返回值：
    /// 1. 一个按 `taskType` 分发启动、停止和视频帧的运行时。
    static func makeRuntime() -> any PhoneTaskCapabilityRuntime {
        RegisteredPhoneTaskCapabilityRuntime(builders: builders)
    }

    /// 清空注册表。
    ///
    /// 主要用于测试隔离，业务代码不应在应用运行过程中调用。
    static func resetForTesting() {
        builders = [:]
    }
}

/// 手机端任务能力工厂注册器。
///
/// 主要功能：
/// 1. 兼容早期只能注入单个手机能力的装配入口。
/// 2. 为未迁移到 `PhoneTaskCapabilityRegistry` 的宿主保留可运行路径。
///
/// 新业务能力应优先使用 `PhoneTaskCapabilityRegistry.register(taskType:runtimeBuilder:)`。
@MainActor
enum PhoneCapabilityRuntimeFactory {
    private static var legacyBuilder: (() -> any PhoneTaskCapabilityRuntime)?

    /// 注册当前应用使用的旧式单手机能力工厂。
    ///
    /// 参数：
    /// 1. `newBuilder`：创建手机能力运行时的工厂。
    ///
    /// 注意：
    /// 1. 该入口无法表达 `taskType`，多个业务能力同时注册时仍会以后注册者为准。
    /// 2. 新业务能力应改用 `PhoneTaskCapabilityRegistry.register(taskType:runtimeBuilder:)`。
    static func register(_ newBuilder: @escaping () -> any PhoneTaskCapabilityRuntime) {
        legacyBuilder = newBuilder
    }

    /// 创建一个新的手机能力运行时实例。
    ///
    /// 返回值：
    /// 1. 优先返回按任务类型分发的组合运行时。
    /// 2. 如果没有新式注册，则返回旧式单能力运行时。
    /// 3. 两种注册都没有时返回空实现运行时。
    static func makeRuntime() -> any PhoneTaskCapabilityRuntime {
        if PhoneTaskCapabilityRegistry.hasRegisteredRuntimes {
            return PhoneTaskCapabilityRegistry.makeRuntime()
        }
        return legacyBuilder?() ?? NoopPhoneTaskCapabilityRuntime()
    }

    /// 清空旧式工厂。
    ///
    /// 主要用于测试隔离，业务代码不应在应用运行过程中调用。
    static func resetForTesting() {
        legacyBuilder = nil
    }
}

private extension PhoneTaskCapabilityRegistry {
    /// 当前是否已有按任务类型注册的业务运行时。
    static var hasRegisteredRuntimes: Bool {
        !builders.isEmpty
    }
}

/// 手机端能力工厂启动器。
///
/// 主要功能：
/// 1. 收集各业务能力在编译期注册的安装函数。
/// 2. 在应用启动时统一执行注册。
@MainActor
enum PhoneCapabilityBootstrap {
    private static var installers: [() -> Void] = []

    /// 注册一个安装函数。
    static func registerInstaller(_ installer: @escaping () -> Void) {
        installers.append(installer)
    }

    /// 执行全部已注册安装函数。
    static func applyRegisteredInstallers() {
        for installer in installers {
            installer()
        }
    }
}

/// 手机端任务基础状态。
struct PhoneTaskState: Equatable {
    let taskID: String
    let taskType: String
    let streamID: String
    let glassDeviceID: String
    let phoneDeviceID: String
}

/// 手机端任务能力运行时协议。
@MainActor
protocol PhoneTaskCapabilityRuntime: AnyObject {
    var activeTaskDescription: String? { get }
    var latestSummary: String? { get }
    var latestSuccess: Bool? { get }

    func startTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        streamID: String,
        glassDeviceID: String,
        phoneDeviceID: String,
        params: [String: Any]
    )

    func stopTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        reason: String
    )

    func processFrame(
        store: CameraStreamStore,
        image: UIImage,
        sequence: Int
    )
}

/// 按任务类型分发的手机任务能力运行时。
///
/// 主要功能：
/// 1. 根据服务端下发的 `taskType` 创建对应业务能力运行时。
/// 2. 记录 `taskID` 与业务运行时的关系，保证停止任务时回到同一个实例。
/// 3. 将视频帧投递给当前活跃任务对应的业务运行时。
final class RegisteredPhoneTaskCapabilityRuntime: PhoneTaskCapabilityRuntime {
    private let builders: [String: () -> any PhoneTaskCapabilityRuntime]
    private var runtimesByTaskID: [String: any PhoneTaskCapabilityRuntime] = [:]
    private var taskTypesByTaskID: [String: String] = [:]
    private var activeTaskID: String?
    private var latestRuntime: (any PhoneTaskCapabilityRuntime)?

    /// 创建组合运行时。
    ///
    /// 参数：
    /// 1. `builders`：按 `taskType` 保存的业务能力运行时工厂。
    init(builders: [String: () -> any PhoneTaskCapabilityRuntime]) {
        self.builders = builders
    }

    var activeTaskDescription: String? {
        if let activeTaskID, let runtime = runtimesByTaskID[activeTaskID] {
            return runtime.activeTaskDescription
        }
        return runtimesByTaskID.values.compactMap(\.activeTaskDescription).first
    }

    var latestSummary: String? {
        latestRuntime?.latestSummary ?? runtimesByTaskID.values.compactMap(\.latestSummary).first
    }

    var latestSuccess: Bool? {
        latestRuntime?.latestSuccess ?? runtimesByTaskID.values.compactMap(\.latestSuccess).first
    }

    func startTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        streamID: String,
        glassDeviceID: String,
        phoneDeviceID: String,
        params: [String: Any]
    ) {
        guard let builder = builders[taskType] else {
            NoopPhoneTaskCapabilityRuntime().startTask(
                store: store,
                taskID: taskID,
                taskType: taskType,
                streamID: streamID,
                glassDeviceID: glassDeviceID,
                phoneDeviceID: phoneDeviceID,
                params: params
            )
            return
        }

        let runtime = builder()
        runtimesByTaskID[taskID] = runtime
        taskTypesByTaskID[taskID] = taskType
        activeTaskID = taskID
        latestRuntime = runtime
        runtime.startTask(
            store: store,
            taskID: taskID,
            taskType: taskType,
            streamID: streamID,
            glassDeviceID: glassDeviceID,
            phoneDeviceID: phoneDeviceID,
            params: params
        )
    }

    func stopTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        reason: String
    ) {
        let targetTaskID = resolveTaskID(taskID: taskID, taskType: taskType)
        guard let targetTaskID, let runtime = runtimesByTaskID[targetTaskID] else {
            NoopPhoneTaskCapabilityRuntime().stopTask(
                store: store,
                taskID: taskID,
                taskType: taskType,
                reason: reason
            )
            return
        }

        runtime.stopTask(
            store: store,
            taskID: targetTaskID,
            taskType: taskTypesByTaskID[targetTaskID] ?? taskType,
            reason: reason
        )
        runtimesByTaskID.removeValue(forKey: targetTaskID)
        taskTypesByTaskID.removeValue(forKey: targetTaskID)
        if activeTaskID == targetTaskID {
            activeTaskID = runtimesByTaskID.keys.first
        }
        latestRuntime = runtime
    }

    func processFrame(
        store: CameraStreamStore,
        image: UIImage,
        sequence: Int
    ) {
        guard let activeTaskID, let runtime = runtimesByTaskID[activeTaskID] else {
            return
        }
        latestRuntime = runtime
        runtime.processFrame(store: store, image: image, sequence: sequence)
    }

    /// 根据停止消息中的 `taskID` 或 `taskType` 找到正在运行的任务。
    ///
    /// 参数：
    /// 1. `taskID`：服务端任务编号，可能为空。
    /// 2. `taskType`：服务端任务类型，用于旧消息兜底。
    ///
    /// 返回值：
    /// 1. 找到任务时返回实际 `taskID`，否则返回 `nil`。
    private func resolveTaskID(taskID: String, taskType: String) -> String? {
        if !taskID.isEmpty, runtimesByTaskID[taskID] != nil {
            return taskID
        }
        return taskTypesByTaskID.first { _, storedTaskType in
            storedTaskType == taskType
        }?.key
    }
}

/// 默认空实现手机任务能力运行时。
///
/// 主要功能：
/// 1. 在未注入任何业务能力时保持 SDK运行时 可运行。
/// 2. 明确区分“运行时在线”与“已注入业务能力”两个概念。
@MainActor
final class NoopPhoneTaskCapabilityRuntime: PhoneTaskCapabilityRuntime {
    var activeTaskDescription: String? { nil }
    var latestSummary: String? { nil }
    var latestSuccess: Bool? { nil }

    func startTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        streamID: String,
        glassDeviceID: String,
        phoneDeviceID: String,
        params: [String: Any]
    ) {
        store.markError("当前 SDK运行时 未注入手机业务能力，无法处理任务类型：\(taskType)")
    }

    func stopTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        reason: String
    ) {
    }

    func processFrame(
        store: CameraStreamStore,
        image: UIImage,
        sequence: Int
    ) {
    }
}
