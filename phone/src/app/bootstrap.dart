import '../api/gateway/server_gateway.dart';
import '../api/session/connection_manager.dart';
import '../backend_task_core/manager/task_manager.dart';
import '../infra/config.dart';
import '../infra/logging.dart';
import '../skill/builtin/asr_skill.dart';
import '../skill/builtin/local_yolo_skill.dart';
import '../skill/registry/skill_registry.dart';

class PhoneAppContainer {
  PhoneAppContainer({
    required this.gateway,
    required this.connectionManager,
    required this.taskManager,
    required this.skillRegistry,
    required this.settings,
    required this.deviceId,
  });

  final ServerGateway gateway;
  final ConnectionManager connectionManager;
  final LocalTaskManager taskManager;
  final SkillRegistry skillRegistry;
  final PhoneSettings settings;
  final String deviceId;

  Future<void> start({required Map<String, dynamic> auth}) async {
    connectionManager.openSession('conn_server_primary');
    await gateway.connect();
    connectionManager.bindDevice(
      connectionId: 'conn_server_primary',
      deviceId: deviceId,
      module: 'phone-api',
    );
    await gateway.registerDevice(
      deviceId: deviceId,
      deviceType: 'phone',
      protocolVersion: settings.protocolVersion,
      capabilities: const ['video_stream_receive', 'local_yolo', 'asr'],
      auth: auth,
    );
    connectionManager.markOnline(deviceId);
    logEvent('phone.bootstrap.started', fields: {'device_id': deviceId});
  }

  Future<void> tickHeartbeat({
    int? batteryLevel,
    List<String> activeTaskIds = const [],
    String? connectionQuality,
  }) async {
    await gateway.sendHeartbeat(
      deviceId: deviceId,
      protocolVersion: settings.protocolVersion,
      batteryLevel: batteryLevel,
      activeTaskIds: activeTaskIds,
      connectionQuality: connectionQuality,
    );
  }
}

PhoneAppContainer bootstrapPhoneApp(
  void Function(String payload) onSend, {
  String deviceId = 'dev_phone_001',
  PhoneSettings settings = const PhoneSettings(),
}) {
  return PhoneAppContainer(
    gateway: ServerGateway(
      onSend: onSend,
      onStateChanged: (state) => logEvent('phone.gateway.state', fields: {'state': state.name}),
    ),
    connectionManager: ConnectionManager(),
    taskManager: LocalTaskManager(),
    skillRegistry: SkillRegistry()
      ..register(LocalYoloSkill())
      ..register(AsrSkill()),
    settings: settings,
    deviceId: deviceId,
  );
}
