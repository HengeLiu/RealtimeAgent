import Foundation

/// JSON 配置中的通用值。
///
/// 主要功能：
/// 1. 保留 `properties` 和历史配置字段中的布尔、数字、字符串、数组和对象。
/// 2. 避免 Swift 侧把协议字段压缩成固定枚举，便于跟随 server 配置演进。
enum JSONValue: Codable, Equatable {
    case string(String)
    case bool(Bool)
    case number(Double)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            self = .object(try container.decode([String: JSONValue].self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value):
            try container.encode(value)
        case let .bool(value):
            try container.encode(value)
        case let .number(value):
            try container.encode(value)
        case let .object(value):
            try container.encode(value)
        case let .array(value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var object: Any {
        switch self {
        case let .string(value):
            return value
        case let .bool(value):
            return value
        case let .number(value):
            return value
        case let .object(value):
            return value.mapValues { $0.object }
        case let .array(value):
            return value.map { $0.object }
        case .null:
            return NSNull()
        }
    }
}

/// realtime-agent 端侧鉴权配置。
///
/// 主要功能：
/// 1. 支持本地 `disabled`、静态 token 和后续 signed token。
/// 2. 保持字段名与 `realtime-agent.config.sync` 生成结果一致。
struct AuthConfig: Codable, Equatable {
    var mode: String
    var token: String?
    var signedToken: String?

    enum CodingKeys: String, CodingKey {
        case mode
        case token
        case signedToken = "signed_token"
    }

    var payload: [String: Any] {
        var data: [String: Any] = ["mode": mode]
        if let token {
            data["token"] = token
        }
        if let signedToken {
            data["signed_token"] = signedToken
        }
        return data
    }
}

/// SDK 自动硬件接入开关。
///
/// 主要功能：保持硬件默认禁用，只有配置显式 `enabled=true` 时才让 SDK 注册并维护对应链路。
struct HardwareEnableConfig: Codable, Equatable {
    var enabled: Bool

    static let disabled = HardwareEnableConfig(enabled: false)
}

/// speaker 播放 buffer 配置。
struct SpeakerBufferConfig: Codable, Equatable {
    var startWatermarkMS: Int
    var lowWatermarkMS: Int
    var highWatermarkMS: Int
    var maxBufferMS: Int

    enum CodingKeys: String, CodingKey {
        case startWatermarkMS = "start_watermark_ms"
        case lowWatermarkMS = "low_watermark_ms"
        case highWatermarkMS = "high_watermark_ms"
        case maxBufferMS = "max_buffer_ms"
    }

    static let `default` = SpeakerBufferConfig(
        startWatermarkMS: 120,
        lowWatermarkMS: 300,
        highWatermarkMS: 800,
        maxBufferMS: 1200
    )
}

/// speaker 自动接入配置。
struct SpeakerConfig: Codable, Equatable {
    var enabled: Bool
    var buffer: SpeakerBufferConfig

    static let disabled = SpeakerConfig(enabled: false, buffer: .default)
}

/// iOS phone 参考端配置。
///
/// 主要功能：
/// 1. 从 `AppConfig.json` 读取 server、user、device、auth、硬件 enable 和 properties。
/// 2. 为 SDK 标准入口提供当前配置。
/// 3. 缺少配置文件时提供本地默认值，便于打开工程后立即编译。
struct AppConfig: Codable, Equatable {
    var serverURL: String
    var userID: String
    var deviceID: String
    var auth: AuthConfig
    var protocolVersion: String
    var directCameraSinkPort: UInt16
    var audioInput: HardwareEnableConfig
    var camera: HardwareEnableConfig
    var speaker: SpeakerConfig
    var properties: [String: JSONValue]
    var supports: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case serverURL = "server_url"
        case userID = "user_id"
        case deviceID = "device_id"
        case auth
        case protocolVersion = "protocol_version"
        case directCameraSinkPort = "direct_camera_sink_port"
        case audioInput = "audio_input"
        case camera
        case speaker
        case properties
        case supports
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        serverURL = try container.decode(String.self, forKey: .serverURL)
        userID = try container.decode(String.self, forKey: .userID)
        deviceID = try container.decode(String.self, forKey: .deviceID)
        auth = try container.decode(AuthConfig.self, forKey: .auth)
        protocolVersion = try container.decodeIfPresent(String.self, forKey: .protocolVersion) ?? "realtime-agent.v1"
        directCameraSinkPort = try container.decodeIfPresent(UInt16.self, forKey: .directCameraSinkPort) ?? 9001
        audioInput = try container.decodeIfPresent(HardwareEnableConfig.self, forKey: .audioInput) ?? .disabled
        camera = try container.decodeIfPresent(HardwareEnableConfig.self, forKey: .camera) ?? .disabled
        speaker = try container.decodeIfPresent(SpeakerConfig.self, forKey: .speaker) ?? .disabled
        properties = try container.decodeIfPresent([String: JSONValue].self, forKey: .properties) ?? [:]
        supports = try container.decodeIfPresent([String: JSONValue].self, forKey: .supports) ?? [:]
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(serverURL, forKey: .serverURL)
        try container.encode(userID, forKey: .userID)
        try container.encode(deviceID, forKey: .deviceID)
        try container.encode(auth, forKey: .auth)
        try container.encode(protocolVersion, forKey: .protocolVersion)
        try container.encode(directCameraSinkPort, forKey: .directCameraSinkPort)
        try container.encode(audioInput, forKey: .audioInput)
        try container.encode(camera, forKey: .camera)
        try container.encode(speaker, forKey: .speaker)
        try container.encode(properties, forKey: .properties)
        try container.encode(supports, forKey: .supports)
    }

    init(
        serverURL: String,
        userID: String,
        deviceID: String,
        auth: AuthConfig,
        protocolVersion: String,
        directCameraSinkPort: UInt16 = 9001,
        audioInput: HardwareEnableConfig = .disabled,
        camera: HardwareEnableConfig = .disabled,
        speaker: SpeakerConfig = .disabled,
        properties: [String: JSONValue],
        supports: [String: JSONValue] = [:]
    ) {
        self.serverURL = serverURL
        self.userID = userID
        self.deviceID = deviceID
        self.auth = auth
        self.protocolVersion = protocolVersion
        self.directCameraSinkPort = directCameraSinkPort
        self.audioInput = audioInput
        self.camera = camera
        self.speaker = speaker
        self.properties = properties
        self.supports = supports
    }

    static func load() -> AppConfig {
        let bundle = Bundle.main
        let candidateURLs = [
            bundle.url(forResource: "AppConfig", withExtension: "json"),
            bundle.url(forResource: "AppConfig.example", withExtension: "json"),
        ].compactMap { $0 }
        for url in candidateURLs {
            if let data = try? Data(contentsOf: url),
               let config = try? JSONDecoder().decode(AppConfig.self, from: data) {
                return config
            }
        }
        return .defaultLocal
    }

    static let defaultLocal = AppConfig(
        serverURL: "http://127.0.0.1:8765",
        userID: "user-endpoint-001",
        deviceID: "dev-ios-phone-001",
        auth: AuthConfig(mode: "disabled", token: nil, signedToken: nil),
        protocolVersion: "realtime-agent.v1",
        directCameraSinkPort: 9001,
        audioInput: .disabled,
        camera: .disabled,
        speaker: .disabled,
        properties: [
            "direct.camera_sink": .bool(true),
            "direct.camera_sink.path": .string("/ws/camera"),
            "direct.camera_sink.frame_format": .string("realtime_agent.direct_frame.v1"),
            "audio.aec": .string("replaceable"),
            "audio.wake_word": .string("manual"),
        ],
        supports: [:]
    )
}
