import Foundation
import Network

/// 最小相机流接收服务。
///
/// 主要功能：
/// 1. 在 iPhone 上监听固定 TCP 端口。
/// 2. 接受眼镜端发起的 WebSocket 连接。
/// 3. 将连接交给专用会话对象处理。
@MainActor
final class CameraSinkServer {
    private let store: CameraStreamStore
    private let queue = DispatchQueue(label: "com.openai.glasses.receiver.server")
    private var listener: NWListener?
    private var sessions: [CameraSinkConnection] = []

    let path = "/ws/camera"
    let port: UInt16

    /// 初始化服务对象。
    ///
    /// 参数：
    /// 1. `store`：页面共享状态。
    /// 2. `port`：监听端口，默认使用 9001。
    init(store: CameraStreamStore, port: UInt16 = 9001) {
        self.store = store
        self.port = port
    }

    /// 启动监听。
    ///
    /// 主要逻辑：
    /// 1. 创建 TCP 监听器。
    /// 2. 注册状态和新连接回调。
    /// 3. 启动后更新页面状态。
    func start() {
        guard listener == nil else {
            return
        }

        do {
            let nwPort = NWEndpoint.Port(rawValue: port) ?? 9001
            let listener = try NWListener(using: .tcp, on: nwPort)
            listener.stateUpdateHandler = { [weak self] state in
                guard let self else {
                    return
                }
                Task { @MainActor in
                    self.handleListenerState(state)
                }
            }
            listener.newConnectionHandler = { [weak self] connection in
                guard let self else {
                    return
                }
                Task { @MainActor in
                    self.accept(connection)
                }
            }
            self.listener = listener
            listener.start(queue: queue)
        } catch {
            store.markError("监听启动失败：\(error.localizedDescription)")
        }
    }

    /// 停止监听并关闭所有活动连接。
    ///
    /// 主要逻辑：
    /// 1. 关闭所有现存会话。
    /// 2. 取消底层监听器。
    func stop() {
        let currentSessions = sessions
        sessions.removeAll()
        currentSessions.forEach { session in
            session.stop(reason: "用户结束视频接收")
        }
        listener?.cancel()
        listener = nil
    }

    /// 处理监听器状态变化。
    ///
    /// 参数：
    /// 1. `state`：监听器状态。
    private func handleListenerState(_ state: NWListener.State) {
        switch state {
        case .ready:
            store.markListening(port: port)
        case let .failed(error):
            store.markError("监听失败：\(error.localizedDescription)")
        case .cancelled:
            store.markDisconnected("监听已取消")
        default:
            break
        }
    }

    /// 接受新连接。
    ///
    /// 参数：
    /// 1. `connection`：底层网络连接。
    private func accept(_ connection: NWConnection) {
        let session = CameraSinkConnection(connection: connection, path: path, store: store) { [weak self] closedSession in
            Task { @MainActor in
                self?.sessions.removeAll { $0 === closedSession }
            }
        }
        sessions.append(session)
        session.start(on: queue)
    }
}

/// 手机调试应用配置。
///
/// 主要功能：
/// 1. 从独立 `AppConfig.plist` 读取服务端地址。
/// 2. 保存手机设备编号、配对令牌和目标眼镜编号。
/// 3. 让后续切换局域网或服务端时无需改动 Swift 代码。
struct ReceiverAppConfig {
    let serverBaseURLString: String
    let phoneDeviceID: String
    let pairToken: String
    let desiredGlassDeviceID: String

