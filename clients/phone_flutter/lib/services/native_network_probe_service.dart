import 'package:flutter/services.dart';

class NativeNetworkProbeService {
  static const MethodChannel _channel = MethodChannel('nextgen.native_network_probe');

  Future<Map<String, dynamic>> probe(String url) async {
    final result = await _channel.invokeMethod<Map<Object?, Object?>>(
      'probeServer',
      <String, dynamic>{'url': url},
    );
    return (result ?? const <Object?, Object?>{}).map(
      (key, value) => MapEntry(key.toString(), value),
    );
  }
}
