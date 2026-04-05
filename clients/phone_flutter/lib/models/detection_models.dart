class ObjectObservation {
  const ObjectObservation({
    required this.centerX,
    required this.centerY,
    required this.area,
    required this.score,
    required this.position,
    required this.polygon,
  });

  final double centerX;
  final double centerY;
  final double area;
  final double score;
  final String position;
  final List<List<double>> polygon;

  Map<String, dynamic> toJson() => {
        'center_x': centerX,
        'center_y': centerY,
        'area': area,
        'score': score,
        'position': position,
        'polygon': polygon,
      };
}

class FindObjectFrameAnalysis {
  const FindObjectFrameAnalysis({
    required this.frameWidth,
    required this.frameHeight,
    required this.targetName,
    required this.found,
    required this.candidateCount,
    required this.source,
    this.objectObservation,
  });

  final int frameWidth;
  final int frameHeight;
  final String targetName;
  final bool found;
  final int candidateCount;
  final String source;
  final ObjectObservation? objectObservation;

  Map<String, dynamic> toJson() => {
        'frame_width': frameWidth,
        'frame_height': frameHeight,
        'target_name': targetName,
        'found': found,
        'candidate_count': candidateCount,
        'source': source,
        'object_observation': objectObservation?.toJson(),
        'hand_observation': null,
      };
}

class GuidanceHint {
  const GuidanceHint({
    required this.sessionId,
    required this.text,
    required this.priority,
  });

  final String sessionId;
  final String text;
  final String priority;

  Map<String, dynamic> toJson() => {
        'session_id': sessionId,
        'hint_type': 'guidance',
        'text': text,
        'priority': priority,
      };
}
