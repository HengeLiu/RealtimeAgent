import Foundation

public struct AudioChatDevice {
    public var deviceID: String
    public var userID: String
    public var name: String
    public var role: String?
    public var runtime: [String: Any]
    public var properties: [String: Any]
    public var sensors: [[String: Any]]
    public var actuators: [[String: Any]]

    public init(deviceID: String) {
        self.deviceID = deviceID
        self.userID = ""
        self.name = deviceID
        self.runtime = ["platform": "ios", "language": "swift"]
        self.properties = [:]
        self.sensors = []
        self.actuators = []
    }

    public func user(_ userID: String) -> AudioChatDevice {
        var copy = self
        copy.userID = userID
        return copy
    }

    public func named(_ name: String) -> AudioChatDevice {
        var copy = self
        copy.name = name
        return copy
    }

    public func role(_ role: String) -> AudioChatDevice {
        var copy = self
        copy.role = role
        return copy
    }

    public func sensorRgb(modes: [String] = ["single"], format: String = "jpeg", frequencyHz: Double? = nil) -> AudioChatDevice {
        var copy = self
        var defaults: [String: Any] = ["format": format]
        if let frequencyHz { defaults["frequency_hz"] = frequencyHz }
        copy.sensors.append(["type": "rgb", "modes": modes, "default": defaults])
        return copy
    }

    public func actuatorVibrator(commands: [String] = ["vibrate"]) -> AudioChatDevice {
        var copy = self
        copy.actuators.append(["type": "vibrator", "commands": commands])
        return copy
    }

    public var registrationPayload: [String: Any] {
        var props = properties
        if let role { props["device_role"] = role }
        var supports: [String: Any] = [:]
        if !sensors.isEmpty { supports["sensors"] = sensors }
        if !actuators.isEmpty { supports["actuators"] = actuators }
        return [
            "device_id": deviceID,
            "name": name,
            "device_name": name,
            "client_type": runtime["platform"] as? String ?? "ios",
            "sdk_version": "0.1.0",
            "runtime": runtime,
            "properties": props,
            "supports": supports,
        ]
    }
}
