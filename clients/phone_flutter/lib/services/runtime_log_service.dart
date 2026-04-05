import 'dart:io';

import 'package:path_provider/path_provider.dart';

class RuntimeLogService {
  RuntimeLogService._({
    required this.logFile,
  });

  final File logFile;

  String get logFilePath => logFile.path;

  static Future<RuntimeLogService> create({
    required String deviceId,
  }) async {
    final documentsDir = await getApplicationDocumentsDirectory();
    final logsDir = Directory('${documentsDir.path}/logs');
    if (!await logsDir.exists()) {
      await logsDir.create(recursive: true);
    }
    final sanitizedDeviceId = deviceId.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');
    final logFile = File('${logsDir.path}/$sanitizedDeviceId-runtime.log');
    if (!await logFile.exists()) {
      await logFile.create(recursive: true);
    }
    return RuntimeLogService._(logFile: logFile);
  }

  Future<void> append(String line) async {
    await logFile.writeAsString('$line\n', mode: FileMode.append, flush: true);
  }
}
