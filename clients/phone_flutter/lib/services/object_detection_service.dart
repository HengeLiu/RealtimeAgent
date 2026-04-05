import 'dart:math' as math;
import 'dart:typed_data';

import 'package:image/image.dart' as img;

import '../models/detection_models.dart';

class ObjectDetectionService {
  const ObjectDetectionService();

  FindObjectFrameAnalysis analyzeJpegFrame({
    required Uint8List jpegBytes,
    required String targetName,
  }) {
    final decoded = img.decodeJpg(jpegBytes);
    if (decoded == null) {
      throw StateError('无法解码 JPEG 帧');
    }

    final candidates = _extractCandidates(decoded, targetName);
    final primary = candidates.isEmpty ? null : candidates.first;

    return FindObjectFrameAnalysis(
      frameWidth: decoded.width,
      frameHeight: decoded.height,
      targetName: targetName,
      found: primary != null,
      candidateCount: candidates.length,
      source: 'flutter_phone_raw_frame',
      objectObservation: primary,
    );
  }

  List<ObjectObservation> _extractCandidates(img.Image image, String targetName) {
    final width = image.width;
    final height = image.height;
    final visited = List<bool>.filled(width * height, false);
    final List<ObjectObservation> candidates = [];

    bool isForeground(int x, int y) {
      final pixel = image.getPixel(x, y);
      final r = pixel.r.toInt();
      final g = pixel.g.toInt();
      final b = pixel.b.toInt();
      final luminance = ((r * 299) + (g * 587) + (b * 114)) / 1000.0;
      final spread = [r, g, b]..sort();
      final contrast = spread.last - spread.first;
      return luminance >= 180 || contrast >= 55;
    }

    int indexOf(int x, int y) => y * width + x;

    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        final startIndex = indexOf(x, y);
        if (visited[startIndex] || !isForeground(x, y)) {
          visited[startIndex] = true;
          continue;
        }

        final queue = <List<int>>[
          [x, y]
        ];
        visited[startIndex] = true;
        var minX = x;
        var maxX = x;
        var minY = y;
        var maxY = y;
        var area = 0;

        while (queue.isNotEmpty) {
          final point = queue.removeLast();
          final px = point[0];
          final py = point[1];
          area += 1;
          minX = math.min(minX, px);
          maxX = math.max(maxX, px);
          minY = math.min(minY, py);
          maxY = math.max(maxY, py);

          for (final offset in const [
            [1, 0],
            [-1, 0],
            [0, 1],
            [0, -1],
          ]) {
            final nx = px + offset[0];
            final ny = py + offset[1];
            if (nx < 0 || ny < 0 || nx >= width || ny >= height) {
              continue;
            }
            final nextIndex = indexOf(nx, ny);
            if (visited[nextIndex]) {
              continue;
            }
            visited[nextIndex] = true;
            if (isForeground(nx, ny)) {
              queue.add([nx, ny]);
            }
          }
        }

        if (area < (width * height * 0.015)) {
          continue;
        }

        final boxWidth = (maxX - minX + 1).toDouble();
        final boxHeight = (maxY - minY + 1).toDouble();
        final rectArea = boxWidth * boxHeight;
        final fillRatio = rectArea <= 0 ? 0.0 : area / rectArea;
        final aspectRatio = math.max(boxWidth, boxHeight) / math.max(1.0, math.min(boxWidth, boxHeight));
        final phoneShapeBonus = _scorePhoneShape(targetName, aspectRatio);
        final score = math.min(
          0.99,
          math.max(
            0.1,
            0.35 +
                math.min(0.35, area / math.max(width * height * 0.25, 1.0)) +
                math.min(0.2, fillRatio * 0.2) +
                phoneShapeBonus,
          ),
        );

        final centerX = minX + (boxWidth / 2.0);
        final centerY = minY + (boxHeight / 2.0);
        candidates.add(
          ObjectObservation(
            centerX: centerX,
            centerY: centerY,
            area: area.toDouble(),
            score: score,
            position: _getCenterPosition(centerX, centerY, width, height),
            polygon: [
              [minX.toDouble(), minY.toDouble()],
              [maxX.toDouble(), minY.toDouble()],
              [maxX.toDouble(), maxY.toDouble()],
              [minX.toDouble(), maxY.toDouble()],
            ],
          ),
        );
      }
    }

    candidates.sort((a, b) {
      final areaCompare = b.area.compareTo(a.area);
      if (areaCompare != 0) {
        return areaCompare;
      }
      return b.score.compareTo(a.score);
    });
    return candidates;
  }

  double _scorePhoneShape(String targetName, double aspectRatio) {
    final normalized = targetName.toLowerCase().replaceAll(RegExp(r'\s+'), '');
    final isPhoneTarget = normalized.contains('手机') ||
        normalized.contains('phone') ||
        normalized.contains('iphone') ||
        normalized.contains('android');
    if (!isPhoneTarget) {
      return 0.0;
    }
    if (aspectRatio >= 1.4 && aspectRatio <= 2.6) {
      return 0.18;
    }
    if (aspectRatio >= 1.2 && aspectRatio <= 3.0) {
      return 0.08;
    }
    return 0.0;
  }

  String _getCenterPosition(double centerX, double centerY, int width, int height) {
    final frameCenterX = width / 2.0;
    final frameCenterY = height / 2.0;
    final threshold = math.max(20.0, math.min(width, height) * 0.08);
    final dx = centerX - frameCenterX;
    final dy = centerY - frameCenterY;

    String? horizontal;
    String? vertical;
    if (dx.abs() > threshold) {
      horizontal = dx > 0 ? 'right' : 'left';
    }
    if (dy.abs() > threshold) {
      vertical = dy > 0 ? 'down' : 'up';
    }
    if (horizontal != null && vertical != null) {
      return '${vertical}_$horizontal';
    }
    return horizontal ?? vertical ?? 'center';
  }
}
