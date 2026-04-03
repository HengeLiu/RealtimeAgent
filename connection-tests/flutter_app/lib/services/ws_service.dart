import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

class WsService {
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  final StreamController<Map<String, dynamic>> _eventController =
      StreamController<Map<String, dynamic>>.broadcast();

  Stream<Map<String, dynamic>> get events => _eventController.stream;

  bool get isConnected => _channel != null;

  Future<void> connect(String httpBaseUrl) async {
    await disconnect();
    final uri = Uri.parse('${_toWsBase(httpBaseUrl)}/ws/app');
    _channel = WebSocketChannel.connect(uri);
    _subscription = _channel!.stream.listen(
      (dynamic raw) {
        if (raw is String) {
          try {
            final decoded = jsonDecode(raw);
            if (decoded is Map<String, dynamic>) {
              _eventController.add(decoded);
              return;
            }
          } catch (_) {
            _eventController.add({
              'type': 'plain_text',
              'text': raw,
            });
            return;
          }
        }
        _eventController.add({
          'type': 'unknown_event',
          'data': raw.toString(),
        });
      },
      onDone: () {
        _eventController.add({
          'type': 'socket_closed',
        });
      },
      onError: (Object error) {
        _eventController.add({
          'type': 'socket_error',
          'message': error.toString(),
        });
      },
    );
  }

  Future<void> disconnect() async {
    await _subscription?.cancel();
    await _channel?.sink.close();
    _subscription = null;
    _channel = null;
  }

  void sendJson(Map<String, dynamic> payload) {
    _channel?.sink.add(jsonEncode(payload));
  }

  String _toWsBase(String input) {
    final normalized = normalizeHttpBase(input);
    if (normalized.startsWith('https://')) {
      return normalized.replaceFirst('https://', 'wss://');
    }
    return normalized.replaceFirst('http://', 'ws://');
  }

  static String normalizeHttpBase(String input) {
    final trimmed = input.trim();
    if (trimmed.isEmpty) {
      return '';
    }
    if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
      return trimmed.replaceAll(RegExp(r'/$'), '');
    }
    return 'http://${trimmed.replaceAll(RegExp(r'/$'), '')}';
  }

  void dispose() {
    disconnect();
    _eventController.close();
  }
}
