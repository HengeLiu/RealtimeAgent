import Foundation

enum RealtimeAgentStreamChannel: Hashable, Sendable {
    case audioInput
    case audioOutput
    case visualInput

    var debugName: String {
        switch self {
        case .audioInput:
            return "audio_input"
        case .audioOutput:
            return "audio_output"
        case .visualInput:
            return "visual_input"
        }
    }
}

protocol RealtimeAgentWebSocketTransport: AnyObject, Sendable {
    func connectControl(url: URL) async throws
    func connectStream(channel: RealtimeAgentStreamChannel, url: URL) async throws
    func sendControl(text: String) async throws
    func receiveControl() async throws -> String
    func sendStream(data: Data, channel: RealtimeAgentStreamChannel) async throws
    func receiveStream(channel: RealtimeAgentStreamChannel) async throws -> Data
    func close() async
}

final class URLSessionRealtimeAgentTransport: RealtimeAgentWebSocketTransport, @unchecked Sendable {
    private let session: URLSession
    private var controlSocket: URLSessionWebSocketTask?
    private var streamSockets: [RealtimeAgentStreamChannel: URLSessionWebSocketTask] = [:]

    init(session: URLSession = .shared) {
        self.session = session
    }

    func connectControl(url: URL) async throws {
        let socket = session.webSocketTask(with: url)
        controlSocket = socket
        socket.resume()
        try await waitUntilSocketReady(socket)
    }

    func connectStream(channel: RealtimeAgentStreamChannel, url: URL) async throws {
        let socket = session.webSocketTask(with: url)
        streamSockets[channel]?.cancel(with: .normalClosure, reason: nil)
        streamSockets[channel] = socket
        socket.resume()
        do {
            try await waitUntilSocketReady(socket)
        } catch {
            if streamSockets[channel] === socket {
                streamSockets[channel] = nil
            }
            socket.cancel(with: .goingAway, reason: nil)
            throw error
        }
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

    func sendStream(data: Data, channel: RealtimeAgentStreamChannel) async throws {
        guard let streamSocket = streamSockets[channel] else {
            throw RealtimeAgentDeviceError.missingWebSocket(channel.debugName)
        }
        try await streamSocket.send(.data(data))
    }

    func receiveStream(channel: RealtimeAgentStreamChannel) async throws -> Data {
        guard let streamSocket = streamSockets[channel] else {
            throw RealtimeAgentDeviceError.missingWebSocket(channel.debugName)
        }
        let message = try await streamSocket.receive()
        guard case let .data(data) = message else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("unexpected stream websocket message")
        }
        return data
    }

    func close() async {
        controlSocket?.cancel(with: .normalClosure, reason: nil)
        for socket in streamSockets.values {
            socket.cancel(with: .normalClosure, reason: nil)
        }
        controlSocket = nil
        streamSockets = [:]
    }

    private func waitUntilSocketReady(_ socket: URLSessionWebSocketTask) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            socket.sendPing { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }
}
