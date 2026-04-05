import 'dart:typed_data';

import '../../models/detection_models.dart';
import '../../models/detector_backend_models.dart';
import '../object_detection_service.dart';
import 'detector_backend.dart';

class HeuristicDetectorBackend extends DetectorBackend {
  const HeuristicDetectorBackend({ObjectDetectionService? detectionService})
      : _detectionService = detectionService ?? const ObjectDetectionService();

  final ObjectDetectionService _detectionService;

  @override
  DetectorBackendType get backendType => DetectorBackendType.heuristic;

  @override
  String get displayName => '启发式检测';

  @override
  Future<FindObjectFrameAnalysis> analyzeJpegFrame({
    required Uint8List jpegBytes,
    required String targetName,
  }) async {
    return _detectionService.analyzeJpegFrame(
      jpegBytes: jpegBytes,
      targetName: targetName,
    );
  }
}
