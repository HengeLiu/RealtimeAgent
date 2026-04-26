import Foundation
import UIKit

@MainActor
private let registerFindObjectPhoneCapabilityInstaller: Void = {
    PhoneCapabilityBootstrap.registerInstaller {
        PhoneCapabilityRuntimeFactory.register {
            FindObjectPhoneCapabilityRuntime()
        }
    }
}()

/// 找物体能力运行时。
///
/// 主要功能：
/// 1. 保存示例任务状态。
/// 2. 执行本地占位检测。
/// 3. 将结构化结果通过通用任务事件接口上报给服务端。
@MainActor
final class FindObjectPhoneCapabilityRuntime: PhoneTaskCapabilityRuntime {
    private var activeTask: FindObjectPhoneTaskState?
    private let detector: YoloObjectDetector = HeuristicYoloObjectDetector()
    private var lastReportAt: Date?
    private var hasReportedHit = false

    var activeTaskDescription: String? {
        guard let activeTask else {
            return nil
        }
        return "find_object / \(activeTask.targetObject)"
    }

    var latestSummary: String?
    var latestSuccess: Bool?

    func startTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        streamID: String,
        glassDeviceID: String,
        phoneDeviceID: String,
        params: [String: Any]
    ) {
        guard taskType == "find_object_phone_task" else {
            return
        }
        let targetObject = (params["target_object"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !taskID.isEmpty, !targetObject.isEmpty else {
            store.markError("手机任务启动参数不完整")
            return
        }
        activeTask = FindObjectPhoneTaskState(
            base: PhoneTaskState(
                taskID: taskID,
                taskType: taskType,
                streamID: streamID,
                glassDeviceID: glassDeviceID,
                phoneDeviceID: phoneDeviceID
            ),
            targetObject: targetObject
        )
        latestSummary = nil
        latestSuccess = nil
        lastReportAt = nil
        hasReportedHit = false
    }

    func stopTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        reason: String
    ) {
        guard let activeTask else {
            return
        }
        if !taskID.isEmpty, activeTask.base.taskID != taskID {
            return
        }
        if !taskType.isEmpty, activeTask.base.taskType != taskType {
            return
        }
        self.activeTask = nil
    }

    func processFrame(
        store: CameraStreamStore,
        image: UIImage,
        sequence: Int
    ) {
        guard let activeTask else {
            return
        }
        let detection = detector.detect(
            image: image,
            targetObject: activeTask.targetObject,
            frameSequence: sequence
        )
        latestSummary = detection.summary
        latestSuccess = detection.found

        if !shouldReport(detection: detection, sequence: sequence) {
            return
        }
        if detection.found {
            hasReportedHit = true
        }
        lastReportAt = Date()
        Task {
            do {
                try await PhoneTaskEventReportAPI.report(
                    taskID: activeTask.base.taskID,
                    phoneDeviceID: activeTask.base.phoneDeviceID,
                    eventName: "phone.vision.find_object.result",
                    payload: [
                        "target_object": detection.targetObject,
                        "found": detection.found,
                        "confidence": detection.confidence,
                        "position": detection.position,
                        "frame_seq": detection.frameSequence,
                        "summary": detection.summary,
                    ]
                )
            } catch {
                await MainActor.run {
                    store.markError("手机任务结果上报失败：\(error.localizedDescription)")
                }
            }
        }
    }

    private func shouldReport(detection: VisionDetection, sequence: Int) -> Bool {
        if detection.found {
            return !hasReportedHit
        }
        if sequence % 10 != 0 {
            return false
        }
        guard let lastReportAt else {
            return true
        }
        return Date().timeIntervalSince(lastReportAt) >= 2
    }
}

struct FindObjectPhoneTaskState: Equatable {
    let base: PhoneTaskState
    let targetObject: String
}

struct VisionDetection: Equatable {
    let targetObject: String
    let found: Bool
    let confidence: Double
    let position: String
    let frameSequence: Int
    let summary: String
}

protocol YoloObjectDetector {
    func detect(image: UIImage, targetObject: String, frameSequence: Int) -> VisionDetection
}

final class HeuristicYoloObjectDetector: YoloObjectDetector {
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

    private static func positionSummary(for image: UIImage) -> String {
        if image.size.width <= 0 {
            return "中间"
        }
        return image.size.width >= image.size.height ? "中间" : "前方"
    }
}
