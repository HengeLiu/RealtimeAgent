class MediaModel {
  const MediaModel({
    required this.mediaId,
    required this.mediaType,
    this.codec,
    this.format,
    this.sampleRate,
    this.channels,
    this.width,
    this.height,
    this.durationMs,
    this.frameIndex,
    this.chunkIndex,
    this.isFinal,
    this.capturedAt,
    this.payloadRef,
    this.metadata = const {},
  });

  final String mediaId;
  final String mediaType;
  final String? codec;
  final String? format;
  final int? sampleRate;
  final int? channels;
  final int? width;
  final int? height;
  final int? durationMs;
  final int? frameIndex;
  final int? chunkIndex;
  final bool? isFinal;
  final String? capturedAt;
  final String? payloadRef;
  final Map<String, dynamic> metadata;

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'media_id': mediaId,
      'media_type': mediaType,
      'codec': codec,
      'format': format,
      'sample_rate': sampleRate,
      'channels': channels,
      'width': width,
      'height': height,
      'duration_ms': durationMs,
      'frame_index': frameIndex,
      'chunk_index': chunkIndex,
      'is_final': isFinal,
      'captured_at': capturedAt,
      'payload_ref': payloadRef,
      'metadata': metadata,
    };
  }
}

