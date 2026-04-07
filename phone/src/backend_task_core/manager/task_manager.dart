class LocalTask {
  LocalTask({required this.taskId, required this.taskType, required this.status});

  final String taskId;
  final String taskType;
  String status;
}

class LocalTaskManager {
  final Map<String, LocalTask> _tasks = {};

  void register(LocalTask task) {
    _tasks[task.taskId] = task;
  }

  LocalTask? get(String taskId) {
    return _tasks[taskId];
  }
}
