import Foundation
import Testing
import UIKit
@testable import GlassesVideoReceiver

private final class TestPhoneCapabilityRuntime: PhoneTaskCapabilityRuntime {
    private(set) var startedTask: PhoneTaskState?
    private(set) var stoppedTaskID: String?
    private(set) var processedSequences: [Int] = []

    var activeTaskDescription: String? {
        guard let startedTask else { return nil }
        return "\(startedTask.taskType) / \(startedTask.taskID)"
    }

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
        startedTask = PhoneTaskState(
            taskID: taskID,
            taskType: taskType,
            streamID: streamID,
            glassDeviceID: glassDeviceID,
            phoneDeviceID: phoneDeviceID
        )
        stoppedTaskID = nil
    }

    func stopTask(
        store: CameraStreamStore,
        taskID: String,
        taskType: String,
        reason: String
    ) {
        guard startedTask?.taskID == taskID else {
            return
        }
        startedTask = nil
        stoppedTaskID = taskID
    }

    func processFrame(
        store: CameraStreamStore,
        image: UIImage,
        sequence: Int
    ) {
        processedSequences.append(sequence)
    }
}

/// iOS 视频回显核心逻辑测试。
///
/// 主要覆盖：
/// 1. 媒体帧解析。
/// 2. WebSocket 掩码帧解析。
/// 3. WebSocket 握手计算。
struct GlassesVideoReceiverTests {
    /// 测试目标：验证 `MediaFrameDecoder` 可正确解析合法相机帧。
    ///
    /// 测试方法：
    /// 1. 构造合法 `MediaFrame(camera_frame)` 原始字节。
    /// 2. 调用解码器读取结构化结果。
    ///
    /// 预期结果：
    /// 1. `streamID`、`sequence` 与 `payload` 均与构造值一致。
    @Test
    func testDecodeCameraFrameSuccess() throws {
        let payload = Data([0xFF, 0xD8, 0xFF, 0xD9])
        let raw = try Self.makeMediaFrameRaw(payload: payload, payloadSize: payload.count)

        let frame = try MediaFrameDecoder.decodeCameraFrame(from: raw)

        #expect(frame.streamID == "stream_cam_001")
        #expect(frame.sequence == 7)
        #expect(frame.codec == "jpeg")
        #expect(frame.payload == payload)
    }

