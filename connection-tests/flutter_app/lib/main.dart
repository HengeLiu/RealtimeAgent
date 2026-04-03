import 'package:flutter/material.dart';

import 'screens/test_screen.dart';

void main() {
  runApp(const AiGlassesTestApp());
}

class AiGlassesTestApp extends StatelessWidget {
  const AiGlassesTestApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AI Glasses Test',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: const TestScreen(),
    );
  }
}
