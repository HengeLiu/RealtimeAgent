import Foundation
import CoreML
import ImageIO
import UIKit
import Vision

@MainActor
/// 找物体手机端业务插件安装器。
///
/// 主要功能：
/// 1. 把 `find_object_phone_task` 注册到 SDK 手机任务能力注册表。
/// 2. 让业务 Xcode 入口可以在 App 启动时显式完成插件装配。
enum FindObjectPhoneCapabilityInstaller {
    /// 注册找物体手机任务运行时。
    ///
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：当前 SDK 注册接口不抛出异常；重复注册时以 SDK 注册表行为为准。
    static func install() {
        PhoneTaskCapabilityRegistry.register(taskType: "find_object_phone_task") {
            FindObjectPhoneCapabilityRuntime()
        }
    }
}

/// 找物体能力运行时。
///
/// 主要功能：
/// 1. 保存示例任务状态。
/// 2. 优先执行 CoreML YOLO 检测，无模型时回退启发式检测。
/// 3. 将结构化结果通过通用任务事件接口上报给服务端。
@MainActor
final class FindObjectPhoneCapabilityRuntime: PhoneTaskCapabilityRuntime {
    private var activeTask: FindObjectPhoneTaskState?
    private var detector: YoloObjectDetector = HeuristicYoloObjectDetector()
    private var lastReportAt: Date?
    private var hasReportedHit = false
    private var frameStride = 1

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
        detector = FindObjectDetectorFactory.makeDetector(params: params)
        frameStride = max(1, params["frame_stride"] as? Int ?? 1)
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
        if frameStride > 1, sequence % frameStride != 0 {
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
        var payload: [String: Any] = [
            "target_object": detection.targetObject,
            "found": detection.found,
            "confidence": detection.confidence,
            "position": detection.position,
            "frame_seq": detection.frameSequence,
            "source": detection.source,
            "summary": detection.summary,
        ]
        if let label = detection.label {
            payload["label"] = label
        }
        if let boundingBox = detection.boundingBox {
            payload["bbox"] = boundingBox
        }
        Task {
            do {
                try await PhoneTaskEventReportAPI.report(
                    taskID: activeTask.base.taskID,
                    phoneDeviceID: activeTask.base.phoneDeviceID,
                    eventName: "phone.vision.find_object.result",
                    payload: payload
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
    let label: String?
    let boundingBox: [String: Double]?
    let frameSequence: Int
    let source: String
    let summary: String
}

protocol YoloObjectDetector {
    func detect(image: UIImage, targetObject: String, frameSequence: Int) -> VisionDetection
}

enum FindObjectDetectorFactory {
    /// 创建手机侧找物检测器。
    ///
    /// 主要逻辑：
    /// 1. 优先加载业务 App 资源里的 CoreML YOLO 模型。
    /// 2. 没有模型资源时回退启发式检测，保证注册、视频和事件回流链路仍可自测。
    /// 3. 不在这里下载模型或访问 SDK 内部路径，模型交付由业务 App 资源负责。
    ///
    /// 参数：
    /// 1. `params`：服务端下发的手机任务参数，可包含 `model_name` 和 `score_threshold`。
    ///
    /// 返回值：
    /// 1. 可执行单帧检测的检测器。
    ///
    /// 异常情况：
    /// 1. 模型不存在或加载失败时回退启发式检测，不中断手机任务启动。
    static func makeDetector(params: [String: Any]) -> YoloObjectDetector {
        let modelName = (params["model_name"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let scoreThreshold = params["score_threshold"] as? Double ?? 0.25
        if let detector = CoreMLYoloObjectDetector.makeDefault(
            preferredModelName: modelName,
            scoreThreshold: scoreThreshold
        ) {
            return detector
        }
        return HeuristicYoloObjectDetector()
    }
}

final class CoreMLYoloObjectDetector: YoloObjectDetector {
    private let model: VNCoreMLModel
    private let scoreThreshold: Double

    /// 创建 CoreML YOLO 检测器。
    ///
    /// 主要功能：封装 Vision + CoreML 推理入口，让业务插件直接处理眼镜视频帧。
    /// 主要属性：`model` 保存已加载的 Vision 模型，`scoreThreshold` 控制最低置信度。
    init(compiledModelURL: URL, scoreThreshold: Double) throws {
        let mlModel = try MLModel(contentsOf: compiledModelURL)
        model = try VNCoreMLModel(for: mlModel)
        self.scoreThreshold = scoreThreshold
    }

    /// 按约定加载业务 App 内置 YOLO 模型。
    ///
    /// 参数：
    /// 1. `preferredModelName`：可选模型名，不带扩展名，例如 `FindObjectYOLO`。
    /// 2. `scoreThreshold`：最低置信度阈值。
    ///
    /// 返回值：
    /// 1. 成功加载时返回 CoreML 检测器；否则返回 `nil`。
    ///
    /// 异常情况：
    /// 1. 本函数吞掉模型加载异常并返回 `nil`，避免无模型开发环境无法启动手机端。
    static func makeDefault(preferredModelName: String?, scoreThreshold: Double) -> CoreMLYoloObjectDetector? {
        let names = [
            preferredModelName,
            "FindObjectYOLO",
            "find_object_yolo",
            "YOLOv8FindObject",
            "yolo_find_object",
        ].compactMap { $0 }.filter { !$0.isEmpty }
        for name in names {
            if let url = Bundle.main.url(forResource: name, withExtension: "mlmodelc") {
                return try? CoreMLYoloObjectDetector(compiledModelURL: url, scoreThreshold: scoreThreshold)
            }
        }
        return nil
    }

    func detect(image: UIImage, targetObject: String, frameSequence: Int) -> VisionDetection {
        guard let cgImage = image.cgImage else {
            return VisionDetection(
                targetObject: targetObject,
                found: false,
                confidence: 0,
                position: "unknown",
                label: nil,
                boundingBox: nil,
                frameSequence: frameSequence,
                source: "coreml_yolo",
                summary: "无法解码当前画面"
            )
        }

        let request = VNCoreMLRequest(model: model)
        request.imageCropAndScaleOption = .scaleFit
        let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up)
        do {
            try handler.perform([request])
        } catch {
            return VisionDetection(
                targetObject: targetObject,
                found: false,
                confidence: 0,
                position: "unknown",
                label: nil,
                boundingBox: nil,
                frameSequence: frameSequence,
                source: "coreml_yolo_error",
                summary: "YOLO 检测失败：\(error.localizedDescription)"
            )
        }

        let observations = (request.results ?? []).compactMap { $0 as? VNRecognizedObjectObservation }
        guard let best = Self.bestObservation(
            observations: observations,
            targetObject: targetObject,
            scoreThreshold: scoreThreshold
        ) else {
            return VisionDetection(
                targetObject: targetObject,
                found: false,
                confidence: 0,
                position: "unknown",
                label: nil,
                boundingBox: nil,
                frameSequence: frameSequence,
                source: "coreml_yolo",
                summary: "暂未找到\(targetObject)"
            )
        }

        let rect = best.observation.boundingBox
        let position = FindObjectGuidance.positionSummary(
            normalizedCenterX: Double(rect.midX),
            normalizedCenterYFromBottom: Double(rect.midY)
        )
        let confidence = Double(best.confidence)
        return VisionDetection(
            targetObject: targetObject,
            found: true,
            confidence: confidence,
            position: position,
            label: best.label,
            boundingBox: [
                "x": Double(rect.origin.x),
                "y": Double(rect.origin.y),
                "width": Double(rect.width),
                "height": Double(rect.height),
            ],
            frameSequence: frameSequence,
            source: "coreml_yolo",
            summary: FindObjectGuidance.summary(targetObject: targetObject, position: position)
        )
    }

    private static func bestObservation(
        observations: [VNRecognizedObjectObservation],
        targetObject: String,
        scoreThreshold: Double
    ) -> (observation: VNRecognizedObjectObservation, label: String, confidence: Float)? {
        var best: (observation: VNRecognizedObjectObservation, label: String, confidence: Float)?
        for observation in observations {
            guard let label = observation.labels.first else {
                continue
            }
            let confidence = label.confidence * observation.confidence
            if Double(confidence) < scoreThreshold {
                continue
            }
            if !FindObjectLabelMatcher.matches(targetObject: targetObject, label: label.identifier) {
                continue
            }
            if best == nil || confidence > best!.confidence {
                best = (observation, label.identifier, confidence)
            }
        }
        return best
    }
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
            label: found ? "heuristic_foreground" : nil,
            boundingBox: nil,
            frameSequence: frameSequence,
            source: "heuristic",
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

enum FindObjectGuidance {
    /// 根据 Vision 归一化中心点生成方向摘要。
    ///
    /// 参数：
    /// 1. `normalizedCenterX`：目标中心横坐标，范围约为 0 到 1。
    /// 2. `normalizedCenterYFromBottom`：目标中心纵坐标，Vision 坐标系原点在左下。
    ///
    /// 返回值：
    /// 1. 中文方向词，用于服务端播报。
    ///
    /// 异常情况：
    /// 1. 输入越界时按原值判断，不主动抛出异常。
    static func positionSummary(normalizedCenterX: Double, normalizedCenterYFromBottom: Double) -> String {
        let threshold = 0.12
        let dx = normalizedCenterX - 0.5
        let dy = normalizedCenterYFromBottom - 0.5
        if abs(dx) <= threshold, abs(dy) <= threshold {
            return "中间"
        }
        if abs(dx) >= abs(dy) {
            return dx > 0 ? "右侧" : "左侧"
        }
        return dy > 0 ? "上方" : "下方"
    }

    static func summary(targetObject: String, position: String) -> String {
        if position == "中间" {
            return "找到\(targetObject)，目标基本在画面中间"
        }
        return "找到\(targetObject)，它在画面\(position)"
    }
}

enum FindObjectLabelMatcher {
    private static let aliases: [String: [String]] = [
        "手机": ["phone", "cell phone", "mobile phone", "smartphone", "iphone"],
        "水杯": ["cup", "mug", "bottle"],
        "杯子": ["cup", "mug", "bottle"],
        "钥匙": ["key", "keys"],
        "钱包": ["wallet", "purse"],
        "门卡": ["card", "id card", "access card"],
        "书": ["book"],
        "药": ["medicine", "pill bottle", "bottle"],
    ]

    /// 判断 YOLO 标签是否可视为用户目标。
    ///
    /// 主要逻辑：
    /// 1. 对中英文标签做空白、连字符和大小写归一化。
    /// 2. 用少量业务常见物品别名连接中文目标和 COCO/YOLO 英文标签。
    ///
    /// 参数：
    /// 1. `targetObject`：用户说出的目标物体。
    /// 2. `label`：模型输出标签。
    ///
    /// 返回值：
    /// 1. 匹配时返回 `true`。
    ///
    /// 异常情况：
    /// 1. 空目标或空标签返回 `false`。
    static func matches(targetObject: String, label: String) -> Bool {
        let target = normalize(targetObject)
        let normalizedLabel = normalize(label)
        if target.isEmpty || normalizedLabel.isEmpty {
            return false
        }
        if target == normalizedLabel || target.contains(normalizedLabel) || normalizedLabel.contains(target) {
            return true
        }
        let candidates = candidateLabels(for: targetObject)
        return candidates.contains(normalizedLabel)
    }

    static func candidateLabels(for targetObject: String) -> Set<String> {
        let target = normalize(targetObject)
        var result: Set<String> = [target]
        for (name, values) in aliases {
            let normalizedName = normalize(name)
            let normalizedValues = Set(values.map(normalize))
            if target.contains(normalizedName) || normalizedValues.contains(target) {
                result.insert(normalizedName)
                result.formUnion(normalizedValues)
            }
        }
        return result
    }

    private static func normalize(_ text: String) -> String {
        text
            .lowercased()
            .filter { !$0.isWhitespace && $0 != "-" && $0 != "_" && $0 != "/" }
    }
}
