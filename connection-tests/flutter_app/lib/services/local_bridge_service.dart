import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

class LocalBridgeService {
  LocalBridgeService({this.port = 9100});

  final int port;
  HttpServer? _server;
  WebSocket? _esp32Socket;
  String? _localHost;

  final StreamController<Map<String, dynamic>> _eventController =
      StreamController<Map<String, dynamic>>.broadcast();

  Stream<Map<String, dynamic>> get events => _eventController.stream;

  String? get localHost => _localHost;
  bool get isEsp32Connected => _esp32Socket != null;

  Future<void> start() async {
    if (_server != null) {
      return;
    }

    _localHost = await _pickPreferredIpv4();
    _server = await HttpServer.bind(InternetAddress.anyIPv4, port, shared: true);
    _server!.listen(_handleRequest, onError: (Object error) {
      _eventController.add({
        'type': 'local_server_error',
        'message': error.toString(),
      });
    });

    _eventController.add({
      'type': 'local_server_ready',
      'host': _localHost,
      'port': port,
      'path': '/ws/direct',
    });
  }

  Future<bool> refreshEndpoint({String? peerIp}) async {
    final nextHost = await _pickPreferredIpv4(peerIp: peerIp);
    final changed = nextHost != _localHost;
    _localHost = nextHost;

    if (changed) {
      _eventController.add({
        'type': 'local_server_ready',
        'host': _localHost,
        'port': port,
        'path': '/ws/direct',
      });
    }

    return changed;
  }

  Future<void> stop() async {
    await _esp32Socket?.close();
    _esp32Socket = null;
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> sendText(String text) async {
    _esp32Socket?.add(text);
  }

  Future<void> sendJson(Map<String, dynamic> payload) async {
    _esp32Socket?.add(jsonEncode(payload));
  }

  void _handleRequest(HttpRequest request) async {
    if (request.uri.path == '/ws/direct' && WebSocketTransformer.isUpgradeRequest(request)) {
      final socket = await WebSocketTransformer.upgrade(request);
      await _attachEsp32Socket(socket);
      return;
    }

    if (request.uri.path == '/health') {
      final body = jsonEncode({
        'ok': true,
        'host': _localHost,
        'port': port,
        'esp32_connected': isEsp32Connected,
      });
      request.response
        ..headers.contentType = ContentType.json
        ..write(body);
      await request.response.close();
      return;
    }

    request.response.statusCode = HttpStatus.notFound;
    await request.response.close();
  }

  Future<void> _attachEsp32Socket(WebSocket socket) async {
    await _esp32Socket?.close();
    _esp32Socket = socket;
    _eventController.add({
      'type': 'direct_connection',
      'status': 'connected',
    });

    socket.listen(
      (dynamic raw) {
        if (raw is String) {
          try {
            final decoded = jsonDecode(raw);
            if (decoded is Map<String, dynamic>) {
              _eventController.add({
                'type': 'direct_json',
                'payload': decoded,
              });
              return;
            }
          } catch (_) {
            _eventController.add({
              'type': 'direct_text',
              'text': raw,
            });
            return;
          }
        }

        if (raw is List<int>) {
          _eventController.add({
            'type': 'direct_frame',
            'bytes': Uint8List.fromList(raw),
          });
          return;
        }

        _eventController.add({
          'type': 'direct_unknown',
          'data': raw.toString(),
        });
      },
      onDone: () {
        _esp32Socket = null;
        _eventController.add({
          'type': 'direct_connection',
          'status': 'disconnected',
        });
      },
      onError: (Object error) {
        _esp32Socket = null;
        _eventController.add({
          'type': 'direct_connection',
          'status': 'error',
          'message': error.toString(),
        });
      },
      cancelOnError: true,
    );
  }

  Future<String?> _pickPreferredIpv4({String? peerIp}) async {
    final interfaces = await NetworkInterface.list(
      includeLoopback: false,
      type: InternetAddressType.IPv4,
    );

    String? fallback;
    for (final interface in interfaces) {
      for (final address in interface.addresses) {
        final ip = address.address;
        if (_isPrivateIpv4(ip)) {
          fallback ??= ip;
          if (peerIp != null && _sameSubnet(ip, peerIp)) {
            return ip;
          }
          if (_looksLikeHotspotGateway(ip)) {
            fallback = ip;
          }
        }
      }
    }
    return fallback;
  }

  bool _isPrivateIpv4(String ip) {
    return ip.startsWith('192.168.') ||
        ip.startsWith('10.') ||
        ip.startsWith('172.16.') ||
        ip.startsWith('172.17.') ||
        ip.startsWith('172.18.') ||
        ip.startsWith('172.19.') ||
        ip.startsWith('172.20.') ||
        ip.startsWith('172.21.') ||
        ip.startsWith('172.22.') ||
        ip.startsWith('172.23.') ||
        ip.startsWith('172.24.') ||
        ip.startsWith('172.25.') ||
        ip.startsWith('172.26.') ||
        ip.startsWith('172.27.') ||
        ip.startsWith('172.28.') ||
        ip.startsWith('172.29.') ||
        ip.startsWith('172.30.') ||
        ip.startsWith('172.31.');
  }

  bool _sameSubnet(String a, String b) {
    final pa = a.split('.');
    final pb = b.split('.');
    if (pa.length != 4 || pb.length != 4) {
      return false;
    }
    return pa[0] == pb[0] && pa[1] == pb[1] && pa[2] == pb[2];
  }

  bool _looksLikeHotspotGateway(String ip) {
    return ip.endsWith('.1');
  }

  void dispose() {
    stop();
    _eventController.close();
  }
}
