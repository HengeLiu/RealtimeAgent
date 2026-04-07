void logEvent(String eventName, {Map<String, dynamic> fields = const {}}) {
  // 第一阶段最小日志骨架
  // ignore: avoid_print
  print({'event': eventName, ...fields});
}