    /// 测试目标：验证 `MediaFrameDecoder` 在长度不一致时拒绝非法数据。
    ///
    /// 测试方法：
    /// 1. 构造 `payload_size` 大于真实负载的媒体帧。
    /// 2. 调用解码器。
    ///
    /// 预期结果：
    /// 1. 返回 `invalidPayloadSize` 错误。
    @Test
    func testDecodeCameraFrameRejectsInvalidPayloadSize() throws {
        let payload = Data([0xFF, 0xD8, 0xFF, 0xD9])
        let raw = try Self.makeMediaFrameRaw(payload: payload, payloadSize: payload.count + 2)

        #expect(throws: MediaFrameDecodeError.invalidPayloadSize(expected: 6, actual: 4)) {
            try MediaFrameDecoder.decodeCameraFrame(from: raw)
        }
    }

    /// 测试目标：验证 `WebSocketFrameParser` 可正确解析掩码二进制帧。
    ///
    /// 测试方法：
    /// 1. 构造带掩码的二进制 WebSocket 帧。
    /// 2. 调用解析器。
    ///
    /// 预期结果：
    /// 1. 返回的二进制消息负载与原始输入一致。
    @Test
    func testWebSocketFrameParserParsesMaskedBinaryMessage() {
        let payload = Data([0x01, 0x02, 0x03, 0x04])
        let mask = Data([0x11, 0x22, 0x33, 0x44])
        let maskedPayload = Data(payload.enumerated().map { index, byte in
            byte ^ mask[index % 4]
        })

        var raw = Data([0x82, 0x80 | UInt8(payload.count)])
        raw.append(mask)
        raw.append(maskedPayload)

        let parser = WebSocketFrameParser()
        let events = parser.append(raw)

        #expect(events == [.binary(payload)])
    }

    /// 测试目标：验证 `WebSocketFrameParser` 可正确拼接分片二进制消息。
    ///
    /// 测试方法：
    /// 1. 构造一个首帧 opcode=2、末帧 opcode=0 的两段分片消息。
    /// 2. 分两次喂给解析器。
    ///
    /// 预期结果：
    /// 1. 首次追加时不产出事件。
    /// 2. 第二次追加后返回完整的二进制消息。
    @Test
    func testWebSocketFrameParserReassemblesFragmentedBinaryMessage() {
        let firstPayload = Data([0x01, 0x02, 0x03])
        let secondPayload = Data([0x04, 0x05])
        let parser = WebSocketFrameParser()

        let firstFrame = Data([0x02, UInt8(firstPayload.count)]) + firstPayload
        let secondFrame = Data([0x80, UInt8(secondPayload.count)]) + secondPayload

        let firstEvents = parser.append(firstFrame)
        let secondEvents = parser.append(secondFrame)

        #expect(firstEvents.isEmpty)
        #expect(secondEvents == [.binary(firstPayload + secondPayload)])
    }

    /// 测试目标：验证握手应答值计算正确。
    ///
    /// 测试方法：
    /// 1. 使用 RFC 示例 key 计算 `Sec-WebSocket-Accept`。
    ///
    /// 预期结果：
    /// 1. 输出结果与 RFC 示例一致。
    @Test
    func testWebSocketAcceptValueMatchesRFCExample() {
        let result = WebSocketHandshake.acceptValue(for: "dGhlIHNhbXBsZSBub25jZQ==")
        #expect(result == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
    }

    /// 测试目标：验证握手响应严格使用 CRLF 分隔。
    ///
    /// 测试方法：
    /// 1. 生成一份完整握手响应。
    /// 2. 检查响应中包含标准 HTTP 状态行、关键头和结尾空行。
    ///
    /// 预期结果：
    /// 1. 每一行之间都使用 `\r\n`。
    /// 2. 响应末尾以 `\r\n\r\n` 结束。
    @Test
    func testWebSocketResponseDataUsesCRLF() throws {
        let responseData = WebSocketHandshake.responseData(for: "dGhlIHNhbXBsZSBub25jZQ==")
        let responseText = try #require(String(data: responseData, encoding: .utf8))

        #expect(responseText.contains("HTTP/1.1 101 Switching Protocols\r\n"))
        #expect(responseText.contains("Upgrade: websocket\r\n"))
        #expect(responseText.contains("Connection: Upgrade\r\n"))
        #expect(responseText.contains("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n"))
        #expect(responseText.hasSuffix("\r\n\r\n"))
    }

    /// 测试目标：验证通用手机任务停止消息会同步结束当前视频会话。
    ///
    /// 测试方法：
    /// 1. 构造一个正在接收视频且存在通用手机任务的页面状态。
    /// 2. 调用 `stopPhoneTask`。
    ///
    /// 预期结果：
    /// 1. 当前手机任务被清空。
    /// 2. 视频连接状态被重置为等待下一次接收。
    @MainActor
    @Test
    func testStopPhoneTaskAlsoFinishesVideoSession() {
        let capabilityRuntime = TestPhoneCapabilityRuntime()
        let store = CameraStreamStore(capabilityRuntime: capabilityRuntime)
        store.isConnected = true
        store.startPhoneTask(
            taskID: "task-001",
            taskType: "mock_phone_task",
            streamID: "stream-001",
            glassDeviceID: "glass-001",
            phoneDeviceID: "phone-001",
            params: ["mode": "test"]
        )

        store.stopPhoneTask(taskID: "task-001", taskType: "mock_phone_task", reason: "task.cancelled")

        #expect(store.activeTaskDescription == nil)
        #expect(store.isConnected == false)
        #expect(store.latestImage == nil)
        #expect(capabilityRuntime.stoppedTaskID == "task-001")
    }

    /// 测试目标：验证 iOS 手机 SDK 可按 `taskType` 同时注册多个业务能力。
    ///
    /// 测试方法：
    /// 1. 向 `PhoneTaskCapabilityRegistry` 注册两个不同任务类型。
    /// 2. 使用组合运行时分别启动两个手机任务。
    /// 3. 停止指定任务，检查只命中对应业务运行时。
    ///
    /// 预期结果：
    /// 1. 两个业务运行时都能被启动，不会互相覆盖。
    /// 2. 停止某个任务时只停止该任务对应的运行时。
    @MainActor
    @Test
    func testPhoneTaskCapabilityRegistryDispatchesMultipleTaskTypes() {
        PhoneTaskCapabilityRegistry.resetForTesting()
        PhoneCapabilityRuntimeFactory.resetForTesting()
        defer {
            PhoneTaskCapabilityRegistry.resetForTesting()
            PhoneCapabilityRuntimeFactory.resetForTesting()
        }
        let findObjectRuntime = TestPhoneCapabilityRuntime()
        let trafficLightRuntime = TestPhoneCapabilityRuntime()
        PhoneTaskCapabilityRegistry.register(taskType: "find_object_phone_task") {
            findObjectRuntime
        }
        PhoneTaskCapabilityRegistry.register(taskType: "traffic_light_phone_task") {
            trafficLightRuntime
        }
        let store = CameraStreamStore(capabilityRuntime: PhoneCapabilityRuntimeFactory.makeRuntime())

        store.startPhoneTask(
            taskID: "task-find",
            taskType: "find_object_phone_task",
            streamID: "stream-find",
            glassDeviceID: "glass-001",
            phoneDeviceID: "phone-001",
            params: ["target": "水杯"]
        )
        store.startPhoneTask(
            taskID: "task-traffic",
            taskType: "traffic_light_phone_task",
            streamID: "stream-traffic",
            glassDeviceID: "glass-001",
            phoneDeviceID: "phone-001",
            params: [:]
        )

        store.stopPhoneTask(taskID: "task-find", taskType: "find_object_phone_task", reason: "test.stop")

        #expect(findObjectRuntime.startedTask == nil)
        #expect(findObjectRuntime.stoppedTaskID == "task-find")
        #expect(trafficLightRuntime.startedTask?.taskID == "task-traffic")
        #expect(trafficLightRuntime.stoppedTaskID == nil)
    }

    /// 测试目标：验证组合运行时只把视频帧投递给当前活跃手机任务。
    ///
    /// 测试方法：
    /// 1. 注册两个任务类型并依次启动。
    /// 2. 更新一帧图像。
    /// 3. 检查收到帧的是后启动的活跃任务运行时。
    ///
    /// 预期结果：
    /// 1. 当前活跃任务收到帧。
    /// 2. 非活跃任务不会收到该帧。
    @MainActor
    @Test
    func testPhoneTaskCapabilityRegistryRoutesFramesToActiveTask() throws {
        PhoneTaskCapabilityRegistry.resetForTesting()
        PhoneCapabilityRuntimeFactory.resetForTesting()
        defer {
            PhoneTaskCapabilityRegistry.resetForTesting()
            PhoneCapabilityRuntimeFactory.resetForTesting()
        }
        let findObjectRuntime = TestPhoneCapabilityRuntime()
        let trafficLightRuntime = TestPhoneCapabilityRuntime()
        PhoneTaskCapabilityRegistry.register(taskType: "find_object_phone_task") {
            findObjectRuntime
        }
        PhoneTaskCapabilityRegistry.register(taskType: "traffic_light_phone_task") {
            trafficLightRuntime
        }
        let store = CameraStreamStore(capabilityRuntime: PhoneTaskCapabilityRegistry.makeRuntime())

        store.startPhoneTask(
            taskID: "task-find",
            taskType: "find_object_phone_task",
            streamID: "stream-find",
            glassDeviceID: "glass-001",
            phoneDeviceID: "phone-001",
            params: [:]
        )
        store.startPhoneTask(
            taskID: "task-traffic",
            taskType: "traffic_light_phone_task",
            streamID: "stream-traffic",
            glassDeviceID: "glass-001",
            phoneDeviceID: "phone-001",
            params: [:]
        )

        let image = try #require(Self.makeSolidImage(red: 0, green: 1, blue: 0))
        store.updateLatestFrame(image: image, sequence: 42)

        #expect(findObjectRuntime.processedSequences.isEmpty)
        #expect(trafficLightRuntime.processedSequences == [42])
    }

    /// 测试目标：验证统一服务端地址可正确派生控制与 HTTP 地址。
    ///
    /// 测试方法：
    /// 1. 构造只包含统一服务端地址的配置。
    /// 2. 分别读取 HTTP 基地址和控制 WebSocket 地址。
    ///
    /// 预期结果：
    /// 1. HTTP 基地址保持原值。
    /// 2. 控制地址自动补全为 `/ws/control`。
    @Test
    func testReceiverAppConfigDerivesControlAndHTTPURLsFromSingleServerBaseURL() {
        let config = ReceiverAppConfig(
            serverBaseURLString: "http://192.168.10.8:8765",
            phoneDeviceID: "phone-001",
            pairToken: "pair-phone-token",
            desiredGlassDeviceID: "glass-001"
        )

        #expect(config.serverHTTPBaseURLString == "http://192.168.10.8:8765")
        #expect(config.serverControlWebSocketURLString == "ws://192.168.10.8:8765/ws/control")
    }

    /// 构造测试所需媒体帧原始字节。
    ///
    /// 参数：
    /// 1. `payload`：真实 JPEG 字节。
    /// 2. `payloadSize`：头部中声明的负载长度。
    ///
    /// 返回值：
    /// 1. 完整媒体帧原始字节。
    ///
    /// 异常情况：
    /// 1. JSON 编码失败时抛出异常。
    private static func makeMediaFrameRaw(payload: Data, payloadSize: Int) throws -> Data {
        let header: [String: Any] = [
            "version": "v1",
            "stream_id": "stream_cam_001",
            "frame_type": "camera_frame",
            "seq": 7,
            "ts_ms": 1_700_000_000_000 as Int64,
            "codec": "jpeg",
            "payload_size": payloadSize,
            "final": false
        ]
        let headerData = try JSONSerialization.data(withJSONObject: header)
        var result = Data()
        let headerLength = UInt32(headerData.count).bigEndian
        withUnsafeBytes(of: headerLength) { result.append(contentsOf: $0) }
        result.append(headerData)
        result.append(payload)
        return result
    }

    /// 构造纯色测试图。
    ///
    /// 参数：
    /// 1. `red/green/blue`：颜色通道值，范围 0 到 1。
    ///
    /// 返回值：
    /// 1. `UIImage`，失败时返回 `nil`。
    private static func makeSolidImage(red: CGFloat, green: CGFloat, blue: CGFloat) -> UIImage? {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 4, height: 4))
        return renderer.image { context in
            UIColor(red: red, green: green, blue: blue, alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: 4, height: 4))
        }
    }
}
