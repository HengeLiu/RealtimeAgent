class ConnectionSession {
  ConnectionSession({
    required this.connectionId,
    this.deviceId,
    this.module,
    this.lastHeartbeatAt,
    this.state = 'offline',
  });

  final String connectionId;
  String? deviceId;
  String? module;
  DateTime? lastHeartbeatAt;
  String state;

  void bindDevice({required String deviceId, String? module}) {
    this.deviceId = deviceId;
    this.module = module;
  }

  void markHeartbeat() {
    lastHeartbeatAt = DateTime.now().toUtc();
  }
}

