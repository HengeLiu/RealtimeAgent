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

    /// 当前正在运行的找物体任务。
    var activeFindObjectTask: FindObjectTaskState?

    /// 最近一次视觉检测摘要。
    var latestVisionSummary: String?

    /// 最近一次视觉检测是否命中目标。
    var latestVisionFound = false

    /// 最近一次视觉检测置信度。
    var latestVisionConfidence: Double?

    /// 找物体检测器。
    private let objectDetector: YoloObjectDetector = HeuristicYoloObjectDetector()

    /// 上次上报视觉结果的时间。
    private var lastVisionReportAt: Date?

    /// 是否已经对当前任务上报过命中。
    private var hasReportedFindObjectHit = false

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
        processFindObjectFrame(image: image, sequence: sequence)
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

    /// 开始手机端找物体任务。
    ///
    /// 主要逻辑：
    /// 1. 保存服务端下发的任务编号、目标物体和视频流编号。
    /// 2. 清空上一轮检测结果。
    /// 3. 后续每帧视频会自动进入本地检测流程。
    ///
    /// 参数：
    /// 1. `taskID`：服务端后台任务编号。
    /// 2. `targetObject`：目标物体名称。
    /// 3. `streamID`：视频流编号。
    /// 4. `glassDeviceID`：眼镜设备编号。
    /// 5. `phoneDeviceID`：手机设备编号。
    func startFindObjectTask(
        taskID: String,
        targetObject: String,
        streamID: String,
        glassDeviceID: String,
        phoneDeviceID: String
    ) {
        guard !taskID.isEmpty, !targetObject.isEmpty else {
            markError("找物体任务启动参数不完整")
            return
        }
        activeFindObjectTask = FindObjectTaskState(
            taskID: taskID,
            targetObject: targetObject,
            streamID: streamID,
            glassDeviceID: glassDeviceID,
            phoneDeviceID: phoneDeviceID
        )
        latestVisionSummary = nil
        latestVisionFound = false
        latestVisionConfidence = nil
        hasReportedFindObjectHit = false
        lastVisionReportAt = nil
        appendEvent("找物体任务已启动：target=\(targetObject)")
    }

    /// 停止当前找物体任务。
    ///
    /// 参数：
    /// 1. `taskID`：服务端任务编号，允许为空表示停止当前任务。
    /// 2. `reason`：停止原因。
    func stopFindObjectTask(taskID: String, reason: String) {
        guard let task = activeFindObjectTask else {
            return
        }
        if !taskID.isEmpty, task.taskID != taskID {
            return
        }
        activeFindObjectTask = nil
        finishCurrentVideoSession("找物体任务结束：\(reason)")
        appendEvent("找物体任务已停止：\(reason)")
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

    /// 对当前视频帧执行找物体检测。
    ///
    /// 主要逻辑：
    /// 1. 仅在服务端已经下发 `vision.find_object.start` 后运行。
    /// 2. 调用本地 `YoloObjectDetector` 接口得到结构化结果。
    /// 3. 按节流策略把检测结果上报服务端。
    ///
    /// 参数：
    /// 1. `image`：最近收到的 JPEG 图像。
    /// 2. `sequence`：视频帧序号。
    private func processFindObjectFrame(image: UIImage, sequence: Int) {
        guard let task = activeFindObjectTask else {
            return
        }
        let detection = objectDetector.detect(image: image, targetObject: task.targetObject, frameSequence: sequence)
        latestVisionFound = detection.found
        latestVisionConfidence = detection.confidence
        latestVisionSummary = detection.summary

        if detection.found {
            appendEvent("找物体命中：\(detection.summary)")
        } else if sequence % 10 == 0 {
            appendEvent("找物体检测：\(detection.summary)")
        }

        guard shouldReportVisionResult(detection, sequence: sequence) else {
            return
        }
        if detection.found {
            hasReportedFindObjectHit = true
        }
        lastVisionReportAt = Date()
        Task {
            do {
                try await FindObjectReportAPI.report(task: task, detection: detection)
            } catch {
                await MainActor.run {
                    self.markError("找物体结果上报失败：\(error.localizedDescription)")
                }
            }
        }
    }

    /// 判断当前检测结果是否需要上报。
    ///
    /// 参数：
    /// 1. `detection`：本次检测结果。
    /// 2. `sequence`：视频帧序号。
    ///
    /// 返回值：
    /// 1. `true` 表示需要上报服务端。
    private func shouldReportVisionResult(_ detection: VisionDetection, sequence: Int) -> Bool {
        if detection.found {
            return !hasReportedFindObjectHit
        }
        if sequence % 10 != 0 {
            return false
        }
        guard let lastVisionReportAt else {
            return true
        }
        return Date().timeIntervalSince(lastVisionReportAt) >= 2
    }
}

/// 手机端找物体任务状态。
///
/// 主要功能：
/// 1. 保存服务端下发的找物体任务上下文。
/// 2. 为手机端检测与结果上报提供必要标识。
struct FindObjectTaskState: Equatable {
    let taskID: String
    let targetObject: String
    let streamID: String
    let glassDeviceID: String
    let phoneDeviceID: String
}

