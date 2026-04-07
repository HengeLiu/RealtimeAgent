class DeviceModel {
  const DeviceModel({
    required this.deviceId,
    required this.deviceType,
    required this.protocolVersion,
    required this.capabilities,
    required this.status,
    this.deviceName,
    this.deviceModel,
    this.firmwareVersion,
    this.lastSeenAt,
    this.metadata = const {},
  });

  final String deviceId;
  final String deviceType;
  final String protocolVersion;
  final List<String> capabilities;
  final String status;
  final String? deviceName;
  final String? deviceModel;
  final String? firmwareVersion;
  final String? lastSeenAt;
  final Map<String, dynamic> metadata;

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'device_id': deviceId,
      'device_type': deviceType,
      'protocol_version': protocolVersion,
      'capabilities': capabilities,
      'status': status,
      'device_name': deviceName,
      'device_model': deviceModel,
      'firmware_version': firmwareVersion,
      'last_seen_at': lastSeenAt,
      'metadata': metadata,
    };
  }
}
