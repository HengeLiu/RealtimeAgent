class Endpoint {
  const Endpoint({required this.deviceId, required this.module});

  final String deviceId;
  final String module;

  Map<String, dynamic> toJson() {
    return {'device_id': deviceId, 'module': module};
  }

  factory Endpoint.fromJson(Map<String, dynamic> json) {
    return Endpoint(
      deviceId: json['device_id'] as String,
      module: json['module'] as String,
    );
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
    this.correlationId,
    this.taskId,
    this.sessionId,
    this.priority = 'normal',
    this.requiresAck = false,
    this.metadata = const {},
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
  final String? correlationId;
  final String? taskId;
  final String? sessionId;
  final String priority;
  final bool requiresAck;
  final Map<String, dynamic> metadata;

  Map<String, dynamic> toJson() {
    final data = <String, dynamic>{
      'message_id': messageId,
      'trace_id': traceId,
      'message_type': messageType,
      'message_name': messageName,
      'protocol_version': protocolVersion,
      'source': source.toJson(),
      'target': target.toJson(),
      'timestamp': timestamp,
      'payload': payload,
      'priority': priority,
      'requires_ack': requiresAck,
      'metadata': metadata,
    };
    if (correlationId != null) data['correlation_id'] = correlationId;
    if (taskId != null) data['task_id'] = taskId;
    if (sessionId != null) data['session_id'] = sessionId;
    return data;
  }

  factory Envelope.fromJson(Map<String, dynamic> json) {
    return Envelope(
      messageId: json['message_id'] as String,
      traceId: json['trace_id'] as String,
      messageType: json['message_type'] as String,
      messageName: json['message_name'] as String,
      protocolVersion: json['protocol_version'] as String,
      source: Endpoint.fromJson(json['source'] as Map<String, dynamic>),
      target: Endpoint.fromJson(json['target'] as Map<String, dynamic>),
      timestamp: json['timestamp'] as String,
      payload: Map<String, dynamic>.from(json['payload'] as Map? ?? const {}),
      correlationId: json['correlation_id'] as String?,
      taskId: json['task_id'] as String?,
      sessionId: json['session_id'] as String?,
      priority: (json['priority'] as String?) ?? 'normal',
      requiresAck: (json['requires_ack'] as bool?) ?? false,
      metadata: Map<String, dynamic>.from(json['metadata'] as Map? ?? const {}),
    );
  }
}
