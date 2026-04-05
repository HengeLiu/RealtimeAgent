import '../models/detector_backend_models.dart';
import 'detector_backends/coreml_detector_backend.dart';
import 'detector_backends/detector_backend.dart';
import 'detector_backends/heuristic_detector_backend.dart';
import 'detector_backends/onnx_detector_backend.dart';
import 'detector_backends/tflite_detector_backend.dart';

class DetectorBackendRegistry {
  DetectorBackendRegistry()
      : availableConfigs = const [
          DetectorBackendConfig(
            type: DetectorBackendType.heuristic,
            displayName: '启发式检测',
            enabled: true,
          ),
          DetectorBackendConfig(
            type: DetectorBackendType.coreml,
            displayName: 'CoreML 后端',
            modelAssetPath: 'assets/models/find_object.mlmodelc',
            labelsAssetPath: 'assets/models/find_object_labels.txt',
            inputWidth: 640,
            inputHeight: 640,
            scoreThreshold: 0.25,
            iouThreshold: 0.45,
            enabled: false,
          ),
          DetectorBackendConfig(
            type: DetectorBackendType.tflite,
            displayName: 'TFLite 后端',
            modelAssetPath: 'assets/models/find_object.tflite',
            labelsAssetPath: 'assets/models/find_object_labels.txt',
            inputWidth: 640,
            inputHeight: 640,
            scoreThreshold: 0.25,
            iouThreshold: 0.45,
            enabled: false,
          ),
          DetectorBackendConfig(
            type: DetectorBackendType.onnxRuntime,
            displayName: 'ONNX Runtime Mobile 后端',
            modelAssetPath: 'assets/models/find_object.onnx',
            labelsAssetPath: 'assets/models/find_object_labels.txt',
            inputWidth: 640,
            inputHeight: 640,
            scoreThreshold: 0.25,
            iouThreshold: 0.45,
            enabled: false,
          ),
        ];

  final List<DetectorBackendConfig> availableConfigs;

  DetectorBackend create(DetectorBackendType type) {
    final config = availableConfigs.firstWhere((item) => item.type == type);
    switch (type) {
      case DetectorBackendType.heuristic:
        return const HeuristicDetectorBackend();
      case DetectorBackendType.coreml:
        return CoreMlDetectorBackend(config: config);
      case DetectorBackendType.tflite:
        return TfliteDetectorBackend(config: config);
      case DetectorBackendType.onnxRuntime:
        return OnnxDetectorBackend(config: config);
    }
  }
}
