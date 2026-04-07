class TaskModel {
  TaskModel({required this.taskId, required this.taskType, required this.status, this.input = const {}});

  final String taskId;
  final String taskType;
  String status;
  final Map<String, dynamic> input;
}
