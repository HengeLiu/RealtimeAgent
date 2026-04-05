import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../models/detection_models.dart';
import '../models/detector_backend_models.dart';
import '../models/runtime_models.dart';
import '../services/control_api_service.dart';
import '../services/detector_backend_registry.dart';
import '../services/local_control_server.dart';
import '../services/native_network_probe_service.dart';
import '../tasks/find_object_task.dart';

class PhoneRuntimeController {
  PhoneRuntimeController({
    required this.deviceId,
    required this.serverBaseUrl,
    required this.listenPort,
  });

  final String deviceId;
  final String serverBaseUrl;
  final int listenPort;

  final ControlApiService _api = ControlApiService();
  final DetectorBackendRegistry _detectorRegistry = DetectorBackendRegistry();
  final NativeNetworkProbeService _nativeProbeService = NativeNetworkProbeService();
  final List<String> _logs = <String>[];
  final Map<String, PeerSessionState> _peerSessions = <String, PeerSessionState>{};
  final Map<String, FindObjectTask> _findObjectTasks = <String, FindObjectTask>{};

  LocalControlServer? _server;
  Timer? _heartbeatTimer;
  DetectorBackendType _selectedBackend = DetectorBackendType.heuristic;

  List<String> get logs => List<String>.unmodifiable(_logs);
  List<PeerSessionState> get peerSessions => _peerSessions.values.toList();
  String? get localHost => _server?.localHost;
  DetectorBackendType get selectedBackend => _selectedBackend;
  List<DetectorBackendConfig> get detectorConfigs => _detectorRegistry.availableConfigs;

  Future<void> start() async {
    try {
      _appendLog('runtime_starting: server=$serverBaseUrl listenPort=$listenPort');
      final interfaceSummary = await collectInterfaceSummary();
      _appendLog('network_interfaces: ${jsonEncode(interfaceSummary)}');
      final probeSummary = await probeServerConnectivity();
      _appendLog('server_probe: ${jsonEncode(probeSummary)}');
      final nativeProbeSummary = await probeServerNativeConnectivity();
      _appendLog('native_server_probe: ${jsonEncode(nativeProbeSummary)}');
      _server = LocalControlServer(
        port: listenPort,
        deviceId: deviceId,
        onPreparePeerLink: _handlePreparePeerLink,
        onStopPeerLink: _handleStopPeerLink,
        onPeerFrame: _handlePeerFrame,
      );
      await _server!.start();
      _appendLog('local_control_server_started: host=${_server?.localHost} port=$listenPort');
      await register();
      _heartbeatTimer = Timer.periodic(const Duration(seconds: 2), (_) {
        unawaited(heartbeat());
      });
      _appendLog('runtime_started');
    } catch (error) {
      _appendLog('runtime_start_failed: $error');
      await stop();
      rethrow;
    }
  }

  Future<void> stop() async {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    await _server?.stop();
    _server = null;
    _appendLog('runtime_stopped');
  }

  void selectDetectorBackend(DetectorBackendType backendType) {
    _selectedBackend = backendType;
    _appendLog('detector_backend_selected: ${backendType.name}');
  }

  Future<void> register() async {
    final endpoint = _buildEndpoint();
    if (endpoint == null) {
      _appendLog('无法注册：本机地址未准备好');
      return;
    }
    final result = await _api.registerDevice(
      serverBaseUrl: serverBaseUrl,
      deviceId: deviceId,
      endpoint: endpoint,
    );
    _appendLog('register: ${jsonEncode(result)}');
  }

  Future<void> heartbeat() async {
    final endpoint = _buildEndpoint();
    if (endpoint == null) {
      return;
    }
    final result = await _api.heartbeat(
      serverBaseUrl: serverBaseUrl,
      deviceId: deviceId,
      endpoint: endpoint,
    );
    _appendLog('heartbeat: ${jsonEncode(result)}');
  }

  Future<Map<String, dynamic>> fetchSnapshot() async {
    final result = await _api.fetchSnapshot(serverBaseUrl);
    _appendLog('snapshot: fetched');
    return result;
  }

  Future<List<Map<String, dynamic>>> collectInterfaceSummary() async {
    final interfaces = await NetworkInterface.list(
      includeLoopback: false,
      type: InternetAddressType.any,
    );
    return interfaces
        .map(
          (interface) => {
            'name': interface.name,
            'addresses': interface.addresses
                .map(
                  (address) => {
                    'address': address.address,
                    'type': address.type.name,
                  },
                )
                .toList(),
          },
        )
        .toList();
  }

