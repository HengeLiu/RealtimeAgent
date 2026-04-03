import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'ws_service.dart';

class ApiService {
  String normalizeBase(String input) => WsService.normalizeHttpBase(input);

  Uri healthUri(String baseUrl) => Uri.parse('${normalizeBase(baseUrl)}/health');

  Uri latestFrameUri(String baseUrl, {int? ts}) {
    final value = ts ?? DateTime.now().millisecondsSinceEpoch;
    return Uri.parse('${normalizeBase(baseUrl)}/latest-frame?ts=$value');
  }

  Future<Map<String, dynamic>> fetchHealth(String baseUrl) async {
    final response = await http.get(healthUri(baseUrl));
    if (response.statusCode != 200) {
      throw HttpException('Health check failed: ${response.statusCode}');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> uploadImage({
    required String baseUrl,
    required File file,
    String note = '',
  }) async {
    final uri = Uri.parse('${normalizeBase(baseUrl)}/upload/image');
    final request = http.MultipartRequest('POST', uri)
      ..fields['note'] = note
      ..files.add(await http.MultipartFile.fromPath('file', file.path));

    final streamed = await request.send();
    final response = await http.Response.fromStream(streamed);
    if (response.statusCode != 200) {
      throw HttpException('Upload failed: ${response.statusCode}');
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }
}
