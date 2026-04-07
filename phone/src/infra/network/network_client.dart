class NetworkClient {
  const NetworkClient();

  Future<Map<String, dynamic>> postJson(
    String url, {
    required Map<String, dynamic> body,
    Map<String, String> headers = const <String, String>{},
  }) async {
    return <String, dynamic>{
      'url': url,
      'headers': headers,
      'body': body,
      'status': 'stubbed',
    };
  }
}