  Future<Map<String, dynamic>> probeServerConnectivity() async {
    final normalizedBaseUrl = _api.normalizeBaseUrl(serverBaseUrl);
    final uri = Uri.parse('$normalizedBaseUrl/health');
    final result = <String, dynamic>{
      'target': uri.toString(),
      'host': uri.host,
      'port': uri.port,
    };

    try {
      final socket = await Socket.connect(uri.host, uri.port, timeout: const Duration(seconds: 3));
      result['socket_connect'] = 'ok';
      await socket.close();
    } catch (error) {
      result['socket_connect'] = 'error';
      result['socket_error'] = error.toString();
    }

    final client = HttpClient()..connectionTimeout = const Duration(seconds: 4);
    try {
      final request = await client.getUrl(uri);
      final response = await request.close();
      result['http_status'] = response.statusCode;
      result['http_reason'] = response.reasonPhrase;
      await response.drain();
    } catch (error) {
      result['http_error'] = error.toString();
    } finally {
      client.close(force: true);
    }

    return result;
  }

  Future<Map<String, dynamic>> probeServerNativeConnectivity() async {
    final normalizedBaseUrl = _api.normalizeBaseUrl(serverBaseUrl);
    try {
      return await _nativeProbeService.probe('$normalizedBaseUrl/health');
    } catch (error) {
      return <String, dynamic>{
        'target': '$normalizedBaseUrl/health',
        'native_probe_error': error.toString(),
      };
    }
  }

  Future<Map<String, dynamic>> _handlePreparePeerLink(
    String taskSessionId,
    String peerDeviceId,
    String streamType,
  ) async {
    final endpoint = ControlEndpoint(
      host: _server?.localHost ?? '127.0.0.1',
      port: listenPort,
      scheme: 'ws',
      basePath: '/peer-link/$taskSessionId',
    );
    _peerSessions[taskSessionId] = PeerSessionState(
      sessionId: taskSessionId,
      peerDeviceId: peerDeviceId,
      streamType: streamType,
      status: 'listening',
      listenEndpoint: endpoint.toJson(),
    );
    _findObjectTasks.putIfAbsent(taskSessionId, () => FindObjectTask(targetName: '手机'));
    _appendLog('prepare_peer_link: $taskSessionId');
    unawaited(
      _api.reportTaskState(
        serverBaseUrl: serverBaseUrl,
        sessionId: taskSessionId,
        status: 'starting',
        phase: 'preparing_peer_link',
        summary: <String, dynamic>{'status': 'listening'},
      ),
    );
    return {
      'task_session_id': taskSessionId,
      'runtime': 'phone',
      'status': 'listening',
      'listen_endpoint': endpoint.toJson(),
    };
  }

  Future<Map<String, dynamic>> _handleStopPeerLink(String taskSessionId) async {
    final session = _peerSessions[taskSessionId];
    if (session != null) {
      _peerSessions[taskSessionId] = session.copyWith(status: 'closed');
    }
    _findObjectTasks.remove(taskSessionId);
    _appendLog('stop_peer_link: $taskSessionId');
    unawaited(
      _api.reportTaskState(
        serverBaseUrl: serverBaseUrl,
        sessionId: taskSessionId,
        status: 'completed',
        phase: 'peer_link_closed',
        summary: <String, dynamic>{'status': 'closed'},
      ),
    );
    return {
      'task_session_id': taskSessionId,
      'runtime': 'phone',
      'status': 'closed',
    };
  }

