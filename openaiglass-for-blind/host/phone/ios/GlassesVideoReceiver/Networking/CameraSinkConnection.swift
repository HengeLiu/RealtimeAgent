import Foundation
import Network
import UIKit

/// 单条视频流连接会话。
///
/// 主要功能：
/// 1. 完成 WebSocket 握手。
/// 2. 解析后续 WebSocket 二进制帧。
/// 3. 解码 `MediaFrame(camera_frame)` 并刷新 UI。
final class CameraSinkConnection {
    private let connection: NWConnection
    private let path: String
    private let store: CameraStreamStore
    private let onClose: (CameraSinkConnection) -> Void
    private let frameParser = WebSocketFrameParser()

    private var handshakeBuffer = Data()
    private var isHandshakeCompleted = false
    private var isClosed = false

    /// 初始化连接会话。
    ///
    /// 参数：
    /// 1. `connection`：底层网络连接。
    /// 2. `path`：期望接收路径。
    /// 3. `store`：页面共享状态。
    /// 4. `onClose`：连接结束回调。
    init(
        connection: NWConnection,
        path: String,
        store: CameraStreamStore,
        onClose: @escaping (CameraSinkConnection) -> Void
    ) {
        self.connection = connection
        self.path = path
        self.store = store
        self.onClose = onClose
    }

    /// 启动连接处理。
    ///
    /// 参数：
    /// 1. `queue`：连接处理队列。
    func start(on queue: DispatchQueue) {
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else {
                return
            }
            switch state {
            case .ready:
                Task { @MainActor in
                    let endpointText = self.connection.endpoint.debugDescription
                    self.store.markConnected(endpointText)
                }
                self.receiveNextChunk()
            case let .failed(error):
                Task { @MainActor in
                    self.store.markError("连接失败：\(error.localizedDescription)")
                }
                self.close(reason: "连接失败")
            case .cancelled:
                self.close(reason: "连接取消")
            default:
                break
            }
        }
        connection.start(queue: queue)
    }

    /// 持续接收后续字节流。
    private func receiveNextChunk() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else {
                return
            }

            if let error {
                Task { @MainActor in
                    self.store.markError("接收失败：\(error.localizedDescription)")
                }
                self.close(reason: "接收失败")
                return
            }

            if let data, !data.isEmpty {
                self.handleReceivedData(data)
            }

            if isComplete {
                self.close(reason: "对端关闭")
                return
            }
            self.receiveNextChunk()
        }
    }

    /// 处理一批收到的 TCP 数据。
    ///
    /// 参数：
    /// 1. `data`：新收到的字节流。
    private func handleReceivedData(_ data: Data) {
        if !isHandshakeCompleted {
            handshakeBuffer.append(data)
            completeHandshakeIfPossible()
            return
        }

        let events = frameParser.append(data)
        for event in events {
            handleFrameEvent(event)
        }
    }

    /// 在条件满足时完成握手。
    ///
    /// 主要逻辑：
    /// 1. 读取完整 HTTP 头。
    /// 2. 校验请求路径和 key。
    /// 3. 回写握手成功响应。
    /// 4. 把剩余字节继续交给帧解析器。
    private func completeHandshakeIfPossible() {
        guard let headerRange = handshakeBuffer.range(of: Data("\r\n\r\n".utf8)) else {
            return
        }

        let requestData = handshakeBuffer.subdata(in: 0..<headerRange.upperBound)
        let remainingData = handshakeBuffer.advanced(by: headerRange.upperBound)
        handshakeBuffer.removeAll(keepingCapacity: false)

        guard let requestText = String(data: requestData, encoding: .utf8) else {
            Task { @MainActor in
                self.store.markError("握手请求解析失败")
            }
            close(reason: "握手失败")
            return
        }

        let requestLines = requestText.components(separatedBy: "\r\n")
        guard let requestLine = requestLines.first, requestLine.contains("GET \(path) ") else {
            Task { @MainActor in
                self.store.markError("握手路径非法")
            }
            close(reason: "握手路径非法")
            return
        }

        let websocketKey = requestLines.first { line in
            line.lowercased().hasPrefix("sec-websocket-key:")
        }?.split(separator: ":", maxSplits: 1).last?.trimmingCharacters(in: .whitespaces)

        guard let websocketKey else {
            Task { @MainActor in
                self.store.markError("握手缺少 Sec-WebSocket-Key")
            }
            close(reason: "握手缺少 key")
            return
        }

        connection.send(content: WebSocketHandshake.responseData(for: websocketKey), completion: .contentProcessed { [weak self] sendError in
            guard let self else {
                return
            }
            if let sendError {
                Task { @MainActor in
                    self.store.markError("握手应答发送失败：\(sendError.localizedDescription)")
                }
                self.close(reason: "握手应答失败")
                return
            }
            self.isHandshakeCompleted = true
            if !remainingData.isEmpty {
                let events = self.frameParser.append(remainingData)
                for event in events {
                    self.handleFrameEvent(event)
                }
            }
        })
    }

    /// 处理已解析的 WebSocket 事件。
    ///
    /// 参数：
    /// 1. `event`：帧解析器输出的事件。
    private func handleFrameEvent(_ event: WebSocketFrameEvent) {
        switch event {
        case let .binary(data):
            handleBinaryMessage(data)
        case let .ping(payload):
            sendPong(payload)
        case .close:
            close(reason: "收到 close 帧")
        }
    }

    /// 处理媒体二进制消息。
    ///
    /// 参数：
    /// 1. `data`：完整 `MediaFrame` 原始字节。
    private func handleBinaryMessage(_ data: Data) {
        do {
            let cameraFrame = try MediaFrameDecoder.decodeCameraFrame(from: data)
            guard let image = UIImage(data: cameraFrame.payload) else {
                Task { @MainActor in
                    self.store.markError("JPEG 解码失败")
                }
                return
            }
            Task { @MainActor in
                self.store.updateLatestFrame(image: image, sequence: cameraFrame.sequence)
            }
        } catch {
            Task { @MainActor in
                self.store.markError(error.localizedDescription)
            }
        }
    }

    /// 发送 pong 帧。
    ///
    /// 参数：
    /// 1. `payload`：ping 原始负载。
    private func sendPong(_ payload: Data) {
        var frame = Data([0x8A])
        if payload.count < 126 {
            frame.append(UInt8(payload.count))
        } else {
            frame.append(126)
            frame.append(UInt8((payload.count >> 8) & 0xFF))
            frame.append(UInt8(payload.count & 0xFF))
        }
        frame.append(payload)
        connection.send(content: frame, completion: .contentProcessed { _ in })
    }

    /// 关闭当前连接。
    ///
    /// 参数：
    /// 1. `reason`：关闭原因。
    private func close(reason: String) {
        guard !isClosed else {
            return
        }
        isClosed = true
        connection.cancel()
        Task { @MainActor in
            self.store.markDisconnected(reason)
        }
        onClose(self)
    }

    /// 供上层主动关闭当前连接。
    ///
    /// 参数：
    /// 1. `reason`：关闭原因。
    func stop(reason: String) {
        close(reason: reason)
    }
}

private extension Data {
    /// 返回移除前缀字节后的数据。
    ///
    /// 参数：
    /// 1. `offset`：要跳过的字节数。
    ///
    /// 返回值：
    /// 1. 新的数据切片。
    func advanced(by offset: Int) -> Data {
        subdata(in: offset..<count)
    }
}
