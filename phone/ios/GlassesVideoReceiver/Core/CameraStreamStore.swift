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

    /// 当前控制连接是否处于重试等待中。
    var isServerRetryScheduled = false

    /// 下一次控制连接重试时间。
    var serverRetryAt: Date?

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
}
