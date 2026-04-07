class SessionModel {
  const SessionModel({
    required this.sessionId,
    required this.sessionType,
    required this.participants,
    required this.status,
    required this.startedAt,
    this.endedAt,
    this.context = const {},
  });

  final String sessionId;
  final String sessionType;
  final List<String> participants;
  final String status;
  final String startedAt;
  final String? endedAt;
  final Map<String, dynamic> context;

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'session_id': sessionId,
      'session_type': sessionType,
      'participants': participants,
      'status': status,
      'started_at': startedAt,
      'ended_at': endedAt,
      'context': context,
    };
  }
}

