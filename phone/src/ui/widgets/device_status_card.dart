import 'package:flutter/material.dart';

class DeviceStatusCard extends StatelessWidget {
  const DeviceStatusCard({
    super.key,
    required this.deviceId,
    required this.status,
  });

  final String deviceId;
  final String status;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        title: Text('Device: $deviceId'),
        subtitle: Text('Status: $status'),
      ),
    );
  }
}

