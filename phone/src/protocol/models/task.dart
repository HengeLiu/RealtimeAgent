class TaskModel {
  TaskModel({
    required this.taskId,
    required this.taskType,
    required this.status,
    this.source = 'agent',
    this.priority = 'normal',
    this.input = const {},
    this.context = const {},
    this.result = const {},
    this.createdAt,
    this.updatedAt,
  });

  final String taskId;
  final String taskType;
  String status;
  final String source;
  final String priority;
  final Map<String, dynamic> input;
  final Map<String, dynamic> context;
  final Map<String, dynamic> result;
  final String? createdAt;
  final String? updatedAt;

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'task_id': taskId,
      'task_type': taskType,
      'status': status,
      'source': source,
      'priority': priority,
      'input': input,
      'context': context,
      'result': result,
      'created_at': createdAt,
      'updated_at': updatedAt,
    };
  }
}