    /// 返回服务端 HTTP 基地址。
    ///
    /// 主要逻辑：
    /// 1. 统一把服务端地址视为 HTTP 基地址。
    /// 2. 停止任务、结果上报等接口都直接复用该地址。
    var serverHTTPBaseURLString: String {
        serverBaseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// 返回服务端控制 WebSocket 地址。
    ///
    /// 主要逻辑：
    /// 1. 基于统一服务端地址推导控制通道。
    /// 2. 自动把 `http/https` 转成 `ws/wss`，并补上 `/ws/control` 路径。
    var serverControlWebSocketURLString: String {
        guard var components = URLComponents(string: serverHTTPBaseURLString) else {
            return serverHTTPBaseURLString
        }
        if components.scheme == "https" {
            components.scheme = "wss"
        } else {
            components.scheme = "ws"
        }
        components.path = "/ws/control"
        components.query = nil
        components.fragment = nil
        return components.string ?? serverHTTPBaseURLString
    }

    /// 读取主应用配置文件。
    ///
    /// 返回值：
    /// 1. 成功时返回配置对象。
    /// 2. 配置缺失或格式错误时返回 `nil`。
    static func load() -> ReceiverAppConfig? {
        guard
            let url = Bundle.main.url(forResource: "AppConfig", withExtension: "plist"),
            let data = try? Data(contentsOf: url),
            let object = try? PropertyListSerialization.propertyList(from: data, format: nil),
            let dictionary = object as? [String: Any]
        else {
            return nil
        }

        guard
            let serverBaseURLString = dictionary["serverBaseURLString"] as? String,
            let phoneDeviceID = dictionary["phoneDeviceID"] as? String,
            let pairToken = dictionary["pairToken"] as? String,
            let desiredGlassDeviceID = dictionary["desiredGlassDeviceID"] as? String
        else {
            return nil
        }

        return ReceiverAppConfig(
            serverBaseURLString: serverBaseURLString,
            phoneDeviceID: phoneDeviceID,
            pairToken: pairToken,
            desiredGlassDeviceID: desiredGlassDeviceID
        )
    }
}

/// 手机控制面客户端。
///
/// 主要功能：
/// 1. 连接服务端控制 WebSocket。
/// 2. 上报手机接收地址与目标眼镜编号。
/// 3. 维持心跳并接收绑定完成通知。
@MainActor
final class PhoneControlClient {
    private static let reconnectIntervalSeconds = 5

    /// 控制连接重试原因类型。
    ///
    /// 主要功能：
    /// 1. 区分“注册失败待重试”和“连接断开待重试”。
    /// 2. 让页面展示更准确的状态文字。
    private enum ReconnectReasonKind {
        case registerFailed
        case connectionLost
    }

    private let store: CameraStreamStore
    private let session: URLSession
    private let config: ReceiverAppConfig
    private var webSocketTask: URLSessionWebSocketTask?
    private var heartbeatTimer: Timer?
    private var currentSinkURI: String?
    private var isStopping = false
    private var isConnecting = false
    private var reconnectTask: Task<Void, Never>?

    /// 初始化控制面客户端。
    ///
    /// 参数：
    /// 1. `store`：页面共享状态。
    init(store: CameraStreamStore) {
        self.store = store
        self.session = URLSession(configuration: .default)
        guard let loadedConfig = ReceiverAppConfig.load() else {
            fatalError("AppConfig.plist 缺失或格式非法，无法启动手机控制客户端")
        }
        self.config = loadedConfig
    }

    /// 启动控制面连接。
    ///
    /// 参数：
    /// 1. `sinkURI`：当前手机的视频接收地址。
    func start(with sinkURI: String?) {
        guard let sinkURI, !sinkURI.isEmpty else {
            store.markError("当前没有可用接收地址，暂时无法注册到服务器")
            return
        }
        currentSinkURI = sinkURI
        isStopping = false
        ensureConnected(reason: "app_active")
    }

    /// 当接收地址刷新后，按需重新注册。
    ///
    /// 参数：
    /// 1. `sinkURI`：新的接收地址。
    func reregisterIfNeeded(with sinkURI: String?) {
        guard let sinkURI, !sinkURI.isEmpty else {
            return
        }
        guard currentSinkURI != sinkURI else {
            ensureConnected(reason: "sink_uri_unchanged")
            return
        }
        currentSinkURI = sinkURI
        reconnectNow(reason: "sink_uri_changed")
    }

