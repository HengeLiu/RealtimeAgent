import Foundation
import CoreML
import UIKit

@MainActor
/// 红绿灯手机端业务插件安装器。
///
/// 主要功能：
/// 1. 把 `traffic_light_phone_task` 注册到 SDK 手机任务能力注册表。
/// 2. 让业务 Xcode 入口可以在 App 启动时显式完成插件装配。
enum TrafficLightPhoneCapabilityInstaller {
    /// 注册红绿灯手机任务运行时。
    ///
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：当前 SDK 注册接口不抛出异常；重复注册时以 SDK 注册表行为为准。
    static func install() {
        PhoneTaskCapabilityRegistry.register(taskType: "traffic_light_phone_task") {
            TrafficLightPhoneCapabilityRuntime()
        }
    }
}

/// 红绿灯识别能力运行时。
///
/// 主要功能：
/// 1. 接收 SDK 通用运行时下发的 `traffic_light_phone_task`。
/// 2. 基于相机帧执行本地红绿灯状态判断。
/// 3. 通过通用任务事件接口向服务端回传结构化结果。
@MainActor
final class TrafficLightPhoneCapabilityRuntime: PhoneTaskCapabilityRuntime {
    private var activeTask: TrafficLightPhoneTaskState?
    private var detector: TrafficLightDetector = TrafficLightDetectorFactory.makeDetector(params: [:])
    private var lastReportedSignal: TrafficSignal = .unknown
    private var lastReportAt: Date?
    private var frameStride = 1

    var activeTaskDescription: String? {
        guard let activeTask else {
            return nil
        }
        if activeTask.crossingName.isEmpty {
            return "traffic_light"
        }
        return "traffic_light / \(activeTask.crossingName)"
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
        guard taskType == "traffic_light_phone_task" else {
            return
        }
        guard !taskID.isEmpty else {
            store.markError("红绿灯手机任务缺少 task_id")
            return
        }
        activeTask = TrafficLightPhoneTaskState(
            base: PhoneTaskState(
                taskID: taskID,
                taskType: taskType,
                streamID: streamID,
                glassDeviceID: glassDeviceID,
                phoneDeviceID: phoneDeviceID
            ),
            crossingName: params["crossing_name"] as? String ?? "",
            stopAfterFirstSignal: params["stop_after_first_signal"] as? Bool ?? true
        )
        detector = TrafficLightDetectorFactory.makeDetector(params: params)
        frameStride = max(1, params["frame_stride"] as? Int ?? 1)
        latestSummary = nil
        latestSuccess = nil
        lastReportedSignal = .unknown
        lastReportAt = nil
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
        let detection = detector.detect(image: image, frameSequence: sequence)
        latestSummary = detection.summary
        latestSuccess = detection.signal != .unknown
        if !shouldReport(detection: detection) {
            return
        }
        lastReportedSignal = detection.signal
        lastReportAt = Date()
        var payload: [String: Any] = [
            "signal": detection.signal.rawValue,
            "confidence": detection.confidence,
            "frame_seq": detection.frameSequence,
            "summary": detection.summary,
            "crossing_name": activeTask.crossingName,
            "source": detection.source,
        ]
        if let label = detection.label {
            payload["label"] = label
        }
        Task {
            do {
                try await PhoneTaskEventReportAPI.report(
                    taskID: activeTask.base.taskID,
                    phoneDeviceID: activeTask.base.phoneDeviceID,
                    eventName: "phone.vision.traffic_light.result",
                    payload: payload
                )
            } catch {
                await MainActor.run {
                    store.markError("红绿灯识别结果上报失败：\(error.localizedDescription)")
                }
            }
        }
    }

    private func shouldReport(detection: TrafficLightDetection) -> Bool {
        if detection.signal != .unknown {
            return detection.signal != lastReportedSignal
        }
        guard let lastReportAt else {
            return true
        }
        return Date().timeIntervalSince(lastReportAt) >= 2
    }
}

struct TrafficLightPhoneTaskState: Equatable {
    let base: PhoneTaskState
    let crossingName: String
    let stopAfterFirstSignal: Bool
}

enum TrafficSignal: String {
    case red
    case yellow
    case green
    case unknown
}

struct TrafficLightDetection: Equatable {
    let signal: TrafficSignal
    let confidence: Double
    let frameSequence: Int
    let summary: String
    let source: String
    let label: String?
}

protocol TrafficLightDetector {
    func detect(image: UIImage, frameSequence: Int) -> TrafficLightDetection
}

