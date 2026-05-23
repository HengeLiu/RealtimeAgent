import Foundation

public struct RealtimeAgentDevice: @unchecked Sendable {
    public var deviceID: String
    public var userID: String
    public var name: String
    public var role: String?
    public var clientType: String
    public var sdkVersion: String
    public var runtime: [String: Any]
    public var properties: [String: Any]
    public var authPayload: [String: Any]?
    public var sensors: [[String: Any]]
    public var actuators: [[String: Any]]

    public init(deviceID: String) {
        self.deviceID = deviceID
        self.userID = ""
        self.name = deviceID
        self.clientType = "ios"
        self.sdkVersion = "0.1.0"
        self.runtime = ["platform": "ios", "language": "swift"]
        self.properties = [:]
        self.authPayload = nil
        self.sensors = []
        self.actuators = []
    }

    public func user(_ userID: String) -> RealtimeAgentDevice {
        var copy = self
        copy.userID = userID
        return copy
    }

    public func named(_ name: String) -> RealtimeAgentDevice {
        var copy = self
        copy.name = name
        return copy
    }

    public func role(_ role: String) -> RealtimeAgentDevice {
        var copy = self
        copy.role = role
        return copy
    }

    /// 设置注册 payload 中的客户端类型。
    public func clientType(_ clientType: String) -> RealtimeAgentDevice {
        var copy = self
        copy.clientType = clientType
        return copy
    }

    /// 设置注册 payload 中的 SDK 版本。
    public func sdkVersion(_ sdkVersion: String) -> RealtimeAgentDevice {
        var copy = self
        copy.sdkVersion = sdkVersion
        return copy
    }

    /// 批量设置设备属性。
    public func properties(_ properties: [String: Any]) -> RealtimeAgentDevice {
        var copy = self
        copy.properties = properties
        return copy
    }

    /// 直接设置结构化 supports。
    ///
    /// 主要功能：让参考端和配置同步工具可以把已生成的 sensors / actuators 原样透传给 server。
    public func supports(_ supports: [String: Any]) -> RealtimeAgentDevice {
        var copy = self
        copy.sensors = supports["sensors"] as? [[String: Any]] ?? []
        copy.actuators = supports["actuators"] as? [[String: Any]] ?? []
        return copy
    }

    public func sensorRgb(modes: [String] = ["single"], format: String = "jpeg", frequencyHz: Double? = nil) -> RealtimeAgentDevice {
        var copy = self
        var defaults: [String: Any] = ["format": format]
        if let frequencyHz { defaults["frequency_hz"] = frequencyHz }
        copy.sensors.append(["type": "rgb", "modes": modes, "default": defaults])
        return copy
    }

    public func actuatorVibrator(commands: [String] = ["vibrate"]) -> RealtimeAgentDevice {
        var copy = self
        copy.actuators.append(["type": "vibrator", "commands": commands])
        return copy
    }

    /// 设置注册鉴权 payload。
    ///
    /// 主要功能：把 static token 或 signed token 等端侧鉴权信息写入注册 payload。
    /// 主要逻辑：不解释鉴权模式，只透传给 server 校验，避免 SDK 绑定具体配对服务。
    /// 参数：`auth` 为符合 realtime-agent 协议的鉴权字段。
    /// 返回值：更新后的设备声明。
    /// 异常情况：本函数不抛错，server 会在注册阶段返回失败原因。
    public func auth(_ auth: [String: Any]) -> RealtimeAgentDevice {
        var copy = self
        copy.authPayload = auth
        return copy
    }

    public var registrationPayload: [String: Any] {
        var props = properties
        if let role { props["device_role"] = role }
        var supports: [String: Any] = [:]
        if !sensors.isEmpty { supports["sensors"] = sensors }
        if !actuators.isEmpty { supports["actuators"] = actuators }
        var payload: [String: Any] = [
            "device_id": deviceID,
            "name": name,
            "device_name": name,
            "client_type": clientType,
            "sdk_version": sdkVersion,
            "runtime": runtime,
            "properties": props,
            "supports": supports,
        ]
        if let authPayload { payload["auth"] = authPayload }
        return payload
    }
}