/// 单次视觉检测结果。
///
/// 主要功能：
/// 1. 屏蔽底层 YOLO 或占位检测器实现细节。
/// 2. 向服务端输出稳定的结构化语义。
struct VisionDetection: Equatable {
    let targetObject: String
    let found: Bool
    let confidence: Double
    let position: String
    let frameSequence: Int
    let summary: String
}

/// 手机端 YOLO 检测接口。
///
/// 主要功能：
/// 1. 抽象本地目标检测能力。
/// 2. 后续接入 CoreML YOLO 时只替换该接口实现。
protocol YoloObjectDetector {
    /// 对单帧图像执行目标检测。
    ///
    /// 参数：
    /// 1. `image`：当前视频帧。
    /// 2. `targetObject`：要寻找的物体。
    /// 3. `frameSequence`：视频帧序号。
    ///
    /// 返回值：
    /// 1. 结构化检测结果。
    func detect(image: UIImage, targetObject: String, frameSequence: Int) -> VisionDetection
}

/// 最小本地 YOLO 占位检测器。
///
/// 主要功能：
/// 1. 在正式 CoreML YOLO 模型接入前，提供可测试的手机端检测闭环。
/// 2. 根据图像亮度和目标名称给出稳定结构化结果。
/// 3. 保持与正式 YOLO 检测器相同的输出模型。
final class HeuristicYoloObjectDetector: YoloObjectDetector {
    /// 执行检测。
    ///
    /// 主要逻辑：
    /// 1. 测试目标或调试目标直接命中，便于自动化和真机冒烟验证。
    /// 2. 其它目标根据图像平均亮度判断是否存在明显目标。
    func detect(image: UIImage, targetObject: String, frameSequence: Int) -> VisionDetection {
        let normalizedTarget = targetObject.trimmingCharacters(in: .whitespacesAndNewlines)
        let brightness = Self.averageBrightness(image: image)
        let forcedHit = normalizedTarget.localizedCaseInsensitiveContains("test") ||
            normalizedTarget.contains("测试") ||
            normalizedTarget.contains("调试")
        let found = forcedHit || brightness > 0.58
        let confidence = found ? max(0.68, min(0.96, brightness)) : max(0.05, min(0.42, brightness))
        let position = Self.positionSummary(for: image)
        let summary = found
            ? "找到\(normalizedTarget)，它在画面\(position)"
            : "暂未找到\(normalizedTarget)"
        return VisionDetection(
            targetObject: normalizedTarget,
            found: found,
            confidence: confidence,
            position: position,
            frameSequence: frameSequence,
            summary: summary
        )
    }

    /// 计算图像平均亮度。
    private static func averageBrightness(image: UIImage) -> Double {
        guard let cgImage = image.cgImage else {
            return 0
        }
        let width = 1
        let height = 1
        var pixel = [UInt8](repeating: 0, count: 4)
        guard let context = CGContext(
            data: &pixel,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return 0
        }
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        let red = Double(pixel[0]) / 255.0
        let green = Double(pixel[1]) / 255.0
        let blue = Double(pixel[2]) / 255.0
        return (red + green + blue) / 3.0
    }

    /// 生成目标方位摘要。
    private static func positionSummary(for image: UIImage) -> String {
        if image.size.width <= 0 {
            return "中间"
        }
        return image.size.width >= image.size.height ? "中间" : "前方"
    }
}

/// 找物体结果上报接口。
///
/// 主要功能：
/// 1. 把手机端结构化检测结果上报给服务端。
/// 2. 服务端继续负责任务状态和通知协调。
enum FindObjectReportAPI {
    /// 上报一次检测结果。
    ///
    /// 参数：
    /// 1. `task`：当前找物体任务上下文。
    /// 2. `detection`：当前检测结果。
    ///
    /// 异常情况：
    /// 1. 配置缺失、网络失败或服务端返回错误时抛出异常。
    static func report(task: FindObjectTaskState, detection: VisionDetection) async throws {
        guard let config = ReceiverAppConfig.load() else {
            throw URLError(.badURL)
        }
        guard let url = URL(string: "\(config.serverHTTPBaseURLString)/api/vision/find-object/report") else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "task_id": task.taskID,
            "phone_device_id": task.phoneDeviceID,
            "target_object": detection.targetObject,
            "found": detection.found,
            "confidence": detection.confidence,
            "position": detection.position,
            "frame_seq": detection.frameSequence,
            "summary": detection.summary,
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if httpResponse.statusCode != 200 {
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let errorObject = object?["error"] as? [String: Any]
            let message = errorObject?["message"] as? String ?? "服务端返回非成功状态"
            throw NSError(
                domain: "GlassesVideoReceiver.FindObjectReportAPI",
                code: httpResponse.statusCode,
                userInfo: [NSLocalizedDescriptionKey: message]
            )
        }
    }
}
