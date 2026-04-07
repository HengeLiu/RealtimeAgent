class TaskStateMachine {
  static const Map<String, Set<String>> transitions = {
    'created': {'queued'},
    'queued': {'preparing'},
    'preparing': {'running'},
    'running': {'waiting_input', 'paused', 'completed', 'failed', 'cancelled', 'timed_out'},
    'waiting_input': {'running'},
    'paused': {'running', 'cancelled'},
    'completed': <String>{},
    'failed': <String>{},
    'cancelled': <String>{},
    'timed_out': <String>{},
  };

  bool canTransition(String current, String next) {
    return transitions[current]?.contains(next) ?? false;
  }
}
