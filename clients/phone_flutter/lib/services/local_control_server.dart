import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/runtime_models.dart';

typedef PeerFrameHandler = Future<Map<String, dynamic>> Function(
  String taskSessionId,
  String path,
  Map<String, dynamic> payload,
);

class LocalControlServer {
  LocalControlServer({
    required this.port,
    required this.deviceId,
    required this.onPreparePeerLink,
    required this.onStopPeerLink,
    required this.onPeerFrame,
  });

  final int port;
  final String deviceId;
  final Future<Map<String, dynamic>> Function(
    String taskSessionId,
    String peerDeviceId,
    String streamType,
  ) onPreparePeerLink;
  final Future<Map<String, dynamic>> Function(String taskSessionId) onStopPeerLink;
  final PeerFrameHandler onPeerFrame;

  HttpServer? _server;
  String? _localHost;

  String? get localHost => _localHost;

  Future<void> start() async {
    if (_server != null) {
      return;
    }
    _localHost = await _pickPreferredIpv4();
    _server = await HttpServer.bind(InternetAddress.anyIPv4, port, shared: true);
    _server!.listen(_handleRequest);
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> _handleRequest(HttpRequest request) async {
    if (request.uri.path == '/health') {
      await _writeJson(request.response, {
        'ok': true,
        'device_id': deviceId,
        'host': _localHost,
        'port': port,
      });
      return;
    }

    if (request.uri.path == '/device-api/task/prepare-peer-link' && request.method == 'POST') {
      final payload = jsonDecode(await utf8.decoder.bind(request).join()) as Map<String, dynamic>;
      final result = await onPreparePeerLink(
        payload['task_session_id'] as String,
        payload['peer_device_id'] as String,
        payload['stream_type'] as String,
      );
      await _writeJson(request.response, {'ok': true, ...result});
      return;
    }

    if (request.uri.path == '/device-api/task/stop-peer-link' && request.method == 'POST') {
      final payload = jsonDecode(await utf8.decoder.bind(request).join()) as Map<String, dynamic>;
      final result = await onStopPeerLink(payload['task_session_id'] as String);
      await _writeJson(request.response, {'ok': true, ...result});
      return;
    }

    if (request.uri.pathSegments.isNotEmpty &&
        request.uri.pathSegments.first == 'peer-link' &&
        WebSocketTransformer.isUpgradeRequest(request)) {
      final taskSessionId = request.uri.pathSegments.length >= 2 ? request.uri.pathSegments[1] : '';
      final socket = await WebSocketTransformer.upgrade(request);
      _handlePeerSocket(taskSessionId, socket);
      return;
    }

    request.response.statusCode = HttpStatus.notFound;
    await request.response.close();
  }

  Future<void> _handlePeerSocket(String taskSessionId, WebSocket socket) async {
    socket.listen((dynamic raw) async {
      if (raw is! String) {
        return;
      }
      final decoded = jsonDecode(raw) as Map<String, dynamic>;
      final requestId = decoded['request_id'];
      final path = decoded['path'] as String? ?? '';
      final payload = (decoded['payload'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{};
      try {
        final responsePayload = await onPeerFrame(taskSessionId, path, payload);
        socket.add(jsonEncode({
          'request_id': requestId,
          'status': 'ok',
          'payload': responsePayload,
        }));
      } catch (error) {
        socket.add(jsonEncode({
          'request_id': requestId,
          'status': 'error',
          'error': error.toString(),
        }));
      }
    });
  }

  Future<void> _writeJson(HttpResponse response, Map<String, dynamic> payload) async {
    response.headers.contentType = ContentType.json;
    response.write(jsonEncode(payload));
    await response.close();
  }

  Future<String?> _pickPreferredIpv4() async {
    final interfaces = await NetworkInterface.list(
      includeLoopback: false,
      type: InternetAddressType.IPv4,
    );
    for (final interface in interfaces) {
      for (final address in interface.addresses) {
        final ip = address.address;
        if (ip.startsWith('192.168.') || ip.startsWith('10.') || ip.startsWith('172.')) {
          return ip;
        }
      }
    }
    return null;
  }
}
