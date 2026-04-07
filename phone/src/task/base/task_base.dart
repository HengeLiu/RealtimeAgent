abstract class TaskBase {
  String get taskType;

  void validateInput(Map<String, dynamic> input);
  Future<void> prepare();
  Future<Map<String, dynamic>> run();
  Future<void> pause();
  Future<void> resume();
  Future<void> cancel();
  Map<String, dynamic> buildResult();
}
