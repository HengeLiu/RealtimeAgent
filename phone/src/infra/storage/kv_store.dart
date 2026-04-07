class KvStore {
  final Map<String, dynamic> _memory = <String, dynamic>{};

  void set(String key, dynamic value) {
    _memory[key] = value;
  }

  T? get<T>(String key) {
    final value = _memory[key];
    if (value is T) return value;
    return null;
  }

  void remove(String key) {
    _memory.remove(key);
  }
}

