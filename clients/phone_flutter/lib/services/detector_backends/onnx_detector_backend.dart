import 'dart:typed_data';

import '../../models/detection_models.dart';
import '../../models/detector_backend_models.dart';
import 'detector_backend.dart';

class OnnxDetectorBackend extends DetectorBackend {
  const OnnxDetectorBackend({required this.config});

  final DetectorBackendConfig config;

  @override
  DetectorBackendType get backendType => DetectorBackendType.onnxRuntime;

  @override
  String get displayName => config.displayName;

  @override
  Future<FindObjectFrameAnalysis> analyzeJpegFrame({
    required Uint8List jpegBytes,
    required String targetName,
  }) {
    throw UnimplementedError('ONNX Runtime Mobile 检测后端尚未接入真实模型。');
  }
}
