class DeviceModel {
  const DeviceModel({
    required this.deviceId,
    required this.deviceType,
    required this.protocolVersion,
    required this.capabilities,
    required this.status,
  });

  final String deviceId;
  final String deviceType;
  final String protocolVersion;
  final List<String> capabilities;
  final String status;
}
