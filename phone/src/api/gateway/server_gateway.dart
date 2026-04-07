import 'dart:async';
import 'dart:convert';

import '../../protocol/messages/envelope.dart';
import '../handlers/server_message_handler.dart';

enum GatewayConnectionState {
  offline,
  connecting,
  registering,
  online,
  degraded,
  reconnecting,
  error,
}

class ServerGateway {
  ServerGateway({required this.onSend, this.onStateChanged});

  final void Function(String payload) onSend;
  final void Function(GatewayConnectionState state)? onStateChanged;

  final Map<String, List<ServerMessageHandler>> _messageHandlers =
      <String, List<ServerMessageHandler>>{};
  final Map<String, List<ServerMessageHandler>> _domainHandlers =
      <String, List<ServerMessageHandler>>{};

  GatewayConnectionState _state = GatewayConnectionState.offline;
  int _seq = 0;

  GatewayConnectionState get state => _state;

  Future<void> connect() async {
    _setState(GatewayConnectionState.connecting);
  }

  Future<void> disconnect() async {
    _setState(GatewayConnectionState.offline);
  }

  Future<void> reconnect({
    required String deviceId,
    required String deviceType,
    required String protocolVersion,
    required List<String> capabilities,
    required Map<String, dynamic> auth,
  }) async {
    _setState(GatewayConnectionState.reconnecting);
    await registerDevice(
      deviceId: deviceId,
      deviceType: deviceType,
      protocolVersion: protocolVersion,
      capabilities: capabilities,
      auth: auth,
    );
  }

  Future<void> registerDevice({
    required String deviceId,
    required String deviceType,
    required String protocolVersion,
    required List<String> capabilities,
    required Map<String, dynamic> auth,
    Map<String, dynamic> network = const {},
  }) async {
    _setState(GatewayConnectionState.registering);
    send(
      Envelope(
        messageId: _nextId('msg'),
        traceId: _nextId('trace'),
        messageType: 'command',
        messageName: 'system.register',
        protocolVersion: protocolVersion,
        source: Endpoint(deviceId: deviceId, module: 'phone-api'),
        target: Endpoint(deviceId: 'dev_server_main', module: 'server-api'),
        timestamp: DateTime.now().toUtc().toIso8601String(),
        requiresAck: true,
        payload: {
          'device': {
            'device_id': deviceId,
            'device_type': deviceType,
            'protocol_version': protocolVersion,
            'capabilities': capabilities,
            'status': 'registering',
          },
          'auth': auth,
          'network': network,
        },
      ),
    );
  }

  Future<void> sendHeartbeat({
    required String deviceId,
    required String protocolVersion,
    String deviceStatus = 'online',
    int? batteryLevel,
    List<String> activeTaskIds = const [],
    String? connectionQuality,
  }) async {
    send(
      Envelope(
        messageId: _nextId('msg'),
        traceId: _nextId('trace'),
        messageType: 'event',
        messageName: 'system.heartbeat',
        protocolVersion: protocolVersion,
        source: Endpoint(deviceId: deviceId, module: 'phone-api'),
        target: Endpoint(deviceId: 'dev_server_main', module: 'server-api'),
        timestamp: DateTime.now().toUtc().toIso8601String(),
        payload: {
          'device_status': deviceStatus,
          'battery_level': batteryLevel,
          'active_task_ids': activeTaskIds,
          'connection_quality': connectionQuality,
        },
      ),
    );
  }

  void send(Envelope envelope) {
    onSend(jsonEncode(envelope.toJson()));
  }

  void addMessageHandler(String messageName, ServerMessageHandler handler) {
    _messageHandlers.putIfAbsent(messageName, () => <ServerMessageHandler>[]).add(handler);
  }

  void addDomainHandler(String domain, ServerMessageHandler handler) {
    _domainHandlers.putIfAbsent(domain, () => <ServerMessageHandler>[]).add(handler);
  }

  Future<void> handleIncomingRaw(String rawPayload) async {
    final dynamic parsed = jsonDecode(rawPayload);
    if (parsed is! Map<String, dynamic>) {
      _setState(GatewayConnectionState.error);
      return;
    }

    final envelope = Envelope.fromJson(parsed);
    await _handleStateMessages(envelope);

    final handlers = <ServerMessageHandler>[
      ...?_messageHandlers[envelope.messageName],
      ...?_domainHandlers[envelope.messageName.split('.').first],
    ];

    for (final handler in handlers) {
      await handler.handle(envelope);
    }
  }

  Future<void> _handleStateMessages(Envelope envelope) async {
    switch (envelope.messageName) {
      case 'system.registered':
        _setState(GatewayConnectionState.online);
        break;
      case 'system.heartbeat_ack':
        if (_state != GatewayConnectionState.online) {
          _setState(GatewayConnectionState.online);
        }
        break;
      case 'system.error':
        _setState(GatewayConnectionState.error);
        break;
      default:
        break;
    }
  }

  void markDegraded() {
    _setState(GatewayConnectionState.degraded);
  }

  void _setState(GatewayConnectionState next) {
    if (_state == next) return;
    _state = next;
    onStateChanged?.call(next);
  }

  String _nextId(String prefix) {
    _seq += 1;
    final millis = DateTime.now().millisecondsSinceEpoch;
    return '${prefix}_${millis}_$_seq';
  }
}
