import 'dart:typed_data';

import '../../models/detection_models.dart';
import '../../models/detector_backend_models.dart';

abstract class DetectorBackend {
  const DetectorBackend();

  DetectorBackendType get backendType;

  String get displayName;

  Future<FindObjectFrameAnalysis> analyzeJpegFrame({
    required Uint8List jpegBytes,
    required String targetName,
  });
}