    /// 停止控制面连接。
    func stop() {
        isStopping = true
        isConnecting = false
        reconnectTask?.cancel()
        reconnectTask = nil
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        currentSinkURI = nil
        store.clearBinding()
        store.markServerDisconnected("用户结束控制连接")
    }

    /// 通知服务端停止当前视频接收任务。
    func stopVideoReceiving() async {
        let glassDeviceID = store.boundGlassDeviceID ??
            store.activeFindObjectTask?.glassDeviceID ??
            config.desiredGlassDeviceID
        guard !glassDeviceID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            store.markError("当前缺少眼镜设备编号，无法请求服务端停止视频任务")
            return
        }
        do {
            let result = try await DebugVideoTaskAPI.stop(glassDeviceID: glassDeviceID, config: config)
            if result.noop {
                store.events.insert("[\(Self.timestampText())] 服务端确认当前视频任务已结束，无需重复停止", at: 0)
            } else {
                store.events.insert("[\(Self.timestampText())] 已请求服务端停止视频任务：glass=\(glassDeviceID)", at: 0)
            }
        } catch {
            store.markError("请求服务端停止视频任务失败：\(error.localizedDescription)")
        }
    }

    /// 持续接收服务端消息。
    private func receiveNextMessage() {
        webSocketTask?.receive { [weak self] result in
            guard let self else {
                return
            }
            Task { @MainActor in
                switch result {
                case let .success(message):
                    self.handleIncomingMessage(message)
                    if !self.isStopping {
                        self.receiveNextMessage()
                    }
                case let .failure(error):
                    if !self.isStopping {
                        self.handleConnectionFailure(reason: error.localizedDescription)
                    }
                }
            }
        }
    }

    /// 处理一条服务端消息。
    ///
    /// 参数：
    /// 1. `message`：WebSocket 消息对象。
    private func handleIncomingMessage(_ message: URLSessionWebSocketTask.Message) {
        let text: String
        switch message {
        case let .string(value):
            text = value
        case let .data(data):
            guard let value = String(data: data, encoding: .utf8) else {
                store.markError("收到服务端二进制消息，但无法解析为文本")
                return
            }
            text = value
        @unknown default:
            store.markError("收到未知类型的控制消息")
            return
        }

        guard
            let rawData = text.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: rawData),
            let messageObject = object as? [String: Any],
            let name = messageObject["name"] as? String,
            let payload = messageObject["payload"] as? [String: Any]
        else {
            store.markError("服务端控制消息格式非法")
            return
        }

