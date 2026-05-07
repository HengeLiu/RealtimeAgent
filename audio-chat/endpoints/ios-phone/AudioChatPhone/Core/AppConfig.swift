import Foundation

/// JSON 配置中的通用值。
///
/// 主要功能：
/// 1. 保留 `capabilities` 和 `subscriptions.filter` 中的布尔、数字、字符串、数组和对象。
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

/// audio-chat 端侧鉴权配置。
///
/// 主要功能：
/// 1. 支持本地 `disabled`、静态 token 和后续 signed token。
/// 2. 保持字段名与 `audio-chat.config.sync` 生成结果一致。
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

/// 端侧事件订阅配置。
///
/// 主要功能：
/// 1. 声明端侧愿意接收的事件模式。
/// 2. 使用 filter 表达 stream_type 等路由条件。
struct SubscriptionConfig: Codable, Equatable {
    var event: String
    var filter: [String: JSONValue]?

    var payload: [String: Any] {
        var data: [String: Any] = ["event": event]
        if let filter {
            data["filter"] = filter.mapValues { $0.object }
        }
        return data
    }
}

/// iOS phone 参考端配置。
///
/// 主要功能：
/// 1. 从 `AppConfig.json` 读取 server、user、device、auth、capabilities 和 subscriptions。
/// 2. 为注册事件提供协议兼容 payload。
/// 3. 缺少配置文件时提供本地默认值，便于打开工程后立即编译。
struct AppConfig: Codable, Equatable {
    var serverURL: String
    var userID: String
    var deviceID: String
    var auth: AuthConfig
    var protocolVersion: String
    var capabilities: [String: JSONValue]
    var subscriptions: [SubscriptionConfig]

    enum CodingKeys: String, CodingKey {
        case serverURL = "server_url"
        case userID = "user_id"
        case deviceID = "device_id"
        case auth
        case protocolVersion = "protocol_version"
        case capabilities
        case subscriptions
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
        protocolVersion: "audio-chat.v1",
        capabilities: [
            "streams.produce": .array([.string("sensor.rgb"), .string("sensor.mic")]),
            "streams.consume": .array([.string("actuator.speaker"), .string("actuator.haptic")]),
            "sensor.rgb": .bool(true),
            "phone.task.find_object_phone_task": .bool(true),
            "phone.task.traffic_light_phone_task": .bool(true),
            "audio.aec": .string("replaceable"),
            "audio.wake_word": .string("manual"),
        ],
        subscriptions: [
            SubscriptionConfig(event: "stream.control.*", filter: ["stream_type": .string("sensor.rgb")]),
            SubscriptionConfig(event: "stream.output.*", filter: ["stream_type": .string("actuator.speaker")]),
            SubscriptionConfig(event: "control.device.command.*", filter: nil),
            SubscriptionConfig(event: "control.audio_session.*", filter: nil),
        ]
    )
}
