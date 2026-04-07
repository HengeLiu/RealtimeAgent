import 'dart:convert';

import '../messages/envelope.dart';

class JsonMessageCodec {
  const JsonMessageCodec();

  String encode(Envelope envelope) {
    return jsonEncode(envelope.toJson());
  }

  Envelope decode(String rawPayload) {
    final dynamic parsed = jsonDecode(rawPayload);
    if (parsed is! Map<String, dynamic>) {
      throw const FormatException('invalid envelope payload');
    }
    return Envelope.fromJson(parsed);
  }
}

