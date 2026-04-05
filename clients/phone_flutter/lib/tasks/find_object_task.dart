import '../models/detection_models.dart';

class FindObjectTask {
  FindObjectTask({required this.targetName});

  String targetName;
  String phase = 'waiting_stream';

  GuidanceHint buildHint({
    required String sessionId,
    required FindObjectFrameAnalysis analysis,
  }) {
    if (analysis.found && analysis.objectObservation != null) {
      phase = 'guiding';
      final position = analysis.objectObservation!.position;
      String text;
      switch (position) {
        case 'center':
          text = '已发现$targetName，目标基本居中，请保持当前方向';
          break;
        case 'left':
          text = '已发现$targetName，请向左';
          break;
        case 'right':
          text = '已发现$targetName，请向右';
          break;
        case 'up':
          text = '已发现$targetName，请向上';
          break;
        case 'down':
          text = '已发现$targetName，请向下';
          break;
        default:
          text = '已发现$targetName，位置：$position';
      }
      return GuidanceHint(
        sessionId: sessionId,
        text: text,
        priority: 'high',
      );
    }

    phase = 'scanning';
    return GuidanceHint(
      sessionId: sessionId,
      text: '尚未检测到$targetName，继续扫描',
      priority: 'high',
    );
  }
}
