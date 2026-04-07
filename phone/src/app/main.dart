import 'package:flutter/material.dart';

import 'bootstrap.dart';
import '../ui/pages/video_link_page.dart';

void main() {
  final container = bootstrapPhoneApp((payload) {
    // ignore: avoid_print
    print('phone outbound: $payload');
  });
  container.start(auth: const {'token': 'masked'});

  runApp(const PhoneApp());
}

class PhoneApp extends StatelessWidget {
  const PhoneApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'OpenAI Glasses Phone Runtime',
      theme: ThemeData(useMaterial3: true),
      home: const VideoLinkPage(),
    );
  }
}
