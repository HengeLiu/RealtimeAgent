class TaskRegistry {
  final Set<String> _taskTypes = {};

  void register(String taskType) {
    _taskTypes.add(taskType);
  }

  bool contains(String taskType) {
    return _taskTypes.contains(taskType);
  }
}
