enum TaskStatus {
  created('created'),
  queued('queued'),
  preparing('preparing'),
  running('running'),
  waitingInput('waiting_input'),
  paused('paused'),
  completed('completed'),
  cancelled('cancelled'),
  failed('failed'),
  timedOut('timed_out');

  const TaskStatus(this.value);
  final String value;
}

