import 'connection_session.dart';

class ConnectionManager {
  final Map<String, ConnectionSession> _sessions = <String, ConnectionSession>{};
  final Map<String, String> _connectionIdByDevice = <String, String>{};

  ConnectionSession openSession(String connectionId) {
    final session = ConnectionSession(connectionId: connectionId, state: 'connecting');
    _sessions[connectionId] = session;
    return session;
  }

  ConnectionSession? getByConnection(String connectionId) {
    return _sessions[connectionId];
  }

  ConnectionSession? getByDevice(String deviceId) {
    final connectionId = _connectionIdByDevice[deviceId];
    if (connectionId == null) return null;
    return _sessions[connectionId];
  }

  void bindDevice({
    required String connectionId,
    required String deviceId,
    String? module,
  }) {
    final session = _sessions[connectionId];
    if (session == null) return;
    session.bindDevice(deviceId: deviceId, module: module);
    session.state = 'registering';
    _connectionIdByDevice[deviceId] = connectionId;
  }

  void markOnline(String deviceId) {
    final session = getByDevice(deviceId);
    if (session == null) return;
    session.state = 'online';
    session.markHeartbeat();
  }

  void closeSession(String connectionId) {
    final session = _sessions.remove(connectionId);
    if (session?.deviceId != null) {
      _connectionIdByDevice.remove(session!.deviceId);
    }
  }
}

