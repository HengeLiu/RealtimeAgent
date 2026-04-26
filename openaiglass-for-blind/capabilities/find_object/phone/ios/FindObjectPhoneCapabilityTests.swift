import Testing
import UIKit
@testable import GlassesVideoReceiver

/// 官方 find_object 手机能力测试。
///
/// 主要覆盖：
/// 1. 示例能力中的启发式视觉检测。
/// 2. 示例能力与手机宿主的任务停止协作。
struct FindObjectPhoneCapabilityTests {
    /// 测试目标：验证手机端最小 YOLO 检测接口可输出结构化命中结果。
    ///
    /// 测试方法：
    /// 1. 构造一张纯白测试图像。
    /// 2. 使用 `HeuristicYoloObjectDetector` 检测目标。
    ///
    /// 预期结果：
    /// 1. 检测结果命中。
    /// 2. 结果包含目标名称、置信度、位置和摘要。
    @Test
    func testHeuristicYoloDetectorReturnsStructuredHit() throws {
        let image = try #require(Self.makeSolidImage(red: 1, green: 1, blue: 1))
        let detector = HeuristicYoloObjectDetector()

        let result = detector.detect(image: image, targetObject: "测试水杯", frameSequence: 3)

        #expect(result.found)
        #expect(result.targetObject == "测试水杯")
        #expect(result.confidence > 0.6)
        #expect(result.position.isEmpty == false)
        #expect(result.summary.contains("测试水杯"))
    }

    private static func makeSolidImage(red: CGFloat, green: CGFloat, blue: CGFloat) -> UIImage? {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 4, height: 4))
        return renderer.image { context in
            UIColor(red: red, green: green, blue: blue, alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: 4, height: 4))
        }
    }
}
