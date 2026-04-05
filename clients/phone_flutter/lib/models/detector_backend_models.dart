enum DetectorBackendType {
  heuristic,
  coreml,
  tflite,
  onnxRuntime,
}

class DetectorBackendConfig {
  const DetectorBackendConfig({
    required this.type,
    required this.displayName,
    this.modelAssetPath,
    this.labelsAssetPath,
    this.inputWidth,
    this.inputHeight,
    this.scoreThreshold,
    this.iouThreshold,
    this.enabled = true,
  });

  final DetectorBackendType type;
  final String displayName;
  final String? modelAssetPath;
  final String? labelsAssetPath;
  final int? inputWidth;
  final int? inputHeight;
  final double? scoreThreshold;
  final double? iouThreshold;
  final bool enabled;

  Map<String, dynamic> toJson() => {
        'type': type.name,
        'display_name': displayName,
        'model_asset_path': modelAssetPath,
        'labels_asset_path': labelsAssetPath,
        'input_width': inputWidth,
        'input_height': inputHeight,
        'score_threshold': scoreThreshold,
        'iou_threshold': iouThreshold,
        'enabled': enabled,
      };
}
