import CryptoKit
import Foundation
import Network

@MainActor
final class DirectCameraSinkServer {
    private let queue = DispatchQueue(label: "dev.realtimeagent.direct-camera-sink")
    private let port: UInt16
    private let onFrame: (DirectCameraFrame) -> Void
    private let onState: (String) -> Void
    private var listener: NWListener?
    private var connections: [DirectCameraSinkConnection] = []

    let path = "/ws/camera"

    init(port: UInt16, onState: @escaping (String) -> Void, onFrame: @escaping (DirectCameraFrame) -> Void) {
        self.port = port
        self.onState = onState
        self.onFrame = onFrame
    }

    var sinkURIs: [String] {
        RealtimeAgentIPAddressProvider.loadIPv4Addresses().map { "ws://\($0):\(port)\(path)" }
    }

    func start() {
        guard listener == nil else {
            onState("直连相机接收已启动")
            return
        }
        do {
            let listener = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: port) ?? 9001)
            listener.stateUpdateHandler = { [weak self] state in
                Task { @MainActor in self?.handleState(state) }
            }
            listener.newConnectionHandler = { [weak self] connection in
                Task { @MainActor in self?.accept(connection) }
            }
            self.listener = listener
            listener.start(queue: queue)
        } catch {
            onState("直连相机接收启动失败：\(error.localizedDescription)")
        }
    }

    func stop() {
        let active = connections
        connections.removeAll()
        active.forEach { $0.stop(reason: "sink stopped") }
        listener?.cancel()
        listener = nil
        onState("直连相机接收已停止")
    }

    private func handleState(_ state: NWListener.State) {
        switch state {
        case .ready:
            onState("直连相机接收中：\(sinkURIs.first ?? "无可用 IPv4 地址")")
        case let .failed(error):
            onState("直连相机接收失败：\(error.localizedDescription)")
        case .cancelled:
            onState("直连相机接收已取消")
        default:
            break
        }
    }

    private func accept(_ connection: NWConnection) {
        let session = DirectCameraSinkConnection(connection: connection, path: path, onState: onState, onFrame: onFrame) { [weak self] closed in
            Task { @MainActor in self?.connections.removeAll { $0 === closed } }
        }
        connections.append(session)
        session.start(on: queue)
    }
}

final class DirectCameraSinkConnection {
    private let connection: NWConnection
    private let path: String
    private let onState: (String) -> Void
    private let onFrame: (DirectCameraFrame) -> Void
    private let onClose: (DirectCameraSinkConnection) -> Void
    private let parser = DirectWebSocketFrameParser()
    private var handshakeBuffer = Data()
    private var handshakeCompleted = false
    private var closed = false

    init(
        connection: NWConnection,
        path: String,
        onState: @escaping (String) -> Void,
        onFrame: @escaping (DirectCameraFrame) -> Void,
        onClose: @escaping (DirectCameraSinkConnection) -> Void
    ) {
        self.connection = connection
        self.path = path
        self.onState = onState
        self.onFrame = onFrame
        self.onClose = onClose
    }

    func start(on queue: DispatchQueue) {
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else {
                return
            }
            switch state {
            case .ready:
                Task { @MainActor in self.onState("ESP32 相机已直连：\(self.connection.endpoint.debugDescription)") }
                self.receiveNext()
            case let .failed(error):
                Task { @MainActor in self.onState("ESP32 相机直连失败：\(error.localizedDescription)") }
                self.close(reason: "connection failed")
            case .cancelled:
                self.close(reason: "connection cancelled")
            default:
                break
            }
        }
        connection.start(queue: queue)
    }

    func stop(reason: String) {
        close(reason: reason)
    }

    private func receiveNext() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else {
                return
            }
            if let error {
                Task { @MainActor in self.onState("ESP32 相机接收失败：\(error.localizedDescription)") }
                self.close(reason: "receive failed")
                return
            }
            if let data, !data.isEmpty {
                self.handle(data)
            }
            if isComplete {
                self.close(reason: "peer closed")
                return
            }
            self.receiveNext()
        }
    }

    private func handle(_ data: Data) {
        if !handshakeCompleted {
            handshakeBuffer.append(data)
            completeHandshakeIfPossible()
            return
        }
        parser.append(data).forEach(handleFrameEvent)
    }

    private func completeHandshakeIfPossible() {
        guard let range = handshakeBuffer.range(of: Data("\r\n\r\n".utf8)) else {
            return
        }
        let requestData = handshakeBuffer.subdata(in: 0..<range.upperBound)
        let remaining = handshakeBuffer.subdata(in: range.upperBound..<handshakeBuffer.count)
        handshakeBuffer.removeAll(keepingCapacity: false)
        guard let request = String(data: requestData, encoding: .utf8),
              request.components(separatedBy: "\r\n").first?.contains("GET \(path) ") == true,
              let key = request.components(separatedBy: "\r\n").first(where: { $0.lowercased().hasPrefix("sec-websocket-key:") })?.split(separator: ":", maxSplits: 1).last?.trimmingCharacters(in: .whitespaces) else {
            Task { @MainActor in self.onState("ESP32 相机 WebSocket 握手失败") }
            close(reason: "handshake failed")
            return
        }
        connection.send(content: Self.handshakeResponse(for: key), completion: .contentProcessed { [weak self] error in
            guard let self else {
                return
            }
            if let error {
                Task { @MainActor in self.onState("ESP32 相机握手响应失败：\(error.localizedDescription)") }
                self.close(reason: "handshake response failed")
                return
            }
            self.handshakeCompleted = true
            if !remaining.isEmpty {
                self.parser.append(remaining).forEach(self.handleFrameEvent)
            }
        })
    }

    private func handleFrameEvent(_ event: DirectWebSocketFrameEvent) {
        switch event {
        case let .binary(data):
            do {
                let frame = try DirectCameraFrameCodec.decode(data)
                Task { @MainActor in self.onFrame(frame) }
            } catch {
                Task { @MainActor in self.onState("ESP32 相机帧解码失败：\(error.localizedDescription)") }
            }
        case let .ping(payload):
            sendPong(payload)
        case .close:
            close(reason: "close frame")
        }
    }

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

    private func close(reason: String) {
        guard !closed else {
            return
        }
        closed = true
        connection.cancel()
        Task { @MainActor in self.onState("ESP32 相机直连结束：\(reason)") }
        onClose(self)
    }

    private static func handshakeResponse(for key: String) -> Data {
        let combined = Data((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").utf8)
        let accept = Data(Insecure.SHA1.hash(data: combined)).base64EncodedString()
        return Data([
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Accept: \(accept)",
            "",
            "",
        ].joined(separator: "\r\n").utf8)
    }
}
