abstract class TaskBase {
  String get taskType;

  Future<void> prepare();
  Future<Map<String, dynamic>> run();
  Future<void> cancel();
}
