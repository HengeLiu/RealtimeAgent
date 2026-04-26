import Foundation
import UIKit

@MainActor
private let registerTrafficLightPhoneCapabilityInstaller: Void = {
    PhoneCapabilityBootstrap.registerInstaller {
        PhoneCapabilityRuntimeFactory.register {
            TrafficLightPhoneCapabilityRuntime()
        }
    }
}()

/// 红绿灯识别能力运行时。
///
/// 主要功能：
/// 1. 接收 SDK 通用运行时下发的 `traffic_light_phone_task`。
/// 2. 基于相机帧执行本地红绿灯状态判断。
/// 3. 通过通用任务事件接口向服务端回传结构化结果。
@MainActor
final class TrafficLightPhoneCapabilityRuntime: PhoneTaskCapabilityRuntime {
    private var activeTask: TrafficLightPhoneTaskState?
    private let detector: TrafficLightDetector = HeuristicTrafficLightDetector()
    private var lastReportedSignal: TrafficSignal = .unknown
    private var lastReportAt: Date?

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
        let detection = detector.detect(image: image, frameSequence: sequence)
        latestSummary = detection.summary
        latestSuccess = detection.signal != .unknown
        if !shouldReport(detection: detection) {
            return
        }
        lastReportedSignal = detection.signal
        lastReportAt = Date()
        Task {
            do {
                try await PhoneTaskEventReportAPI.report(
                    taskID: activeTask.base.taskID,
                    phoneDeviceID: activeTask.base.phoneDeviceID,
                    eventName: "phone.vision.traffic_light.result",
                    payload: [
                        "signal": detection.signal.rawValue,
                        "confidence": detection.confidence,
                        "frame_seq": detection.frameSequence,
                        "summary": detection.summary,
                        "crossing_name": activeTask.crossingName,
                    ]
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
}

protocol TrafficLightDetector {
    func detect(image: UIImage, frameSequence: Int) -> TrafficLightDetection
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
            summary: Self.summary(signal: signal)
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