  Future<Map<String, dynamic>> _handlePeerFrame(
    String taskSessionId,
    String path,
    Map<String, dynamic> payload,
  ) async {
    final session = _peerSessions[taskSessionId];
    if (session != null) {
      _peerSessions[taskSessionId] = session.copyWith(status: 'connected');
    }
    unawaited(
      _api.reportTaskState(
        serverBaseUrl: serverBaseUrl,
        sessionId: taskSessionId,
        status: 'running',
        phase: 'stream_connected',
        summary: <String, dynamic>{'status': 'connected'},
      ),
    );
    _appendLog('peer_frame: $taskSessionId $path');
    if (path == '/health') {
      return {
        'ok': true,
        'task_session_id': taskSessionId,
        'runtime': 'phone',
      };
    }
    if (path == '/stream/frame') {
      final targetName = (payload['target_name'] as String?)?.trim().isNotEmpty == true
          ? payload['target_name'] as String
          : (_findObjectTasks[taskSessionId]?.targetName ?? '手机');
      final jpegBase64 = payload['jpeg_base64'] as String? ?? '';
      final frameBytes = base64Decode(jpegBase64);
      final backend = _detectorRegistry.create(_selectedBackend);
      final analysis = await backend.analyzeJpegFrame(
        jpegBytes: Uint8List.fromList(frameBytes),
        targetName: targetName,
      );
      final task = _findObjectTasks.putIfAbsent(
        taskSessionId,
        () => FindObjectTask(targetName: targetName),
      );
      task.targetName = targetName;
      final hint = task.buildHint(sessionId: taskSessionId, analysis: analysis);
      _appendLog(
        'find_object_stream_frame: $taskSessionId backend=${backend.displayName} found=${analysis.found} candidateCount=${analysis.candidateCount} hint=${hint.text}',
      );
      final stateSummary = <String, dynamic>{
        'target_name': targetName,
        'found': analysis.found,
        'position': analysis.objectObservation?.position ?? 'unknown',
        'phase': task.phase,
      };
      unawaited(
        _api.reportTaskState(
          serverBaseUrl: serverBaseUrl,
          sessionId: taskSessionId,
          status: (payload['mark_completed'] == true) ? 'completed' : 'running',
          phase: task.phase,
          summary: stateSummary,
          result: (payload['mark_completed'] == true) ? <String, dynamic>{'target_name': targetName} : null,
        ),
      );
      return {
        'task_session_id': taskSessionId,
        'status': (payload['mark_completed'] == true) ? 'completed' : 'running',
        'frame_index': payload['frame_index'],
        'phase': task.phase,
        'state_summary': stateSummary,
        'hint': hint.toJson(),
        'analysis': analysis.toJson(),
        'backend': _selectedBackend.name,
      };
    }
    if (path == '/find-object/frame-analysis') {
      final targetName = (payload['target_name'] as String?)?.trim().isNotEmpty == true
          ? payload['target_name'] as String
          : '手机';
      final analysisPayload = (payload['analysis'] as Map?)?.cast<String, dynamic>() ?? <String, dynamic>{};
      final objectObservationPayload =
          (analysisPayload['object_observation'] as Map?)?.cast<String, dynamic>();
      final analysis = FindObjectFrameAnalysis(
        frameWidth: analysisPayload['frame_width'] as int? ?? 0,
        frameHeight: analysisPayload['frame_height'] as int? ?? 0,
        targetName: targetName,
        found: analysisPayload['found'] as bool? ?? false,
        candidateCount: analysisPayload['candidate_count'] as int? ?? 0,
        source: analysisPayload['source'] as String? ?? 'peer_analysis',
        objectObservation: objectObservationPayload == null
            ? null
            : ObjectObservation(
                centerX: (objectObservationPayload['center_x'] as num?)?.toDouble() ?? 0,
                centerY: (objectObservationPayload['center_y'] as num?)?.toDouble() ?? 0,
                area: (objectObservationPayload['area'] as num?)?.toDouble() ?? 0,
                score: (objectObservationPayload['score'] as num?)?.toDouble() ?? 0,
                position: objectObservationPayload['position'] as String? ?? 'unknown',
                polygon: ((objectObservationPayload['polygon'] as List?) ?? const [])
                    .map((dynamic item) => ((item as List).map((dynamic v) => (v as num).toDouble()).toList()))
                    .toList(),
              ),
      );
      final task = _findObjectTasks.putIfAbsent(
        taskSessionId,
        () => FindObjectTask(targetName: targetName),
      );
      task.targetName = targetName;
      final hint = task.buildHint(sessionId: taskSessionId, analysis: analysis);
      _appendLog(
        'find_object_frame_analysis: $taskSessionId found=${analysis.found} candidateCount=${analysis.candidateCount} hint=${hint.text}',
      );
      final stateSummary = <String, dynamic>{
        'target_name': targetName,
        'found': analysis.found,
        'position': analysis.objectObservation?.position ?? 'unknown',
        'phase': task.phase,
      };
      unawaited(
        _api.reportTaskState(
          serverBaseUrl: serverBaseUrl,
          sessionId: taskSessionId,
          status: 'running',
          phase: task.phase,
          summary: stateSummary,
        ),
      );
      return {
        'task_session_id': taskSessionId,
        'status': 'ok',
        'phase': task.phase,
        'state_summary': stateSummary,
        'hint': hint.toJson(),
      };
    }
    return {
      'task_session_id': taskSessionId,
      'status': 'ignored',
      'path': path,
    };
  }

  ControlEndpoint? _buildEndpoint() {
    final host = _server?.localHost;
    if (host == null || host.isEmpty) {
      return null;
    }
    return ControlEndpoint(host: host, port: listenPort);
  }

  void _appendLog(String message) {
    final now = DateTime.now().toIso8601String();
    _logs.insert(0, '[$now] $message');
    if (_logs.length > 200) {
      _logs.removeRange(200, _logs.length);
    }
  }
}
