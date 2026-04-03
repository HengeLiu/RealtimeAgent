import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../services/api_service.dart';
import '../services/local_bridge_service.dart';
import '../services/ws_service.dart';

class TestScreen extends StatefulWidget {
  const TestScreen({super.key});

  @override
  State<TestScreen> createState() => _TestScreenState();
}

class _TestScreenState extends State<TestScreen> with WidgetsBindingObserver {
  final _serverController = TextEditingController(text: 'http://139.224.163.108:8000');
  final _messageController = TextEditingController();
  final _picker = ImagePicker();
  final _wsService = WsService();
  final _apiService = ApiService();
  final _localBridge = LocalBridgeService(port: 9100);

  StreamSubscription<Map<String, dynamic>>? _cloudSubscription;
  StreamSubscription<Map<String, dynamic>>? _localSubscription;
  Timer? _endpointRefreshTimer;

  bool _cloudConnecting = false;
  bool _cloudConnected = false;
  bool _uploading = false;
  bool _directConnected = false;
  String _cloudStatus = '未连接';
  String _localStatus = '本地服务未启动';
  String _mode = 'direct_preferred';
  String _sendTarget = 'esp32';
  String? _localHost;
  String? _esp32Ip;
  int _localPort = 9100;
  Uint8List? _latestFrameBytes;
  String? _latestCloudFrameUrl;
  final List<String> _logs = <String>[];
  int _statusRefreshCounter = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _cloudSubscription = _wsService.events.listen(_handleCloudEvent);
    _localSubscription = _localBridge.events.listen(_handleLocalEvent);
    unawaited(_localBridge.start());
    _endpointRefreshTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      unawaited(_refreshLocalEndpoint(requestStatus: !_directConnected));
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _cloudSubscription?.cancel();
    _localSubscription?.cancel();
    _endpointRefreshTimer?.cancel();
    _serverController.dispose();
    _messageController.dispose();
    _wsService.dispose();
    _localBridge.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(_refreshLocalEndpoint(requestStatus: true));
    }
  }

  Future<void> _connectCloud() async {
    final baseUrl = _serverController.text.trim();
    if (baseUrl.isEmpty) {
      _appendLog('请先填写服务器地址');
      return;
    }

    setState(() {
      _cloudConnecting = true;
      _cloudStatus = '连接中...';
    });

    try {
      final health = await _apiService.fetchHealth(baseUrl);
      await _wsService.connect(baseUrl);
      setState(() {
        _cloudConnected = true;
        _cloudStatus = '云端已连接';
      });
      _appendLog('服务端健康检查成功: ${health['ok']}');
      await _registerDirectEndpointIfPossible();
      _requestCloudStatus();
    } catch (error) {
      setState(() {
        _cloudConnected = false;
        _cloudStatus = '连接失败';
      });
      _appendLog('云端连接失败: $error');
    } finally {
      if (mounted) {
        setState(() {
          _cloudConnecting = false;
        });
      }
    }
  }

  Future<void> _disconnectCloud() async {
    if (_cloudConnected) {
      _wsService.sendJson({'type': 'clear_direct_endpoint'});
    }
    await _wsService.disconnect();
    if (!mounted) {
      return;
    }
    setState(() {
      _cloudConnected = false;
      _cloudStatus = '已断开';
    });
    _appendLog('已断开云端连接');
  }

  Future<void> _registerDirectEndpointIfPossible() async {
    if (!_cloudConnected || _localHost == null || _localHost!.isEmpty) {
      return;
    }
    _wsService.sendJson({
      'type': 'register_direct_endpoint',
      'host': _localHost,
      'port': _localPort,
      'path': '/ws/direct',
      'mode': _mode,
    });
    _appendLog('已向服务器登记直连端点: $_localHost:$_localPort/ws/direct');
  }

  Future<void> _refreshLocalEndpoint({bool requestStatus = false}) async {
    final changed = await _localBridge.refreshEndpoint(peerIp: _esp32Ip);
    if (changed) {
      _appendLog('检测到本地直连地址变化，已刷新');
      if (_cloudConnected) {
        await _registerDirectEndpointIfPossible();
      }
    }

    if (_cloudConnected && requestStatus) {
      _statusRefreshCounter++;
      if (_statusRefreshCounter % 2 == 1) {
        _requestCloudStatus();
      }
    }
  }

  void _requestCloudStatus() {
    if (_cloudConnected) {
      _wsService.sendJson({'type': 'get_status'});
    }
  }

  void _tryApplyStatusText(String text) {
    if (!text.startsWith('STATUS:')) {
      return;
    }

    final payload = text.substring('STATUS:'.length);
    final parts = payload.split(',');
    final values = <String, String>{};
    for (final part in parts) {
      final idx = part.indexOf('=');
      if (idx <= 0) {
        continue;
      }
      final key = part.substring(0, idx).trim();
      final value = part.substring(idx + 1).trim();
      values[key] = value;
    }

    final ip = values['ip'];
    if (ip != null && ip.isNotEmpty && ip != '0.0.0.0' && ip != _esp32Ip) {
      _esp32Ip = ip;
      _appendLog('识别到 ESP32 当前 IP: $ip');
      unawaited(_refreshLocalEndpoint());
    }
  }

  Future<void> _sendText() async {
    final text = _messageController.text.trim();
    if (text.isEmpty) {
      _appendLog('文字内容不能为空');
      return;
    }

    if (_sendTarget == 'esp32' || _sendTarget == 'both') {
      if (_mode == 'direct_preferred' && _directConnected) {
        await _localBridge.sendText('TEXT:$text');
        _appendLog('已直连发送给ESP32: $text');
      } else if (_cloudConnected) {
        _wsService.sendJson({
          'type': 'send_text_glasses',
          'text': text,
        });
        _appendLog('已通过服务器转发给ESP32: $text');
      } else {
        _appendLog('ESP32 不可发送: 直连未建立且云端未连接');
      }
    }

    if (_sendTarget == 'server' || _sendTarget == 'both') {
      if (_cloudConnected) {
        _wsService.sendJson({
          'type': 'send_text_server',
          'text': text,
        });
        _appendLog('已发送到服务器/WebUI: $text');
      } else {
        _appendLog('服务器未连接，无法发送服务器消息');
      }
    }

    _messageController.clear();
  }

  Future<void> _requestSnapshot() async {
    if (_mode == 'direct_preferred' && _directConnected) {
      await _localBridge.sendText('SNAP:HQ');
      _appendLog('已通过直连请求ESP32抓拍');
      return;
    }

    if (_cloudConnected) {
      _wsService.sendJson({'type': 'request_snapshot'});
      _appendLog('已通过服务器请求ESP32抓拍');
      return;
    }

    _appendLog('当前没有可用链路请求抓拍');
  }

  Future<void> _requestStatus() async {
    if (_mode == 'direct_preferred' && _directConnected) {
      await _localBridge.sendText('GET_STATUS');
      _appendLog('已通过直连读取ESP32状态');
      return;
    }

    if (_cloudConnected) {
      _wsService.sendJson({'type': 'get_status'});
      _appendLog('已通过服务器读取ESP32状态');
      return;
    }

    _appendLog('当前没有可用链路读取状态');
  }

  Future<void> _uploadImage() async {
    final XFile? file = await _picker.pickImage(source: ImageSource.gallery);
    if (file == null) {
      _appendLog('已取消选择图片');
      return;
    }

    setState(() {
      _uploading = true;
    });

    try {
      final result = await _apiService.uploadImage(
        baseUrl: _serverController.text.trim(),
        file: File(file.path),
        note: 'uploaded_from_flutter',
      );
      _appendLog('图片上传成功: ${result['url']}');
    } catch (error) {
      _appendLog('图片上传失败: $error');
    } finally {
      if (mounted) {
        setState(() {
          _uploading = false;
        });
      }
    }
  }

  void _handleCloudEvent(Map<String, dynamic> event) {
    final type = event['type']?.toString() ?? 'unknown';
    if (!mounted) {
      return;
    }

    switch (type) {
      case 'welcome':
        _appendLog('服务端欢迎消息已收到');
        _updateCloudFrame(event['latest_frame_url']?.toString());
        unawaited(_registerDirectEndpointIfPossible());
        break;
      case 'state':
        final url = event['latest_frame_url']?.toString();
        if (_latestFrameBytes == null) {
          _updateCloudFrame(url);
        }
        break;
      case 'frame_ready':
        if (_latestFrameBytes == null) {
          _updateCloudFrame(event['url']?.toString());
        }
        _appendLog('服务器收到ESP32图片');
        break;
      case 'glasses_text':
      case 'glasses_server_text':
        _appendLog('ESP32云端消息: ${event['text']}');
        _tryApplyStatusText(event['text']?.toString() ?? '');
        break;
      case 'server_text':
      case 'app_server_text':
        _appendLog('服务器/WebUI消息: ${event['text']}');
        break;
      case 'send_text_result':
      case 'request_snapshot_result':
      case 'get_status_result':
      case 'set_fps_result':
      case 'set_framesize_result':
      case 'set_quality_result':
      case 'register_direct_endpoint_result':
        _appendLog('服务端响应: $event');
        break;
      case 'app_image_uploaded':
        _appendLog('服务端已记录手机上传图片: ${event['url']}');
        break;
      case 'socket_closed':
        setState(() {
          _cloudConnected = false;
          _cloudStatus = '云端连接已关闭';
        });
        _appendLog('云端 WebSocket 已关闭');
        break;
      case 'socket_error':
        setState(() {
          _cloudConnected = false;
          _cloudStatus = '云端连接出错';
        });
        _appendLog('云端 WebSocket 错误: ${event['message']}');
        break;
      default:
        _appendLog('收到云端事件: $event');
    }
  }

  void _handleLocalEvent(Map<String, dynamic> event) {
    final type = event['type']?.toString() ?? 'unknown';
    if (!mounted) {
      return;
    }

    switch (type) {
      case 'local_server_ready':
        setState(() {
          _localHost = event['host']?.toString();
          _localPort = (event['port'] as num?)?.toInt() ?? 9100;
          _localStatus = _localHost == null ? '本地服务已启动，但未拿到局域网IP' : '本地服务已启动';
        });
        _appendLog('本地直连服务启动: ${_localHost ?? "未知IP"}:$_localPort/ws/direct');
        unawaited(_registerDirectEndpointIfPossible());
        break;
      case 'direct_connection':
        final status = event['status']?.toString() ?? 'unknown';
        setState(() {
          _directConnected = status == 'connected';
          _localStatus = '直连状态: $status';
        });
        _appendLog('ESP32直连状态: $status');
        if (status == 'connected') {
          unawaited(_localBridge.sendText('GET_STATUS'));
        }
        break;
      case 'direct_text':
        _appendLog('ESP32直连消息: ${event['text']}');
        _tryApplyStatusText(event['text']?.toString() ?? '');
        break;
      case 'direct_frame':
        setState(() {
          _latestFrameBytes = event['bytes'] as Uint8List?;
        });
        break;
      case 'direct_json':
        _appendLog('ESP32直连JSON: ${event['payload']}');
        break;
      case 'local_server_error':
        _appendLog('本地服务错误: ${event['message']}');
        break;
      default:
        _appendLog('收到直连事件: $event');
    }
  }

  void _updateCloudFrame(String? relativeUrl) {
    if (relativeUrl == null || relativeUrl.isEmpty) {
      return;
    }
    final base = _apiService.normalizeBase(_serverController.text.trim());
    setState(() {
      _latestCloudFrameUrl = '$base$relativeUrl';
    });
  }

  void _appendLog(String message) {
    final ts = DateTime.now().toIso8601String().substring(11, 19);
    setState(() {
      _logs.insert(0, '[$ts] $message');
      if (_logs.length > 80) {
        _logs.removeLast();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AI眼镜三端直连测试')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _serverController,
                decoration: const InputDecoration(
                  labelText: '服务器地址',
                  hintText: 'http://139.224.163.108:8000',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 12,
                runSpacing: 12,
                children: [
                  ElevatedButton(
                    onPressed: _cloudConnecting ? null : _connectCloud,
                    child: const Text('连接云端'),
                  ),
                  OutlinedButton(
                    onPressed: _cloudConnected ? _disconnectCloud : null,
                    child: const Text('断开云端'),
                  ),
                  OutlinedButton(
                    onPressed: _requestStatus,
                    child: const Text('读取ESP32状态'),
                  ),
                  OutlinedButton(
                    onPressed: _requestSnapshot,
                    child: const Text('请求抓拍'),
                  ),
                  OutlinedButton(
                    onPressed: _uploading ? null : _uploadImage,
                    child: Text(_uploading ? '上传中...' : '上传手机图片到服务器'),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text('云端状态: $_cloudStatus'),
              Text('本地状态: $_localStatus'),
              Text('本地端点: ${_localHost ?? "未识别"}:$_localPort/ws/direct'),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: _mode,
                decoration: const InputDecoration(
                  labelText: '通信模式',
                  border: OutlineInputBorder(),
                ),
                items: const [
                  DropdownMenuItem(value: 'direct_preferred', child: Text('直连优先，云端回退')),
                  DropdownMenuItem(value: 'cloud_only', child: Text('仅云端中转')),
                ],
                onChanged: (value) {
                  if (value == null) {
                    return;
                  }
                  setState(() {
                    _mode = value;
                  });
                  unawaited(_registerDirectEndpointIfPossible());
                },
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: _sendTarget,
                decoration: const InputDecoration(
                  labelText: '发送目标',
                  border: OutlineInputBorder(),
                ),
                items: const [
                  DropdownMenuItem(value: 'esp32', child: Text('发给 ESP32')),
                  DropdownMenuItem(value: 'server', child: Text('发给 服务器/WebUI')),
                  DropdownMenuItem(value: 'both', child: Text('同时发给 ESP32 和 服务器')),
                ],
                onChanged: (value) {
                  if (value == null) {
                    return;
                  }
                  setState(() {
                    _sendTarget = value;
                  });
                },
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _messageController,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: '发送文字',
                  hintText: '你可以测 App -> ESP32 / Server / Both',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: _sendText,
                child: const Text('发送文字'),
              ),
              const SizedBox(height: 20),
              const Text(
                '图像预览',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              AspectRatio(
                aspectRatio: 4 / 3,
                child: Container(
                  color: Colors.black12,
                  alignment: Alignment.center,
                  child: _latestFrameBytes != null
                      ? Image.memory(
                          _latestFrameBytes!,
                          fit: BoxFit.contain,
                          gaplessPlayback: true,
                        )
                      : _latestCloudFrameUrl != null
                          ? Image.network(
                              _latestCloudFrameUrl!,
                              fit: BoxFit.contain,
                              gaplessPlayback: true,
                              errorBuilder: (_, __, ___) => const Text('云端图片加载失败'),
                            )
                          : const Text('暂无图片'),
                ),
              ),
              const SizedBox(height: 20),
              const Text(
                '运行日志',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Container(
                height: 320,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  border: Border.all(color: Colors.black12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: _logs.isEmpty
                    ? const Center(child: Text('暂无日志'))
                    : ListView.builder(
                        itemCount: _logs.length,
                        itemBuilder: (context, index) => Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Text(_logs[index]),
                        ),
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
