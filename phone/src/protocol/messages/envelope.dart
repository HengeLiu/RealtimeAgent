class Endpoint {
  const Endpoint({required this.deviceId, required this.module});

  final String deviceId;
  final String module;

  Map<String, dynamic> toJson() {
    return {'device_id': deviceId, 'module': module};
  }
}

class Envelope {
  const Envelope({
    required this.messageId,
    required this.traceId,
    required this.messageType,
    required this.messageName,
    required this.protocolVersion,
    required this.source,
    required this.target,
    required this.timestamp,
    required this.payload,
    this.requiresAck = false,
  });

  final String messageId;
  final String traceId;
  final String messageType;
  final String messageName;
  final String protocolVersion;
  final Endpoint source;
  final Endpoint target;
  final String timestamp;
  final Map<String, dynamic> payload;
  final bool requiresAck;

  Map<String, dynamic> toJson() {
    return {
      'message_id': messageId,
      'trace_id': traceId,
      'message_type': messageType,
      'message_name': messageName,
      'protocol_version': protocolVersion,
      'source': source.toJson(),
      'target': target.toJson(),
      'timestamp': timestamp,
      'payload': payload,
      'requires_ack': requiresAck,
    };
  }
}
