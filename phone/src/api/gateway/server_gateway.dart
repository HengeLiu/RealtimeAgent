import 'dart:convert';

import '../../protocol/messages/envelope.dart';

class ServerGateway {
  ServerGateway({required this.onSend});

  final void Function(String payload) onSend;

  void send(Envelope envelope) {
    onSend(jsonEncode(envelope.toJson()));
  }
}
