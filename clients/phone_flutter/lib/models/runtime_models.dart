class ControlEndpoint {
  const ControlEndpoint({
    required this.host,
    required this.port,
    this.scheme = 'http',
    this.basePath = '/device-api',
  });

  final String host;
  final int port;
  final String scheme;
  final String basePath;

  Map<String, dynamic> toJson() => {
        'host': host,
        'port': port,
        'scheme': scheme,
        'base_path': basePath,
      };
}

class PeerSessionState {
  const PeerSessionState({
    required this.sessionId,
    required this.peerDeviceId,
    required this.streamType,
    required this.status,
    this.listenEndpoint,
  });

  final String sessionId;
  final String peerDeviceId;
  final String streamType;
  final String status;
  final Map<String, dynamic>? listenEndpoint;

  PeerSessionState copyWith({
    String? status,
    Map<String, dynamic>? listenEndpoint,
  }) {
    return PeerSessionState(
      sessionId: sessionId,
      peerDeviceId: peerDeviceId,
      streamType: streamType,
      status: status ?? this.status,
      listenEndpoint: listenEndpoint ?? this.listenEndpoint,
    );
  }
}
