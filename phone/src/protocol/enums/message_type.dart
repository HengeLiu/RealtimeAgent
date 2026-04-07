enum MessageType {
  command('command'),
  event('event'),
  state('state'),
  stream('stream'),
  ack('ack'),
  error('error');

  const MessageType(this.value);
  final String value;
}