enum TrafficLightDetectorFactory {
    /// 创建红绿灯检测器。
    ///
    /// 主要逻辑：
    /// 1. 优先加载 App Bundle 中已编译的 CoreML YOLO 模型。
    /// 2. 模型资源不存在或加载失败时回退启发式检测，保证手机任务链路仍能自测。
    /// 3. 支持服务端后续通过 `model_name` 和 `score_threshold` 覆盖默认配置。
    ///
    /// 参数：
    /// 1. `params`：服务端下发的手机任务参数。
    ///
    /// 返回值：
    /// 1. 可执行单帧红绿灯检测的检测器。
    ///
    /// 异常情况：
    /// 1. 本函数不向外抛出异常；模型失败时回退启发式检测。
    static func makeDetector(params: [String: Any]) -> TrafficLightDetector {
        let modelName = (params["model_name"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let scoreThreshold = params["score_threshold"] as? Double ?? 0.35
        if let detector = CoreMLTrafficLightDetector.makeDefault(
            preferredModelName: modelName,
            scoreThreshold: scoreThreshold
        ) {
            return detector
        }
        return HeuristicTrafficLightDetector()
    }
}

final class CoreMLTrafficLightDetector: TrafficLightDetector {
    private let model: MLModel
    private let scoreThreshold: Double
    private let inputSize = 640
    private let outputName = "var_1363"
    private let labels = ["blank", "countdown_blank", "countdown_go", "countdown_stop", "crossing", "go", "stop"]

    /// 创建 CoreML 红绿灯检测器。
    ///
    /// 主要功能：加载本地 `TrafficLightYOLO.mlmodelc`，并解析未内置 NMS 的 YOLO 原始输出。
    /// 主要属性：`model` 保存 CoreML 模型，`scoreThreshold` 控制最低置信度。
    init(compiledModelURL: URL, scoreThreshold: Double) throws {
        model = try MLModel(contentsOf: compiledModelURL)
        self.scoreThreshold = scoreThreshold
    }

    /// 按约定加载业务 App 内置红绿灯模型。
    ///
    /// 参数：
    /// 1. `preferredModelName`：可选模型名，不带扩展名，例如 `TrafficLightYOLO`。
    /// 2. `scoreThreshold`：最低置信度阈值。
    ///
    /// 返回值：
    /// 1. 成功加载时返回 CoreML 检测器；否则返回 `nil`。
    ///
    /// 异常情况：
    /// 1. 模型不存在或加载失败时返回 `nil`，由调用方回退启发式检测。
    static func makeDefault(preferredModelName: String?, scoreThreshold: Double) -> CoreMLTrafficLightDetector? {
        let names = [
            preferredModelName,
            "TrafficLightYOLO",
            "traffic_light_yolo",
            "trafficlight",
        ].compactMap { $0 }.filter { !$0.isEmpty }
        for name in names {
            if let url = Bundle.main.url(forResource: name, withExtension: "mlmodelc") {
                return try? CoreMLTrafficLightDetector(compiledModelURL: url, scoreThreshold: scoreThreshold)
            }
        }
        return nil
    }

    func detect(image: UIImage, frameSequence: Int) -> TrafficLightDetection {
        guard let pixelBuffer = Self.makePixelBuffer(image: image, size: inputSize) else {
            return TrafficLightDetection(
                signal: .unknown,
                confidence: 0,
                frameSequence: frameSequence,
                summary: "无法解码当前画面",
                source: "coreml_yolo_error",
                label: nil
            )
        }
        do {
            let input = try MLDictionaryFeatureProvider(dictionary: ["image": pixelBuffer])
            let result = try model.prediction(from: input)
            guard let output = result.featureValue(for: outputName)?.multiArrayValue else {
                return TrafficLightDetection(
                    signal: .unknown,
                    confidence: 0,
                    frameSequence: frameSequence,
                    summary: "红绿灯模型输出格式不符合预期",
                    source: "coreml_yolo_error",
                    label: nil
                )
            }
            return decode(output: output, frameSequence: frameSequence)
        } catch {
            return TrafficLightDetection(
                signal: .unknown,
                confidence: 0,
                frameSequence: frameSequence,
                summary: "红绿灯模型推理失败：\(error.localizedDescription)",
                source: "coreml_yolo_error",
                label: nil
            )
        }
    }

    private func decode(output: MLMultiArray, frameSequence: Int) -> TrafficLightDetection {
        guard output.shape.count == 3, output.shape[1].intValue >= 11 else {
            return TrafficLightDetection(
                signal: .unknown,
                confidence: 0,
                frameSequence: frameSequence,
                summary: "红绿灯模型输出维度不符合预期",
                source: "coreml_yolo_error",
                label: nil
            )
        }
        let candidateCount = output.shape[2].intValue
        var best: (labelIndex: Int, confidence: Double)?
        for candidate in 0..<candidateCount {
            for labelIndex in 0..<labels.count {
                let signal = Self.signal(for: labels[labelIndex])
                if signal == .unknown {
                    continue
                }
                let confidence = value(output, channel: 4 + labelIndex, candidate: candidate)
                if confidence < scoreThreshold {
                    continue
                }
                if best == nil || confidence > best!.confidence {
                    best = (labelIndex, confidence)
                }
            }
        }
        guard let best else {
            return TrafficLightDetection(
                signal: .unknown,
                confidence: 0,
                frameSequence: frameSequence,
                summary: "暂未识别到明确红绿灯状态",
                source: "coreml_yolo",
                label: nil
            )
        }
        let label = labels[best.labelIndex]
        let signal = Self.signal(for: label)
        return TrafficLightDetection(
            signal: signal,
            confidence: best.confidence,
            frameSequence: frameSequence,
            summary: Self.summary(signal: signal, label: label),
            source: "coreml_yolo",
            label: label
        )
    }

    private func value(_ output: MLMultiArray, channel: Int, candidate: Int) -> Double {
        let strides = output.strides.map(\.intValue)
        let offset = channel * strides[1] + candidate * strides[2]
        return output.dataPointer.advanced(by: offset * MemoryLayout<Float32>.stride)
            .assumingMemoryBound(to: Float32.self)
            .pointee
            .isFiniteValue
    }

    private static func signal(for label: String) -> TrafficSignal {
        switch label {
        case "go", "countdown_go":
            return .green
        case "stop", "countdown_stop":
            return .red
        default:
            return .unknown
        }
    }

    private static func summary(signal: TrafficSignal, label: String) -> String {
        switch signal {
        case .green:
            return "前方绿灯，可以谨慎通过"
        case .red:
            return "前方红灯，请停下等待"
        case .yellow:
            return "前方黄灯，请暂缓通过"
        case .unknown:
            return "识别到\(label)，但不是明确通行信号"
        }
    }

    private static func makePixelBuffer(image: UIImage, size: Int) -> CVPixelBuffer? {
        let attrs = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
        ] as CFDictionary
        var pixelBuffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            size,
            size,
            kCVPixelFormatType_32BGRA,
            attrs,
            &pixelBuffer
        )
        guard status == kCVReturnSuccess, let pixelBuffer else {
            return nil
        }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer {
            CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
        }
        guard let context = CGContext(
            data: CVPixelBufferGetBaseAddress(pixelBuffer),
            width: size,
            height: size,
            bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(pixelBuffer),
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
        ) else {
            return nil
        }
        context.clear(CGRect(x: 0, y: 0, width: size, height: size))
        guard let cgImage = image.cgImage else {
            return nil
        }
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: size, height: size))
        return pixelBuffer
    }
}

