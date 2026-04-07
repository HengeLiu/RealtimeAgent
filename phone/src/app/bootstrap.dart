import '../api/gateway/server_gateway.dart';
import '../backend_task_core/manager/task_manager.dart';

class PhoneAppContainer {
  PhoneAppContainer({required this.gateway, required this.taskManager});

  final ServerGateway gateway;
  final LocalTaskManager taskManager;
}

PhoneAppContainer bootstrapPhoneApp(void Function(String payload) onSend) {
  return PhoneAppContainer(
    gateway: ServerGateway(onSend: onSend),
    taskManager: LocalTaskManager(),
  );
}
