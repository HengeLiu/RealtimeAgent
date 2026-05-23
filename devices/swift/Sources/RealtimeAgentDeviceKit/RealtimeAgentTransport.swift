import Foundation

protocol RealtimeAgentWebSocketTransport: AnyObject, Sendable {
    func connectControl(url: URL) async throws
    func connectStream(url: URL) async throws
    func sendControl(text: String) async throws
    func receiveControl() async throws -> String
    func sendStream(data: Data) async throws
    func receiveStream() async throws -> Data
    func close() async
}

final class URLSessionRealtimeAgentTransport: RealtimeAgentWebSocketTransport, @unchecked Sendable {
    private let session: URLSession
    private var controlSocket: URLSessionWebSocketTask?
    private var streamSocket: URLSessionWebSocketTask?

    init(session: URLSession = .shared) {
        self.session = session
    }

    func connectControl(url: URL) async throws {
        let socket = session.webSocketTask(with: url)
        controlSocket = socket
        socket.resume()
    }

    func connectStream(url: URL) async throws {
        let socket = session.webSocketTask(with: url)
        streamSocket = socket
        socket.resume()
    }

    func sendControl(text: String) async throws {
        guard let controlSocket else {
            throw RealtimeAgentDeviceError.missingWebSocket("control")
        }
        try await controlSocket.send(.string(text))
    }

    func receiveControl() async throws -> String {
        guard let controlSocket else {
            throw RealtimeAgentDeviceError.missingWebSocket("control")
        }
        let message = try await controlSocket.receive()
        guard case let .string(text) = message else {
            throw RealtimeAgentDeviceError.invalidEvent("unexpected control websocket message")
        }
        return text
    }

    func sendStream(data: Data) async throws {
        guard let streamSocket else {
            throw RealtimeAgentDeviceError.missingWebSocket("stream")
        }
        try await streamSocket.send(.data(data))
    }

    func receiveStream() async throws -> Data {
        guard let streamSocket else {
            throw RealtimeAgentDeviceError.missingWebSocket("stream")
        }
        let message = try await streamSocket.receive()
        guard case let .data(data) = message else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("unexpected stream websocket message")
        }
        return data
    }

    func close() async {
        controlSocket?.cancel(with: .normalClosure, reason: nil)
        streamSocket?.cancel(with: .normalClosure, reason: nil)
        controlSocket = nil
        streamSocket = nil
    }
}