private extension Float32 {
    var isFiniteValue: Double {
        if isFinite {
            return Double(self)
        }
        return 0
    }
}

final class HeuristicTrafficLightDetector: TrafficLightDetector {
    func detect(image: UIImage, frameSequence: Int) -> TrafficLightDetection {
        let color = Self.averageColor(image: image)
        let signal = Self.signal(from: color)
        let confidence = Self.confidence(signal: signal, color: color)
        return TrafficLightDetection(
            signal: signal,
            confidence: confidence,
            frameSequence: frameSequence,
            summary: Self.summary(signal: signal),
            source: "heuristic",
            label: nil
        )
    }

    private static func averageColor(image: UIImage) -> (red: Double, green: Double, blue: Double) {
        guard let cgImage = image.cgImage else {
            return (0, 0, 0)
        }
        var pixel = [UInt8](repeating: 0, count: 4)
        guard let context = CGContext(
            data: &pixel,
            width: 1,
            height: 1,
            bitsPerComponent: 8,
            bytesPerRow: 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return (0, 0, 0)
        }
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: 1, height: 1))
        return (
            Double(pixel[0]) / 255.0,
            Double(pixel[1]) / 255.0,
            Double(pixel[2]) / 255.0
        )
    }

    private static func signal(from color: (red: Double, green: Double, blue: Double)) -> TrafficSignal {
        if color.green > 0.46, color.green > color.red * 1.18 {
            return .green
        }
        if color.red > 0.48, color.green > 0.32, color.blue < 0.35 {
            return .yellow
        }
        if color.red > 0.46, color.red > color.green * 1.18 {
            return .red
        }
        return .unknown
    }

    private static func confidence(
        signal: TrafficSignal,
        color: (red: Double, green: Double, blue: Double)
    ) -> Double {
        switch signal {
        case .green:
            return min(0.96, max(0.62, color.green))
        case .yellow:
            return min(0.94, max(0.60, (color.red + color.green) / 2.0))
        case .red:
            return min(0.96, max(0.62, color.red))
        case .unknown:
            return 0
        }
    }

    private static func summary(signal: TrafficSignal) -> String {
        switch signal {
        case .green:
            return "前方绿灯，可以谨慎通过"
        case .yellow:
            return "前方黄灯，请暂缓通过"
        case .red:
            return "前方红灯，请停下等待"
        case .unknown:
            return "暂未识别到明确红绿灯状态"
        }
    }
}
