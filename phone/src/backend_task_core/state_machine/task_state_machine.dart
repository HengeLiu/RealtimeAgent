class TaskStateMachine {
  static const Map<String, Set<String>> transitions = {
    'created': {'queued'},
    'queued': {'running'},
    'running': {'paused', 'completed', 'failed', 'cancelled'},
    'paused': {'running', 'cancelled'},
  };

  bool canTransition(String current, String next) {
    return transitions[current]?.contains(next) ?? false;
  }
}
