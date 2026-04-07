import '../../protocol/messages/envelope.dart';

abstract class ServerMessageHandler {
  Future<void> handle(Envelope envelope);
}
