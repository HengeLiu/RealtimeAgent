import Foundation
import Testing
@testable import GlassesVideoReceiver

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
}
