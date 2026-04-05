import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/runtime_models.dart';

class ControlApiService {
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
    final response = await http.post(
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
    );
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> heartbeat({
    required String serverBaseUrl,
    required String deviceId,
    required ControlEndpoint endpoint,
  }) async {
    final response = await http.post(
      Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/devices/heartbeat'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'device_id': deviceId,
        'status': 'ready',
        'endpoint': endpoint.toJson(),
      }),
    );
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> fetchSnapshot(String serverBaseUrl) async {
    final response = await http.get(Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/snapshot'));
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> reportTaskState({
    required String serverBaseUrl,
    required String sessionId,
    required String status,
    required String phase,
    required Map<String, dynamic> summary,
    Map<String, dynamic>? result,
  }) async {
    final response = await http.post(
      Uri.parse('${normalizeBaseUrl(serverBaseUrl)}/tasks/$sessionId/state'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'runtime': 'phone',
        'status': status,
        'phase': phase,
        'summary': summary,
        if (result != null) 'result': result,
      }),
    );
    return jsonDecode(response.body) as Map<String, dynamic>;
  }
}
