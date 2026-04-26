import Foundation

/// WebSocket 帧解析结果。
///
/// 主要功能：
/// 1. 对上层暴露可处理的二进制、ping 和关闭事件。
enum WebSocketFrameEvent: Equatable {
    case binary(Data)
    case ping(Data)
    case close
}

/// WebSocket 帧解析器。
///
/// 主要功能：
/// 1. 逐步接收 TCP 字节流。
/// 2. 解析最小 WebSocket 帧结构。
/// 3. 输出完整事件供媒体处理层消费。
final class WebSocketFrameParser {
    /// 单次解析结果。
    ///
    /// 主要功能：
    /// 1. 区分“成功消费了一帧但暂时不对上层产出事件”和“产出完整事件”。
    /// 2. 避免分片消息场景下把解析过程误判为缓冲区不足。
    private enum ParseOutcome {
        case event(WebSocketFrameEvent)
        case consumedWithoutEvent
    }

    private var buffer = Data()
    private var fragmentedOpcode: UInt8?
    private var fragmentedPayload = Data()

    /// 追加一段原始 TCP 数据并尝试解析完整帧。
    ///
    /// 参数：
    /// 1. `data`：新收到的字节流。
    ///
    /// 返回值：
    /// 1. 当前批次成功解析出的所有事件。
    func append(_ data: Data) -> [WebSocketFrameEvent] {
        buffer.append(data)
        var events: [WebSocketFrameEvent] = []

        while let outcome = parseOneFrame() {
            switch outcome {
            case let .event(event):
                events.append(event)
            case .consumedWithoutEvent:
                continue
            }
        }
        return events
    }

    /// 解析一帧完整 WebSocket 消息。
    ///
    /// 返回值：
    /// 1. 若当前缓冲不足则返回 `nil`。
    /// 2. 若足够则返回结构化事件。
    private func parseOneFrame() -> ParseOutcome? {
        guard buffer.count >= 2 else {
            return nil
        }

        let first = buffer[0]
        let second = buffer[1]
        let isFinalFrame = (first & 0x80) != 0
        let opcode = first & 0x0F
        let isMasked = (second & 0x80) != 0
        var payloadLength = Int(second & 0x7F)
        var cursor = 2

        if payloadLength == 126 {
            guard buffer.count >= cursor + 2 else {
                return nil
            }
            payloadLength = Int(buffer[cursor]) << 8 | Int(buffer[cursor + 1])
            cursor += 2
        } else if payloadLength == 127 {
            guard buffer.count >= cursor + 8 else {
                return nil
            }
            let lengthBytes = buffer[cursor..<(cursor + 8)]
            payloadLength = lengthBytes.reduce(0) { partialResult, byte in
                (partialResult << 8) | Int(byte)
            }
            cursor += 8
        }

        var maskKey = Data()
        if isMasked {
            guard buffer.count >= cursor + 4 else {
                return nil
            }
            maskKey = buffer.subdata(in: cursor..<(cursor + 4))
            cursor += 4
        }

        guard buffer.count >= cursor + payloadLength else {
            return nil
        }

        var payload = buffer.subdata(in: cursor..<(cursor + payloadLength))
        if isMasked {
            for index in payload.indices {
                let maskIndex = (index - payload.startIndex) % 4
                payload[index] ^= maskKey[maskKey.startIndex + maskIndex]
            }
        }

        buffer.removeSubrange(0..<(cursor + payloadLength))

        switch opcode {
        case 0x2:
            if isFinalFrame {
                return .event(.binary(payload))
            }
            fragmentedOpcode = opcode
            fragmentedPayload = payload
            return .consumedWithoutEvent
        case 0x0:
            guard fragmentedOpcode == 0x2 else {
                return .consumedWithoutEvent
            }
            fragmentedPayload.append(payload)
            if isFinalFrame {
                let completePayload = fragmentedPayload
                fragmentedOpcode = nil
                fragmentedPayload.removeAll(keepingCapacity: false)
                return .event(.binary(completePayload))
            }
            return .consumedWithoutEvent
        case 0x9:
            return .event(.ping(payload))
        case 0x8:
            fragmentedOpcode = nil
            fragmentedPayload.removeAll(keepingCapacity: false)
            return .event(.close)
        default:
            return .consumedWithoutEvent
        }
    }
}
