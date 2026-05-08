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
        #expect(result.source == "heuristic")
        #expect(result.summary.contains("测试水杯"))
    }

    /// 测试目标：验证中文找物目标能匹配常见 YOLO 英文标签。
    ///
    /// 测试方法：
    /// 1. 直接调用标签匹配器。
    /// 2. 使用“手机”和 `cell phone` 这组真实 COCO 标签。
    ///
    /// 预期结果：
    /// 1. 中文目标和英文检测标签匹配成功。
    @Test
    func testFindObjectLabelMatcherMapsChineseTargetToYoloLabel() {
        #expect(FindObjectLabelMatcher.matches(targetObject: "手机", label: "cell phone"))
        #expect(FindObjectLabelMatcher.matches(targetObject: "水杯", label: "cup"))
        #expect(!FindObjectLabelMatcher.matches(targetObject: "钱包", label: "traffic light"))
    }

    /// 测试目标：验证检测框中心点能生成稳定的中文方向。
    ///
    /// 测试方法：
    /// 1. 构造偏左、偏右和居中的归一化中心点。
    ///
    /// 预期结果：
    /// 1. 返回的方向词可直接进入服务端播报摘要。
    @Test
    func testFindObjectGuidanceBuildsPositionSummary() {
        #expect(FindObjectGuidance.positionSummary(normalizedCenterX: 0.2, normalizedCenterYFromBottom: 0.5) == "左侧")
        #expect(FindObjectGuidance.positionSummary(normalizedCenterX: 0.8, normalizedCenterYFromBottom: 0.5) == "右侧")
        #expect(FindObjectGuidance.positionSummary(normalizedCenterX: 0.51, normalizedCenterYFromBottom: 0.49) == "中间")
    }

    private static func makeSolidImage(red: CGFloat, green: CGFloat, blue: CGFloat) -> UIImage? {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 4, height: 4))
        return renderer.image { context in
            UIColor(red: red, green: green, blue: blue, alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: 4, height: 4))
        }
    }
}