        switch name {
        case "device.registered":
            isConnecting = false
            store.markServerRegistered(phoneDeviceID: config.phoneDeviceID)
            let intervalMS = payload["heartbeat_interval_ms"] as? Int ?? 5000
            scheduleHeartbeat(intervalMS: intervalMS)
        case "device.binded":
            let glassDeviceID = payload["glass_device_id"] as? String ?? ""
            let phoneDeviceID = payload["phone_device_id"] as? String ?? config.phoneDeviceID
            store.markBound(glassDeviceID: glassDeviceID, phoneDeviceID: phoneDeviceID)
        case "vision.find_object.start":
            let taskID = payload["task_id"] as? String ?? ""
            let targetObject = payload["target_object"] as? String ?? ""
            let streamID = payload["stream_id"] as? String ?? ""
            let glassDeviceID = payload["glass_device_id"] as? String ?? config.desiredGlassDeviceID
            store.startFindObjectTask(
                taskID: taskID,
                targetObject: targetObject,
                streamID: streamID,
                glassDeviceID: glassDeviceID,
                phoneDeviceID: config.phoneDeviceID
            )
        case "vision.find_object.stop":
            let taskID = payload["task_id"] as? String ?? ""
            let reason = payload["reason"] as? String ?? "server_requested"
            store.stopFindObjectTask(taskID: taskID, reason: reason)
        case "device.register.failed":
            heartbeatTimer?.invalidate()
            heartbeatTimer = nil
            webSocketTask?.cancel(with: .normalClosure, reason: nil)
            webSocketTask = nil
            isConnecting = false
            let errorObject = payload["error"] as? [String: Any]
            let message = errorObject?["message"] as? String ?? "未知注册错误"
            store.markError("手机注册失败：\(message)")
            scheduleReconnect(reason: "注册失败：\(message)", kind: .registerFailed)
        default:
            break
        }
    }

    /// 发送注册消息。
    private func sendRegisterMessage() {
        guard let sinkURI = currentSinkURI else {
            return
        }
        sendControlMessage(
            semantic: "request",
            name: "device.register",
            payload: [
                "device_id": config.phoneDeviceID,
                "device_type": "phone",
                "firmware_version": "0.1.0-ios",
                "camera_sink_ws_uri": sinkURI,
                "desired_glass_device_id": config.desiredGlassDeviceID,
                "auth": [
                    "mode": "pair_token",
                    "pair_token": config.pairToken,
                ],
            ]
        )
    }

    /// 确保控制连接处于可用状态。
    ///
    /// 主要逻辑：
    /// 1. 前台运行时重复调用也只会保留一条连接。
    /// 2. 如果当前没有连接，就立刻建立新连接并发送注册。
    ///
    /// 参数：
    /// 1. `reason`：本次触发连接的原因。
    private func ensureConnected(reason: String) {
        guard !isStopping else {
            return
        }
        guard let sinkURI = currentSinkURI, !sinkURI.isEmpty else {
            store.markError("当前没有可用接收地址，暂时无法连接服务端")
            return
        }
        guard !isConnecting else {
            return
        }
        if webSocketTask != nil {
            return
        }
        guard let url = URL(string: config.serverControlWebSocketURLString) else {
            store.markError("服务端控制地址格式非法")
            return
        }

        reconnectTask?.cancel()
        reconnectTask = nil
        isConnecting = true
        store.markServerConnecting(reason)

        let task = session.webSocketTask(with: url)
        webSocketTask = task
        task.resume()
        receiveNextMessage()
        sendRegisterMessage()
    }

    /// 立即销毁旧连接并重新发起注册。
    ///
    /// 参数：
    /// 1. `reason`：触发重连的原因。
    private func reconnectNow(reason: String) {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        isConnecting = false
        ensureConnected(reason: reason)
    }

    /// 处理连接失败后的统一清理。
    ///
    /// 参数：
    /// 1. `reason`：失败原因。
    private func handleConnectionFailure(reason: String) {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        webSocketTask = nil
        isConnecting = false
        scheduleReconnect(reason: reason, kind: .connectionLost)
    }

    /// 安排下一次自动重试。
    ///
    /// 参数：
    /// 1. `reason`：当前失败原因。
    private func scheduleReconnect(reason: String, kind: ReconnectReasonKind) {
        guard !isStopping else {
            return
        }
        reconnectTask?.cancel()
        switch kind {
        case .registerFailed:
            store.markServerRetryScheduled(reason: reason, retryAfterSeconds: Self.reconnectIntervalSeconds)
        case .connectionLost:
            store.markServerReconnectScheduled(reason: reason, retryAfterSeconds: Self.reconnectIntervalSeconds)
        }
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(Self.reconnectIntervalSeconds))
            await MainActor.run {
                guard let self else {
                    return
                }
                self.reconnectTask = nil
                self.ensureConnected(reason: "scheduled_retry")
            }
        }
    }

    /// 安排周期性心跳。
    ///
    /// 参数：
    /// 1. `intervalMS`：心跳间隔，单位毫秒。
    private func scheduleHeartbeat(intervalMS: Int) {
        heartbeatTimer?.invalidate()
        let intervalSeconds = max(Double(intervalMS) / 1000.0, 0.5)
        heartbeatTimer = Timer.scheduledTimer(withTimeInterval: intervalSeconds, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.sendHeartbeat()
            }
        }
    }

    /// 发送单次心跳。
    private func sendHeartbeat() {
        sendControlMessage(
            semantic: "notify",
            name: "device.heartbeat",
            payload: [
                "device_id": config.phoneDeviceID,
            ]
        )
    }

    /// 发送一条控制消息。
    ///
    /// 参数：
    /// 1. `semantic`：消息语义。
    /// 2. `name`：消息名。
    /// 3. `payload`：业务负载。
    private func sendControlMessage(
        semantic: String,
        name: String,
        payload: [String: Any]
    ) {
        let message: [String: Any] = [
            "version": "v1",
            "message_id": "msg_\(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())",
            "channel": "control",
            "semantic": semantic,
            "name": name,
            "source": [
                "device_id": config.phoneDeviceID,
                "device_type": "phone",
                "module": "phone-ios",
            ],
            "target": [
                "device_id": "server-main",
                "device_type": "server",
                "module": "server-api",
            ],
            "ts": Int(Date().timeIntervalSince1970 * 1000),
            "payload": payload,
            "meta": [:],
        ]

        guard JSONSerialization.isValidJSONObject(message) else {
            store.markError("控制消息序列化前校验失败")
            return
        }
        do {
            let data = try JSONSerialization.data(withJSONObject: message)
            guard let text = String(data: data, encoding: .utf8) else {
                store.markError("控制消息编码失败")
                return
            }
            webSocketTask?.send(.string(text)) { [weak self] error in
                guard let self, let error else {
                    return
                }
                Task { @MainActor in
                    if !self.isStopping {
                        self.store.markError("发送控制消息失败：\(error.localizedDescription)")
                        self.handleConnectionFailure(reason: "发送控制消息失败：\(error.localizedDescription)")
                    }
                }
            }
        } catch {
            store.markError("控制消息 JSON 编码失败：\(error.localizedDescription)")
        }
    }

    /// 生成当前时间字符串。
    ///
    /// 返回值：
    /// 1. 适合写入事件列表的时间文本。
    private static func timestampText() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: Date())
    }
}

