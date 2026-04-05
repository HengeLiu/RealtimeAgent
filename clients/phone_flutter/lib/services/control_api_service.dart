import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/runtime_models.dart';

class ControlApiService {
  static const Duration _requestTimeout = Duration(seconds: 6);

  String normalizeBaseUrl(String input) {
    final trimmed = input.trim();
    if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
      return trimmed.replaceAll(RegExp(r'/$'), '');
    }
    return 'http://${trimmed.replaceAll(RegExp(r'/$'), '')}';
  }

  Future<Map<String, dynamic>> registerDevice({
    required String serverBaseUrl,
    required String deviceId,
    required ControlEndpoint endpoint,
  }) async {
    final response = await http
        .post(
          Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/devices/register'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'device_id': deviceId,
            'runtime': 'phone',
            'display_name': '手机',
            'endpoint': endpoint.toJson(),
            'capabilities': ['local_detection', 'ocr', 'map_navigation'],
            'status': 'ready',
          }),
        )
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'registerDevice');
  }

  Future<Map<String, dynamic>> heartbeat({
    required String serverBaseUrl,
    required String deviceId,
    required ControlEndpoint endpoint,
  }) async {
    final response = await http
        .post(
          Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/devices/heartbeat'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'device_id': deviceId,
            'status': 'ready',
            'endpoint': endpoint.toJson(),
          }),
        )
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'heartbeat');
  }

  Future<Map<String, dynamic>> fetchSnapshot(String serverBaseUrl) async {
    final response = await http
        .get(Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/snapshot'))
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'fetchSnapshot');
  }

  Future<Map<String, dynamic>> reportTaskState({
    required String serverBaseUrl,
    required String sessionId,
    required String status,
    required String phase,
    required Map<String, dynamic> summary,
    Map<String, dynamic>? result,
  }) async {
    final response = await http
        .post(
          Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/tasks/$sessionId/state'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'runtime': 'phone',
            'status': status,
            'phase': phase,
            'summary': summary,
            if (result != null) 'result': result,
          }),
        )
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'reportTaskState');
  }

  Future<Map<String, dynamic>> reportPeerLinkReady({
    required String serverBaseUrl,
    required String sessionId,
    required ControlEndpoint listenEndpoint,
    required String streamType,
    required String expiresAt,
  }) async {
    final response = await http
        .post(
          Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/tasks/$sessionId/peer-link/ready'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'listen_endpoint': listenEndpoint.toJson(),
            'stream_type': streamType,
            'expires_at': expiresAt,
          }),
        )
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'reportPeerLinkReady');
  }

  Future<Map<String, dynamic>> reportPeerLinkStatus({
    required String serverBaseUrl,
    required String sessionId,
    required String runtime,
    required String status,
    String? reason,
  }) async {
    final response = await http
        .post(
          Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/tasks/$sessionId/peer-link/status'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'runtime': runtime,
            'status': status,
            if (reason != null) 'reason': reason,
          }),
        )
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'reportPeerLinkStatus');
  }

  Future<Map<String, dynamic>> reportPeerLinkBroken({
    required String serverBaseUrl,
    required String sessionId,
    required String runtime,
    required String reason,
    bool autoRecover = false,
  }) async {
    final response = await http
        .post(
          Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/tasks/$sessionId/peer-link/broken'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'runtime': runtime,
            'reason': reason,
            'auto_recover': autoRecover,
          }),
        )
        .timeout(_requestTimeout);
    return _decodeJsonResponse(response, 'reportPeerLinkBroken');
  }

  Map<String, dynamic> _decodeJsonResponse(http.Response response, String operation) {
    final body = response.body.isEmpty ? '{}' : response.body;
    final decoded = jsonDecode(body) as Map<String, dynamic>;
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw StateError('$operation failed: status=${response.statusCode}, body=$decoded');
    }
    return decoded;
  }
}
