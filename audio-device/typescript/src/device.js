export class DeviceBuilder {
  constructor(deviceId) {
    this.deviceId = deviceId;
    this.userId = "";
    this.deviceName = deviceId;
    this.deviceRole = "";
    this.runtimeValue = { platform: "browser", language: "javascript" };
    this.clientType = "browser";
    this.sdkVersion = "0.1.0";
    this.properties = {};
    this.sensors = [];
    this.actuators = [];
  }

  static define(deviceId) {
    return new DeviceBuilder(deviceId);
  }

  user(userId) {
    this.userId = userId;
    return this;
  }

  name(name) {
    this.deviceName = name;
    return this;
  }

  role(role) {
    this.deviceRole = role;
    return this;
  }

  runtime(value) {
    this.runtimeValue = { ...value };
    this.clientType = value.platform ?? this.clientType;
    return this;
  }

  property(key, value) {
    this.properties[key] = value;
    return this;
  }

  sensorRgb({ modes = ["single"], format = "jpeg", frequencyHz = undefined, sampleCount = undefined, width = undefined, height = undefined, external = undefined } = {}) {
    const defaults = { format };
    if (frequencyHz !== undefined) defaults.frequency_hz = frequencyHz;
    if (sampleCount !== undefined) defaults.sample_count = sampleCount;
    if (width !== undefined) defaults.width = width;
    if (height !== undefined) defaults.height = height;
    const item = { type: "rgb", modes, default: defaults };
    if (external) item.external = { ...external };
    this.sensors.push(item);
    return this;
  }

  actuatorVibrator(commands = ["vibrate"]) {
    this.actuators.push({ type: "vibrator", commands });
    return this;
  }

  supports() {
    const result = {};
    if (this.sensors.length) result.sensors = this.sensors;
    if (this.actuators.length) result.actuators = this.actuators;
    if (!Object.keys(result).length) throw new Error("device supports must not be empty");
    return result;
  }

  registrationPayload() {
    if (!this.userId) throw new Error("user_id is required");
    const properties = { ...this.properties };
    if (this.deviceRole) properties.device_role = this.deviceRole;
    return {
      device_id: this.deviceId,
      name: this.deviceName,
      device_name: this.deviceName,
      client_type: this.clientType,
      sdk_version: this.sdkVersion,
      runtime: this.runtimeValue,
      properties,
      supports: this.supports(),
    };
  }
}
