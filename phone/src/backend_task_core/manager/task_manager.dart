import '../state_machine/task_state_machine.dart';

class LocalTask {
  LocalTask({required this.taskId, required this.taskType, required this.status});

  final String taskId;
  final String taskType;
  String status;
}

class LocalTaskManager {
  LocalTaskManager({TaskStateMachine? stateMachine}) : _stateMachine = stateMachine ?? TaskStateMachine();

  final Map<String, LocalTask> _tasks = {};
  final TaskStateMachine _stateMachine;

  LocalTask create(String taskId, String taskType) {
    final task = LocalTask(taskId: taskId, taskType: taskType, status: 'created');
    _tasks[taskId] = task;
    updateStatus(taskId, 'queued');
    return task;
  }

  void register(LocalTask task) {
    _tasks[task.taskId] = task;
  }

  LocalTask? get(String taskId) {
    return _tasks[taskId];
  }

  List<LocalTask> list() {
    return _tasks.values.toList(growable: false);
  }

  void updateStatus(String taskId, String status) {
    final task = _tasks[taskId];
    if (task == null) return;
    if (!_stateMachine.canTransition(task.status, status)) {
      throw StateError('invalid transition: ${task.status} -> $status');
    }
    task.status = status;
  }

  void start(String taskId) {
    final task = _tasks[taskId];
    if (task == null) return;
    if (task.status == 'queued') updateStatus(taskId, 'preparing');
    updateStatus(taskId, 'running');
  }

  void complete(String taskId) {
    updateStatus(taskId, 'completed');
  }

  void cancel(String taskId) {
    updateStatus(taskId, 'cancelled');
  }
}
