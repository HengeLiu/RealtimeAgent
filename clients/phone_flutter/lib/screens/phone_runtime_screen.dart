import 'package:flutter/material.dart';

import '../models/detector_backend_models.dart';
import '../runtime/phone_runtime_controller.dart';

class PhoneRuntimeScreen extends StatefulWidget {
  const PhoneRuntimeScreen({super.key});

  @override
  State<PhoneRuntimeScreen> createState() => _PhoneRuntimeScreenState();
}

class _PhoneRuntimeScreenState extends State<PhoneRuntimeScreen> {
  final _serverController = TextEditingController(text: 'http://127.0.0.1:18090');
  final _deviceController = TextEditingController(text: 'phone-001');
  final _portController = TextEditingController(text: '19092');

  PhoneRuntimeController? _runtime;
  Map<String, dynamic>? _snapshot;
  bool _starting = false;

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
    });
    final runtime = PhoneRuntimeController(
      deviceId: _deviceController.text.trim(),
      serverBaseUrl: _serverController.text.trim(),
      listenPort: int.tryParse(_portController.text.trim()) ?? 19092,
    );
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
          _InfoCard(
            title: '本机状态',
            child: Text(runtime == null
                ? '尚未启动'
                : 'localHost=${runtime.localHost ?? 'unknown'}\npeerSessions=${runtime.peerSessions.length}\nbackend=${runtime.selectedBackend.name}'),
          ),
          const SizedBox(height: 16),
          if (runtime != null)
            _InfoCard(
              title: '检测后端',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: runtime.detectorConfigs
                    .map(
                      (config) => RadioListTile<DetectorBackendType>(
                        value: config.type,
                        groupValue: runtime.selectedBackend,
                        onChanged: config.enabled
                            ? (value) {
                                if (value == null) {
                                  return;
                                }
                                setState(() {
                                  runtime.selectDetectorBackend(value);
                                });
                              }
                            : null,
                        title: Text(config.displayName),
                        subtitle: Text(
                          config.enabled
                              ? '可用'
                              : '占位未启用${config.modelAssetPath == null ? '' : '\n${config.modelAssetPath}'}',
                        ),
                      ),
                    )
                    .toList(),
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
