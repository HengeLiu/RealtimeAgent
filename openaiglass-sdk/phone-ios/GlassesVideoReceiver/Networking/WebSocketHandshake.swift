import CryptoKit
import Foundation

/// WebSocket 握手辅助工具。
///
/// 主要功能：
/// 1. 计算服务端 `Sec-WebSocket-Accept`。
/// 2. 生成最小握手成功响应。
enum WebSocketHandshake {
    /// 根据客户端的 `Sec-WebSocket-Key` 计算应答值。
    ///
    /// 参数：
    /// 1. `key`：客户端握手请求中的原始 key。
    ///
    /// 返回值：
    /// 1. 服务端应返回的 `Sec-WebSocket-Accept`。
    static func acceptValue(for key: String) -> String {
        let combined = Data((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").utf8)
        let digest = Insecure.SHA1.hash(data: combined)
        return Data(digest).base64EncodedString()
    }

    /// 生成握手成功响应报文。
    ///
    /// 参数：
    /// 1. `key`：客户端握手请求中的原始 key。
    ///
    /// 返回值：
    /// 1. 可直接回写到 TCP 连接中的 HTTP 响应数据。
    static func responseData(for key: String) -> Data {
        let accept = acceptValue(for: key)
        let response = [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Accept: \(accept)",
            "",
            "",
        ].joined(separator: "\r\n")
        return Data(response.utf8)
    }
}
