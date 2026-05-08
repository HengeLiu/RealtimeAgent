import Foundation

enum DirectWebSocketFrameEvent: Equatable {
    case binary(Data)
    case ping(Data)
    case close
}

final class DirectWebSocketFrameParser {
    private var buffer = Data()
    private var fragmentedOpcode: UInt8?
    private var fragmentedPayload = Data()

    func append(_ data: Data) -> [DirectWebSocketFrameEvent] {
        buffer.append(data)
        var events: [DirectWebSocketFrameEvent] = []
        while let event = parseOneFrame() {
            if let event {
                events.append(event)
            }
        }
        return events
    }

    private func parseOneFrame() -> DirectWebSocketFrameEvent?? {
        guard buffer.count >= 2 else {
            return nil
        }
        let first = buffer[0]
        let second = buffer[1]
        let isFinal = (first & 0x80) != 0
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
            payloadLength = buffer[cursor..<(cursor + 8)].reduce(0) { ($0 << 8) | Int($1) }
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
            if isFinal {
                return .some(.binary(payload))
            }
            fragmentedOpcode = opcode
            fragmentedPayload = payload
            return .some(nil)
        case 0x0:
            guard fragmentedOpcode == 0x2 else {
                return .some(nil)
            }
            fragmentedPayload.append(payload)
            if isFinal {
                let complete = fragmentedPayload
                fragmentedOpcode = nil
                fragmentedPayload.removeAll(keepingCapacity: false)
                return .some(.binary(complete))
            }
            return .some(nil)
        case 0x9:
            return .some(.ping(payload))
        case 0x8:
            fragmentedOpcode = nil
            fragmentedPayload.removeAll(keepingCapacity: false)
            return .some(.close)
        default:
            return .some(nil)
        }
    }
}