/// 服务端视频任务调试接口客户端。
///
/// 主要功能：
/// 1. 在手机点击“完成”后通知服务端停止当前视频任务。
private enum DebugVideoTaskAPI {
    /// 停止接口返回值。
    struct StopResult {
        let noop: Bool
    }

    /// 请求服务端停止当前眼镜的视频任务。
    ///
    /// 参数：
    /// 1. `glassDeviceID`：目标眼镜编号。
    ///
    /// 异常情况：
    /// 1. 网络请求失败或服务端返回错误时抛出异常。
    static func stop(glassDeviceID: String, config: ReceiverAppConfig) async throws -> StopResult {
        guard let url = URL(string: "\(config.serverHTTPBaseURLString)/api/debug/phone-video-link/stop") else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "glass_device_id": glassDeviceID,
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        let object = try JSONSerialization.jsonObject(with: data)
        guard
            let responseObject = object as? [String: Any]
        else {
            throw URLError(.cannotParseResponse)
        }
        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if httpResponse.statusCode != 200 {
            let errorObject = responseObject["error"] as? [String: Any]
            let message = errorObject?["message"] as? String ?? "服务端返回非成功状态"
            throw NSError(
                domain: "GlassesVideoReceiver.DebugVideoTaskAPI",
                code: httpResponse.statusCode,
                userInfo: [NSLocalizedDescriptionKey: message]
            )
        }
        guard
            let status = responseObject["status"] as? String,
            status == "ok",
            let task = responseObject["task"] as? [String: Any]
        else {
            throw URLError(.cannotParseResponse)
        }
        return StopResult(noop: task["noop"] as? Bool ?? false)
    }
}
