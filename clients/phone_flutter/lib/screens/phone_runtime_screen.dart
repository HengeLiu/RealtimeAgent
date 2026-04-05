import 'package:flutter/material.dart';

import '../models/detector_backend_models.dart';
import '../runtime/phone_runtime_controller.dart';

class PhoneRuntimeScreen extends StatefulWidget {
  const PhoneRuntimeScreen({super.key});

  @override
  State<PhoneRuntimeScreen> createState() => _PhoneRuntimeScreenState();
}

class _PhoneRuntimeScreenState extends State<PhoneRuntimeScreen> {
  final _serverController = TextEditingController(text: 'http://192.168.10.5:18490');
  final _deviceController = TextEditingController(text: 'phone-001');
  final _portController = TextEditingController(text: '19092');

  PhoneRuntimeController? _runtime;
  Map<String, dynamic>? _snapshot;
  bool _starting = false;
  String? _startupError;

  DetectorBackendType? get _selectedBackend => _runtime?.selectedBackend;

  @override
  void dispose() {
    _runtime?.stop();
    _serverController.dispose();
    _deviceController.dispose();
    _portController.dispose();
    super.dispose();
  }

  Future<void> _startRuntime() async {
    setState(() {
      _starting = true;
      _startupError = null;
    });
    final runtime = PhoneRuntimeController(
      deviceId: _deviceController.text.trim(),
      serverBaseUrl: _serverController.text.trim(),
      listenPort: int.tryParse(_portController.text.trim()) ?? 19092,
    );
    try {
      await runtime.start();
      final snapshot = await runtime.fetchSnapshot();
      if (!mounted) {
        return;
      }
      setState(() {
        _runtime = runtime;
        _snapshot = snapshot;
        _starting = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _runtime = runtime;
        _starting = false;
        _startupError = error.toString();
      });
    }
  }

  Future<void> _refreshSnapshot() async {
    if (_runtime == null) {
      return;
    }
    final snapshot = await _runtime!.fetchSnapshot();
    if (!mounted) {
      return;
    }
    setState(() {
      _snapshot = snapshot;
    });
  }

  @override
  Widget build(BuildContext context) {
    final runtime = _runtime;
    final startupHint = _buildStartupHint(_startupError);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Nextgen Phone Runtime'),
        actions: [
          IconButton(
            onPressed: runtime == null ? null : _refreshSnapshot,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _serverController,
            decoration: const InputDecoration(labelText: 'Server Base URL'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _deviceController,
            decoration: const InputDecoration(labelText: 'Device ID'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _portController,
            decoration: const InputDecoration(labelText: 'Local Listen Port'),
            keyboardType: TextInputType.number,
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _starting || runtime != null ? null : _startRuntime,
            child: Text(_starting ? '启动中...' : '启动手机端通信壳'),
          ),
          const SizedBox(height: 24),
          if (_startupError != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '启动失败：$_startupError',
                    style: TextStyle(color: Theme.of(context).colorScheme.error),
                  ),
                  if (startupHint != null) ...[
                    const SizedBox(height: 8),
                    Text(startupHint),
                  ],
                ],
              ),
            ),
          _InfoCard(
            title: '本机状态',
            child: Text(runtime == null
                ? '尚未启动'
                : 'localHost=${runtime.localHost ?? 'unknown'}\npeerSessions=${runtime.peerSessions.length}\nbackend=${runtime.selectedBackend.name}\nlogFile=${runtime.logFilePath ?? 'preparing...'}'),
          ),
          const SizedBox(height: 16),
          if (runtime != null)
            _InfoCard(
              title: '检测后端',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  DropdownButtonFormField<DetectorBackendType>(
                    initialValue: _selectedBackend,
                    decoration: const InputDecoration(
                      labelText: '检测后端',
                      border: OutlineInputBorder(),
                    ),
                    items: runtime.detectorConfigs
                        .map(
                          (config) => DropdownMenuItem<DetectorBackendType>(
                            value: config.type,
                            enabled: config.enabled,
                            child: Text(
                              config.enabled
                                  ? config.displayName
                                  : '${config.displayName}（未启用）',
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: (value) {
                      if (value == null) {
                        return;
                      }
                      setState(() {
                        runtime.selectDetectorBackend(value);
                      });
                    },
                  ),
                  const SizedBox(height: 12),
                  ...runtime.detectorConfigs.map(
                    (config) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        '${config.displayName}：'
                        '${config.enabled ? '可用' : '占位未启用'}'
                        '${config.modelAssetPath == null ? '' : '\n模型路径：${config.modelAssetPath}'}',
                      ),
                    ),
                  ),
                ],
              ),
            ),
          if (runtime != null) const SizedBox(height: 16),
          _InfoCard(
            title: 'Server Snapshot',
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(_snapshot?.toString() ?? '暂无'),
            ),
          ),
          const SizedBox(height: 16),
          _InfoCard(
            title: 'Runtime Logs',
            child: Text(runtime == null ? '暂无' : runtime.logs.join('\n\n')),
          ),
        ],
      ),
    );
  }

  String? _buildStartupHint(String? error) {
    if (error == null) {
      return null;
    }
    if (error.contains('No route to host') || error.contains('errno = 65')) {
      return '排查建议：\n'
          '1. 到 iPhone 的“设置 -> 隐私与安全性 -> 本地网络”，确认 Nextgen Phone Flutter 已开启；\n'
          '2. 删除手机上的旧 App 后重新安装；\n'
          '3. 确认 Server Base URL 仍为 http://192.168.10.5:18490；\n'
          '4. 若仍失败，先关闭手机上的 VPN / 代理类 App 再重试。';
    }
    return null;
  }
}

class _InfoCard extends StatelessWidget {
  const _InfoCard({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    );
  }
}
